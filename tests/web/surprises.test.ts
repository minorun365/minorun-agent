import { test } from 'node:test';
import assert from 'node:assert/strict';
import { candidatesFor, chooseGift, chooseSupport, SUPPORT_INTERVAL, freshState, gifts } from '../../web/src/surprises';
test('支援案内は5回答目から10％、外れ続けても強制表示しない', () => {
  const s = freshState();
  s.turns = 4;
  assert.equal(chooseSupport(s, 0, 1e9, () => 0), undefined);
  s.turns = 5;
  assert.equal(chooseSupport(s, 0, 1e9, () => .099), gifts['support-github']);
  assert.equal(chooseSupport(s, 0, 1e9, () => .1), undefined);
  s.turns = 100;
  assert.equal(chooseSupport(s, 0, 1e9, () => .9), undefined);
});
test('支援案内は7日間・会話内の再表示・付箋同士の間隔を守る', () => {
  const s = { ...freshState(), turns: 5 };
  const last = 1e9;
  assert.equal(chooseSupport(s, last, last + SUPPORT_INTERVAL - 1, () => 0), undefined);
  assert.equal(chooseSupport(s, last, last + SUPPORT_INTERVAL, () => 0), gifts['support-github']);
  for (const invalid of [null, NaN, Infinity, -1, last + SUPPORT_INTERVAL + 1]) {
    assert.equal(chooseSupport(s, invalid, last + SUPPORT_INTERVAL, () => 0), undefined);
  }
  s.seen = ['support'];
  assert.equal(chooseSupport(s, 0, last, () => 0), undefined);
  s.seen = ['slide']; s.lastTurn = 4;
  assert.equal(chooseSupport(s, 0, last, () => 0), undefined);
  s.lastTurn = 3; s.lastAt = last - 59_999;
  assert.equal(chooseSupport(s, 0, last, () => 0), undefined);
  s.lastAt = last - 60_000;
  assert.equal(chooseSupport(s, 0, last, () => 0), gifts['support-github']);
  s.seen = ['slide', 'english', 'experience'];
  assert.equal(chooseSupport(s, 0, last, () => 0), undefined);
});
test('初回表示は対象外で、抽選の外れでは何も出さない', () => {
  const s = freshState();
  assert.equal(chooseGift(s, [gifts.experience], 1e6, () => 0), undefined);
  s.turns = 1;
  assert.equal(chooseGift(s, [gifts.experience], 1e6, () => .4), undefined);
  assert.equal(chooseGift(s, [gifts.experience], 1e6, () => .39), gifts.experience);
});
test('2問・1分の間隔、同じ付箋の除外、1会話3回までを守る', () => {
  const s = { turns: 2, lastTurn: 1, lastAt: 1e6, seen: ['experience'] };
  assert.equal(chooseGift(s, [gifts.thanks], 2e6, () => 0), undefined);
  s.turns = 3;
  assert.equal(chooseGift(s, [gifts.thanks], 1.059e6, () => 0), undefined);
  assert.equal(chooseGift(s, [gifts.experience], 1.06e6, () => 0), undefined);
  assert.equal(chooseGift(s, [gifts.thanks], 1.06e6, () => 0), gifts.thanks);
  s.seen.push('thanks', 'english');
  assert.equal(chooseGift(s, [gifts.slide], 2e6, () => 0), undefined);
});
test('Expoの深掘りと、去年のエージェントの話を区別する', () => {
  assert.deepEqual(candidatesFor('デモを頼むには', 'Expo', 1), [gifts.slide]);
  assert.deepEqual(candidatesFor('デモを頼むには', 'Expo', 2), [gifts.english]);
  assert.deepEqual(candidatesFor('去年のキーノート', '', 0), [gifts.experience]);
  assert.deepEqual(candidatesFor('去年のエージェントについて', '', 0), [gifts.slide]);
  assert.deepEqual(candidatesFor('ホテルの場所', '', 0), [gifts.slide]);
});

test('一般的な話題でも候補があり、初回は4回答までに出会える', () => {
  const s = freshState();
  const candidates = candidatesFor('ホテルの場所', 'hotels', 0);
  s.turns = 3;
  assert.equal(chooseGift(s, candidates, 1e6, () => .9), undefined);
  s.turns = 4;
  assert.equal(chooseGift(s, candidates, 1e6, () => .9), gifts.slide);
  s.seen = ['slide']; s.lastTurn = 4; s.lastAt = 1e6; s.turns = 6;
  assert.equal(chooseGift(s, [gifts.experience], 2e6, () => .9), undefined);
});

test('支援先は2種類から選び、どちらを表示しても再表示間隔を共有する', () => {
  const state = { ...freshState(), turns: 5 };
  for (const [draw, id, source] of [
    [.49, 'support-github', 'https://github.com/sponsors/minorun365'],
    [.5, 'support-x', 'https://x.com/minorun365/subs'],
  ] as const) {
    const rolls = [0, draw];
    const gift = chooseSupport(state, 0, 1e9, () => rolls.shift()!);
    assert.equal(gift?.id, id);
    assert.equal(gift?.source, source);
    assert.equal(chooseSupport({ ...state, seen: [id] }, 0, 1e9, () => 0), undefined);
  }
});
