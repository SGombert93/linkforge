"""Optional end-to-end smoke test: pip install playwright; playwright install chromium.
Run from the project root: python tests/browser_smoke.py
Uses an isolated database and a signed test session; production has no auth bypass.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from itsdangerous import TimestampSigner
from PIL import Image
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as temp:
    env=dict(os.environ,SESSION_SECRET=secrets.token_urlsafe(48),DATA_DIR=temp,BASE_URL='http://127.0.0.1:8765',OIDC_DISCOVERY_URL='')
    process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:create_app','--factory','--host','127.0.0.1','--port','8765'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen('http://127.0.0.1:8765/healthz',timeout=1);break
            except Exception:time.sleep(.1)
        else:raise RuntimeError(process.stderr.read().decode())
        sid=secrets.token_urlsafe(32)
        db=sqlite3.connect(Path(temp)/'linkforge.sqlite3')
        db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',(hashlib.sha256(sid.encode()).hexdigest(),'test','Test Artist',int(time.time())+3600,'browser-test-csrf'));db.commit();db.close()
        signed=TimestampSigner(env['SESSION_SECRET']).sign(base64.b64encode(json.dumps({'sid':sid}).encode())).decode()
        with sync_playwright() as pw:
            browser=pw.chromium.launch(args=['--no-sandbox'])
            context=browser.new_context(viewport={'width':1360,'height':1000})
            context.add_cookies([{'name':'linkforge_session','value':signed,'url':'http://127.0.0.1:8765','httpOnly':True,'sameSite':'Lax'}])
            errors=[];external=[]
            def intercept(route):
                if route.request.url.startswith('http://127.0.0.1:8765'):
                    return route.continue_()
                external.append(route.request.url)
                route.fulfill(status=200,content_type='application/javascript',body='')
            context.route('**/*',intercept)
            page=context.new_page();page.on('pageerror',lambda err:errors.append(str(err)))
            page.goto('http://127.0.0.1:8765/admin/new')
            page.get_by_label('Title / page name').fill('Afterglow')
            page.get_by_label('Artist / band').fill('North / Static')
            page.get_by_label('URL stub').fill('afterglow')
            image=io.BytesIO();Image.new('RGB',(700,700),'#531f36').save(image,'PNG')
            page.locator('[data-upload="cover"]').set_input_files({'name':'cover.png','mimeType':'image/png','buffer':image.getvalue()})
            expect(page.locator('[data-upload="cover"]')).to_be_enabled()
            page.get_by_role('button',name='Appearance',exact=True).click()
            page.locator('[name=accent]').fill('#922f48')
            page.locator('[name=opacity]').fill('65')
            page.get_by_role('button',name='Links',exact=True).click()
            for service,url in [('spotify','https://open.spotify.com/album/test'),('deezer','https://deezer.com/album/test')]:
                page.locator('#service-picker').select_option(service)
                page.get_by_role('button',name='＋ Add link',exact=True).click()
                page.get_by_label('Destination URL',exact=True).last.fill(url)
            page.get_by_role('button',name='Tracking & legal').click()
            page.get_by_label('Meta Pixel ID').fill('123456789')
            page.get_by_label('Google Ads conversion ID').fill('AW-123456')
            page.get_by_label('Google Ads conversion label').fill('testLabel')
            page.get_by_label('GA4 measurement ID (optional)').fill('G-ABC123')
            page.get_by_label('Privacy information',exact=True).fill('Test operator privacy information.')
            page.get_by_label('Imprint / legal notice').fill('Test Artist, Example street.')
            page.get_by_role('button',name='Release',exact=True).click()
            page.get_by_label('Published — visible at its public URL').check()
            page.get_by_role('button',name='Save page',exact=True).click()
            expect(page.locator('#save-status')).to_have_text('Saved · published')
            page.frame_locator('#preview').locator('h1').filter(has_text='Afterglow').wait_for()
            assert not external,external
            assert page.frame_locator('#preview').locator('.service').count()==2
            screenshots=Path(os.getenv('QA_SCREENSHOTS','/workspace/linkforge-qa'));screenshots.mkdir(exist_ok=True)
            page.screenshot(path=str(screenshots/'admin.png'),full_page=True)
            # Save and reload to verify editor state is persisted.
            page.reload();assert page.get_by_label('Title / page name').input_value()=='Afterglow'
            page.goto('http://127.0.0.1:8765/r/afterglow')
            assert page.locator('.service').count()==2
            assert page.locator('#consent').is_visible()
            assert not external
            page.get_by_role('button',name='Reject',exact=True).click();page.reload()
            assert not page.locator('#consent').is_visible();assert not external
            page.get_by_role('button',name='Cookie settings',exact=True).click()
            page.get_by_role('button',name='Accept',exact=True).click()
            expect(page.locator('script[src*=googletagmanager],script[src*=facebook]')).to_have_count(2)
            page.locator('.service').first.dispatch_event('click',{'ctrlKey':True,'button':0})
            page.locator('.service').last.dispatch_event('click',{'ctrlKey':True,'button':0})
            g=page.evaluate('dataLayer.map(x=>Array.from(x))')
            f=page.evaluate('fbq.queue.map(x=>Array.from(x))')
            assert len([e for e in g if e[0:2]==['event','conversion']])==1,g
            assert len([e for e in g if e[0:2]==['event','streaming_link_click']])==2,g
            assert len([e for e in f if e[0:2]==['trackCustom','StreamingLinkClick']])==1,f
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert page.locator('.icon').evaluate_all('(imgs)=>imgs.every(i=>i.complete&&i.naturalWidth>0)')
            page.screenshot(path=str(screenshots/'release-mobile.png'),full_page=True)
            page.get_by_role('button',name='Privacy',exact=True).click();assert page.locator('#privacy').is_visible()
            page.get_by_role('button',name='Close',exact=True).click()
            page.get_by_role('button',name='Cookie settings',exact=True).click()
            before=len(external)
            page.get_by_role('button',name='Reject',exact=True).click();page.wait_for_load_state('load')
            assert len(external)==before
            assert not errors,errors
            browser.close()
            print('Browser smoke passed: editor, upload, save/reload, preview, mobile layout, icons, consent, conversion deduplication, revocation. Screenshots:',screenshots)
    finally:
        process.terminate();process.wait(timeout=10)
