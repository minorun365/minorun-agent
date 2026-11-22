import assert from 'node:assert/strict';
import test from 'node:test';
import { saveCarryover, takeCarryover, type LastTurn } from '../../web/src/carryover';

class Mem implements Storage {
  m = new Map<string, string>();
  get length() { return this.m.size; }
  clear() { this.m.clear(); }
  getItem(k: string) { return this.m.get(k) ?? null; }
  key(i: number) { return [...this.m.keys()][i] ?? null; }
  removeItem(k: string) { this.m.delete(k); }
  setItem(k: string, v: string) { this.m.set(k, v); }
}
const turn: LastTurn = { q: 'バッジはどこ？', a: '会場の受付です。', next: ['次の質問'], offer: true, via: 'ai', slides: [] };

test('ログイン前の質問と回答を、戻ってきたときに1回だけ取り出せる', () => {
  const s = new Mem();
  saveCarryover(s, turn, { kind: 'offer' }, 1000);
  assert.deepEqual(takeCarryover(s, 2000), { turn, pending: { kind: 'offer' }, at: 1000 });
  assert.equal(takeCarryover(s, 2000), null);
});

test('作成中のスライドも控え、よそのURLは捨てる', () => {
  const s = new Mem();
  const slides = [{ job: 'abcDEF123456', base: '/s/abcDEF123456/' }, { job: 'x', base: 'https://evil.example/' }];
  saveCarryover(s, { ...turn, slides }, null, 1000);
  assert.deepEqual(takeCarryover(s, 2000)?.turn?.slides, [{ job: 'abcDEF123456', base: '/s/abcDEF123456/' }]);
});

test('30分より古い控えは描き直さない', () => {
  const s = new Mem();
  saveCarryover(s, turn, null, 0);
  assert.equal(takeCarryover(s, 31 * 60 * 1000), null);
});

test('URL のスライド依頼は回答が無くても続けられる', () => {
  const s = new Mem();
  saveCarryover(s, null, { kind: 'url', text: 'https://example.com をスライドに' }, 1000);
  assert.deepEqual(takeCarryover(s, 1000)?.pending, { kind: 'url', text: 'https://example.com をスライドに' });
});

test('ログインなしの上限で断られた頼みは、ログイン後にそのまま送り直す', () => {
  const s = new Mem();
  saveCarryover(s, null, { kind: 'ask', text: 'Strands Agents の新機能をスライドにまとめて' }, 1000);
  assert.deepEqual(takeCarryover(s, 1000)?.pending, { kind: 'ask', text: 'Strands Agents の新機能をスライドにまとめて' });
  saveCarryover(s, null, { kind: 'ask', text: '  ' } as never, 1000);
  assert.equal(takeCarryover(s, 1000), null);
});

test('回答の無い「この回答をスライドに」は続けない', () => {
  const s = new Mem();
  saveCarryover(s, null, { kind: 'offer' }, 1000);
  assert.equal(takeCarryover(s, 1000), null);
});

test('壊れた控えは捨てる', () => {
  const s = new Mem();
  s.setItem('minorun-login-carryover', '{oops');
  assert.equal(takeCarryover(s, 0), null);
  assert.equal(s.length, 0);
});

test('何も無いときは控えを消す', () => {
  const s = new Mem();
  saveCarryover(s, turn, null, 1);
  saveCarryover(s, null, null, 2);
  assert.equal(s.length, 0);
});
