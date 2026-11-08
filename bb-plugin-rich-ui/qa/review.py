"""Real Chromium component screenshots; explicitly not native ChatGPT or BB timeline parity.
Requires Python playwright and a Chromium executable supplied with --chromium.
"""
import argparse, asyncio, functools, http.server, json, threading, urllib.request
from pathlib import Path
from playwright.async_api import async_playwright

async def review(args):
    root = Path(__file__).resolve().parent
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    # Exact CSS from the running BB; no copied or invented theme tokens.
    import re
    html = urllib.request.urlopen(args.host).read().decode()
    sheets = re.findall(r'href="([^" ]+\.css)"', html)
    assert sheets, 'No BB stylesheets found'
    css_links = ''.join('<link rel="stylesheet" href="' + args.host.rstrip('/') + path + '">' for path in sheets)
    (root/'dist/index.html').write_text('<!doctype html><html><head><meta charset="utf-8">' + css_links + '<link rel="stylesheet" href="plugin.css"><link rel="stylesheet" href="fixture.css">' + '</head><body class="bg-background"><div id="root"></div><script type="module" src="fixture.js"></script></body></html>')
    report = {'surface': 'isolated actual React components with running BB CSS; NOT host timeline', 'host': args.host, 'stylesheets': sheets, 'results': [], 'errors': []}
    try:
      async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=args.chromium, args=['--no-sandbox'])
        try:
          for width in (520, 1500):
            for theme in ('light', 'dark'):
              page = await browser.new_page(viewport={'width':width, 'height':1200}, color_scheme=theme)
              page.on('pageerror', lambda err: report['errors'].append(str(err)))
              await page.goto(f'http://127.0.0.1:{server.server_port}/dist/index.html')
              await page.evaluate('(theme)=>document.documentElement.classList.toggle("dark",theme==="dark")', theme)
              await page.locator('[role=progressbar]').first.wait_for()
              await page.wait_for_timeout(300)
              data = await page.locator('main').evaluate('''el => ({
                width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
                rows: [...el.querySelectorAll('section[aria-label="Readiness timings"] .grid')].map(row=>{
                  const [label,value]=row.children;return {label:label.textContent,value:value.textContent,labelRight:label.getBoundingClientRect().right,valueLeft:value.getBoundingClientRect().left};
                }),
                bars:[...el.querySelectorAll('[role=progressbar]')].map(b=>({value:b.getAttribute('aria-valuenow'),ratio:b.firstElementChild.getBoundingClientRect().width/b.getBoundingClientRect().width,color:getComputedStyle(b.firstElementChild).backgroundColor})),
                badges:[...el.querySelectorAll('[data-tone] > .chatgpt-status-badge')].map(b=>{
                  const ctx=document.createElement('canvas').getContext('2d');
                  const base=getComputedStyle(document.body).backgroundColor;
                  ctx.fillStyle=base;ctx.fillRect(0,0,1,1);
                  ctx.fillStyle=getComputedStyle(b).backgroundColor;ctx.fillRect(0,0,1,1);
                  const bg=[...ctx.getImageData(0,0,1,1).data].slice(0,3);
                  ctx.fillStyle=getComputedStyle(b).color;ctx.fillRect(0,0,1,1);
                  const fg=[...ctx.getImageData(0,0,1,1).data].slice(0,3);
                  const lum=rgb=>rgb.map(v=>v/255).map(v=>v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4).reduce((a,v,i)=>a+v*[0.2126,0.7152,0.0722][i],0);
                  return {tone:b.dataset.tone,color:getComputedStyle(b).color,background:getComputedStyle(b).backgroundColor,contrast:(Math.max(lum(bg),lum(fg))+0.05)/(Math.min(lum(bg),lum(fg))+0.05)};
                }),
                overflow:[...el.querySelectorAll('section,span,strong')].filter(e=>e.getBoundingClientRect().right>el.getBoundingClientRect().right+1).map(e=>e.textContent)
              })''')
              data['theme'] = theme
              assert data['scrollWidth'] <= width, data
              assert not data['overflow'], data
              assert all(b['contrast']>=4.5 for b in data['badges']), data
              assert len(data['badges'])==5 and len(data['rows'])==2, data
              assert all(r['labelRight'] <= r['valueLeft'] for r in data['rows']), data
              assert [b['value'] for b in data['bars']]==['100','70','0','100'], data
              assert all(abs(b['ratio']-int(b['value'])/100)<0.005 for b in data['bars']), data
              await page.locator('main').screenshot(path=str(out/f'components-{width}-{theme}.png'))
              report['results'].append(data)
              await page.close()
        finally: await browser.close()
    finally:
      server.shutdown()
      (out/'report.json').write_text(json.dumps(report,indent=2))
    assert not report['errors'],report['errors']

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--chromium',required=True);p.add_argument('--out',required=True);p.add_argument('--host',default='http://127.0.0.1:38886')
    asyncio.run(review(p.parse_args()))
