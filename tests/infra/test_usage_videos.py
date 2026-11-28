"""監視ダッシュボードの「動画」タブ。python3 -m unittest tests/infra/test_usage_videos.py"""
import datetime as dt
import unittest
from unittest.mock import patch

import test_usage_page

m = test_usage_page.m
vv = m.videos_view


def job(key, when, user='1111111111111111', log=None, **status):
    return {'job': key, 'at': int(when.timestamp()), 'user': user, 'status': status, 'log': log or {}}


class VideosTabTests(unittest.TestCase):
    setUp = test_usage_page.UsagePageTests.setUp

    def test_owner_of_guest_is_anonymous(self):
        self.assertEqual(vv.identifier('a:5149dc4b88a40a56'), 'A-5149dc4b88a40a56')
        self.assertEqual(vv.identifier('1111111111111111'), 'G-1111111111111111')
        self.assertEqual(m._selected(dict(vv.row('a:5149dc4b88a40a56'), audience='general'), 'anonymous'), True)
        self.assertEqual(m._selected(dict(vv.row('a:5149dc4b88a40a56'), audience='general'), 'google'), False)

    def test_states_and_stale_work_counts_as_failure(self):
        now = self.start + dt.timedelta(hours=2)
        self.assertEqual(vv.state(job('w', now, stage='voicing', at=int(now.timestamp())), now), 'working')
        self.assertEqual(vv.state(job('s', now, stage='voicing', at=int((now - dt.timedelta(hours=1)).timestamp())), now), 'error')
        self.assertEqual(vv.state(job('d', now, stage='done'), now), 'done')

    def test_panel_shows_poster_title_doc_length_and_cost(self):
        at = self.start + dt.timedelta(hours=1)
        log = {'elapsed': 744, 'usage': {'input': 100000, 'output': 30000}}  # 0.4 + 0.6 = $1.00
        done = job('abc', at, user='a:5149dc4b88a40a56', log=log, stage='done', title='<題>', seconds=187.1,
                   pages=60, total=120, name='資料.pdf')
        failed = job('bad', at, stage='error', message='資料を読めませんでした。')
        out = vv.panel([done, failed], self.now, False, m._url, 'この日')
        self.assertIn('src="/v/abc/poster.jpg"', out)
        self.assertIn('href="/v/abc/"', out)
        self.assertIn('&lt;題&gt;', out)
        self.assertIn('3分07秒', out)
        self.assertIn('60ページ（全120ページの先頭）', out)
        self.assertIn('作成12分', out)
        self.assertIn('$1.00', out)
        self.assertIn('資料：資料.pdf', out)
        self.assertIn('u=A-5149dc4b88a40a56', out)
        self.assertIn('資料を読めませんでした', out)
        self.assertIn('できた 1・作成中 0・失敗 1', out)

    def test_read_failure_is_not_zero(self):
        self.assertIn('取得できませんでした', vv.panel(None, self.now, False, m._url, 'この日'))

    def test_render_has_tab_with_count_and_filters_owner(self):
        at = self.start + dt.timedelta(hours=1)
        jobs = [job('mine', at, user='aaaaaaaaaaaaaaaa', stage='done', title='本人'),
                job('theirs', at, user='a:bbbbbbbbbbbbbbbb', stage='done', title='一般')]
        with patch.object(vv, 'TABLE', 't'), patch.object(vv, 'load', return_value=jobs), \
                patch.object(vv, '_jobs', return_value=jobs), patch.object(m, 'bans', return_value=[]), \
                patch.object(m, '_owner_actors', return_value={'user-aaaaaaaaaaaaaaaa'}):
            page = m.render('videos', None, 1)
            self.assertIn('動画<span class="tab-count">1</span>', page)
            self.assertIn('<h3>一般</h3>', page)
            self.assertNotIn('<h3>本人</h3>', page)
            self.assertIn('動画<span class="tab-count">2</span>', m.render('videos', None, 1, u='all'))
            summary = m.render(None, None, 1)
            self.assertIn('動画<span class="tab-count">1</span>', summary)
            self.assertNotIn('poster.jpg', summary)


if __name__ == '__main__':
    unittest.main()


class VideoCostTests(unittest.TestCase):
    setUp = test_usage_page.UsagePageTests.setUp

    def test_api_cost_skips_jobs_that_fell_back_to_bedrock(self):
        at = self.start + dt.timedelta(hours=1, minutes=5)
        ms = int(at.timestamp() * 1000)
        done = {'job': 'x', 'ok': True, 'usage': {'input': 100000, 'output': 30000}}  # $1.00

        def fake(_logs, _group, pattern, s, e):
            if 'model_route' in pattern:
                return [(ms, 'stream-b', {'model_route': 'bedrock'})]
            return [(ms, 'stream-a', done), (ms, 'stream-b', dict(done, job='y'))]
        with patch.object(vv, 'LOG_PREFIX', 'p'), patch.object(vv, '_log_groups', return_value=['g']), \
                patch.object(vv, '_events', side_effect=fake), patch.object(vv.boto3, 'client'):
            out = vv.api_usd_by_hour(self.start, self.start + dt.timedelta(days=1))
        self.assertEqual(list(out.values()), [1.0])
        self.assertEqual(list(out), [self.start + dt.timedelta(hours=1)])

    def test_video_cost_goes_to_total_but_not_aws_or_questions(self):
        before, _, _, _ = m.report(u='all')
        with patch.object(vv, 'api_usd_by_hour', return_value={self.start + dt.timedelta(hours=1): 1.0}):
            d, hrs, total, rows = m.report(u='all')
            page = m.render(None, None, 1)
        self.assertAlmostEqual(d['cost']['video_api'], 1.0)
        self.assertAlmostEqual(d['total'], before['total'] + 1.0)
        self.assertAlmostEqual(d['aws_total'], before['aws_total'])
        self.assertAlmostEqual(d['unallocated_usd'], before['unallocated_usd'] + 1.0)
        self.assertAlmostEqual(sum(p['estimated_usd'] for p in d['people']) + d['unallocated_usd'], d['total'])
        self.assertIn('Opus 5.5（解説動画・Claude API）', page)
