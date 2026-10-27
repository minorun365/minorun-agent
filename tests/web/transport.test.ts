import assert from 'node:assert/strict';
import test from 'node:test';
import { ChatTransport, TransportError, type Incident } from '../../web/src/transport';
const busy = () => new Response(JSON.stringify({ message: 'Session operation in progress, please retry' }), { status: 409 });
const ok = () => new Response('data: {"type":"done"}\n\n');

test('先読みの本文が終わるまで質問を送らない', async () => {
  let close!: () => void;
  const calls: unknown[] = [];
  const t = new ChatTransport(async body => {
    calls.push(body);
    return body.warmup ? new Response(new ReadableStream({ start(c) { close = () => c.close(); } })) : ok();
  }, () => {});
  const warm = t.warmup();
  assert.equal(t.warmup(), warm);
  const answer = t.question({ prompt: 'hello' });
  await new Promise(r => setImmediate(r));
  assert.equal(calls.length, 1);
  close(); await answer;
  assert.equal(calls.length, 2);
});
test('起動競合だけを再試行し同じ質問IDを保ち復旧を記録する', async () => {
  const calls: unknown[] = [], events: Incident[] = [], delays: number[] = [];
  const t = new ChatTransport(async body => { calls.push(body); return calls.length < 3 ? busy() : ok(); }, e => events.push(e), async ms => { delays.push(ms); });
  const body = { prompt: 'hello', tid: 'abcdefgh' };
  const res = await t.question(body);
  assert.equal(res.status, 200); assert.deepEqual(calls, [body, body, body]);
  assert.equal(new Set(events.map(e => e.id)).size, 1);
  assert.deepEqual(events.map(e => e.state), ['retrying', 'retrying', 'recovered']);
  assert.ok(delays[0] >= 500 && delays[1] >= 1000);
});
test('競合が続いても4回で終了し日本語で案内する', async () => {
  const events: Incident[] = []; let calls = 0;
  const t = new ChatTransport(async () => { calls++; return busy(); }, e => events.push(e), async () => {});
  await assert.rejects(t.question({}), e => e instanceof TransportError && e.reason === 'session_busy' && !e.message.includes('Session'));
  assert.equal(calls, 4); assert.equal(events.at(-1)?.state, 'failed');
});
test('他の409、5xx、429、通信切断は自動再送しない', async () => {
  for (const status of [409, 500, 429, 0]) {
    let calls = 0;
    const t = new ChatTransport(async () => { calls++; if (!status) throw new Error('network'); return new Response('{}', { status }); }, () => {}, async () => {});
    await assert.rejects(t.question({})); assert.equal(calls, 1);
  }
});
test('先読み失敗でも次の質問は試せる。停止通知を一般エラーに変えない', async () => {
  let calls = 0;
  const t = new ChatTransport(async () => { calls++; return calls === 1 ? new Response('{}', {status: 500}) : new Response(JSON.stringify({error:'banned',message:'利用停止です'}),{status:403}); }, () => {});
  await t.warmup();
  await assert.rejects(t.question({}), e => e instanceof TransportError && e.reason === 'banned' && e.message === '利用停止です');
});
// 先読みの応答が届かず止まったとき（10/27（火）に3件）。サーバー側は返し終えていた
const stalled = (signal?: AbortSignal) => new Promise<Response>((_, reject) => {
  signal?.addEventListener('abort', () => reject(signal.reason));
});
test('先読みが時間切れなら1回だけ送り直し、時間切れとして記録して復旧を残す', async () => {
  const events: Incident[] = []; let calls = 0;
  const tr = new ChatTransport(async (_body, signal) => { calls++; return calls === 1 ? stalled(signal) : ok(); }, e => events.push(e), async () => {}, 20);
  const warm = tr.warmup();
  const res = await warm;
  assert.equal(res?.status, 200); assert.equal(calls, 2);
  assert.deepEqual(events.map(e => [e.kind, e.state, e.attempts]), [['timeout', 'retrying', 1], ['timeout', 'recovered', 2]]);
  assert.equal(new Set(events.map(e => e.id)).size, 1);
});
test('先読みが2回とも止まっても質問は送れ、送り直しは1回まで', async () => {
  const events: Incident[] = []; let warmCalls = 0;
  const tr = new ChatTransport(async (body, signal) => {
    if (body.warmup) { warmCalls++; return stalled(signal); }
    return ok();
  }, e => events.push(e), async () => {}, 20);
  tr.warmup();
  const answer = tr.question({ prompt: 'hello' });
  assert.equal((await answer).status, 200);
  assert.equal(warmCalls, 2);
  assert.deepEqual(events.map(e => e.state), ['retrying', 'failed']);
});
test('質問の通信切断は時間切れ扱いにせず送り直さない', async () => {
  const events: Incident[] = []; let calls = 0;
  const tr = new ChatTransport(async () => { calls++; throw new TypeError('Load failed'); }, e => events.push(e), async () => {});
  await assert.rejects(tr.question({ prompt: 'x' }));
  assert.equal(calls, 1); assert.deepEqual(events.map(e => [e.kind, e.state]), [['network', 'failed']]);
});
