import assert from 'node:assert/strict';
import test from 'node:test';
import { PLACEHOLDER, SLIDES_PLACEHOLDER, SLIDES_STARTER, slidesRequest, wantsSlidesHint, withSlidesStarter } from '../../web/src/slides-hint';

test('URLが入っていて、まだスライドを頼んでいないときだけ札を出す', () => {
  assert.equal(wantsSlidesHint('https://qiita.com/minorun365/items/abc'), true);
  assert.equal(wantsSlidesHint('これ読んで http://example.com/a'), true);
  assert.equal(wantsSlidesHint('https://qiita.com/x をスライドにして'), false);
  assert.equal(wantsSlidesHint('https://example.com/a を資料にして'), false);
  assert.equal(wantsSlidesHint('re:Inventまであと何日？'), false);
  assert.equal(wantsSlidesHint('https://'), false);
});

test('札から送る文は、URLの後ろに頼み方を足す。すでに頼んでいれば足さない', () => {
  assert.equal(slidesRequest(' https://example.com/a '), 'https://example.com/a\nこの記事をスライドにして');
  assert.equal(slidesRequest('https://example.com/a をスライドにして'), 'https://example.com/a をスライドにして');
});

test('質問候補には、ときどき最後の1つと入れ替えて混ぜる', () => {
  const list = ['a', 'b', 'c'];
  assert.deepEqual(withSlidesStarter(list, () => 0.1), ['a', 'b', SLIDES_STARTER]);
  assert.deepEqual(withSlidesStarter(list, () => 0.9), list);
  assert.deepEqual(withSlidesStarter([], () => 0), []);
  assert.deepEqual(withSlidesStarter(['a', SLIDES_STARTER], () => 0), ['a', SLIDES_STARTER]);
});

test('入力欄の薄い案内は、スマホ幅で折り返さない長さにする（折り返すと Safari のキーボード上の欄に隠れる）', () => {
  // 390px の入力欄は全角15字ほどが1行。余裕を見て12字まで
  for (const t of [PLACEHOLDER, SLIDES_PLACEHOLDER]) assert.ok(t.length <= 12, t);
});
