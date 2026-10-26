import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { POSES, PHASE_POSES, TOOL_POSES, PosePicker, PoseDirector } from '../../web/src/poses.js';

test('採用した23素材が存在し、すべて表示先を持つ', () => {
  const assigned = new Set([...Object.values(PHASE_POSES).flat(), ...Object.values(TOOL_POSES).flat()]);
  assert.equal(POSES.length, 23);
  for (const pose of POSES) {
    assert.ok(existsSync(`web/public/chara/${pose}.webp`), pose);
    assert.ok(assigned.has(pose), pose);
  }
  assert.ok(!assigned.has('bow' as never));
});
test('同じ役割での連続重複と読み込み失敗した素材を避ける', () => {
  const picker = new PosePicker(() => 0);
  assert.equal(picker.pick('greeting', ['wave', 'listen']), 'wave');
  assert.equal(picker.pick('greeting', ['wave', 'listen']), 'listen');
  assert.equal(picker.pick('greeting', ['wave', 'listen'], new Set(['wave'])), 'listen');
  assert.equal(picker.pick('greeting', ['wave', 'listen'], new Set(['wave', 'listen'])), 'idle');
});
test('本文の断片で再抽選せず、回答への移行で保留したツール表示を取り消す', t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] });
  const seen: string[] = [];
  const d = new PoseDirector((_, state) => seen.push(state));
  d.tool('search_2026'); d.tool('search_web');
  d.phase('answering'); d.phase('answering');
  t.mock.timers.tick(1000);
  assert.deepEqual(seen, ['tool:search_2026', 'answering']);
});
test('続いたツールは最新のものを表示し、エラー後は戻さない', t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] });
  const seen: string[] = [];
  const d = new PoseDirector((_, state) => seen.push(state));
  d.tool('now'); d.tool('search_2026'); d.tool('search_sessions');
  t.mock.timers.tick(599);
  assert.deepEqual(seen, ['tool:now']);
  t.mock.timers.tick(1);
  assert.deepEqual(seen, ['tool:now', 'tool:search_sessions']);
  d.tool('search_web'); d.phase('error'); t.mock.timers.tick(1000);
  assert.equal(seen.at(-1), 'error'); assert.equal(seen.length, 3);
});

test('全ツールの実行中は回答用の説明・ひらめきを使わない', () => {
  for (const [name, poses] of Object.entries(TOOL_POSES)) {
    for (const pose of poses) assert.ok(!PHASE_POSES.answering.includes(pose as never), `${name}: ${pose}`);
  }
});
test('空の本文や空白では回答へ移らず、保留中の画像準備も取り消さない', t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] });
  const seen: string[] = [];
  const d = new PoseDirector((_, state) => seen.push(state));
  d.tool('search_2026'); d.tool('show_map');
  assert.equal(d.answer(''), false);
  assert.equal(d.answer(' \n\t', true), false);
  t.mock.timers.tick(600);
  assert.deepEqual(seen, ['tool:search_2026', 'tool:show_map']);
  assert.equal(d.answer('会場はこちらです。', true), true);
  assert.deepEqual(seen, ['tool:search_2026', 'tool:show_map', 'map']);
});
test('回答途中の追加ツール後も、次の本文まで作業姿勢を保つ', t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] });
  const seen: string[] = [];
  const d = new PoseDirector((_, state) => seen.push(state));
  d.answer('調べた内容です。'); d.tool('show_map');
  d.answer('\n'); t.mock.timers.tick(1000);
  assert.deepEqual(seen, ['answering', 'tool:show_map']);
  d.answer('写真はこちらです。'); d.answer('続きです。');
  assert.deepEqual(seen, ['answering', 'tool:show_map', 'answering']);
});

test('直前のツールから600ms以内でも回答から画像準備には即座に戻る', t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] });
  const seen: string[] = [];
  const d = new PoseDirector((_, state) => seen.push(state));
  d.tool('search_2026'); d.answer('構成で気をつけた点です。'); d.tool('show_map');
  assert.deepEqual(seen, ['tool:search_2026', 'answering', 'tool:show_map']);
});
test('画像が未表示なら本文とストリームが終わっても回答ポーズへ移らない', () => {
  const seen: string[] = [];
  const d = new PoseDirector((_, state) => seen.push(state));
  const ready = d.prepareImage();
  assert.equal(d.answer('アーキテクチャ図を添えました。'), false);
  assert.deepEqual(seen, ['tool:show_map']);
  ready(); ready();
  assert.deepEqual(seen, ['tool:show_map', 'answering']);
});
test('複数画像の読み込みは全件を待ち、次の質問やエラーへ古い完了を反映しない', t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] });
  const seen: string[] = [];
  const d = new PoseDirector((_, state) => seen.push(state));
  const first = d.prepareImage(), second = d.prepareImage();
  d.answer('地図です。', true); first();
  assert.equal(seen.at(-1), 'tool:show_map');
  second(); assert.equal(seen.at(-1), 'map');
  const old = d.prepareImage(); d.answer('古い回答'); d.phase('thinking', true); old();
  assert.equal(seen.at(-1), 'thinking');
  const broken = d.prepareImage(); d.answer('回答'); d.phase('error'); broken();
  t.mock.timers.tick(1000); assert.equal(seen.at(-1), 'error');
});
