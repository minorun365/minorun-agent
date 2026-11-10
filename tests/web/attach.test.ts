import assert from 'node:assert/strict';
import test from 'node:test';
import { AttachError, fitSize, upload, UPLOAD_FAILED } from '../../web/src/attach';

const ticket = { ok: true, image: 'a'.repeat(32), url: 'https://bucket.s3.us-west-2.amazonaws.com/', fields: { key: 'up/x/y.jpg', 'Content-Type': 'image/jpeg' } };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

test('長辺を上限に収め、小さい画像は拡大しない', () => {
  assert.deepEqual(fitSize(4032, 3024), { width: 1568, height: 1176 });
  assert.deepEqual(fitSize(3024, 4032), { width: 1176, height: 1568 });
  assert.deepEqual(fitSize(800, 600), { width: 800, height: 600 });
});

test('宛先をもらい、項目のあとにファイルを置いて送り、番号を返す', async () => {
  const posted: unknown[] = [];
  let sentTo = '', names: string[] = [];
  const image = await upload(async (body) => { posted.push(body); return json(ticket); }, new Blob(['x'], { type: 'image/jpeg' }),
    (async (url: string, init: RequestInit) => { sentTo = url; names = Array.from((init.body as FormData).keys()); return new Response(null, { status: 204 }); }) as typeof fetch);
  assert.deepEqual(posted, [{ upload: true }]);
  assert.equal(sentTo, ticket.url);
  assert.deepEqual(names, ['key', 'Content-Type', 'file']);
  assert.equal(image, ticket.image);
});

test('枚数の上限は確認役の文をそのまま伝え、置き場へは送らない', async () => {
  let sent = false;
  await assert.rejects(
    upload(async () => json({ error: 'image_limit', message: '画像を送れるのは1日5枚までです。' }, 429), new Blob(['x']),
      (async () => { sent = true; return new Response(null); }) as typeof fetch),
    (e: unknown) => e instanceof AttachError && e.reason === 'image_limit' && e.message.includes('1日5枚'));
  assert.equal(sent, false);
});

test('置き場が断ったときは送り直しを案内する', async () => {
  await assert.rejects(
    upload(async () => json(ticket), new Blob(['x']), (async () => new Response('denied', { status: 403 })) as typeof fetch),
    (e: unknown) => e instanceof AttachError && e.reason === 'upload' && e.message === UPLOAD_FAILED);
});
