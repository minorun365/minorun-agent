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

test('3問のうち1問は必ず技術の話題から選ぶ', async () => {
  const { pickStarters, TECH_TOPICS } = await import('../../web/src/starters.ts');
  const tech = new Set(TECH_TOPICS.flatMap((t) => TOPIC_GROUPS[t] ?? []));
  for (let i = 0; i < 300; i++) {
    const got = pickStarters(3);
    assert.ok(got.some((q) => tech.has(q) || q.includes('更新') || q.includes('AgentCore') || q.includes('Bedrock')), got.join(' / '));
  }
});

test('推したい催しの問いは開催日まで約半分の回に1問入り、過ぎたら入れる仕組みが止まる', async () => {
  const { pickStarters, PUSH_QUESTIONS, PUSH_UNTIL } = await import('../../web/src/starters.ts');
  for (const q of PUSH_QUESTIONS) assert.ok(STARTERS.includes(q), `用意した質問に無い: ${q}`);
  const push = new Set(PUSH_QUESTIONS);
  const before = PUSH_UNTIL - 1;
  let hit = 0;
  for (let i = 0; i < 1000; i++) {
    const got = pickStarters(3, [], before);
    assert.equal(got.length, 3);
    if (got.some((q) => push.has(q))) hit++;
  }
  assert.ok(hit > 430 && hit < 650, `開催前に入った回: ${hit}/1000`);
  // 期間後も話題の抽選で偶然出ることはあるが、ずっと少ない
  let after = 0;
  for (let i = 0; i < 1000; i++) if (pickStarters(3, [], PUSH_UNTIL + 1).some((q) => push.has(q))) after++;
  assert.ok(after < 150, `開催後に入った回: ${after}/1000`);
});
