import { test } from 'node:test';
import assert from 'node:assert/strict';
import { STEPS, safeViewer, stepOf } from '../../web/src/slides-card.ts';

test('工房の段階を画面の4段階へ直す', () => {
  assert.equal(STEPS.length, 4);
  assert.equal(stepOf('queued'), -1);
  assert.equal(stepOf('reading'), 0);
  assert.equal(stepOf('writing'), 1);
  assert.equal(stepOf('rendering'), 2);
  assert.equal(stepOf('checking'), 3);
  assert.equal(stepOf('fixing'), 3);
  assert.equal(stepOf('done'), 4);
  assert.equal(stepOf('unknown'), -1);
});

test('ビューアの行き先は status.json の中身ではなく自分の /s/<番号>/ にする', () => {
  const r = safeViewer('/s/abcDEF123456/', { stage: 'done', viewer: 'https://evil.example/', pages: 14 });
  assert.equal(r.viewer, '/s/abcDEF123456/');
  assert.equal(r.thumb, '/s/abcDEF123456/og.jpg?v=14');
});

test('先読みの返事から作ったスライドの一覧を取り出す（よそのURLは通さない）', async () => {
  const { parseSlidesList, maybeWorking } = await import('../../web/src/slides-card.ts');
  const sse = 'data: {"type":"slides_list","items":[{"job":"abcDEF123456","base":"/s/abcDEF123456/","at":100},{"job":"x","base":"https://evil.example/","at":100}]}\n\ndata: {"type":"done"}\n\n';
  assert.deepEqual(parseSlidesList(sse), [{ job: 'abcDEF123456', base: '/s/abcDEF123456/', at: 100 }]);
  assert.deepEqual(parseSlidesList('data: {"type":"done"}\n\n'), []);
  assert.equal(maybeWorking({ job: 'a', base: '/s/a/', at: 1000 }, 1000 * 1000 + 60_000), true);
  // 工房は1本20分で打ち切るので、25分までは作成中として見張る
  assert.equal(maybeWorking({ job: 'a', base: '/s/a/', at: 1000 }, 1000 * 1000 + 20 * 60_000), true);
  assert.equal(maybeWorking({ job: 'a', base: '/s/a/', at: 1000 }, 1000 * 1000 + 26 * 60_000), false);
});

test('アイコンの印：作成中があれば working、無くて見ていない完成品があれば ready', async () => {
  const { badgeOf, rowKind } = await import('../../web/src/slides-card.ts');
  const now = 10_000 * 1000;
  const a = { job: 'aaaaaaaa', base: '/s/aaaaaaaa/', at: 10_000 - 60 };
  const b = { job: 'bbbbbbbb', base: '/s/bbbbbbbb/', at: 10_000 - 120 };
  const old = { job: 'cccccccc', base: '/s/cccccccc/', at: 10_000 - 3600 };
  const st: Record<string, { stage: string } | null | undefined> = { aaaaaaaa: { stage: 'writing' }, bbbbbbbb: { stage: 'done' }, cccccccc: { stage: 'writing' } };
  const get = (j: string) => st[j];
  assert.equal(rowKind(a, st.aaaaaaaa, now), 'working');
  assert.equal(rowKind(old, st.cccccccc, now), 'hidden'); // 15分を過ぎた作成中は止まったものとして出さない
  assert.equal(rowKind(a, undefined, now), 'checking');
  assert.equal(badgeOf([a, b], get, new Set(['bbbbbbbb']), now), 'working');
  assert.equal(badgeOf([b, old], get, new Set(['bbbbbbbb']), now), 'ready');
  assert.equal(badgeOf([b, old], get, new Set(), now), '');
});

test('見張り役：留守の間にできたスライドは「できた」の印になり、見たら消える', async () => {
  const { SlidesTracker } = await import('../../web/src/slides-card.ts');
  const now = Math.floor(Date.now() / 1000);
  const fake = (async (url: string) => new Response(JSON.stringify(url.includes('aaaaaaaa') ? { stage: 'done', title: 'T' } : { stage: 'error' }))) as typeof fetch;
  let changes = 0;
  const t = new SlidesTracker(() => { changes++; }, fake);
  t.set([{ job: 'aaaaaaaa', base: '/s/aaaaaaaa/', at: now - 120 }, { job: 'bbbbbbbb', base: '/s/bbbbbbbb/', at: now - 60 }]);
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(t.get('aaaaaaaa')?.stage, 'done');
  assert.equal(t.badge(), 'ready');
  t.seen();
  assert.equal(t.badge(), '');
  assert.ok(changes > 0);
});

test('調べてまとめるときは最初の段階を「出典を読む」と言い分ける', async () => {
  const { stepLabel, waitLine } = await import('../../web/src/slides-card.ts');
  assert.equal(stepLabel(0, 'topic'), '出典を読む');
  assert.equal(stepLabel(0, 'article'), '記事を読む');
  assert.equal(stepLabel(0), '記事を読む');
  assert.equal(stepLabel(1, 'topic'), '構成を考える');
  assert.equal(waitLine(0, 'topic'), '出典を読んでいます');
});
