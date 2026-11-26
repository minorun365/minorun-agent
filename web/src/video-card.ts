// 資料（PDF・PPTX）から作る解説動画の入口と、作成中のカード（2026-11-26 みのるん選定：入口は案C、動画ページは案B）。
// 入力欄の左の添付ボタンで資料を選ぶと「◯◯ を3分の動画にする」の札を出し、押すと確認役から宛先をもらって資料を送り、
// 作り始める。カードはスライドと同じ形で、動画作成機能が書く v/<番号>/status.json を数秒おきに見て段階を進める。

import { EN, tr } from './i18n';
import { AttachError } from './attach';
import { REFUSED_EN } from './transport';

export interface VideoStatus { stage: string; title?: string; seconds?: number; message?: string; name?: string; pages?: number; total?: number }

/** 確認役が受け付ける上限（interceptor の DOC_MAX_BYTES と同じ） */
export const DOC_MAX_BYTES = 40 * 1024 * 1024;
const DOC_EXT = /\.(pdf|pptx)$/i;
const DOC_TYPES = new Set(['application/pdf', 'application/vnd.openxmlformats-officedocument.presentationml.presentation']);
/** 選ばれたファイルが資料（PDF・PPTX）か。種類の名前が空の端末があるので拡張子でも見る */
export const isDoc = (f: { name?: string; type?: string }) => DOC_TYPES.has(f.type || '') || DOC_EXT.test(f.name || '');

export const DOC_TOO_BIG = tr('資料が大きすぎます。40MB までのファイルを選んでください。', 'This file is too large. Please choose one up to 40 MB.');
const DOC_FAILED = tr('資料を送れませんでした。電波の良いところで、もう一度送ってください。', "The file couldn't be sent. Please try again where the signal is better.");
const VIDEO_EN: Record<string, string> = {
  video_login: 'Please log in with Google to make a video from a document.',
  video_limit: 'You can make 1 video a day. Please try again tomorrow (after midnight Japan time).',
  video_full: 'Video requests are full for today. Please try again tomorrow.',
  video_bad: "The file couldn't be received. Please choose it again.",
};

/** 札の文。資料の名前は途中で「…」に切れて読みにくいので出さず、1つの文にそろえる（2026-11-26 みのるん指示） */
export const CHIP_LABEL = tr('この資料を3分の動画にする', 'Make a 3-minute video of this document');

type Post = (body: Record<string, unknown>) => Promise<Response>;
type Reply = { ok?: boolean; doc?: string; job?: string; url?: string; fields?: Record<string, string>; error?: string; message?: string };

async function ask(post: Post, body: Record<string, unknown>): Promise<Reply> {
  let res: Response;
  try { res = await post(body); } catch { throw new AttachError('network', DOC_FAILED); }
  const data = await res.json().catch(() => ({})) as Reply;
  if (res.status !== 200 || !data.ok) {
    const why = data.error || 'http';
    throw new AttachError(why, EN ? VIDEO_EN[why] ?? REFUSED_EN[why] ?? DOC_FAILED : data.message || DOC_FAILED);
  }
  return data;
}

/** 資料を置き場へ送り、作り始めて、動画の番号を返す */
export async function startVideo(post: Post, file: Blob, name: string, send: typeof fetch = fetch): Promise<string> {
  if (file.size > DOC_MAX_BYTES) throw new AttachError('too_big', DOC_TOO_BIG);
  const ticket = await ask(post, { docupload: true });
  if (!ticket.doc || !ticket.url || !ticket.fields) throw new AttachError('http', DOC_FAILED);
  const form = new FormData();
  for (const [k, v] of Object.entries(ticket.fields)) form.append(k, v);
  form.append('file', file);  // ファイルは最後に置く（S3 は file より後ろの項目を読まない）
  let sent: Response;
  try { sent = await send(ticket.url, { method: 'POST', body: form }); } catch { throw new AttachError('network', DOC_FAILED); }
  if (!sent.ok) throw new AttachError('upload', DOC_FAILED);
  const started = await ask(post, { video: { doc: ticket.doc, name } });
  if (!started.job || !/^[A-Za-z0-9]{8,32}$/.test(started.job)) throw new AttachError('http', DOC_FAILED);
  return started.job;
}

/** 作った動画の番号（新しい順）。ログインしている人だけ */
export async function listVideos(post: Post): Promise<{ job: string; at: number }[]> {
  try {
    const res = await post({ videos: true });
    const data = await res.json() as { videos?: { job: string; at: number }[] };
    return (data.videos ?? []).filter((v) => /^[A-Za-z0-9]{8,32}$/.test(v.job) && Number.isFinite(v.at));
  } catch { return []; }
}

export const VIDEO_STEPS = EN ? ['Read', 'Script', 'Voice', 'Video'] : ['資料を読む', '台本を書く', '声を録る', '動画にまとめる'];
const WAIT_LINES = EN ? ['Reading the document', 'Writing the script', 'Recording the voice', 'Putting the video together'] : ['資料を読んでいます', '台本を書いています', 'ずんだもんが読み上げを録っています', '動画にまとめています'];
/** 動画作成機能の段階を、画面の4段階のどこまで来たかへ直す（-1 は受付待ち） */
export function videoStep(stage: string): number {
  return ({ queued: -1, reading: 0, writing: 1, voicing: 2, rendering: 3, done: 4 } as Record<string, number>)[stage] ?? -1;
}
export const videoLine = (s: VideoStatus) => { const n = videoStep(s.stage); return n >= 0 && n < 4 ? WAIT_LINES[n] : tr('順番を待っています', 'Waiting in line'); };
/** ふだんは約11分（2026-11-26 の本番）。資料が長いと延びる */
export const VIDEO_ETA = tr('できあがるまで10分ほどかかります', 'Takes about 10 minutes');
/** 作成中として見張る長さ。動画作成機能の上限（30分）より長く */
export const VIDEO_GIVE_UP_MS = 40 * 60_000;
const barWidth = (n: number) => [8, 25, 50, 72, 90][Math.max(-1, Math.min(n, 3)) + 1];
const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
export const OK_VIDEO_BASE = /^\/v\/[A-Za-z0-9]{8,32}\/$/;
const mmss = (sec?: number) => (sec ? `${Math.floor(sec / 60)}:${String(Math.round(sec % 60)).padStart(2, '0')}` : '');

/** 完成の札の文。資料が上限より長くて先頭だけで作ったときは、そのことを一言添える（2026-11-26 みのるん指示。700ページの資料が黙って60ページで切られたため） */
export function doneCaption(s: VideoStatus): string {
  const len = mmss(s.seconds);
  const cut = !!(s.pages && s.total && s.total > s.pages);
  if (EN) return `<b>Done!</b> ${cut ? `Made from the first ${s.pages} of ${s.total} pages. ` : ''}Tap to play${len ? ` (${len})` : ''}`;
  return `<b>できました！</b>${cut ? `${s.total}ページのうち先頭${s.pages}ページから作りました。` : ''}タップで再生できます${len ? `（${len}）` : ''}`;
}

/** 作成中のカード。できたら表紙の絵と再生の案内に変わる（スライドのカードと同じ class を使い、見た目と上の帯への移り方をそろえる） */
export function videoCard(base: string, onChange: () => void, fetcher: typeof fetch = fetch.bind(window)): HTMLElement {
  const fig = document.createElement('figure');
  fig.className = 'slides-card video working';
  fig.dataset.base = base;
  fig.innerHTML = `<p class="slides-title">${tr('解説動画を作っています', 'Making your video')}</p><p class="slides-eta">${VIDEO_ETA}</p>`
    + `<div class="slides-bar"><span style="width:${barWidth(-1)}%"></span></div><figcaption class="slides-line">${tr('いまは', '')}${videoLine({ stage: 'queued' })}</figcaption>`;
  const line = fig.querySelector('.slides-line') as HTMLElement;
  const bar = fig.querySelector('.slides-bar span') as HTMLElement;
  const started = Date.now();
  let stopped = false, timer = 0, reading = false;
  const finish = (s: VideoStatus) => {
    stopped = true;
    fig.className = 'slides-card video ready';
    fig.innerHTML = `<a href="${esc(base)}" target="_blank" rel="noopener"><img src="${esc(base)}poster.jpg" alt="${esc(s.title || tr('動画の表紙', 'Video cover'))}" width="1280" height="720"><span class="play" aria-hidden="true"></span></a>`
      + `<figcaption>${doneCaption(s)}</figcaption>`;
    fig.querySelector('img')!.addEventListener('load', onChange);
    onChange();
  };
  const fail = (message: string) => {
    stopped = true;
    fig.className = 'slides-card video failed';
    fig.innerHTML = `<figcaption>${esc(message)}</figcaption>`;
    onChange();
  };
  const tick = async () => {
    if (stopped || reading) return;
    reading = true; clearTimeout(timer);
    try {
      const r = await fetcher(base + 'status.json', { cache: 'no-store' });
      if (r.ok) {
        const s = await r.json() as VideoStatus;
        if (s.stage === 'done') return finish(s);
        if (s.stage === 'error') return fail((EN ? '' : s.message) || tr('動画を作れませんでした。', "The video couldn't be made."));
        const n = videoStep(s.stage);
        bar.style.width = barWidth(n) + '%';
        line.textContent = tr('いまは', '') + videoLine(s);
      }
    } catch { /* 通信の揺れは次の回に任せる */ } finally { reading = false; }
    if (Date.now() - started > VIDEO_GIVE_UP_MS) return fail(tr('時間がかかりすぎたため止めました。少し時間をおいて、もう一度頼んでください。', 'This took too long, so it was stopped. Please wait a bit and ask again.'));
    timer = window.setTimeout(tick, 4000);
  };
  const onVisible = () => { if (stopped) document.removeEventListener('visibilitychange', onVisible); else if (document.visibilityState === 'visible') void tick(); };
  document.addEventListener('visibilitychange', onVisible);
  timer = window.setTimeout(tick, 1500);
  return fig;
}

const when = (at: number) => {
  const d = new Date(at * 1000);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

/** メニューの「作った動画」。各動画の status.json を読んで、題と表紙を出す */
export async function videosList(items: { job: string; at: number }[], fetcher: typeof fetch = fetch.bind(window)): Promise<HTMLElement> {
  const box = document.createElement('div');
  box.className = 'myslides myvideos';
  const rows = await Promise.all(items.map(async (it) => {
    const base = `/v/${it.job}/`;
    let s: VideoStatus | null = null;
    try { const r = await fetcher(base + 'status.json', { cache: 'no-store' }); if (r.ok) s = await r.json() as VideoStatus; } catch { /* 読めないものは外す */ }
    const d = `<span class="d">${when(it.at)}</span>`;
    if (s?.stage === 'done') return `<li><a href="${esc(base)}" target="_blank" rel="noopener"><img src="${esc(base)}poster.jpg" alt="" width="64" height="36"><span class="t">${esc(s.title || tr('解説動画', 'Video'))}</span></a>${d}</li>`;
    if (s?.stage === 'error') return `<li><span class="t mut">${tr('作れませんでした', 'Failed')}</span>${d}</li>`;
    if (s && Date.now() / 1000 - it.at < VIDEO_GIVE_UP_MS / 1000) return `<li class="working"><span class="ph"><i></i></span><span class="t"><b>${tr('作成中…', 'Making…')}</b><small>${esc(videoLine(s))}</small></span>${d}</li>`;
    return '';
  }));
  const list = rows.filter(Boolean);
  if (list.length) box.innerHTML = `<p class="mh">${tr('作った動画', 'Your videos')}</p><ul>${list.join('')}</ul>`;
  return box;
}
