"""Read-only screenshots of EXISTING BB threads. Never submits prompts or changes settings."""
import argparse, asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

async def main(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    report={'surface':'live BB host; existing thread; no input or settings writes','thread':args.thread,'results':[],'errors':[]}
    async with async_playwright() as p:
      browser=await p.chromium.launch(executable_path=args.chromium,args=['--no-sandbox'])
      try:
        for width in (520,1500):
          for theme in ('light','dark'):
            context=await browser.new_context(viewport={'width':width,'height':1100},color_scheme=theme)
            # Read-only browser fixture: bound all network mutations, including telemetry.
            await context.route('**/*', lambda route: route.continue_() if route.request.method in ('GET','HEAD','OPTIONS') else route.abort())
            page=await context.new_page()
            await page.goto(args.host.rstrip('/')+'/threads/'+args.thread,wait_until='domcontentloaded')
            await page.wait_for_timeout(1500)
            # Only this ephemeral document's theme class; no persisted setting.
            await page.evaluate('(theme)=>document.documentElement.classList.toggle("dark",theme==="dark")',theme)
            await page.wait_for_timeout(200)
            data=await page.evaluate('''()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
              headings:[...document.querySelectorAll('h1,h2,h3')].map(e=>({text:e.textContent,fontSize:getComputedStyle(e).fontSize,marginTop:getComputedStyle(e).marginTop,marginBottom:getComputedStyle(e).marginBottom})),
              bars:[...document.querySelectorAll('[role=progressbar]')].map(e=>({value:e.getAttribute('aria-valuenow'),width:e.getBoundingClientRect().width})),
              badges:[...document.querySelectorAll('.chatgpt-status-badge')].map(e=>e.textContent)
            })''')
            data['theme']=theme
            report['results'].append(data)
            await page.screenshot(path=str(out/f'host-{args.thread}-{width}-{theme}.png'))
            await context.close()
      finally:
        await browser.close()
        (out/f'host-{args.thread}.json').write_text(json.dumps(report,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--host',default='http://127.0.0.1:38886');p.add_argument('--thread',required=True);p.add_argument('--chromium',required=True);p.add_argument('--out',required=True)
    asyncio.run(main(p.parse_args()))
