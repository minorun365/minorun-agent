// 最初に出す質問の例（web/src/starters.ts）の形のテスト
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { STARTERS, TOPIC_GROUPS } from '../../web/src/starters.ts';
import topics from '../../knowledge/topics.json';

test('候補は550問以上あり、重複しない', () => {
  assert.ok(STARTERS.length >= 550, `${STARTERS.length}問しかない`);
  assert.equal(new Set(STARTERS).size, STARTERS.length);
});

test('知識の全話題が初回候補に含まれる', () => {
  for (const topic of Object.keys(topics)) {
    assert.ok(TOPIC_GROUPS[topic]?.length >= 6, `${topic}の候補が不足`);
  }
});

test('すべて短い疑問文で、依頼形や崩れた語尾がない', () => {
  for (const s of STARTERS) {
    assert.match(s, /[？?]$/, s);
    assert.ok(s.length <= 22, `長すぎる: ${s}`);
    assert.doesNotMatch(s, /教えて|ですか|ますか|りる？|る？る？/, s);
  }
});

test('3問は別々の話題から選び、除いた問いは出さない', async () => {
  const { GROUPS, pickStarters } = await import('../../web/src/starters.ts');
  const topicOf = new Map(GROUPS.flatMap((g, i) => g.map((q) => [q, i] as const)));
  for (let i = 0; i < 200; i++) {
    const ex = STARTERS[i % STARTERS.length];
    const got = pickStarters(3, [ex]);
    assert.equal(got.length, 3);
    assert.equal(new Set(got.map((q) => topicOf.get(q))).size, 3, got.join(' / '));
    assert.ok(!got.includes(ex));
  }
});
