import { test } from 'node:test';
import assert from 'node:assert/strict';
import { visibleHeight, LayoutViewport } from '../../web/src/viewport.ts';
import { useSideLayout } from '../../web/src/layout-mode.ts';

test('ページ自体が縮んでも、操作バーを含む表示範囲を越えない', () => {
  assert.equal(visibleHeight(440, 384), 384);
});
test('Safariのように表示範囲だけ縮む場合もキーボードより上へ収まる', () => {
  assert.equal(visibleHeight(844, 384), 384);
});
test('同じ高さ・表示範囲APIなし・不正値に対応する', () => {
  assert.equal(visibleHeight(341, 341), 341);
  assert.equal(visibleHeight(844), 844);
  assert.equal(visibleHeight(844, 0), 844);
});


for (const userAgent of ['Mobile Safari', 'EdgiOS Mobile Safari', 'Twitter for iPhone']) {
  test(`${userAgent}: 入力中から送信・キーボード終了まで上下配置を保つ`, () => {
    const viewport = new LayoutViewport();
    viewport.begin(402, 760);
    assert.equal(useSideLayout(402, viewport.measure(402, 320, true), true, userAgent), false);
    for (const h of [320, 360, 402, 540, 679, 720, 760]) {
      assert.equal(useSideLayout(402, viewport.measure(402, h, false), false, userAgent), false);
    }
    // 復帰後の通常の画面サイズ変更には追従する。
    assert.equal(viewport.measure(402, 634, false), 634);
  });
}
test('Xの元々低い画面と横向きは従来の左右配置へ戻る', () => {
  for (const [width, height, ua] of [[402, 634, 'Twitter for iPhone'], [874, 402, 'Mobile Safari']] as const) {
    const viewport = new LayoutViewport();
    viewport.begin(width, height);
    viewport.measure(width, 260, true);
    assert.equal(useSideLayout(width, viewport.measure(width, 260, false), false, ua), true);
  }
});
test('入力中の端末回転では以前の縦向きの高さを引き継がない', () => {
  const viewport = new LayoutViewport();
  viewport.begin(402, 760);
  viewport.measure(402, 320, true);
  assert.equal(viewport.measure(874, 402, false), 402);
  assert.equal(viewport.measure(402, 760, false), 760);
});
test('閉じる途中の再入力とキャンセルでも縮んだ高さで上書きしない', () => {
  const viewport = new LayoutViewport();
  viewport.begin(402, 760);
  viewport.measure(402, 320, true);
  viewport.begin(402, 400);
  assert.equal(viewport.measure(402, 400, false), 760);
  assert.equal(viewport.measure(402, 759.5, false), 759.5);
});
