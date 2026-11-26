import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CHIP_LABEL, isDoc, videoStep } from '../../web/src/video-card';
import { stripView } from '../../web/src/slides-strip';

test('資料は種類の名前か拡張子で見分ける', () => {
  assert.equal(isDoc({ name: 'a.pdf', type: '' }), true);
  assert.equal(isDoc({ name: '提案.PPTX', type: '' }), true);
  assert.equal(isDoc({ name: 'x', type: 'application/pdf' }), true);
  assert.equal(isDoc({ name: 'photo.jpg', type: 'image/jpeg' }), false);
  assert.equal(isDoc({ name: '古い.ppt', type: '' }), false);
});

test('札の文は資料の名前を出さず1つにそろえる', () => {
  assert.equal(CHIP_LABEL, 'この資料を3分の動画にする');
});

test('動画の段階は4つに直し、帯も動画の言葉で出す', () => {
  assert.deepEqual(['queued', 'reading', 'writing', 'voicing', 'rendering', 'done'].map(videoStep), [-1, 0, 1, 2, 3, 4]);
  assert.equal(stripView({ stage: 'voicing' }, '/v/abcdefgh1234/').line, '声を録っています');
  assert.equal(stripView({ stage: 'done' }, '/v/abcdefgh1234/').line, '動画ができました！');
  assert.equal(stripView({ stage: 'writing' }).line, '構成を考えています');
});
