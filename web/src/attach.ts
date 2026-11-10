// 質問に添える画像。画面の中で縮めて JPEG に描き直し（撮影場所などの付帯情報はここで落ちる）、
// 確認役が出した期限つきの宛先へ直接送る。質問の本文には、置き場の番号だけを添える。

export const MAX_EDGE = 1568;
const QUALITY = [0.85, 0.7, 0.55];

export class AttachError extends Error {
  constructor(public reason: string, message: string) { super(message); }
}
export const UNREADABLE = 'この画像は読み込めませんでした。別の画像で試してください。';
export const UPLOAD_FAILED = '画像を送れませんでした。電波の良いところで、もう一度送ってください。';

/** 長辺を max に収めた大きさ（拡大はしない）。 */
export function fitSize(width: number, height: number, max = MAX_EDGE) {
  const scale = Math.min(1, max / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

/** 画像を縮めて JPEG にする。上限に収まるまで画質を下げる。 */
export async function shrink(file: Blob, maxBytes = 1_500_000): Promise<Blob> {
  let bitmap: ImageBitmap;
  try { bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' }); }
  catch { throw new AttachError('unreadable', UNREADABLE); }
  const { width, height } = fitSize(bitmap.width, bitmap.height);
  const canvas = document.createElement('canvas');
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext('2d')!;
  // 透過のある画像は白地に重ねる（JPEG は透過を持てない）
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, width, height);
  ctx.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();
  for (const quality of QUALITY) {
    const blob = await new Promise<Blob | null>((done) => canvas.toBlob(done, 'image/jpeg', quality));
    if (blob && blob.size <= maxBytes) return blob;
  }
  throw new AttachError('unreadable', UNREADABLE);
}

type Post = (body: Record<string, unknown>) => Promise<Response>;
type Ticket = { ok?: boolean; image?: string; url?: string; fields?: Record<string, string>; error?: string; message?: string };

/** 宛先をもらって置き場へ送り、質問に添える番号を返す。 */
export async function upload(post: Post, blob: Blob, send: typeof fetch = fetch): Promise<string> {
  let ticket: Ticket, status: number;
  try {
    const res = await post({ upload: true });
    status = res.status;
    ticket = await res.json().catch(() => ({}));
  } catch { throw new AttachError('network', UPLOAD_FAILED); }
  if (status !== 200 || !ticket.image || !ticket.url || !ticket.fields) {
    throw new AttachError(ticket.error || 'http', ticket.error && ticket.message ? ticket.message : UPLOAD_FAILED);
  }
  const form = new FormData();
  for (const [name, value] of Object.entries(ticket.fields)) form.append(name, value);
  form.append('file', blob);  // ファイルは最後に置く（S3 は file より後ろの項目を読まない）
  let sent: Response;
  try { sent = await send(ticket.url, { method: 'POST', body: form }); }
  catch { throw new AttachError('network', UPLOAD_FAILED); }
  if (!sent.ok) throw new AttachError('upload', UPLOAD_FAILED);
  return ticket.image;
}
