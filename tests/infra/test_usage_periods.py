"""期間境界・登録日時・ユーザー内訳から質問までの一貫性。"""
import datetime as dt
import html
import unittest
import json
import re
import subprocess
from unittest.mock import patch, MagicMock
from test_usage_page import UsagePageTests, m
import periods


class PeriodTests(unittest.TestCase):
    def test_calendar_boundaries(self):
        now = dt.datetime(2027, 1, 2, 12, tzinfo=periods.JST)
        since = dt.datetime(2026, 10, 25, tzinfo=periods.JST)
        _, start, end = periods.bounds('week:', now, since)
        self.assertEqual(start.date(), dt.date(2026, 12, 28))
        self.assertEqual(end, now)
        _, start, end = periods.bounds('month:2026-12-15', now, since)
        self.assertEqual((start.date(), end.date()), (dt.date(2026, 12, 1), dt.date(2027, 1, 1)))
        self.assertEqual(periods.shift('month', start, 1).date(), dt.date(2027, 1, 1))
        self.assertEqual(periods.bounds('all', now, since)[1:], (since, now))
        self.assertEqual(periods.bounds('invalid', now, since)[1].date(), now.date())
        self.assertEqual(periods.bounds('2099-01-01', now, since)[1].date(), now.date())

    def test_leap_month_and_live_period(self):
        now = dt.datetime(2028, 3, 3, tzinfo=periods.JST)
        mode, start, end = periods.bounds('month:2028-02-20', now, now)
        self.assertEqual((end-start).days, 29)
        self.assertEqual(periods.key(mode, start, now), 'month:2028-02-01')
        current = periods.bounds('month:', now, now)[1]
        self.assertEqual(periods.key('month', current, now), 'month:')


class RegistrationPeriodTests(UsagePageTests):
    def test_period_buttons_link_without_dropdown(self):
        now = dt.datetime(2026,11,4,12,tzinfo=m.JST)
        def controls(day):
            mode, start, end = periods.bounds(day, now, m.COST_SINCE)
            data = {'period': mode, 'day': start, 'end': end, 'now': now}
            return html.unescape(m._period_controls(data,'list','google',None,'new'))
        today = controls(None)
        self.assertNotIn('<select', today)
        self.assertNotIn('<input', today)
        units = dict(re.findall(r'<a href="([^"]+)"[^>]*>(日|週|月|全期間)</a>', today))
        self.assertEqual(sorted(units.values()), sorted(['日','週','月','全期間']))
        links = {v: k for k, v in units.items()}
        # 今日を含む期間から単位を替えると、今週・今月（日付を固定しない）へ移る
        self.assertIn('d=week%3A&', links['週'])
        self.assertIn('d=month%3A&', links['月'])
        self.assertIn('d=all', links['全期間'])
        self.assertIn('u=google', links['週'])
        self.assertIn('aria-current="true">日<', today)
        # 未来へは進めない
        self.assertIn('class="step off" aria-hidden="true">›', today)
        # 過去の月から週へ替えると、その月の初日を含む週を出す
        past = controls('month:2026-10-10')
        self.assertIn('<b>2026年10月</b>', past)
        self.assertIn('2026/11/2（月）〜11/4（水）', controls('week:'))
        week = dict((v, k) for k, v in re.findall(r'<a href="([^"]+)"[^>]*>(日|週|月|全期間)</a>', past))['週']
        self.assertIn('d=week%3A2026-09-28', week)
        self.assertIn('aria-label="翌月"', past)
        # 公開日より前の期間へは戻れない
        self.assertIn('class="step off" aria-hidden="true">‹', past)
        self.assertIn('公開から現在まで', controls('all'))
        handler = re.search(r'onclick="([^"]+)"', today).group(1)
        script = '''
const links=[{a:1,attrs:{}},{a:2,attrs:{'aria-current':'true'}}].map(o=>({closest:()=>null,setAttribute(k,v){o.attrs[k]=v},removeAttribute(k){delete o.attrs[k]},o}));
links[0].closest=()=>links[0];const nav={classList:{added:[],add(c){this.added.push(c)}}};
const self={querySelectorAll:()=>links};const document={querySelector:()=>nav};
new Function('event','document',HANDLER).call(self,{target:links[0]},document);
if(links[0].o.attrs['aria-current']!=='true'||links[1].o.attrs['aria-current']||nav.classList.added[0]!=='loading')throw Error('press');
'''.replace('HANDLER', json.dumps(handler))
        subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)

    def test_registration_date_uses_jst(self):
        m._registration_cache = None
        client = MagicMock()
        client.list_users.side_effect = [
            {'Users': [{'Username': 'Google_one', 'UserCreateDate': dt.datetime(2026,10,25,15,tzinfo=dt.timezone.utc)}], 'PaginationToken': 'next'},
            {'Users': [{'Username': 'Google_two', 'UserCreateDate': dt.datetime(2026,10,25,14,59,tzinfo=dt.timezone.utc)}]}]
        # setUp mocks the public lookup; invoke the saved real function.
        with patch.object(m, 'APP_USER_POOL_ID', 'test'), patch.object(m.boto3, 'client', return_value=client):
            users = REAL_REGISTERED()
        self.assertEqual(sorted(v['registered_at'] for v in users.values()), ['2026-10-25T23:59:00+09:00','2026-10-26T00:00:00+09:00'])
        self.assertEqual(client.list_users.call_count, 2)
        m._registration_cache = None

    def test_new_and_active_are_distinct_and_links_preserve_month(self):
        now = dt.datetime(2026,11,2,12,tzinfo=m.JST)
        start = dt.datetime(2026,10,1,tzinfo=m.JST)
        registered = {'user-1111111111111111': {'registered_at':'2026-09-30T23:59:59+09:00'},
                      'user-3333333333333333': {'registered_at':'2026-10-01T00:00:00+09:00'},
                      'user-4444444444444444': {'registered_at':'2026-11-01T00:00:00+09:00'}}
        with patch.object(m, 'questions', return_value=(now,start,self.rows)), patch.object(m, '_registered_users',return_value=registered), patch.object(m, 'bans', return_value=[]):
            data, hrs, total, rows = m.report('month:2026-10-20')
            self.assertEqual((data['registered_google'],data['new_google'],data['active_google']), (3,1,1))
            new_page = html.unescape(m._people_list(data,'new'))
            self.assertIn('G-3333333333333333',new_page)
            self.assertNotIn('G-1111111111111111',new_page)
            self.assertIn('d=month%3A2026-10-01&u=G-3333333333333333',new_page)
            active = m._people_list(data,'active')
            self.assertIn('G-1111111111111111',active)
            self.assertNotIn('G-3333333333333333',active)
            page = m.render('people','month:2026-10-20',1,group='new')
            self.assertIn('name="d" value="month:2026-10-01"',page)
            self.assertIn('この期間の新規登録',page)
            self.assertIn('この期間の質問',page)
            self.assertIn('2026/10/25（日）',page)
            self.assertEqual(total['users'],2)
            daily = m._hours_html(hrs,total,multi=True)
            self.assertIn('日ごと',daily)
            self.assertIn('<td>2</td><td>3</td><td>3</td>',daily)

    def test_unknown_registration_is_not_zero(self):
        with patch.object(m, '_registered_users', return_value=None):
            data = m.report()[0]
        self.assertIsNone(data['new_google'])
        self.assertIn('登録情報を取得できませんでした',m._people_list(data,'new'))

    def test_all_period_json_and_question_query(self):
        now = self.start + dt.timedelta(days=3)
        with patch.object(m, 'questions', return_value=(now,self.start,self.rows)), patch.object(m,'bans',return_value=[]):
            data=m.as_json('all')
            self.assertEqual(data['selection'],'all')
            self.assertEqual(data['period'],'all')
            self.assertEqual(len(data['hours']['rows']),72)
            page=m.render('list','all',1,u='G-1111111111111111')
            self.assertIn('d=all',page)
            self.assertIn('2026/10/25（日） 00:00',page)

    def test_cloudwatch_pagination(self):
        first = {'Id':'m0','Timestamps':[self.start],'Values':[10]}
        second = {'Id':'m0','Timestamps':[self.start+dt.timedelta(hours=1)],'Values':[20]}
        with patch.object(m.cw,'get_metric_data',side_effect=[{'MetricDataResults':[first],'NextToken':'next'}, {'MetricDataResults':[second]}]) as call:
            result=REAL_METRICS(self.start,self.now,3600)
        self.assertEqual(sum(result[('sonnet','in')].values()),30)
        self.assertEqual(call.call_args.kwargs['NextToken'],'next')

REAL_REGISTERED=m._registered_users
REAL_METRICS=m._metrics
