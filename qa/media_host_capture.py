"""Read-only existing BB image evidence; not a native ChatGPT capture."""
import argparse
import asyncio
import hashlib
import json
import re
from urllib.parse import urlsplit, parse_qs
from pathlib import Path
from playwright.async_api import async_playwright


async def main(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {'surface': 'existing live BB message, no prompts or settings writes',
              'results': [], 'errors': []}
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=args.chromium, args=['--no-sandbox'])
        try:
            for width in (520, 1500):
                for theme in ('light', 'dark'):
                    context = await browser.new_context(viewport={'width': width, 'height': 1100}, color_scheme=theme, device_scale_factor=1)
                    await context.route('**/*', lambda route: route.continue_() if route.request.method in ('GET', 'HEAD', 'OPTIONS') else route.abort())
                    page = await context.new_page()
                    page.on('pageerror', lambda error: report['errors'].append(str(error)))
                    await page.goto(args.host.rstrip('/') + '/threads/' + args.thread, wait_until='domcontentloaded')
                    images = page.locator('img[src*="/image-generation/http/cli-image?asset="]')
                    await images.first.wait_for(timeout=20000)
                    await page.wait_for_function('''() => [...document.querySelectorAll('img[src*="/image-generation/http/cli-image?asset="]')].every(i => i.complete && i.naturalWidth > 0)''')
                    await page.evaluate('(theme)=>document.documentElement.classList.toggle("dark",theme==="dark")', theme)
                    await page.evaluate('document.fonts.ready')
                    data = await images.evaluate_all('''els=>els.map(i=>({src:i.getAttribute('src'),alt:i.alt,width:i.getBoundingClientRect().width,height:i.getBoundingClientRect().height,naturalWidth:i.naturalWidth,naturalHeight:i.naturalHeight}))''')
                    assert data and all(i['width'] <= width and i['naturalWidth'] > 0 for i in data), data
                    if args.reference_dir:
                        root = Path(args.reference_dir).resolve(strict=True)
                        for image in data:
                            parsed = urlsplit(image['src'])
                            assert parsed.path == '/api/v1/plugins/image-generation/http/cli-image'
                            asset = parse_qs(parsed.query).get('asset', [''])[0]
                            assert re.fullmatch(r'file_[A-Za-z0-9]{12,80}', asset)
                            reference = (root / (asset + '.png')).resolve(strict=True)
                            assert reference.parent == root
                            response = await context.request.get(args.host.rstrip('/') + parsed.path + '?' + parsed.query)
                            assert response.ok
                            served = await response.body()
                            image['servedSha256'] = hashlib.sha256(served).hexdigest()
                            image['referenceSha256'] = hashlib.sha256(reference.read_bytes()).hexdigest()
                            assert image['servedSha256'] == image['referenceSha256'], 'Image endpoint changed source bytes'
                            (out / (asset + '.png')).write_bytes(reference.read_bytes())
                    for index in range(await images.count()):
                        await images.nth(index).screenshot(path=str(out / f'host-image-{width}-{theme}-{index}.png'))
                    await page.screenshot(path=str(out / f'host-thread-{width}-{theme}.png'))
                    lightbox = None
                    if args.verify_lightbox:
                        await images.first.click()
                        dialog = page.get_by_role('dialog', name='Image preview')
                        await dialog.wait_for()
                        await page.screenshot(path=str(out / f'host-lightbox-{width}-{theme}.png'))
                        await page.keyboard.press('Escape')
                        await dialog.wait_for(state='detached')
                        lightbox = {'opened': True, 'escapeDismissed': True}
                    report['results'].append({'width': width, 'theme': theme, 'images': data, 'lightbox': lightbox})
                    await context.close()
        finally:
            await browser.close()
            (out / 'host-report.json').write_text(json.dumps(report, indent=2))
    assert not report['errors'], report['errors']


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--chromium', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--host', default='http://127.0.0.1:38886')
    parser.add_argument('--thread', required=True)
    parser.add_argument('--reference-dir')
    parser.add_argument('--verify-lightbox', action='store_true')
    asyncio.run(main(parser.parse_args()))
