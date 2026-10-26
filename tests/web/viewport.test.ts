import { test } from 'node:test';
import assert from 'node:assert/strict';
import { visibleHeight } from '../../web/src/viewport.ts';

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
