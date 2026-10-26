"""15秒更新の実スクリプトをブラウザ内で検査するHTMLを生成する。"""
import json
import sys
from pathlib import Path
from unittest.mock import patch
from test_usage_page import UsagePageTests, m
case=UsagePageTests(); case.setUp()
try:
    with patch.object(m,'bans',return_value=[]):
        page=m.render('list',None,1)
finally:
    case.doCleanups()
# 通信と時計を置き換え、画面更新本体を実行。外部サービスへは接続しない。
setup='''<script>
let tick, interval, calls=0, mode='ok';
window.setInterval=(fn,ms)=>{tick=fn;interval=ms};
Object.defineProperty(document,'hidden',{get:()=>mode==='hidden'});
window.fetch=async()=>{calls++; if(mode==='fail')throw new Error('offline');
 return {ok:true,redirected:mode==='redirect',text:async()=>{if(mode==='invalid')return '<html>login</html>';const doc=document.documentElement.cloneNode(true);doc.querySelector('.ans').textContent='更新済みの回答';doc.querySelector('header .at').textContent='更新済みの時刻';doc.querySelector('#summary-panel .v').textContent='99';doc.querySelector('#hours-panel td').textContent='更新済みの合計';return doc.outerHTML;}};};
</script>'''
test='''<script>
(async()=>{
 const checks=[], check=(ok,label)=>{checks.push([ok,label]);if(!ok)throw new Error(label)};
 check(interval===15000,'15秒周期');
 const row=document.querySelector('.list details');row.open=true;const key=row.dataset.row;
 const help=document.querySelector('.people-help');help.open=true;
 const selector=document.querySelector('#who');selector.focus();
 const options=selector.innerHTML;
 await tick();
 check(document.querySelector('.list details[open]').dataset.row===key,'回答の展開を保持');
 check(document.querySelector('.ans').textContent.includes('更新済み'),'新しい回答を表示');
 check(document.querySelector('header .at').textContent==='更新済みの時刻','上部の時刻を更新');
 check(document.querySelector('#summary-panel .v').textContent==='99','要約の件数を更新');
 check(document.querySelector('#hours-panel td').textContent==='更新済みの合計','1時間ごとの集計を更新');
 check(selector.innerHTML===options,'操作中の選択欄を保持');
 check(help.open,'集計の説明を開いたまま保持');
 help.open=false;selector.blur();const selected=selector.value;await tick();
 check(!help.open,'閉じた説明を更新で開かない');
 check(selector.value===selected,'更新後も選択値を保持');
 selector.value='person';selector.dispatchEvent(new Event('change'));
 const search=document.querySelector('#person-search'),input=document.querySelector('#person-query');
 check(!search.hidden&&document.activeElement===input,'利用者指定で検索欄を表示してフォーカス');
 input.value='a-2222';input.dispatchEvent(new Event('input'));
 const visible=()=>[...document.querySelectorAll('#person-results a')].filter(a=>!a.hidden);
 check(visible().length===1&&visible()[0].dataset.person==='A-2222222222222222','IDの一部と小文字で候補を絞る');
 const target=new URL(visible()[0].href);
 check(target.searchParams.get('u')==='A-2222222222222222'&&target.searchParams.get('v')==='list'&&target.searchParams.get('d')==='','候補リンクは利用者と日付と画面を保持');
 const results=document.querySelector('#person-results').innerHTML;
 await tick();check(input.value==='a-2222'&&document.querySelector('#person-results').innerHTML===results,'検索入力中は候補と検索語を保持');
 input.blur();await tick();check(visible().length===1&&input.value==='a-2222','検索欄の外へ移動しても検索条件を保持');
 input.value='not-found';input.dispatchEvent(new Event('input'));
 check(visible().length===0&&!document.querySelector('#person-empty').hidden,'検索結果ゼロを表示');
 let prevented=false;const form=document.querySelector('#people-form');
 form.addEventListener('submit',event=>{prevented=event.defaultPrevented;event.preventDefault()},{once:true});
 form.requestSubmit();check(prevented,'利用者指定のまま空送信しない');
 let broad=false;form.addEventListener('submit',event=>{broad=!event.defaultPrevented;event.preventDefault()},{once:true});
 selector.value='google';selector.dispatchEvent(new Event('change'));
 check(broad&&search.hidden&&document.querySelector('#people-value').value==='google','通常の区分はGETフォームへ選択値を渡す');
 const previous=document.querySelector('#list-panel').innerHTML;
 for(const nextMode of ['fail','redirect','invalid']){mode=nextMode;await tick();check(document.querySelector('#list-panel').innerHTML===previous,nextMode+'時に画面保持');}
 mode='hidden';const count=calls;await tick();check(calls===count,'非表示中は取得しない');
 mode='ok';await tick();check(calls===count+1,'失敗後も再試行');
 document.querySelector('#results').textContent='PASS '+JSON.stringify(checks);
})().catch(e=>document.querySelector('#results').textContent='FAIL '+e.message);
</script>'''
page=page.replace('<head>','<head>'+setup).replace('</body>','<pre id="results">RUNNING</pre>'+test+'</body>')
Path(sys.argv[1]).write_text(page)
