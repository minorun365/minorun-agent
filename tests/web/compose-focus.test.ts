import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';

test('入力欄を開いたイベントが終わる前に、表示してフォーカスする', () => {
  const source = readFileSync(new URL('../../web/src/main.ts', import.meta.url), 'utf8');
  const start = source.indexOf('function openCompose()');
  const end = source.indexOf('\nfunction closeCompose()', start);
  assert.ok(start >= 0 && end > start);
  let visible = false;
  let focused = false;
  const deferred: (() => void)[] = [];
  const context = {
    closeGift() {}, cancelIdle() {}, director: { phase() {} },
    stage: { classList: { add(name: string) { visible = name === 'composing'; } } },
    sendBtn: { classList: { toggle() {} } },
    q: { value: '', focus() { assert.ok(visible); focused = true; } },
    relayout() {},
    setTimeout(fn: () => void) { deferred.push(fn); },
    requestAnimationFrame(fn: () => void) { deferred.push(fn); },
  };
  runInNewContext(source.slice(start, end) + '\nopenCompose();', context);
  assert.ok(focused, 'タイマーや次の描画を待たずにフォーカスする');
});
