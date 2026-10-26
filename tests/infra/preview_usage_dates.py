"""実際の更新スクリプトで、JST日付変更・過去日の固定をSafariでも検査する。"""
import datetime as dt
import json
import sys
from pathlib import Path
from unittest.mock import patch
from test_usage_page import UsagePageTests, m

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
case = UsagePageTests(); case.setUp()
try:
    def page(now, start, day, rows):
        with patch.object(m, 'questions', return_value=(now, start, rows)), patch.object(m, 'bans', return_value=[]):
            return m.render('hours', day, 2, u='google')
    start = case.start
    midnight = start + dt.timedelta(days=1)
    initial = page(start + dt.timedelta(hours=23, minutes=59), start, None, case.rows)
    next_page = page(midnight + dt.timedelta(minutes=1), midnight, None,
                     [dict(case.rows[0], created_at=(midnight + dt.timedelta(seconds=10)).isoformat(), pk='new-day')])
    fixed = page(midnight + dt.timedelta(minutes=1), start, '2026-10-25', case.rows)
    fixed_next = page(midnight + dt.timedelta(days=1, minutes=1), start, '2026-10-25', case.rows)
finally:
    case.doCleanups()

for mode, first, updated in [('rollover', initial, next_page), ('fixed', fixed, fixed_next)]:
    setup = '<script>const nativeFetch=window.fetch.bind(window);let tick;const responseHtml='+json.dumps(updated).replace('</','<\\/')+''';
window.setInterval=fn=>{tick=fn};let calls=[];
window.fetch=async url=>{calls.push(new URL(url).search);return {ok:true,redirected:false,text:async()=>responseHtml}};
</script>'''
    checks = '''
 const checks=[];const check=(ok,label)=>{checks.push([!!ok,label]);if(!ok)throw new Error(label)};
 const firstKey=document.querySelector('.list details').dataset.row;
 document.querySelector('.list details').open=true;
 const url=new URL(location.href);url.searchParams.set('p','2');url.searchParams.set('q','old-question');history.replaceState(null,'',url);
 await tick();
 check(document.querySelector('#who').value==='google','利用者の選択を保持');
 check(!location.search.includes('d=2026-10-26'),'今日を固定日付にしない');
'''
    if mode == 'rollover':
        checks += '''
 check(document.body.dataset.day==='2026-10-26','日本時間の新しい日へ追従');
 check(document.querySelector('header .day b').textContent.includes('10/26'),'上部の日付も更新');
 check(document.querySelector('#hours-panel').textContent.includes('0時台'),'新しい日の0時台を表示');
 check(!document.querySelector('#hours-panel').textContent.includes('23時台'),'前日の23時台を残さない');
 check(document.querySelector('.list details').dataset.row!==firstKey,'前日の展開回答を残さない');
 check(document.querySelector('#people-form [name=d]').value==='','利用者変更後も今日に追従');
 check(!new URL(location.href).searchParams.has('p')&&!new URL(location.href).searchParams.has('q'),'日付変更でページ番号と同文絞り込みを解除');
 check(calls.length===2,'日付変更時は同文絞り込み解除後に取り直す');
 check(document.querySelector('.date-tabs a').textContent==='今日 10/26（月）','左端の日付タブが今日');
 check(document.querySelector('.date-tabs a[aria-current=date]')===document.querySelector('.date-tabs a'),'今日が選択状態');
'''
    else:
        checks += '''
 check(document.body.dataset.day==='2026-10-25','過去日は日付が変わっても固定');
 check(document.querySelector('header .day b').textContent.includes('10/25'),'上部の日付も過去日を維持');
 check(document.querySelector('.list details[open]').dataset.row===firstKey,'過去日の回答の展開を保持');
 check(document.querySelector('#people-form [name=d]').value==='2026-10-25','利用者変更後も過去日を維持');
 check(new URL(location.href).searchParams.get('p')==='2','同じ日はページ番号を保持');
 check(document.querySelector('.date-tabs a').textContent==='今日 10/27（火）','過去日を見ていても今日のタブを更新');
 check(document.querySelector('.date-tabs a[aria-current=date]').textContent==='10/25（日）','過去日の選択状態を維持');
'''
    checks += "return checks;"
    end = '<script>(async()=>{'+checks+'''})().then(checks=>nativeFetch('/result/'''+mode+'''',{method:'POST',body:JSON.stringify({status:'PASS',checks})})).catch(e=>nativeFetch('/result/'''+mode+'''',{method:'POST',body:JSON.stringify({status:'FAIL',error:e.message})}));</script>'''
    (out / f'{mode}.html').write_text(first.replace('<head>','<head>'+setup).replace('</body>',end+'</body>'))
print(out)
