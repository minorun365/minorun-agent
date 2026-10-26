"""利用者の分離・按分・登録一覧・リンクの回帰検査。AWSへの通信は置き換える。"""
import datetime as dt
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

os.environ.update(PAGE_TOKEN='test-only', LOG_TABLE='test', RUNTIME_ARN='test', TOOLS_GATEWAY_ARN='test')
with patch.dict(sys.modules, {'boto3': types.SimpleNamespace(client=lambda *a, **k: MagicMock())}):
    spec = importlib.util.spec_from_file_location('usage_page', Path(__file__).resolve().parents[2] / 'infra/lambda/usage-page/usage_page.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
REAL_PACE = m._pace

class UsagePageTests(unittest.TestCase):
    def setUp(self):
        self.start = dt.datetime(2026,10,25,tzinfo=m.JST)
        self.now = self.start + dt.timedelta(hours=3)
        self.rows = [dict(created_at=(self.start+dt.timedelta(hours=h)).isoformat(), pk=session, anon=anon, question=q, answer='回答',tools=tools,feedback='good',via='typed',first_ms=1000) for h,session,anon,q,tools in [
            (0,'s1','user-1111111111111111','同じ質問', ['search_2026']),
            (0,'s2','2222222222222222','同じ質問', []),
            (1,'s3','user-1111111111111111','別の質問', ['search_recent'])]]
        self.metrics = {k:{} for k in [(model,kind) for model in m.MODELS for kind in m.TOKEN_METRICS]+['vcpu','gb','web']}
        self.metrics[('sonnet','in')] = {self.start:1000000,self.start+dt.timedelta(hours=1):2000000,self.start+dt.timedelta(hours=2):3000000}
        self.patches=[patch.object(m,'questions',return_value=(self.now,self.start,self.rows)),patch.object(m,'_day',return_value=(self.now,self.start,self.now)),patch.object(m,'_metrics',return_value=self.metrics),patch.object(m,'_registered_users',return_value={'user-1111111111111111':{},'user-3333333333333333':{}}),patch.object(m,'_rejected',return_value={'per_minute':2}),patch.object(m,'_pace',return_value=None),patch.object(m,'_owner_actors',return_value=set())]
        for p in self.patches:p.start()
        self.addCleanup(lambda:[p.stop() for p in reversed(self.patches)])

    def test_monthly_pace_remains_after_date_rollover(self):
        tomorrow = self.start + dt.timedelta(days=1, minutes=5)
        pace = {"day_usd": 2.0, "month_usd": 60.0}
        with patch.object(m, 'questions', return_value=(tomorrow, self.start, self.rows)), patch.object(m, '_pace', return_value=pace) as calc:
            data = m.report('2026-10-25')[0]
            calc.assert_called_once_with(tomorrow)
            self.assertEqual(data['pace'], pace)
            self.assertIn('$60', m._summary_html(data))
            self.assertIn('月額予測（直近7日平均）', m._summary_html(dict(data, pace=None)))

    def test_pace_sums_since_launch_and_last_week(self):
        now = dt.datetime(2026, 11, 10, 12, tzinfo=m.JST)
        since = dt.datetime(2026, 10, 25, tzinfo=m.JST)
        metrics = {k: {} for k in self.metrics}
        metrics[('sonnet', 'in')] = {since: 1000000, now - dt.timedelta(days=2): 500000}
        rows = [{'created_at': since.isoformat(), 'tools': ['search_recent']},
                {'created_at': (now - dt.timedelta(days=1)).isoformat(), 'tools': ['search_recent']}]
        m._pace_cache.clear()
        with patch.object(m, 'COST_SINCE', since), patch.object(m, '_metrics', return_value=metrics), patch.object(m, '_rows', return_value=rows):
            pace = REAL_PACE(now)
        m._pace_cache.clear()
        self.assertAlmostEqual(pace['total_usd'], 3.0 + 0.01)
        self.assertAlmostEqual(pace['week_usd'], 1.0 + 0.005)
        self.assertAlmostEqual(pace['month_usd'], 1.005 / 7 * 30)
        html_ = m._summary_html(dict(m.report()[0], pace=pace))
        for label in ('公開から現在までの累計', '月額予測（直近7日平均）', 'この日の概算コスト'):
            self.assertIn(label, html_)

    def test_pace_divides_by_days_since_launch(self):
        since = dt.datetime(2026, 10, 25, tzinfo=m.JST)
        now = since + dt.timedelta(days=2)
        metrics = {k: {} for k in self.metrics}
        metrics[('sonnet', 'in')] = {since: 1000000}
        m._pace_cache.clear()
        with patch.object(m, 'COST_SINCE', since), patch.object(m, '_metrics', return_value=metrics), patch.object(m, '_rows', return_value=[]):
            pace = REAL_PACE(now)
        m._pace_cache.clear()
        self.assertAlmostEqual(pace['week_usd'], 2.0)
        self.assertAlmostEqual(pace['month_usd'], 30.0)

    def test_login_message_renders_without_report_context(self):
        self.assertIn("ログイン", m._message("ログインしてください", "login"))

    def test_today_links_and_form_keep_following_today(self):
        from html.parser import HTMLParser
        class Links(HTMLParser):
            def __init__(self):
                super().__init__(); self.links = []; self.day_values = []
            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag == 'a': self.links.append(a.get('href', ''))
                if tag == 'input' and a.get('name') == 'd': self.day_values.append(a.get('value'))
        with patch.object(m, 'bans', return_value=[]):
            page = m.render('hours', '2026-10-25', 1)
        parsed = Links(); parsed.feed(page)
        self.assertEqual(parsed.day_values, [''])
        self.assertIn('data-follow-today="true"', page)
        self.assertFalse(any('d=2026-10-25' in href for href in parsed.links))
        self.assertIn('今日 10/25（日）', page)
        self.assertLess(page.index('今日 10/25（日）'), page.index('10/24（土）'))

    def test_past_day_stays_fixed_and_today_tab_returns_to_live(self):
        tomorrow = self.start + dt.timedelta(days=1, minutes=5)
        with patch.object(m, 'questions', return_value=(tomorrow, self.start, self.rows)), patch.object(m, 'bans', return_value=[]):
            page = m.render('hours', '2026-10-25', 1, 'typed', 'google')
        self.assertIn('data-follow-today="false"', page)
        self.assertIn('name="d" value="2026-10-25"', page)
        self.assertIn('?v=hours&amp;u=google&amp;f=typed">今日 10/26（月）', page)
        self.assertIn('d=2026-10-25&amp;u=google&amp;f=typed" aria-current="date"', page)
        self.assertNotIn('d=2026-10-26', page)

    def test_midnight_report_has_new_zero_hour(self):
        start = self.start + dt.timedelta(days=1)
        now = start + dt.timedelta(minutes=5)
        row = dict(self.rows[0], created_at=(start + dt.timedelta(minutes=1)).isoformat())
        with patch.object(m, 'questions', return_value=(now, start, [row])):
            data, hrs, total, rows = m.report(None)
        self.assertEqual([h['hour'] for h in hrs], [start])
        self.assertEqual(total['questions'], 1)
        self.assertIn('0時台', m._hours_html(hrs, total))

    def test_totals_and_unallocated(self):
        d,hrs,total,rows=m.report()
        self.assertAlmostEqual(d['total'],12.006)
        self.assertAlmostEqual(d['unallocated_usd'],6)
        self.assertAlmostEqual(sum(p['estimated_usd'] for p in d['people'])+d['unallocated_usd'],d['total'])
        self.assertEqual((d['registered_google'],d['active_google'],d['users']),(2,1,2))
        self.assertEqual(total['sessions'],3)

    def test_hourly_people_deduplicate_and_ignore_unknown(self):
        self.rows.append(dict(self.rows[0], pk='s4'))
        self.rows.append(dict(self.rows[0], pk='s5', anon=None))
        d, hrs, total, rows = m.report()
        self.assertEqual([h['users'] for h in hrs], [0, 1, 2])
        self.assertEqual(total['users'], 2)
        self.assertEqual(total['sessions'], 5)
        filtered = m.as_json(None, 'google')
        self.assertEqual(filtered['hours']['total']['users'], 1)
        self.assertEqual([h['users'] for h in filtered['hours']['rows']], [0, 1, 1])
        self.assertIn('<th>時台</th><th>セッション</th><th>会話</th>', m.render('hours', None, 1))

    def test_filters_and_costs(self):
        d,hrs,total,rows=m.report(u='G-1111111111111111')
        self.assertEqual(len(rows),2)
        self.assertEqual(d['questions'],2)
        self.assertAlmostEqual(d['total'],12.006)
        self.assertEqual(d['rejected'], {'per_minute':2})
        self.assertEqual(total['sessions'],2)
        self.assertAlmostEqual(m.report(u='anonymous')[0]['total'],12.006)
        self.assertAlmostEqual(m.report(u='G-3333333333333333')[0]['total'],12.006)
        self.assertEqual(m.report(u='invalid')[0]['questions'],0)

    def test_duplicate_identity_and_navigation(self):
        page=m.render('list',None,1)
        self.assertIn('同じ質問 2件・2人',page)
        self.assertIn('G-1111111111111111',page)
        self.assertIn('A-2222222222222222',page)
        page=m.render('list',None,1,'typed','G-1111111111111111')
        self.assertNotIn('A-2222222222222222</a>',page)
        self.assertIn('u=G-1111111111111111',page)
        self.assertIn('断った数（全利用）',page)
        rows=m.report()[3]
        content=m._list_html(self.start,rows,1,None,qid=m._question_id(rows[0]))
        self.assertNotIn('>別の質問<',content)

    def test_individual_picker_keeps_missing_person_and_filter(self):
        from html.parser import HTMLParser
        from urllib.parse import parse_qs, urlsplit
        class Links(HTMLParser):
            def __init__(self):
                super().__init__(); self.links=[]
            def handle_starttag(self, tag, attrs):
                attrs=dict(attrs)
                if tag=='a' and 'data-person' in attrs: self.links.append(attrs)
        person='A-9999999999999999'
        d=m.report(u=person)[0]
        page=m._people_html(d,'list','typed')
        links=Links();links.feed(page)
        target=next(link for link in links.links if link['data-person']==person)
        self.assertEqual(parse_qs(urlsplit(target['href']).query),
                         {'d':['2026-10-25'],'v':['list'],'f':['typed'],'u':[person]})
        self.assertIn('value="person" selected',page)
        self.assertIn('value="'+person+'"',page)
        self.assertIn('0問',page)
        self.assertNotIn('value="'+person+'" selected',page)

    def test_json_filter(self):
        out=m.as_json(None,'google')
        self.assertEqual(out['summary']['questions'],2)
        self.assertEqual(out['registered_google'],2)
        self.assertTrue(all(r['identifier'].startswith('G-') for r in out['questions']))
        self.assertEqual(out['cost_basis'],'hourly_question_share')

    def test_no_identifier_not_counted_as_person(self):
        self.rows[1].pop('anon')
        self.assertEqual(m.report()[0]['users'],1)
        self.assertFalse(m._selected(self.rows[1],'anonymous'))

    def test_untrusted_filter_escaped(self):
        page=m.render('list',None,1,u='\"><script>alert(1)</script>')
        self.assertNotIn('<script>alert(1)</script>',page)
        self.assertIn('&lt;script&gt;',page)

    def test_audiences_and_invariant_costs(self):
        self.rows[1]['pk'] = 'e2e-historical-test'
        with patch.object(m, '_owner_actors', return_value={'user-1111111111111111'}):
            for audience, count in [('general', 0), ('owner', 2), ('test', 1), ('all', 3)]:
                d, hrs, total, rows = m.report(u=audience)
                self.assertEqual(d['questions'], count)
                self.assertAlmostEqual(d['total'], 12.006)
                self.assertAlmostEqual(total['usd'], 12.006)
                self.assertAlmostEqual(sum(h['usd'] for h in hrs),12.006)
                self.assertEqual(d['rejected'], {'per_minute':2})
            self.assertEqual(m.report()[0]['questions'], 0)
            self.assertEqual(m.as_json(None)['summary']['questions'], 3)
            page = m.render('hours', None, 1, u='test')
            self.assertIn('value="test" selected', page)
            self.assertIn('u=test', page)
            self.assertIn('概算コスト</div>', page)

    def test_dedicated_probe_identity_and_unknown(self):
        for actor in m.TEST_ACTORS:
            self.assertEqual(m._audience({'anon': actor}, {}), 'test')
        self.assertEqual(m._audience({'question':'これはテストです','anon':'random'}, {}), 'general')
        self.assertEqual(m._audience({'anon':'owner-browser'}, {'owner-browser'}), 'owner')

    def test_owner_registration_auth_csrf_and_validation(self):
        import json
        event = {'rawPath':'/admin/owner', 'requestContext':{'http':{'method':'POST'}},
                 'headers':{'origin':'https://agent.minoruonda.com'},
                 'body':json.dumps({'actors':['0123456789abcdef'], 'csrf':m._sign({'purpose':'owner-registration','exp':m.time.time()+60})})}
        with patch.object(m.ddb,'update_item') as update:
            self.assertEqual(m.handler(event,None)['statusCode'],302)
            update.assert_not_called()
            event['cookies']=['up_session='+m._sign({'email':m.ADMIN_EMAIL,'exp':m.time.time()+60})]
            self.assertEqual(m.handler(event,None)['statusCode'],200)
            update.assert_called_once()
            self.assertEqual(update.call_args.kwargs['Key'],{'pk':{'S':'audience#owner'}})
            event['headers']['origin']='https://evil.example'
            self.assertEqual(m.handler(event,None)['statusCode'],403)
            event['headers']['origin']='https://agent.minoruonda.com'
            event['body']=json.dumps({'actors':['0123456789abcdef'],'csrf':'bad'})
            self.assertEqual(m.handler(event,None)['statusCode'],400)

    def test_owner_lookup_failure_does_not_silently_count_owner_as_public(self):
        with patch.object(m,'_owner_actors',side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError):
                m.report()

    def test_registration_pagination_and_hash(self):
        # 使用箇所と同じ Username のハッシュで照合でき、質問していない登録者も数える。
        self.patches[3].stop()
        c=MagicMock(); c.list_users.side_effect=[{'Users':[{'Username':'Google_one'}],'PaginationToken':'next'},{'Users':[],'PaginationToken':'last'},{'Users':[{'Username':'Google_two'},{'Username':'admin'}]}]
        with patch.object(m,'APP_USER_POOL_ID','app-pool'),patch.object(m,'_registration_cache',None),patch.object(m.boto3,'client',return_value=c):
            users=m._registered_users()
            self.assertEqual(len(users),2)
            self.assertIn('user-'+hashlib.sha256(b'user:Google_one').hexdigest()[:16],users)
            self.assertEqual(c.list_users.call_count,3)
            self.assertEqual(c.list_users.call_args.kwargs['PaginationToken'],'last')

    def test_registration_failure_is_not_zero(self):
        self.patches[3].stop()
        c=MagicMock(); c.list_users.side_effect=RuntimeError('failure')
        with patch.object(m,'APP_USER_POOL_ID','app-pool'),patch.object(m,'_registration_cache',None),patch.object(m.boto3,'client',return_value=c):
            self.assertIsNone(m._registered_users())

if __name__=='__main__':unittest.main()


class BanSectionTests(unittest.TestCase):
    def setUp(self):
        self.items=[{'identifier':'A-0123456789abcdef','at':'2026-10-25T21:51:14+09:00','source':'auto','reason':'詮索','question':'q','day':'2026-10-25','linked_from':''}]

    def test_summary_counts_unreviewed_independent_of_age(self):
        b=m._ban_summary(self.items, dt.datetime(2026,10,25,22,tzinfo=m.JST))
        self.assertEqual((b['total'],b['new'],b['day']),(1,1,'2026-10-25'))
        self.assertEqual(m._ban_summary(self.items, dt.datetime(2026,10,30,tzinfo=m.JST))['new'],1)
        self.assertEqual(m._ban_summary([dict(self.items[0], reviewed_at='2026-10-26T00:00:00+09:00')], self.items[0]['at'])['new'],0)

    def test_card_shows_red_new_count_and_links_to_filter(self):
        d=dict(rejected={},first_p50=None,total=0,pace=None,feedback={'good':0,'bad':0},day=dt.datetime(2026,10,25,tzinfo=m.JST),
               questions=0,via={'typed':0,'ai':0,'starter':0},users=0,cost={})
        out=m._summary_html(d,{'total':1,'new':1,'day':'2026-10-25','ids':set()})
        for t in ['bancard hot','新着 1','v=bans']:
            self.assertIn(t,out)
        self.assertIn('0<small>ID</small>',m._summary_html(d,None))
        out=m._summary_html(d,{'total':1,'new':0,'day':'2026-10-25','ids':set(),'views':2,'attempts':1,'seen_at':'2026-10-25T22:10:00+09:00'})
        self.assertNotIn('停止後の再訪',out)
        self.assertNotIn('自由入力',out)
        self.assertNotIn('入口で止めた要求',out)
        self.assertEqual(m._ban_summary([dict(self.items[0],views=2,attempts=1,seen_at='2026-10-25T22:10:00+09:00')],dt.datetime(2026,10,26,tzinfo=m.JST))['views'],2)

    def test_filter_keeps_only_banned_people(self):
        rows=[dict(created_at='2026-10-25T21:43:00+09:00',anon='0123456789abcdef',question='x',answer='y',pk='s'),
              dict(created_at='2026-10-25T21:44:00+09:00',anon='fedcba9876543210',question='z',answer='y',pk='t')]
        out=m._list_html(dt.datetime(2026,10,25,tzinfo=m.JST),rows,1,'banned',banned={'A-0123456789abcdef'})
        self.assertIn('利用停止中',out); self.assertIn('>x<',out); self.assertNotIn('>z<',out)


class BanReviewTests(unittest.TestCase):
    def setUp(self):
        self.ident = 'A-0123456789abcdef'
        self.ban = dict(identifier=self.ident, at='2026-10-25T21:51:14+09:00', source='auto',
                        reason='判定理由', conv_pk='session', conv_sk='key2', day='2026-10-25', views=7, attempts=2,
                        seen_at='2026-10-26T12:00:00+09:00')
        self.rows = [dict(pk='session', sk=f'key{i}', created_at=f'2026-10-25T21:4{i}:00+09:00',
                          question='同じ入力', answer='回答', anon=self.ident[2:]) for i in (1,2)]

    def event(self, token=None):
        import json
        token = token or m._sign(dict(purpose='ban-review', identifier=self.ident, at=self.ban['at'], exp=m.time.time()+60))
        return dict(rawPath='/admin/ban-reviewed', requestContext={'http':{'method':'POST'}},
                    headers={'origin':'https://agent.minoruonda.com'}, body=json.dumps({'csrf':token}),
                    cookies=['up_session='+m._sign(dict(email=m.ADMIN_EMAIL, exp=m.time.time()+60))])

    def test_exact_keys_distinguish_identical_questions(self):
        self.assertFalse(m._caused_ban(self.rows[0], self.ban))
        self.assertTrue(m._caused_ban(self.rows[1], self.ban))
        self.assertFalse(m._caused_ban(self.rows[1], dict(self.ban, conv_sk='')))

    def test_history_scans_all_pages_and_filters_id(self):
        client=MagicMock()
        client.scan.side_effect=[{'Items':[], 'LastEvaluatedKey':{'pk':{'S':'next'}}},
                                {'Items':[{'pk':{'S':'session'},'sk':{'S':'key2'}, 'created_at':{'S':'2026-10-25'}}]}]
        with patch.object(m,'ddb',client):
            rows=m._ban_history(self.ident)
        self.assertEqual(len(rows),1)
        self.assertEqual(client.scan.call_count,2)
        args=client.scan.call_args.kwargs
        self.assertEqual(args['ExclusiveStartKey'],{'pk':{'S':'next'}})
        self.assertEqual(args['ExpressionAttributeValues'],{':id':{'S':self.ident[2:]}})
        self.assertNotIn('created_at',args['FilterExpression'])
        with self.assertRaises(ValueError): m._ban_history('invalid')

    def test_review_auth_origin_csrf_and_conditional_write(self):
        client=MagicMock()
        with patch.object(m,'ddb',client):
            event=self.event(); event['cookies']=[]
            self.assertEqual(m.handler(event,None)['statusCode'],302)
            event=self.event(); event['headers']['origin']='https://evil.example'
            self.assertEqual(m.handler(event,None)['statusCode'],403)
            self.assertEqual(m.handler(self.event('bad-token'),None)['statusCode'],400)
            event=self.event(); event['requestContext']['http']['method']='GET'
            self.assertEqual(m.handler(event,None)['statusCode'],405)
            client.update_item.assert_not_called()
            self.assertEqual(m.handler(self.event(),None)['statusCode'],200)
            args=client.update_item.call_args.kwargs
            self.assertIn('attribute_exists(pk)',args['ConditionExpression'])
            self.assertEqual(args['ExpressionAttributeValues'][':at']['S'],self.ban['at'])
            self.assertEqual(args['Key']['pk']['S'],'ban#a#0123456789abcdef')
            self.assertIn('if_not_exists',args['UpdateExpression'])

    def test_changed_ban_is_not_marked_reviewed(self):
        class Changed(Exception): pass
        client=MagicMock();client.exceptions.ConditionalCheckFailedException=Changed
        client.update_item.side_effect=Changed()
        with patch.object(m,'ddb',client):
            self.assertEqual(m.handler(self.event(),None)['statusCode'],409)

    def test_detail_has_cause_history_visits_and_escaped_text(self):
        self.rows[1]['question']='<script>alert(1)</script> person@example.com'
        with patch.object(m,'bans',return_value=[self.ban]),patch.object(m,'_ban_history',return_value=self.rows),patch.object(m,'ddb') as client:
            page=m.render_bans(self.ident)
            client.update_item.assert_not_called()
        self.assertIn('停止の決め手になった書き込み',page)
        self.assertIn('全期間・2件',page)
        self.assertIn('<strong>7<small>回',page)
        self.assertIn('2026/10/26（月）12:00:00',page)
        self.assertNotIn('<script>alert(1)',page)
        self.assertNotIn('person@example.com',page)
        self.assertIn('［メール］',page)

    def test_overview_does_not_mark_anyone_read(self):
        with patch.object(m,'bans',return_value=[self.ban]),patch.object(m,'ddb') as client:
            page=m.render_bans()
            self.assertIn('data-review=""',page)
            self.assertIn('v=bans&amp;u='+self.ident,page)
            client.update_item.assert_not_called()

    def test_linked_cause_and_missing_manual_record(self):
        linked=dict(self.ban,identifier='G-fedcba9876543210',source='linked',linked_from=m._ban_key(self.ident),conv_pk='',conv_sk='')
        with patch.object(m,'bans',return_value=[linked,self.ban]),patch.object(m,'_ban_history',side_effect=[[],self.rows]):
            page=m.render_bans(linked['identifier'])
        self.assertIn('停止の決め手になった書き込み',page)
        self.assertIn('元の停止',page)
        with patch.object(m,'bans',return_value=[dict(self.ban,source='manual',conv_pk='',conv_sk='')]),patch.object(m,'_ban_history',return_value=self.rows):
            page=m.render_bans(self.ident)
        self.assertIn('特定できる記録がありません',page)
        self.assertNotIn('停止の決め手になった書き込み',page)

    def test_cost_only_three_metrics(self):
        case=UsagePageTests();case.setUp()
        try:
            page=m._summary_html(m.report()[0])
            self.assertIn('月額予測（直近7日平均）',page)
            self.assertEqual(page.count('class="metric'), 7)
            self.assertIn('metric monthly',page)
        finally:case.doCleanups()
