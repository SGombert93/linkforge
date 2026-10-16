import hashlib
import io
import json
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from urllib.parse import urlsplit

from .config import Settings
from .db import connect, initialize
from .models import Page

ROOT = Path(__file__).parent
SERVICES = json.loads((ROOT / 'services.json').read_text())
Image.MAX_IMAGE_PIXELS = 25_000_000


def roles_at(claims, path):
    value = claims
    for part in path.split('.'):
        value = value.get(part) if isinstance(value, dict) else None
    if isinstance(value, str):
        return value.split()
    return [r for r in value if isinstance(r, str)] if isinstance(value, list) else []


def require_admin(request: Request):
    sid = request.session.get('sid', '')
    with connect(request.app.state.db) as db:
        session = db.execute('SELECT * FROM sessions WHERE id=? AND expires>?',
                             (hashlib.sha256(sid.encode()).hexdigest(), int(time.time()))).fetchone()
    if not session:
        raise HTTPException(401, 'Please sign in with your administrator account.')
    return dict(session)


def require_csrf(request: Request, user=Depends(require_admin)):
    token = request.headers.get('x-csrf-token', '')
    if not secrets.compare_digest(token, user['csrf']):
        raise HTTPException(403, 'Invalid CSRF token. Reload the editor.')
    return user


class BodyLimit:
    def __init__(self, app, limit=12 * 1024 * 1024):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PUT', 'PATCH'):
            return await self.app(scope, receive, send)
        # Buffer up to a fixed limit before multipart/JSON parsing (including chunked bodies).
        messages, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            size += len(message.get('body', b''))
            if size > self.limit:
                return await JSONResponse({'detail': 'Request exceeds 12 MiB.'}, 413)(scope, receive, send)
            messages.append(message)
            if not message.get('more_body', False):
                break
        async def replay():
            return messages.pop(0) if messages else await receive()
        await self.app(scope, replay, send)


def create_app(settings=None):
    settings = settings or Settings()
    settings.validate()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    uploads = settings.data_dir / 'uploads'
    uploads.mkdir(exist_ok=True)
    app = FastAPI(title='LinkForge', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.db = settings.data_dir / 'linkforge.sqlite3'
    initialize(app.state.db)
    templates = Jinja2Templates(directory=str(ROOT / 'templates'))
    app.add_middleware(SessionMiddleware, secret_key=settings.secret, session_cookie='linkforge_session',
                       same_site='lax', https_only=settings.base_url.startswith('https:'), max_age=settings.session_seconds)
    app.add_middleware(BodyLimit)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.base_url).hostname, 'localhost', '127.0.0.1'])
    oauth = OAuth()
    if settings.discovery_url:
        oauth.register(name='provider', client_id=settings.client_id, client_secret=settings.client_secret,
                       server_metadata_url=settings.discovery_url,
                       client_kwargs={'scope': settings.scope, 'code_challenge_method': 'S256'})
    app.state.oauth = oauth
    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
    app.mount('/uploads', StaticFiles(directory=uploads), name='uploads')

    @app.middleware('http')
    async def headers(request, call_next):
        request.state.nonce = secrets.token_urlsafe(24)
        response = await call_next(request)
        nonce = request.state.nonce
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'self'; "
            "form-action 'self'; img-src 'self' data: https://www.facebook.com https://*.google.com https://*.google.de https://*.googleadservices.com https://*.doubleclick.net; "
            f"script-src 'self' 'nonce-{nonce}' https://connect.facebook.net https://www.googletagmanager.com https://www.googleadservices.com https://googleads.g.doubleclick.net; "
            "style-src 'self' 'unsafe-inline'; font-src 'self'; "
            "connect-src 'self' https://*.facebook.com https://*.google-analytics.com https://*.analytics.google.com https://*.google.com https://*.google.de https://*.googleadservices.com https://*.doubleclick.net; "
            "frame-src 'self' https://www.googletagmanager.com https://*.doubleclick.net;")
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        if request.url.path.startswith(('/admin', '/api', '/auth')):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        if settings.base_url.startswith('https:'):
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    def render(request, template, **context):
        return templates.TemplateResponse(request=request, name=template, context=context)

    def get_page(page_id):
        with connect(app.state.db) as db:
            row = db.execute('SELECT * FROM pages WHERE id=?', (page_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Page not found.')
        page = json.loads(row['document'])
        page.update(id=row['id'], version=row['version'])
        return page

    @app.get('/healthz')
    def health():
        with connect(app.state.db) as db:
            db.execute('SELECT 1')
        return {'status': 'ok'}

    @app.get('/')
    def home():
        return RedirectResponse('/admin', status_code=303)

    @app.get('/auth/login')
    async def login(request: Request):
        if not settings.discovery_url or not settings.client_id or not settings.client_secret:
            return render(request, 'message.html', title='Connect your identity provider',
                          message='Set OIDC_DISCOVERY_URL, OIDC_CLIENT_ID and OIDC_CLIENT_SECRET, then restart the application.', status=503)
        # Clear stale state/session; no user-provided redirect URI.
        request.session.clear()
        return await oauth.provider.authorize_redirect(request, settings.base_url + '/auth/callback')

    @app.get('/auth/callback')
    async def callback(request: Request):
        if not settings.discovery_url:
            raise HTTPException(503, 'OIDC is not configured.')
        try:
            token = await oauth.provider.authorize_access_token(request)
            claims = token.get('userinfo')
            if not token.get('id_token') or not claims or not claims.get('sub'):
                raise ValueError('An authenticated OIDC ID token is required.')
            role_claims = claims
            if settings.role_source == 'userinfo':
                role_claims = await oauth.provider.userinfo(token=token)
                if role_claims.get('sub') != claims['sub']:
                    raise ValueError('Userinfo subject mismatch.')
            allowed = 'link_admin' in roles_at(role_claims, settings.role_claim)
            expiry = min(int(time.time()) + settings.session_seconds, int(claims['exp']))
            if expiry <= time.time():
                raise ValueError('Expired identity.')
        except Exception:
            request.session.clear()
            # Never reflect provider errors, codes or tokens into HTML/logs.
            raise HTTPException(400, 'Sign-in failed. Start a new sign-in attempt and check your OIDC configuration.')
        request.session.clear()
        if not allowed:
            raise HTTPException(403, 'The link_admin role is required.')
        sid = secrets.token_urlsafe(32)
        with connect(app.state.db) as db:
            db.execute('DELETE FROM sessions WHERE expires<=?', (int(time.time()),))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',
                       (hashlib.sha256(sid.encode()).hexdigest(), claims['sub'],
                        str(claims.get('name') or claims.get('preferred_username') or claims['sub']),
                        expiry, secrets.token_urlsafe(32)))
        request.session['sid'] = sid
        return RedirectResponse('/admin', status_code=303)

    @app.post('/auth/logout')
    def logout(request: Request, user=Depends(require_csrf)):
        with connect(app.state.db) as db:
            db.execute('DELETE FROM sessions WHERE id=?', (user['id'],))
        request.session.clear()
        return {'ok': True}

    @app.get('/admin', response_class=HTMLResponse)
    def dashboard(request: Request):
        try:
            user = require_admin(request)
        except HTTPException:
            return render(request, 'login.html')
        with connect(app.state.db) as db:
            pages = [dict(r) for r in db.execute('SELECT id,slug,name,published,updated_at FROM pages ORDER BY updated_at DESC')]
        return render(request, 'dashboard.html', user=user, pages=pages)

    @app.get('/admin/new', response_class=HTMLResponse)
    def new(request: Request, user=Depends(require_admin)):
        page = Page(slug='new-release', name='New release').model_dump()
        return render(request, 'editor.html', user=user, page=page, services=SERVICES, page_id='', base_url=settings.base_url)

    @app.get('/admin/pages/{page_id}', response_class=HTMLResponse)
    def editor(page_id: str, request: Request, user=Depends(require_admin)):
        return render(request, 'editor.html', user=user, page=get_page(page_id), services=SERVICES, page_id=page_id, base_url=settings.base_url)

    @app.get('/api/services')
    def services(user=Depends(require_admin)):
        return SERVICES

    @app.post('/api/pages')
    def create(page: Page, user=Depends(require_csrf)):
        page_id = uuid.uuid4().hex
        doc = page.model_dump()
        doc['version'] = 1
        try:
            with connect(app.state.db) as db:
                db.execute('INSERT INTO pages(id,slug,name,published,document) VALUES (?,?,?,?,?)',
                           (page_id, page.slug, page.name, int(page.published), json.dumps(doc)))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'That URL stub is already in use.')
        return {'id': page_id, 'version': 1, 'url': '/r/' + page.slug}

    @app.put('/api/pages/{page_id}')
    def update(page_id: str, page: Page, user=Depends(require_csrf)):
        get_page(page_id)
        doc = page.model_dump()
        doc['version'] += 1
        try:
            with connect(app.state.db) as db:
                cur = db.execute('UPDATE pages SET slug=?,name=?,published=?,document=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=? AND version=?',
                                 (page.slug, page.name, int(page.published), json.dumps(doc), page_id, page.version))
                if not cur.rowcount:
                    raise HTTPException(409, 'This page changed in another tab. Reload before saving.')
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'That URL stub is already in use.')
        return {'id': page_id, 'version': doc['version'], 'url': '/r/' + page.slug}

    @app.delete('/api/pages/{page_id}')
    def delete(page_id: str, user=Depends(require_csrf)):
        with connect(app.state.db) as db:
            if not db.execute('DELETE FROM pages WHERE id=?', (page_id,)).rowcount:
                raise HTTPException(404, 'Page not found.')
        return {'ok': True}

    @app.post('/api/uploads')
    async def upload(file: UploadFile = File(...), user=Depends(require_csrf)):
        raw = await file.read(10 * 1024 * 1024 + 1)
        if len(raw) > 10 * 1024 * 1024:
            raise HTTPException(413, 'Images must be 10 MiB or smaller.')
        try:
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in ('PNG', 'JPEG', 'WEBP'):
                    raise ValueError('Unsupported image format.')
                if source.width * source.height > 25_000_000:
                    raise ValueError('Image exceeds 25 megapixels.')
                image = ImageOps.exif_transpose(source).convert('RGBA')
                image.thumbnail((2560, 2560))
                name = uuid.uuid4().hex + '.webp'
                image.save(uploads / name, 'WEBP', quality=88)
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise HTTPException(400, 'Upload a valid PNG, JPEG or WebP image up to 25 megapixels.')
        return {'url': '/uploads/' + name}

    def public_response(request, page, preview=False):
        page = dict(page)
        if preview:
            page.update(meta_pixel='', google_ads_id='', google_ads_label='', ga4_id='')
        tracking = {k: page[k] for k in ('meta_pixel', 'google_ads_id', 'google_ads_label', 'ga4_id')}
        tracking.update(release_id=page.get('id', ''), release_slug=page['slug'], preview=preview)
        # Unknown presets get a bundled generic icon. Never reflect arbitrary file paths.
        for link in page['links']:
            if not (ROOT / 'static' / 'icons' / (link['icon'] + '.svg')).is_file():
                link['icon'] = 'music'
        response = render(request, 'release.html', page=page, tracking=tracking, preview=preview,
                          canonical=settings.base_url + '/r/' + page['slug'])
        response.headers['Cache-Control'] = 'no-store'
        if preview:
            response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        return response

    @app.post('/api/preview', response_class=HTMLResponse)
    def live_preview(request: Request, page: Page, user=Depends(require_csrf)):
        return public_response(request, page.model_dump(), preview=True)

    @app.get('/admin/pages/{page_id}/preview', response_class=HTMLResponse)
    def saved_preview(page_id: str, request: Request, user=Depends(require_admin)):
        return public_response(request, get_page(page_id), preview=True)

    @app.get('/r/{slug}', response_class=HTMLResponse)
    def release(slug: str, request: Request):
        with connect(app.state.db) as db:
            row = db.execute('SELECT id,document FROM pages WHERE slug=? AND published=1', (slug,)).fetchone()
        if not row:
            raise HTTPException(404, 'Release not found.')
        page = json.loads(row['document'])
        page['id'] = row['id']
        return public_response(request, page)

    return app
