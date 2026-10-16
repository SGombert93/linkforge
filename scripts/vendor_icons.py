"""Refresh trusted, bundled Simple Icons. Missing marks use explicit letter badges."""
import concurrent.futures
import json
from pathlib import Path
import urllib.request
import html

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'app/static/icons'
VERSION='11.15.0'

def download(service):
    slug=service['icon']
    try:
        version={'deezer':'9.21.0','qq':'16.0.0'}.get(slug,VERSION)
        url=f'https://cdn.jsdelivr.net/npm/simple-icons@{version}/icons/{slug}.svg'
        data=urllib.request.urlopen(url,timeout=20).read()
        if b'<svg' not in data or b'<script' in data:
            raise ValueError('Not a plain SVG')
        (DEST/f'{slug}.svg').write_bytes(data)
        return slug, 'Simple Icons '+version
    except Exception:
        initials=''.join(w[0] for w in service['name'].split())[:2].upper()
        # This is a letter badge, not an invented official logo.
        (DEST/f'{slug}.svg').write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect x="1" y="1" width="30" height="30" rx="6" fill="none" stroke="currentColor"/><text x="16" y="21" text-anchor="middle" font-family="sans-serif" font-weight="bold" font-size="13" fill="currentColor">{html.escape(initials)}</text></svg>')
        return slug, 'Letter badge (replaceable in admin)'

if __name__=='__main__':
    services=json.loads((ROOT/'app/services.json').read_text())
    DEST.mkdir(parents=True,exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(download,services))
    (DEST/'sources.json').write_text(json.dumps(dict(results),indent=2))
    print(json.dumps(dict(results),indent=2))
