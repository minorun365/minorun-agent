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
  assert.equal(maybeWorking({ job: 'a', base: '/s/a/', at: 1000 }, 1000 * 1000 + 20 * 60_000), false);
});
