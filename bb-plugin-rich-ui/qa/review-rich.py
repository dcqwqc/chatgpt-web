"""Real Chromium fixture or real BB timeline verification; never a native ChatGPT capture."""
import argparse
import asyncio
import functools
import http.server
import json
from pathlib import Path
import re
import threading
import urllib.request
from playwright.async_api import async_playwright


async def review(args):
    root=Path(__file__).resolve().parent
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    server=None
    if not args.thread:
        class Handler(http.server.SimpleHTTPRequestHandler):
            def do_GET(self):
                if re.fullmatch(r'/api/v1/plugins/image-generation/http/cli-image\?asset=file_[A-Za-z0-9]{12,80}',self.path):
                    with urllib.request.urlopen(args.host.rstrip('/')+self.path) as response:
                        data=response.read();self.send_response(200);self.send_header('Content-Type','image/png');self.end_headers();self.wfile.write(data)
                    return
                super().do_GET()
        handler=functools.partial(Handler,directory=str(root/'dist'))
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        html=urllib.request.urlopen(args.host).read().decode()
        css=''.join('<link rel="stylesheet" href="'+args.host.rstrip('/')+path+'">' for path in re.findall(r'href="([^" ]+\.css)"',html))
        (root/'dist/rich.html').write_text('<!doctype html><html><head><meta charset="utf-8">'+css+'<link rel="stylesheet" href="plugin.css"><style>body{margin:0;background:var(--background)}.rich-fixture{max-width:48rem;margin:auto;padding:1.5rem;color:var(--foreground)}</style></head><body><div id="root"></div><script type="module" src="rich-fixture.js"></script></body></html>')
        url=f'http://127.0.0.1:{server.server_port}/rich.html'
    else:
        url=args.host.rstrip('/')+'/threads/'+args.thread
    report={'surface':'actual BB offline fixture message' if args.thread else 'local actual plugin components; lightbox stubbed', 'thread':args.thread,'results':[],'errors':[]}
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(executable_path=args.chromium,args=['--no-sandbox'])
            try:
                for width in (520,1500):
                    for theme in ('light','dark'):
                        page=await browser.new_page(viewport={'width':width,'height':1100},color_scheme=theme,device_scale_factor=1)
                        page.on('pageerror',lambda e:report['errors'].append(str(e)))
                        await page.route('**/*',lambda route:route.continue_() if route.request.method in ('GET','HEAD','OPTIONS') else route.abort())
                        await page.goto(url,wait_until='domcontentloaded')
                        await page.locator('.chatgpt-chart').nth(2).wait_for(timeout=20000)
                        await page.evaluate('(theme)=>document.documentElement.classList.toggle("dark",theme==="dark")',theme)
                        await page.evaluate('document.fonts.ready')
                        summary=page.locator('.chatgpt-chart summary').first
                        await summary.focus();await summary.press('Enter')
                        assert await page.locator('.chatgpt-chart details').first.get_attribute('open') is not None
                        await page.locator('.chatgpt-chart details').evaluate_all('(els)=>els.forEach(e=>e.open=true)')
                        data=await page.evaluate('''()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
                          charts:[...document.querySelectorAll('.chatgpt-chart')].map(c=>({title:c.getAttribute('aria-label'),svg:c.querySelector('svg').getBoundingClientRect().width,
                           shapes:c.querySelectorAll('rect,circle,path,polyline').length,values:[...c.querySelectorAll('tbody td')].map(n=>n.textContent),labels:[...c.querySelectorAll('tbody th')].map(n=>n.textContent),points:c.querySelector('polyline')?.getAttribute('points')})),
                          controls:[...document.querySelectorAll('.chatgpt-static-control')].map(c=>({label:c.textContent,disabled:c.querySelector('button')?.disabled,checked:c.querySelector('[role=checkbox]')?.getAttribute('aria-checked')})),
                          galleryCaption:document.querySelector('.chatgpt-gallery-caption')?.textContent,
                          unavailable:document.querySelector('.chatgpt-unavailable')?.textContent
                        })''')
                        assert data['scrollWidth']<=width,data
                        assert len(data['charts'])==3,data
                        assert [c['values'] for c in data['charts']]==[['1.25','4','0'],['-2','0','3.5','2','4','1'],['3','7','0']],data
                        assert all(c['svg']>0 and c['shapes']>0 for c in data['charts']),data
                        assert data['charts'][1]['points'].split()[1].startswith('180,'),data
                        assert data['controls'][0]['disabled'] is True,data
                        assert data['controls'][1]['checked']=='true',data
                        assert 'unavailable' in data['unavailable'].lower(),data
                        # Load source images before captures so their dimensions stop moving the timeline.
                        await page.locator('.chatgpt-gallery').scroll_into_view_if_needed()
                        await page.wait_for_function('''()=>[...document.querySelectorAll('.chatgpt-gallery img')].every(i=>i.complete&&i.naturalWidth>0)''')
                        if args.thread:
                            await page.mouse.move(width*.65,300)
                            await page.mouse.wheel(0,-12000)
                            await page.wait_for_timeout(500)
                        for index in range(3):
                            await page.locator('.chatgpt-chart').nth(index).locator('svg').evaluate('(e)=>e.scrollIntoView({block:"start"})')
                            await page.wait_for_timeout(300)
                            await page.locator('.chatgpt-chart').nth(index).locator('svg').screenshot(path=str(out/f'chart-{index}-{width}-{theme}.png'))
                        gallery=page.locator('.chatgpt-gallery')
                        await gallery.scroll_into_view_if_needed()
                        await page.wait_for_function('''()=>[...document.querySelectorAll('.chatgpt-gallery img')].every(i=>i.complete&&i.naturalWidth>0)''')
                        await gallery.screenshot(path=str(out/f'gallery-{width}-{theme}.png'))
                        await page.locator('.chatgpt-static-control').first.screenshot(path=str(out/f'control-{width}-{theme}.png'))
                        if args.thread:
                            await page.locator('.chatgpt-gallery-open').first.click()
                            dialog=page.get_by_role('dialog',name='Image group');await dialog.wait_for()
                            await page.get_by_role('button',name='Next image',exact=True).click()
                            assert await page.get_by_role('button',name='Next image',exact=True).is_disabled()
                            await page.screenshot(path=str(out/f'gallery-lightbox-{width}-{theme}.png'))
                            await page.keyboard.press('Escape');await dialog.wait_for(state='detached')
                            data['galleryNavigation']='opened, advanced to second real image, Escape dismissed'
                        data['theme']=theme;report['results'].append(data)
                        await page.close()
            finally:
                await browser.close()
    finally:
        if server:server.shutdown()
        (out/'report.json').write_text(json.dumps(report,indent=2))
    assert not report['errors'],report['errors']


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--chromium',required=True);p.add_argument('--out',required=True);p.add_argument('--host',default='http://127.0.0.1:38886');p.add_argument('--thread');asyncio.run(review(p.parse_args()))
