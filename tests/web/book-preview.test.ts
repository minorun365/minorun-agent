import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bookGift } from '../../web/src/book-preview';

test('正式名・略称・出版社の出典から対応する本を選ぶ', () => {
  assert.equal(bookGift('『**Claude Code仕事術**』です', [])?.source, 'https://www.amazon.co.jp/dp/4297159392');
  assert.equal(bookGift('**AgentCore 実践入門**を紹介します', [])?.source, 'https://www.amazon.co.jp/dp/4815641234');
  assert.ok(bookGift('448ページです', ['https://www.amazon.co.jp/dp/4815641234/?ref=x']));
  assert.ok(bookGift('初の単著です', ['https://gihyo.jp/book/2026/978-4-297-15939-9']));
});
test('技術の話・別書籍・不正なURLを本の紹介と混同しない', () => {
  assert.equal(bookGift('AgentCoreでClaude Codeを使います。みのるん式を実践します', []), undefined);
  assert.equal(bookGift('別の本です', ['https://www.amazon.co.jp/dp/1234567890']), undefined);
  assert.equal(bookGift('本です', ['https://www.amazon.co.jp.evil.test/dp/4815641234']), undefined);
  assert.equal(bookGift('本です', ['https://www.amazon.co.jp/dp/48156412340']), undefined);
});
test('2冊を紹介したときは最新刊、書影付きのAmazonリンクにする', () => {
  const gift = bookGift('AgentCore本とClaude Code仕事術', []);
  assert.equal(gift?.source, 'https://www.amazon.co.jp/dp/4297159392');
  assert.equal(gift?.preview, true);
  assert.equal(gift?.sourceLabel, 'Amazonで見る');
  assert.ok(gift?.image);
});
