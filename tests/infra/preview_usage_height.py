"""Safariで、PCでもタブで1画面ずつ表示され、質問の一覧がページ全体のスクロールで縦に伸びることを検査する。"""
import datetime as dt
import json
import sys
from pathlib import Path
from unittest.mock import patch
from test_usage_page import UsagePageTests, m

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
case = UsagePageTests(); case.setUp()
pages = {}
try:
    for hours in (1, 12, 24):
        now = case.start + dt.timedelta(hours=hours - 1, minutes=5)
        rows = [dict(case.rows[0], pk=f"s{i}", question=f"確認用の質問 {i}", answer="長い回答。" * 100) for i in range(40)]
        with patch.object(m, "questions", return_value=(now, case.start, rows)), patch.object(m, "bans", return_value=[]):
            pages[str(hours)] = m.render("list", None, 1).replace(m.REFRESH_SCRIPT, "")
            if hours == 1:
                for view in ("sum", "hours", "list"):
                    (out / f"{view}.html").write_text(m.render(view, None, 1).replace(m.REFRESH_SCRIPT, ""))
finally:
    case.doCleanups()
script = r"""
const results=[];
(async()=>{
 for(const width of [900,1024,1440]) for(const hours of ['1','12','24']) {
  const frame=document.createElement('iframe');frame.width=width;frame.height=1000;
  const ready=new Promise(resolve=>frame.onload=resolve);frame.srcdoc=pages[hours];document.body.append(frame);await ready;await frame.contentDocument.fonts.ready;
  const d=frame.contentDocument, w=frame.contentWindow, list=d.querySelector('.list'), errors=[];
  const visible=s=>w.getComputedStyle(d.querySelector(s)).display!=='none';
  if(!visible('nav.tabs')) errors.push('tabs hidden on PC');
  if(!visible('#list-panel')||visible('#hours-panel')||visible('#summary-panel')) errors.push('only list panel should show');
  if(w.getComputedStyle(list).overflowY==='auto') errors.push('list must not scroll inside');
  if(list.scrollHeight>list.clientHeight+1) errors.push('list clipped');
  if(d.documentElement.scrollHeight<=1000) errors.push('page must scroll');
  const before=list.getBoundingClientRect().height;d.querySelector('.list details').open=true;
  if(list.getBoundingClientRect().height<=before) errors.push('expanded answer must grow the page');
  if(d.documentElement.scrollWidth>width) errors.push('horizontal overflow');
  results.push({width,hours,listHeight:list.clientHeight,errors});frame.remove();
 }
 const result={status:results.some(r=>r.errors.length)?'FAIL':'PASS',results};
 document.querySelector('pre').textContent=JSON.stringify(result,null,2);
 await fetch('/result',{method:'POST',body:JSON.stringify(result)});
})();
"""
(out/'index.html').write_text('<!doctype html><meta charset="utf-8"><h1>Height regression</h1><pre>RUNNING</pre><script>const pages='+json.dumps(pages).replace('</','<\\/')+';'+script+'</script>')
print(out)
