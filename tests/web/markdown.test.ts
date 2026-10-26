// 本文の整形（web/src/markdown.ts）のテスト。npm test で走る
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { md } from '../../web/src/markdown.ts';

const srcLabels = (html: string) => [...html.matchAll(/<a class="src"[^>]*>([^<]+)<\/a>/g)].map((m) => m[1]);

test('出典は該当する文の直後に小さなラベルとして付く', () => {
  const html = md('基調講演は Venetian です。[AWS公式](https://aws.amazon.com/events/reinvent/agenda/)');
  assert.match(html, /Venetian です。<a class="src" href="https:\/\/aws\.amazon\.com\/events\/reinvent\/agenda\/" target="_blank" rel="noopener">AWS公式<\/a><\/p>/);
});

test('末尾の「出典」見出しと出典だけの箇条書きは、直前の文へ寄せて節を作らない', () => {
  const html = md('バッジは Venetian で受け取れます。\n\n出典\n- [AWS公式](https://aws.amazon.com/a)\n- https://qiita.com/x/items/1');
  assert.doesNotMatch(html, /出典/);
  assert.deepEqual(srcLabels(html), ['AWS公式', 'Qiita']);
  assert.equal((html.match(/<li>/g) || []).length, 0);
});

test('URL だけが届いたときはドメインから短いラベルを作る', () => {
  const html = md('詳しくはこちら https://dev.classmethod.jp/articles/x/ と https://x.com/minorun365/status/1');
  assert.deepEqual(srcLabels(html), ['DevelopersIO', 'X']);
});

test('長すぎるラベルはドメインのラベルに置き換える', () => {
  const html = md('文。[とても長いラベルで吹き出しを圧迫するもの](https://zenn.dev/a/b)');
  assert.deepEqual(srcLabels(html), ['Zenn']);
});

test('見出しの # は外して本文として出す', () => {
  const html = md('## 2026年について\n本文です。');
  assert.doesNotMatch(html, /#/);
  assert.match(html, /2026年について/);
});

test('HTML は無害化される（タグを注入できない）', () => {
  const html = md('<img src=x onerror=alert(1)> と <script>alert(1)</script>');
  assert.doesNotMatch(html, /<img|<script/);
});

test('閉じていない太字は閉じて出す', () => {
  assert.match(md('**有料'), /<b>有料<\/b>/);
});

test('箇条書きと段落を分けて出す', () => {
  const html = md('結論です。\n\n- A\n- B');
  assert.match(html, /<p>結論です。<\/p><ul><li>A<\/li><li>B<\/li><\/ul>/);
});

test('太字に囲まれたURLを正しい出典として表示する', () => {
  const html = md('ホームページは **https://minoruonda.com/** です。');
  assert.match(html, /href="https:\/\/minoruonda.com\/"/);
  assert.doesNotMatch(html, /\*\*/);
});
