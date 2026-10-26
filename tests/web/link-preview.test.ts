import { test } from 'node:test';
import assert from 'node:assert/strict';
import { previewGift } from '../../web/src/link-preview';

const card = { url: 'https://github.com/minorun365/minorun-agent', image: 'https://opengraph.githubassets.com/hash/minorun365/minorun-agent', title: 'みのるんエージェント', label: 'GitHubを開く' };
test('最終回答の出典に対応する画像を貼り紙へ変える', () => {
  const result = previewGift(card, [card.url + '?utm_source=x#readme']);
  assert.equal(result?.source, card.url);
  assert.equal(result?.image, card.image);
  assert.equal(result?.preview, true);
  assert.equal(result?.sourceLabel, 'GitHubを開く');
});
test('追加調査で消えた出典・他人のページ・不正な画像は出さない', () => {
  assert.equal(previewGift(card, []), undefined);
  for (const url of ['https://github.com/other/repo', 'https://github.com.evil.test/minorun365/repo', 'javascript:alert(1)']) {
    assert.equal(previewGift({ ...card, url }, [url]), undefined);
  }
  assert.equal(previewGift({ ...card, image: 'javascript:alert(1)' }, [card.url]), undefined);
});
