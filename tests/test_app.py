import base64
import hashlib
import io
import json
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from PIL import Image
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from authlib.jose import JsonWebKey, jwt
from app.main import create_app, roles_at
from app.config import Settings
from app.db import connect


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(secret='test-only-secret-'*4, data_dir=tmp_path, base_url='http://testserver',
        discovery_url='https://id.example/.well-known/openid-configuration',client_id='linkforge',client_secret='secret'))

@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def authorize(client, app, expires=None):
    sid='test-session'; csrf='test-csrf'
    with connect(app.state.db) as db:
        db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?)',
         (hashlib.sha256(sid.encode()).hexdigest(),'test-user','Admin',expires or int(time.time())+3600,csrf))
    cookie=TimestampSigner(app.state.settings.secret).sign(base64.b64encode(json.dumps({'sid':sid}).encode())).decode()
    client.cookies.set('linkforge_session',cookie)
    return {'X-CSRF-Token':csrf}


def page(**kw):
    return dict(slug='afterglow',name='Afterglow',artist='North / Static',links=[dict(label='Spotify',url='https://open.spotify.com/album/example',icon='spotify')],**kw)


def test_protected_routes_and_csrf(client,app):
    assert client.get('/admin/new').status_code==401
    assert client.post('/api/pages',json=page()).status_code==401
    headers=authorize(client,app)
    assert client.get('/admin/new').status_code==200
    assert client.post('/api/pages',json=page()).status_code==403
    assert client.post('/api/pages',json=page(),headers=headers).status_code==200
    assert client.post('/auth/logout',headers=headers).status_code==200
    assert client.get('/admin/new').status_code==401


def test_crud_slug_conflict_drafts_and_revision(client,app):
    headers=authorize(client,app)
    r=client.post('/api/pages',json=page(),headers=headers).json()
    assert client.get('/r/afterglow').status_code==404
    assert client.get(f'/admin/pages/{r["id"]}/preview').status_code==200
    assert client.post('/api/pages',json=page(),headers=headers).status_code==409
    payload=page(published=True,version=1)
    assert client.put('/api/pages/'+r['id'],json=payload,headers=headers).status_code==200
    assert client.get('/r/afterglow').status_code==200
    assert client.put('/api/pages/'+r['id'],json=payload,headers=headers).status_code==409
    payload.update(slug='new-name',version=2)
    assert client.put('/api/pages/'+r['id'],json=payload,headers=headers).status_code==200
    assert client.get('/r/afterglow').status_code==404
    assert client.get('/r/new-name').status_code==200
    assert client.delete('/api/pages/'+r['id'],headers=headers).status_code==200
    assert client.get('/r/new-name').status_code==404


@pytest.mark.parametrize('change',[
 {'slug':'../admin'}, {'accent':'red; background:url(https://evil)'},
 {'cover':'https://evil.test/a.png'}, {'meta_pixel':'</script>'},
 {'google_ads_id':'AW-123'}, {'links':[{'label':'bad','url':'javascript:alert(1)'}]},
 {'links':[{'label':'bad','url':'https://user:secret@example.org'}]},
 {'links':[{'label':'bad','url':'https://example.org','icon':'../../a'}]},
])
def test_invalid_data(client,app,change):
    payload=page();payload.update(change)
    assert client.post('/api/pages',json=payload,headers=authorize(client,app)).status_code==422


def test_upload_validation_and_escape(client,app):
    headers=authorize(client,app)
    image=io.BytesIO();Image.new('RGB',(50,50),'red').save(image,'PNG')
    r=client.post('/api/uploads',headers=headers,files={'file':('art.png',image.getvalue(),'image/png')})
    assert r.status_code==200
    assert client.get(r.json()['url']).headers['content-type']=='image/webp'
    assert client.post('/api/uploads',headers=headers,files={'file':('bad.svg',b'<svg onload="alert(1)"/>','image/svg+xml')}).status_code==400
    payload=page(published=True);payload['name']='<script>alert(1)</script>';payload['cover']=r.json()['url']
    client.post('/api/pages',json=payload,headers=headers)
    html=client.get('/r/afterglow').text
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html
    assert 'Content-Security-Policy' in client.get('/r/afterglow').headers


def test_preview_strips_tracking_and_persistence(client,app):
    headers=authorize(client,app)
    payload=page(meta_pixel='123456789',google_ads_id='AW-123',google_ads_label='abcdef',privacy_text='Example privacy',published=True)
    result=client.post('/api/pages',json=payload,headers=headers).json()
    html=client.get('/admin/pages/'+result['id']+'/preview').text
    assert '123456789' not in html
    assert 'AW-123' not in html
    assert '123456789' in client.get('/r/afterglow').text
    with TestClient(create_app(app.state.settings)) as second:
        assert second.get('/r/afterglow').status_code==200


def test_expired_session(client,app):
    authorize(client,app,expires=int(time.time())-1)
    assert client.get('/admin/new').status_code==401


def test_role_matching():
    assert roles_at({'roles':['link_admin']},'roles')==['link_admin']
    assert roles_at({'realm_access':{'roles':['link_admin']}},'realm_access.roles')==['link_admin']
    assert 'link_admin' not in roles_at({'roles':['not_link_admin']},'roles')


@pytest.mark.parametrize('role,signature_valid,expected,override',[
 ('link_admin',True,303,{}),('listener',True,403,{}),('not_link_admin',True,403,{}),('link_admin',False,400,{}),
 ('link_admin',True,400,{'iss':'https://wrong.example'}),
 ('link_admin',True,400,{'aud':'other-app'}),
 ('link_admin',True,400,{'nonce':'wrong-nonce'}),
 ('link_admin',True,400,{'exp':1})])
@respx.mock
def test_real_oidc_code_exchange(client,app,role,signature_valid,expected,override):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    key=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    jwk=JsonWebKey.import_key(key,{'kid':'test-key'})
    discovery={'issuer':'https://id.example','authorization_endpoint':'https://id.example/authorize',
      'token_endpoint':'https://id.example/token','jwks_uri':'https://id.example/jwks',
      'response_types_supported':['code'],'id_token_signing_alg_values_supported':['RS256'],
      'token_endpoint_auth_methods_supported':['client_secret_basic']}
    respx.get(app.state.settings.discovery_url).mock(return_value=httpx.Response(200,json=discovery))
    respx.get('https://id.example/jwks').mock(return_value=httpx.Response(200,json={'keys':[jwk.as_dict(is_private=False)]}))
    redirect=client.get('/auth/login',follow_redirects=False)
    args=parse_qs(urlsplit(redirect.headers['location']).query)
    assert args['code_challenge_method']==['S256']
    assert args['redirect_uri']==['http://testserver/auth/callback']
    signing_key=key if signature_valid else rsa.generate_private_key(public_exponent=65537,key_size=2048).private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    id_token=jwt.encode({'alg':'RS256','kid':'test-key'},{'iss':'https://id.example','aud':'linkforge',
      'sub':'artist','exp':int(time.time())+3600,'iat':int(time.time()),'nonce':args['nonce'][0],
      'roles':[role],**override},signing_key).decode()
    def token_response(request):
        assert 'code_verifier=' in request.content.decode()
        return httpx.Response(200,json={'access_token':'opaque-access-token','token_type':'Bearer','id_token':id_token})
    respx.post('https://id.example/token').mock(side_effect=token_response)
    result=client.get('/auth/callback',params={'code':'auth-code','state':args['state'][0]},follow_redirects=False)
    assert result.status_code==expected, result.text
    assert client.get('/admin/new').status_code==(200 if expected==303 else 401)
    # The ID/access tokens must never be stored in the browser session cookie.
    assert 'opaque-access-token' not in str(client.cookies)


def test_bad_oidc_state(client):
    assert client.get('/auth/callback?code=x&state=forged').status_code==400


def test_all_presets_have_bundled_icons(app):
    from app.main import SERVICES, ROOT
    assert {'spotify','kkbox','qobuz','netease'} <= {s['id'] for s in SERVICES}
    for service in SERVICES:
        assert (ROOT/'static/icons'/f'{service["icon"]}.svg').is_file()
