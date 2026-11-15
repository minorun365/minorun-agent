"""監視ダッシュボードの「スライド」タブ。python3 -m unittest tests/infra/test_usage_slides.py"""
import datetime as dt
import unittest
from unittest.mock import patch

import test_usage_page

m = test_usage_page.m

sv = m.slides_view


def job(name, when, user='1111111111111111', **status):
    return {'job': name, 'at': int(when.timestamp()), 'user': user, 'status': status, 'log': {}}


class SlidesTabTests(unittest.TestCase):
    setUp = test_usage_page.UsagePageTests.setUp  # 同じ架空の質問と費用で描く（親の検査は流し直さない）

    def test_attach_picks_nearest_question_of_same_user(self):
        at = self.start + dt.timedelta(hours=1)
        rows = [dict(created_at=(at + dt.timedelta(minutes=2)).isoformat(), user='1111111111111111', tools=['make_slides'], question='近い https://example.com/a'),
                dict(created_at=(at + dt.timedelta(minutes=9)).isoformat(), user='1111111111111111', tools=['make_slides'], question='遠い'),
                dict(created_at=at.isoformat(), user='9999999999999999', tools=['make_slides'], question='別の人'),
                dict(created_at=at.isoformat(), user='1111111111111111', tools=['search_2026'], question='スライドではない')]
        jobs = sv.attach([job('a', at)], rows)
        self.assertEqual(jobs[0]['row']['question'], '近い https://example.com/a')
        far = sv.attach([job('b', at - dt.timedelta(hours=1))], rows)
        self.assertNotIn('row', far[0])

    def test_states_and_stale_work_counts_as_failure(self):
        now = self.start + dt.timedelta(hours=2)
        fresh = job('w', now, stage='writing', at=int(now.timestamp()))
        stale = job('s', now, stage='writing', at=int((now - dt.timedelta(hours=1)).timestamp()))
        self.assertEqual(sv.state(fresh, now), 'working')
        self.assertEqual(sv.state(stale, now), 'error')
        self.assertEqual(sv.state(job('d', now, stage='done'), now), 'done')

    def test_panel_shows_thumbnail_title_question_and_cost(self):
        at = self.start + dt.timedelta(hours=1)
        done = job('abc', at, stage='done', title='<題>', pages=19)
        done['log'] = {'usd': 0.4567, 'seconds': 95.2, 'rounds': 2}
        done['row'] = {'question': 'これをスライドにして https://example.com/post 連絡先 a@b.co'}
        failed = job('bad', at, stage='error', message='記事の本文を読めませんでした。')
        out = sv.panel([done, failed], self.now, False, m._mask, m._url, 'この日')
        self.assertIn('src="/s/abc/og.jpg"', out)
        self.assertIn('&lt;題&gt;', out)
        self.assertIn('19枚', out)
        self.assertIn('$0.46', out)
        self.assertIn('1回直した', out)
        self.assertIn('>example.com/post</a>', out)
        self.assertNotIn('a@b.co', out)
        self.assertIn('記事の本文を読めませんでした', out)
        self.assertIn('できた 1・作成中 0・失敗 1', out)
        self.assertIn('?v=list&amp;d=2026-10-25&amp;u=G-1111111111111111', out)

    def test_read_failure_is_not_zero(self):
        out = sv.panel(None, self.now, False, m._mask, m._url, 'この日')
        self.assertIn('取得できませんでした', out)

    def test_render_has_tab_with_count_and_filters_owner(self):
        at = self.start + dt.timedelta(hours=1)
        jobs = [job('mine', at, user='aaaaaaaaaaaaaaaa', stage='done', title='本人'),
                job('theirs', at, user='bbbbbbbbbbbbbbbb', stage='done', title='一般')]
        with patch.object(sv, 'TABLE', 't'), patch.object(sv, 'load', return_value=jobs), \
                patch.object(sv, '_jobs', return_value=jobs), patch.object(m, 'bans', return_value=[]), \
                patch.object(m, '_owner_actors', return_value={'user-aaaaaaaaaaaaaaaa'}):
            page = m.render('slides', None, 1)
            self.assertIn('スライド<span class="tab-count">1</span>', page)
            self.assertIn('<h3>一般</h3>', page)
            self.assertNotIn('<h3>本人</h3>', page)
            everyone = m.render('slides', None, 1, u='all')
            self.assertIn('スライド<span class="tab-count">2</span>', everyone)
            summary = m.render(None, None, 1)
            self.assertIn('スライド<span class="tab-count">1</span>', summary)
            self.assertNotIn('og.jpg', summary)


    def test_opus_cost_goes_to_total_and_to_the_slide_question(self):
        self.metrics[('opus', 'out')] = {self.start + dt.timedelta(hours=1): 50000}  # 50万分の1 → $1.00
        self.rows[2]['tools'] = ['make_slides']
        d, hrs, total, rows = m.report(u='all')
        self.assertAlmostEqual(d['cost']['opus'], 1.0)
        self.assertAlmostEqual(d['aws_total'], 13.001)  # 元の 12.006 から Brave 1回分を引き、Opus $1 を足す
        slide_row = next(r for r in rows if r['pk'] == 's3')
        self.assertGreater(slide_row['estimated_usd'], 1.0)
        self.assertAlmostEqual(sum(p['estimated_usd'] for p in d['people']) + d['unallocated_usd'], d['total'])
        self.assertIn('Opus 5.5（スライド作成）', m.render(None, None, 1))

    def test_opus_without_slide_question_stays_unallocated(self):
        self.metrics[('opus', 'out')] = {self.start: 50000}
        d, hrs, total, rows = m.report(u='all')
        self.assertAlmostEqual(d['unallocated_usd'], 7.0)
        self.assertAlmostEqual(sum(p['estimated_usd'] for p in d['people']) + d['unallocated_usd'], d['total'])


if __name__ == '__main__':
    unittest.main()
