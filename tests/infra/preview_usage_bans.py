"""利用停止の一覧・詳細と費用カードを、架空データで描画検査する。"""
import json
import sys
from pathlib import Path
from unittest.mock import patch
from test_usage_page import BanReviewTests, m

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
case = BanReviewTests(); case.setUp()
bans = [case.ban, dict(case.ban, identifier='G-fedcba9876543210', source='manual', reviewed_at='2026-10-26T12:00:00+09:00')]
rows = case.rows + [dict(case.rows[0], sk=str(i), question=f'確認用の書き込み {i}：'+'長めの質問。'*8) for i in range(60)]
with patch.object(m, 'bans', return_value=bans), patch.object(m, '_ban_history', return_value=rows):
    pages = {'bans': m.render_bans(), 'detail': m.render_bans(case.ident), 'detail2': m.render_bans(case.ident, 2)}
for name, page in pages.items():
    pages[name] = page.replace(m.BAN_SCRIPT, '')
    (out / f'{name}.html').write_text(pages[name])
script = r'''
(async()=>{
 const results=[];
 for(const width of [320,375,402,768,900,1440]) for(const [name,page] of Object.entries(pages)) {
  const f=document.createElement('iframe'); f.width=width; f.height=1000;
  const ready=new Promise(resolve=>f.onload=resolve); f.srcdoc=page; document.body.append(f); await ready; await f.contentDocument.fonts.ready;
  const d=f.contentDocument,errors=[];
  if(d.documentElement.scrollWidth>width) errors.push('horizontal overflow');
  if(name.startsWith('detail') && d.querySelectorAll('.list details').length>50) errors.push('pagination');
  const v=d.querySelector('.ban-visits');
  if(v) for(const node of v.querySelectorAll('strong')) {
   const range=d.createRange();range.selectNodeContents(node);
   if(range.getBoundingClientRect().right>v.getBoundingClientRect().right) errors.push('number overflow');
  }
  results.push({width,name,errors}); f.remove();
 }
 const result={status:results.some(x=>x.errors.length)?'FAIL':'PASS',results};
 document.querySelector('pre').textContent=JSON.stringify(result,null,2);
 await fetch('/result/bans',{method:'POST',body:JSON.stringify(result)});
})();
'''
(out/'ban-check.html').write_text('<!doctype html><meta charset="utf-8"><pre>RUNNING</pre><script>const pages='+json.dumps(pages).replace('</','<\\/')+';'+script+'</script>')
print(out)
