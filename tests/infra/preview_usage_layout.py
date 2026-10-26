"""描画検査用HTMLを生成する。python3 tests/infra/preview_usage_layout.py /tmp/usage-layout

ブラウザで index.html を表示し、結果欄が PASS になることを確認する。
金額は架空。ページ全体に加え、各カード内の文字の境界も測る。
"""
import sys
import json
from pathlib import Path
from unittest.mock import patch
from test_usage_page import UsagePageTests, m

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
case = UsagePageTests()
case.setUp()
try:
    with patch.object(m, 'bans', return_value=[]):
        for amount in (0, 1234.56, 99999.99):
            with patch.object(m, '_pace', return_value={'month_usd': amount, 'total_usd': amount * 3, 'week_usd': amount / 4}):
                for view in ('sum', 'hours', 'list'):
                    page = m.render(view, None, 1).replace(m.REFRESH_SCRIPT, '')
                    page = page.replace('</body>', '''<script>
if(location.hash==='#cost') document.querySelector('.stat.cost').scrollIntoView({block:'center'});
</script></body>''')
                    (out / f'{amount}-{view}.html').write_text(page)
finally:
    case.doCleanups()

pages = {str(a): (out / f'{a}-sum.html').read_text() for a in (0, 1234.56, 99999.99)}
(out / 'index.html').write_text('<script>const pages='+json.dumps(pages).replace('</', '<\\/')+';</script>'+'''<!doctype html><meta charset="utf-8"><title>Dashboard layout regression</title>
<h1>Dashboard layout regression</h1><pre id="results">RUNNING</pre><div id="frames"></div><script>
(async () => {
 const results = [], jobs = [], widths = [320,375,402,768,899,900,1024,1280,1440,1920];
 for (const width of widths) for (const amount of ['0','1234.56','99999.99']) jobs.push((async () => {
  const frame=document.createElement('iframe');frame.width=width;frame.height=1000;
  const loaded=new Promise((resolve,reject)=>{frame.onload=resolve;setTimeout(()=>reject(new Error('frame timeout')),30000)});
  frame.srcdoc=pages[amount];document.querySelector('#frames').append(frame);
  await loaded;await frame.contentDocument.fonts.ready;
  const doc=frame.contentDocument,errors=[];
  if(doc.documentElement.scrollWidth>width) errors.push('page overflow');
  const title=doc.querySelector('header h1'), day=doc.querySelector('header .day'), logout=doc.querySelector('header .logout');
  if(title.textContent!=='みのるんエージェント 監視ダッシュボード') errors.push('title spacing');
  if(!logout) errors.push('missing separate logout');
  else {
   const dr=day.getBoundingClientRect(), lr=logout.getBoundingClientRect(), tr=title.getBoundingClientRect();
   const hr=doc.querySelector('header').getBoundingClientRect();
   if(Math.abs((dr.left+dr.right)/2-width/2)>1) errors.push('date is not centered');
   if(Math.abs(lr.right-hr.right)>1||Math.abs(lr.top-hr.top)>1) errors.push('logout is not top right');
   if(tr.right>lr.left||dr.top<lr.bottom) errors.push('header controls overlap');
  }
  const cards=doc.querySelectorAll('.stat');
  if(cards.length!==5||doc.querySelectorAll('.stat.cost').length!==1) errors.push('missing cards');
  const size=frame.contentWindow.getComputedStyle(cards[0].querySelector('.v')).fontSize;
  for(const card of cards) {
   if(frame.contentWindow.getComputedStyle(card.querySelector('.v')).fontSize!==size) errors.push('inconsistent number size');
   const bounds=card.getBoundingClientRect();
   const walker=doc.createTreeWalker(card,NodeFilter.SHOW_TEXT);let node;
   while(node=walker.nextNode()) {
    if(!node.textContent.trim())continue;
    const range=doc.createRange();range.selectNodeContents(node);
    for(const r of range.getClientRects()) if(r.left<bounds.left-1||r.right>bounds.right+1)
      errors.push(card.querySelector('.k').textContent+': '+node.textContent);
   }
  }
  const panel=doc.querySelector('.people-panel'),bounds=panel.getBoundingClientRect();
  for(const control of panel.querySelectorAll('select,input:not([type=hidden]),button')) {
   const r=control.getBoundingClientRect();
   if(r.width && (r.left<bounds.left||r.right>bounds.right)) errors.push('people control overflow');
  }
  const walker=doc.createTreeWalker(panel,NodeFilter.SHOW_TEXT);let node;
  while(node=walker.nextNode()) {
   if(!node.textContent.trim())continue;
   const range=doc.createRange();range.selectNodeContents(node);
   for(const r of range.getClientRects()) if(r.width&&(r.left<bounds.left-1||r.right>bounds.right+1)) errors.push('people text overflow: '+node.textContent);
  }
  results.push({width,amount,errors});document.querySelector('#results').textContent='RUNNING '+results.length+'/30\\n'+JSON.stringify(results);frame.remove();
 })());
 await Promise.all(jobs);
 document.querySelector('#results').textContent=(results.some(r=>r.errors.length)?'FAIL':'PASS')+'\\n'+JSON.stringify(results,null,2);
})().catch(e=>document.querySelector('#results').textContent='ERROR '+e.message);</script>''')
print(out / 'index.html')
