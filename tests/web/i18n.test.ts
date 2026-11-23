// 英語の画面の文言（2026-11-24）。既定（日本語）で読み込んだときに英語の文が揃っていることを確かめる
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { REFUSED_EN } from '../../web/src/transport';
import { GROUPS_EN } from '../../web/src/starters';
import { LANG, readLang, tr } from '../../web/src/i18n';

test('既定は日本語（ブラウザの外や覚えた値が無いときも）', () => {
  assert.equal(readLang(), 'ja');
  assert.equal(LANG, 'ja');
  assert.equal(tr('日本語', 'English'), '日本語');
});

test('確認役が断る理由のすべてに英語の文がある', () => {
  const src = readFileSync(new URL('../../web/src/transport.ts', import.meta.url), 'utf8');
  const reasons = [...src.match(/const REFUSALS = new Set\(\[([^\]]*)\]/)![1].matchAll(/'([a-z_]+)'/g)].map((m) => m[1]);
  for (const r of [...reasons, 'session_owner']) assert.ok(REFUSED_EN[r], r);
});

test('英語の質問候補は英語だけで、短い', () => {
  const all = GROUPS_EN.flat();
  assert.ok(GROUPS_EN.length >= 3);
  for (const q of all) {
    assert.doesNotMatch(q, /[぀-ヿ一-鿿]/, q);
    assert.ok(q.length <= 45 && q.endsWith('?'), q);
  }
});
