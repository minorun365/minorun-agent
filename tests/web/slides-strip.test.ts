import { test } from 'node:test';
import assert from 'node:assert/strict';
import { stripLine, stripView } from '../../web/src/slides-strip.ts';

test('上の帯：段階に応じて棒が伸び、完成・失敗で見え方が変わる', () => {
  assert.equal(stripView(null).kind, 'working');
  assert.equal(stripView({ stage: 'queued' }).pct, 6);
  const order = ['reading', 'writing', 'rendering', 'checking'].map((stage) => stripView({ stage }).pct);
  assert.deepEqual([...order].sort((a, b) => a - b), order);
  assert.ok(order.every((p) => p > 6 && p < 100));
  const done = stripView({ stage: 'done', title: '題' });
  assert.equal(done.kind, 'done'); assert.equal(done.title, '題');
  assert.equal(stripView({ stage: 'error' }).kind, 'error');
});

test('上の帯：スマホ幅で切れないよう、detail の長い文は出さず段階の短い言葉だけにする', () => {
  const long = stripView({ stage: 'checking', detail: '書き出して余白を測っています（2回目）' });
  assert.equal(long.line, '仕上げています');
  for (const stage of ['queued', 'reading', 'writing', 'rendering', 'checking']) assert.ok(stripLine({ stage }).length <= 9, stage);
  assert.equal(stripLine({ stage: 'reading', kind: 'topic' }), '出典を読んでいます');
});
