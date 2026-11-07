import { test } from 'node:test';
import assert from 'node:assert/strict';
import { qiitaGifts, githubGifts, bookGifts } from '../../web/src/topic-gifts';
import { candidatesFor, chooseGift, chooseSupport, SUPPORT_INTERVAL, freshState, gifts, decks, GIFT_LIMIT } from '../../web/src/surprises';
const ids = (q: string, topic = '') => candidatesFor(q, topic, 0).map(g => g.id);
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
  assert.equal(chooseGift(s, [gifts.experience], 1e6, () => .6), undefined);
  assert.equal(chooseGift(s, [gifts.experience], 1e6, () => .59), gifts.experience);
});
test('2問・1分の間隔、同じ付箋の除外、1会話6回までを守る', () => {
  const s = { turns: 2, lastTurn: 1, lastAt: 1e6, seen: ['experience'] };
  assert.equal(chooseGift(s, [gifts.thanks], 2e6, () => 0), undefined);
  s.turns = 3;
  assert.equal(chooseGift(s, [gifts.thanks], 1.059e6, () => 0), undefined);
  assert.equal(chooseGift(s, [gifts.experience], 1.06e6, () => 0), undefined);
  assert.equal(chooseGift(s, [gifts.thanks], 1.06e6, () => 0), gifts.thanks);
  s.seen.push('thanks', 'english', 'deck-hero', 'deck-kiro');
  assert.equal(chooseGift(s, [gifts.slide], 2e6, () => 0), gifts.slide);
  s.seen.push('slide');
  assert.equal(s.seen.length, GIFT_LIMIT);
  assert.equal(chooseGift(s, [gifts.experience, gifts.slide, gifts.thanks], 2e6, () => 0), undefined);
});
test('Expoの深掘りと、去年のエージェントの話を区別する', () => {
  assert.deepEqual(candidatesFor('デモを頼むには', 'Expo', 1), []);
  assert.deepEqual(candidatesFor('デモを頼むには', 'Expo', 2), [gifts.english]);
  assert.deepEqual(ids('去年のキーノート').slice(0, 2), ['experience', 'deck-2025-recap']);
  assert.ok(ids('去年のエージェントについて').includes('slide'));
  assert.deepEqual(candidatesFor('ホテルの場所', '', 0), []);
  assert.deepEqual(ids('このアプリはどうやって作ったの？', 'アプリの作り'), ['slide', 'github-minorun-agent']);
});

test('関係する話題なら、初回は4回答までに出会える', () => {
  const s = freshState();
  const candidates = candidatesFor('去年のエージェント', '', 0);
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

test('話題に合う登壇資料だけを候補にする', () => {
  assert.ok(ids('AgentCoreの新機能は？').includes('deck-agentcore'));
  assert.ok(ids('今年のキーノートで何が発表されそう？').includes('deck-2025-recap'));
  assert.ok(ids('Claude Codeの使い方').includes('deck-claude-code'));
  assert.ok(ids('みのるんさんってどんな人？').includes('deck-hero'));
  assert.ok(ids('初めて参加します。1日の流れは？').includes('deck-5days'));
  assert.ok(ids('質問', 'A2Aとマルチエージェント').includes('deck-a2a'));
  assert.ok(ids('持ち物は何がいる？').includes('qiita-d439a9655cacabe09834'));
  assert.ok(ids('英語のセッションが聞き取れるか不安').includes('github-live-dictation'));
  assert.ok(ids('みのるんさんの著書を教えて').filter(id => id.startsWith('book-')).length === 7);
  assert.ok(ids('MCPって何？').includes('book-mcp'));
  assert.ok(ids('Mastraでエージェントを作りたい').includes('book-mastra'));
  for (const q of ['ホテルの場所', 'Wi-Fiのパスワード', '会場までのシャトルバス', '食事はどこで取れる？', 'チケットの値段', '空港からの移動', '時差ぼけ対策']) assert.deepEqual(ids(q), [], q);
});
test('Qiita・GitHub・著書の付箋は本人のページを指し、全体でIDが重ならない', () => {
  const all = [...decks, ...qiitaGifts, ...githubGifts, ...bookGifts].map(g => g.gift);
  assert.equal(new Set(all.map(g => g.id)).size, all.length);
  assert.ok(qiitaGifts.length >= 20 && githubGifts.length >= 15 && bookGifts.length === 7);
  for (const g of qiitaGifts) assert.match(g.gift.source!, /^https:\/\/qiita\.com\/minorun365\/items\/[0-9a-f]{20}$/);
  for (const g of githubGifts) assert.match(g.gift.source!, /^https:\/\/github\.com\/minorun365\/[\w.-]+$/);
  for (const g of bookGifts) { assert.match(g.gift.source!, /^https:\/\/www\.amazon\.co\.jp\/dp\/\d{9}[\dX]$/); assert.equal(g.gift.preview, false); assert.ok(g.gift.cover); }
  for (const g of all) { assert.match(g.image!, /^https:\/\//); assert.ok(g.title.length <= 20, g.title); }
});
test('登壇資料の付箋は本人のSpeaker Deckの表紙とページだけを指し、IDが重ならない', () => {
  const seen = new Set<string>();
  for (const { gift } of decks) {
    assert.match(gift.image!, /^https:\/\/files\.speakerdeck\.com\/presentations\/[0-9a-f]{32}\/slide_0[_0-9]*\.jpg$/);
    assert.match(gift.source!, /^https:\/\/speakerdeck\.com\/minorun365\/[a-z0-9-]+$/);
    assert.ok(!seen.has(gift.id) && !gifts[gift.id], gift.id); seen.add(gift.id);
    assert.ok(gift.text.length <= 46, gift.text);
  }
});
