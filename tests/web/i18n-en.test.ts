// 英語の画面（?lang=en）で読み込んだときの出典ラベル（2026-11-24）。
// 言語は読み込み時に決まるので、location を用意してから読み込む（node --test はファイルごとに別のプロセス）
import test from 'node:test';
import assert from 'node:assert/strict';

(globalThis as any).location = { search: '?lang=en', href: 'https://agent.minoruonda.com/?lang=en' };
const { inline } = await import('../../web/src/markdown');

const labels = (html: string) => [...html.matchAll(/class="src"[^>]*>([^<]*)</g)].map((m) => m[1]);

test('英語の画面では、日本語のラベルを URL から英語に作り直す', () => {
  assert.deepEqual(labels(inline('Badge pickup. [AWS公式](https://aws.amazon.com/events/reinvent/)')), ['AWS official']);
  assert.deepEqual(labels(inline('A blog post. [はてなブログ](https://foo.hatenablog.com/entry/1)')), ['Hatena Blog']);
  assert.deepEqual(labels(inline('A post. [個人ブログ](https://example.com/p)')), ['example']);
  assert.deepEqual(labels(inline('A guide. [公式ガイド](https://reinvent.awsevents.com/faq/)')), ['AWS official']);
});

test('英語のラベルはそのまま出す', () => {
  assert.deepEqual(labels(inline('Hi. [AWS official](https://aws.amazon.com/)')), ['AWS official']);
  assert.deepEqual(labels(inline('Hi. [Qiita](https://qiita.com/x/items/1)')), ['Qiita']);
});
