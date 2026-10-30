/// <reference types="vite/client" />
// みのるんエージェントの画面（ステージ型）。配置の計算 layout() は試作の stage.js から移植した。
// 返事は /api/chat の SSE を読む。出来事の形は agent/app.py の冒頭に書いてある。

import './surprises.css';
import { ChatTransport, TransportError as Refused, type Incident } from './transport';
import { characterGift, previewGift, type LinkPreview } from './link-preview';
import { bookGift } from './book-preview';
import { POSES, PoseDirector, PosePicker, type Pose } from './poses';
import { visibleHeight, LayoutViewport } from './viewport';
import { useSideLayout } from './layout-mode';
import { candidatesFor, chooseGift, chooseSupport, isSupport, freshState, gifts, type GiftState, type Surprise } from './surprises';
import { esc, md } from './markdown';
import { pickStarters } from './starters';
import { bindSubmitButton } from './submit-button';
import { accessToken, handleRedirect, login, logout, profile } from './auth';

interface MapShown { src: string; alt: string; caption: string; width: number; height: number; source_url: string }
type AgentEvent =
  | { type: 'topic'; heading: string; changed: boolean }
  | { type: 'tool'; name: string; label: string }
  | { type: 'text'; text: string }
  | { type: 'follow_up'; text: string }
  | { type: 'replace'; text: string }
  | ({ type: 'preview' } & LinkPreview)
  | ({ type: 'map' } & MapShown)
  | { type: 'catalog_update'; at: string; lines: string[] }
  | { type: 'suggest'; items: string[] }
  | { type: 'error'; message: string }
  | { type: 'done' };

// 検証用（Simulator はタップできないため）：?ask=質問・?compose=1・?limit=1（上限の場面）・?theme=dark（ダークの案）
const params = new URLSearchParams(location.search);
if (params.get('theme') === 'dark') {
  document.documentElement.dataset.theme = 'dark';
  document.querySelector('meta[name=theme-color]')?.setAttribute('content', '#000000');
}
const $ = <T extends HTMLElement = HTMLElement>(s: string) => document.querySelector(s) as T;
const stage = $('#stage'), bubble = $('#bubble'), answer = $('#answer'), thought = $('#thought'), greet = $('#greet');
const chara = $('#chara'), doing = $('#doing'), suggest = $('#suggest'), sendBtn = $('#send');
const q = $<HTMLTextAreaElement>('#q');
const header = $('.top'), dock = $('.dock'), write = $('.write'), notice = $('.notice');
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const has = (c: string) => stage.classList.contains(c);
// 画像（750×1000）の中で、絵が描かれている上端と下端の位置
const IMG_TOP = 0.03, IMG_BOT = 0.97;

// ---- 匿名ID（端末に残す乱数）と会話ID（タブを閉じるまで同じ） ----
function stored(storage: Storage, key: string): string {
  try {
    let v = storage.getItem(key);
    if (!v) { v = crypto.randomUUID(); storage.setItem(key, v); }
    return v;
  } catch { return crypto.randomUUID(); }
}
const anonId = stored(localStorage, 'minorun-anon-id');
// テスト用タブは本人の通常利用と会話を分け、監視画面で動作テストへ分類する。
const sessionId = params.get('test') === '1'
  ? 'e2e-' + stored(sessionStorage, 'minorun-test-session-id')
  : stored(sessionStorage, 'minorun-session-id');

// ---- iOS：キーボードが出たら見えている高さに合わせる ----
const layoutViewport = new LayoutViewport();
let restingViewportHeight = window.visualViewport?.height ?? innerHeight;
let restingViewportWidth = innerWidth;
function fitViewport() {
  const vv = window.visualViewport;
  const focused = document.activeElement === q;
  // キーボードの操作バーを含む遮蔽はvisualViewportで除く。
  // ページ高のほうが大きくても、表示範囲より下へ入力欄を置かない。
  const h = visibleHeight(innerHeight, vv?.height);
  document.documentElement.style.setProperty('--vvh', h + 'px');
  // 入力欄の祖先をtransformしない。文書座標で表示範囲の先頭へ配置する。
  stage.style.top = (vv?.pageTop ?? scrollY) + 'px';
  if (innerWidth !== restingViewportWidth) { restingViewportHeight = h; restingViewportWidth = innerWidth; }
  if (!focused) { restingViewportHeight = Math.max(restingViewportHeight, h); }
  stage.classList.toggle('kbd', document.activeElement === q && restingViewportHeight - h > 100);
  relayout();
}
if (window.visualViewport) {
  visualViewport!.addEventListener('resize', fitViewport);
  visualViewport!.addEventListener('scroll', fitViewport);
}
addEventListener('resize', fitViewport);
addEventListener('scroll', fitViewport, { passive: true });

// ---- 時計（ラスベガス・日本） ----
const fmt = (tz: string) => new Intl.DateTimeFormat('ja-JP', { timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date());
const tick = () => { $('#clocks').innerHTML = `<span><i>ラスベガス</i>${fmt('America/Los_Angeles')}</span><span><i>日本</i>${fmt('Asia/Tokyo')}</span>`; };
tick(); setInterval(tick, 15000);

// ---- 配置：上端の見出しから下の操作部までの「空き」を測り、その中にキャラ・雲・吹き出しを収める ----
let raf = 0;
const relayout = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; layout(); }); };
// X のアプリ内ブラウザは、読み込んだ後で上の余白（状態バーと X の見出しの分）を足す。サイズの変化の通知が来ないので、見出しの高さが変わったら測り直す
new ResizeObserver(relayout).observe(header, { box: 'border-box' });
function layout() {
  const H = stage.clientHeight, Wd = stage.clientWidth;
  const headB = header.offsetTop + header.offsetHeight + 6;
  const comp = has('composing'), ans = has('answering') && !comp;
  const pad = parseFloat(getComputedStyle(dock).paddingBottom);
  const inner = comp ? write.offsetHeight + notice.offsetHeight + 10 : has('limited') ? 0 : suggest.offsetHeight;
  const dockTop = H - pad - inner - (comp ? 10 : 16);
  const avail = dockTop - headB;
  // 通常ブラウザの縦向きは上下、Xのアプリ内と横向きの低い画面は左右に配置する。
  // PCの中央寄せで小さくした会話領域ではなく、実際の表示領域で低い画面かを判定する。
  const layoutHeight = layoutViewport.measure(innerWidth, visibleHeight(innerHeight, window.visualViewport?.height), comp);
  const side = useSideLayout(innerWidth, layoutHeight, comp, navigator.userAgent);
  stage.classList.toggle('side', side);
  let ch: number, ct: number, cx = Wd / 2;
  if (side && !ans) {
    // 考え中は、頭の上に雲の高さを先に取ってから残りでキャラの大きさを決める（雲が見出しや顔に重ならないように）
    const cloud = has('busy') ? thought.offsetHeight + 24 : 0;
    ch = clamp(Math.min(avail * 0.82, avail - cloud), 72, 330);
    ct = headB + cloud + (avail - cloud - ch) * 0.85;
    cx = Wd - ch * 0.75 / 2 - 4;
  } else if (side) {
    // 回答中（低い画面）：みのるんは右下に小さく、吹き出しはその左に縦長。しっぽは右へ向けて頭を指す
    const contentH = answer.scrollHeight;
    ch = clamp(avail * 0.4, 110, 170);
    const cw = ch * 0.75;
    cx = Wd - cw / 2 - 2;
    ct = dockTop - ch - 2;
    // 足元の少し上まで使い、低い画面でも回答を読める高さを確保する。
    const bubbleBottom = Math.min(ct + ch * 0.85, headB + contentH + 8 + ch * 0.85);
    bubble.style.setProperty('--bb', H - bubbleBottom + 'px');
    bubble.style.setProperty('--bubble-max', bubbleBottom - headB - 4 + 'px');
    // 吹き出しの右端は、みのるんの絵の枠の左端より手前で止める（顔にかからないように）
    bubble.style.setProperty('--bw', Math.max(190, cx - cw / 2 - 14 - 8) + 'px');
    bubble.style.setProperty('--tail-b', Math.max(14, bubbleBottom - (ct + ch * 0.18)) + 'px');
  } else if (!ans) {
    // 待機・考え中：上に雲、下に1行の分を空けて、残りいっぱいにキャラ
    const above = comp ? 4 : Math.max(92, thought.offsetHeight + 14), below = comp ? 0 : 30;
    ch = clamp(Math.min(avail - above - below, Wd * 1.15), 72, 470);
    ct = headB + above + Math.max(0, avail - above - below - ch) * 0.5;
  } else {
    // 回答中：下にキャラ、その頭から吹き出し。文字が増えるとキャラが縮み、最小まで縮んだら吹き出しの中がスクロールする
    const TAIL = 16, GAP = 4, contentH = answer.scrollHeight;
    const maxC = clamp(avail * 0.4, 130, 300), minC = clamp(avail * 0.22, 112, 150);
    ch = clamp((avail - GAP - TAIL - contentH) / (1 - IMG_TOP), minC, maxC);
    const block = Math.min(avail - GAP, contentH + TAIL + ch * (1 - IMG_TOP));
    ct = headB + GAP + (avail - GAP - block) * 0.45 + block - ch;
    if (activeGift) {
      gift.hidden = false;
      // 付箋の高さを先に確保し、長い回答は吹き出し内でスクロールする。
      ch = Math.min(ch, Wd * .62);
      ct = Math.max(headB + 100, Math.min(ct, dockTop - gift.offsetHeight - 24));
    }
    const bb = ct + ch * IMG_TOP - TAIL;
    bubble.style.setProperty('--bb', H - bb + 'px');
    bubble.style.setProperty('--bubble-max', bb - headB - GAP + 'px');
  }
  placeGift({ ct, ch, dockTop, side, ans, width: Wd });
  if (activeGift && !gift.hidden && !side) cx = Wd / 2 - Math.min(Wd / 6, 110);
  bubble.style.setProperty('--gift-tail', activeGift && !gift.hidden ? `calc(50% - ${Wd / 2 - cx}px)` : '50%');
  stage.style.setProperty('--ct', ct + 'px'); stage.style.setProperty('--ch', ch + 'px'); stage.style.setProperty('--cx', cx + 'px');
  const head = ct + ch * IMG_TOP;
  thought.style.top = Math.max(head - thought.offsetHeight - 12, headB + 8) + 'px';  // 雲のふちの丸は箱の上へはみ出すので、見出しから少し離す
  // 雲は頭の上が基本。右に寄せたときは、画面からはみ出さない位置まで左へずらす（雲の幅は保つ）
  const tw = thought.offsetWidth, tx = clamp(cx, tw / 2 + 26, Wd - tw / 2 - 26);  // 雲のふちの丸は箱の外へはみ出すので、余白を広めに取る
  stage.style.setProperty('--tx', tx + 'px');
  // 雲をずらしたときは、しっぽの小さな丸を頭のほうへ並べる（中央揃えの列の中なので、左の余白の半分だけ動く）
  const off = Math.min(cx - tx, tw / 2 - 10);
  thought.style.setProperty('--d1', off > 12 ? off * 0.9 + 'px' : '');
  thought.style.setProperty('--d2', off > 12 ? off * 1.45 + 'px' : '');
  // 足元から少し離して置く（キャラと文字がくっついて見えないように）
  doing.style.top = ct + ch * IMG_BOT + 16 + 'px';
  // あいさつは頭の左上に置き、しっぽの先が頭のてっぺんの少し左を指すようにする（画面の幅に関係なく頭に付いて見える）
  if (side) {
    // 右下に寄せたとき：あいさつは頭の左にまるごと置き、しっぽを右へ向けて頭を指す（頭に重ねない）。
    // 絵の中で頭が占めるのは、横は絵の幅の左右24%より内側、縦は上から3〜35%
    const headLeft = cx - ch * 0.75 * 0.24, headMid = ct + ch * 0.19;
    greet.style.maxWidth = Math.max(150, Math.min(235, headLeft - 14 - 12)) + 'px';
    const gw = greet.offsetWidth, gh = greet.offsetHeight;
    greet.style.left = Math.max(12, headLeft - 14 - gw) + 'px';
    greet.style.top = clamp(headMid - gh / 2, headB + 6, H - gh - 12) + 'px';
  } else {
    greet.style.maxWidth = '';
    const gw = greet.offsetWidth, gh = greet.offsetHeight, tipX = cx - ch * 0.1;
    greet.style.left = clamp(tipX - gw + 29, 12, Wd - gw - 12) + 'px';
    greet.style.top = Math.max(headB + 4, head - gh - 2) + 'px';
  }
  requestAnimationFrame(masks);
}

// 吹き出しに入りきらないとき：切れている側の端をぼかす（中はスクロールできる）
function masks() {
  const ov = answer.scrollHeight > answer.clientHeight + 2;
  bubble.classList.toggle('ovt', ov && answer.scrollTop > 4);
  bubble.classList.toggle('ovb', ov && answer.scrollTop + answer.clientHeight < answer.scrollHeight - 4);
}
answer.addEventListener('scroll', masks, { passive: true });

// 雲：文字の箱の上下の縁に円を並べ、ひとかたまりの影を付ける（2行のときは間を角丸の面で埋める）
function drawCloud() {
  const box = document.querySelector<HTMLElement>('.thought .cloud')!;
  box.querySelector('svg')?.remove();
  const w = box.offsetWidth, h = box.offsetHeight, k = Math.min(h, 54), r = k * 0.42, n = Math.max(3, Math.round((w - k) / (r * 1.25)) + 1);
  let c = `<rect x="${k * 0.35}" y="${h * 0.16}" width="${w - k * 0.7}" height="${h * 0.68}" rx="${k * 0.3}"/>`;
  for (let i = 0; i < n; i++) {
    const x = k * 0.42 + (w - k * 0.84) * (i / (n - 1));
    c += `<circle cx="${x}" cy="${k * 0.34}" r="${k * (i % 2 ? 0.44 : 0.56)}"/><circle cx="${x + r * 0.55}" cy="${h - k * 0.3}" r="${k * (i % 2 ? 0.42 : 0.34)}"/>`;
  }
  if (h > k * 1.2) c += `<rect x="0" y="${k * 0.3}" width="${w}" height="${h - k * 0.6}" rx="${k * 0.45}"/>`;
  box.insertAdjacentHTML('afterbegin', `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><defs><filter id="cs" x="-20%" y="-40%" width="140%" height="180%"><feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#14161c" flood-opacity=".16"/></filter></defs><g style="fill:var(--cloud)" filter="url(#cs)">${c}</g></svg>`);
}

// 追加素材はDOMに用意し、読み込み失敗時は表示中の絵を維持する。
const missing = new Set<string>();
const float = chara.querySelector<HTMLElement>('.float')!;
for (const name of POSES) {
  if (chara.querySelector(`[data-pose="${name}"]`)) continue;
  const img = new Image(); img.alt = ''; img.dataset.pose = name;
  img.src = `/chara/${name}.webp`; float.appendChild(img);
}
let poseRequest = 0, popTimer = 0, restTimer = 0, settleTimer = 0;
function pose(name: Pose, state = 'idle') {
  const request = ++poseRequest;
  const img = chara.querySelector<HTMLImageElement>(`[data-pose="${name}"]`)!;
  const apply = () => {
    if (request !== poseRequest) return;
    chara.querySelectorAll<HTMLImageElement>('img').forEach(i => i.classList.toggle('on', i === img));
    stage.dataset.pose = name; stage.dataset.poseState = state;
    stage.classList.toggle('thinking', state === 'thinking' || state === 'following' || state.startsWith('tool:'));
    clearTimeout(popTimer); stage.classList.remove('idea');
    if (['idea', 'thumb', 'clap'].includes(name)) {
      void float.offsetWidth; stage.classList.add('idea');
      popTimer = window.setTimeout(() => stage.classList.remove('idea'), 500);
    }
  };
  if (img.complete && img.naturalWidth) apply();
  else img.decode().then(apply).catch(() => missing.add(name));
}
chara.querySelectorAll<HTMLImageElement>('img').forEach(i => {
  i.addEventListener('error', () => missing.add(i.dataset.pose!));
});
const director = new PoseDirector(pose, new PosePicker(), missing);
function cancelIdle() { clearTimeout(restTimer); clearTimeout(settleTimer); }
function idle() {
  cancelIdle(); director.phase('idle');
  restTimer = window.setTimeout(() => {
    if (!busy && !activeGift && !has('composing') && !has('limited')) director.phase('resting');
  }, 30_000);
}

// ---- 地図：吹き出しの中に出し、タップで全画面。指で広げて拡大・ずらして移動 ----
const zoom = $('#zoom'), zoomImg = $<HTMLImageElement>('#zoomImg');
let zs = 1, zx = 0, zy = 0;
const pointers = new Map<number, { x: number; y: number }>();
let pinch: { d: number; s: number; cx: number; cy: number; x: number; y: number } | null = null;
const applyZoom = () => { zoomImg.style.transform = `translate(${zx}px,${zy}px) scale(${zs})`; };
function openZoom(m: MapShown) {
  zoomImg.src = m.src; zoomImg.alt = m.alt;
  zoomImg.style.width = m.width + 'px'; zoomImg.style.height = m.height + 'px';
  zs = Math.min(innerWidth / m.width, innerHeight / m.height);
  zx = (innerWidth - m.width * zs) / 2; zy = (innerHeight - m.height * zs) / 2;
  applyZoom(); zoom.classList.add('on');
}
$('#zoomClose').addEventListener('click', () => zoom.classList.remove('on'));
zoom.addEventListener('pointerdown', (e) => { if (e.target === zoomImg || e.target === zoom) { zoom.setPointerCapture(e.pointerId); pointers.set(e.pointerId, { x: e.clientX, y: e.clientY }); } });
zoom.addEventListener('pointermove', (e) => {
  const p = pointers.get(e.pointerId); if (!p) return;
  if (pointers.size === 2) {
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const [a, b] = [...pointers.values()];
    const d = Math.hypot(a.x - b.x, a.y - b.y), cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
    if (!pinch) pinch = { d, s: zs, cx, cy, x: zx, y: zy };
    const ns = clamp(pinch.s * d / pinch.d, 0.2, 6);
    zx = cx - (pinch.cx - pinch.x) * ns / pinch.s; zy = cy - (pinch.cy - pinch.y) * ns / pinch.s; zs = ns;
  } else {
    zx += e.clientX - p.x; zy += e.clientY - p.y;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  }
  applyZoom();
});
const endPointer = (e: PointerEvent) => { pointers.delete(e.pointerId); if (pointers.size < 2) pinch = null; };
zoom.addEventListener('pointerup', endPointer); zoom.addEventListener('pointercancel', endPointer);
zoom.addEventListener('wheel', (e) => {
  e.preventDefault();
  const ns = clamp(zs * (e.deltaY < 0 ? 1.1 : 0.9), 0.2, 6);
  zx = e.clientX - (e.clientX - zx) * ns / zs; zy = e.clientY - (e.clientY - zy) * ns / zs; zs = ns; applyZoom();
}, { passive: false });

// ---- ときどき出る付箋 ----
const gift = document.createElement('aside'); gift.className = 'surprise'; gift.hidden = true;
gift.setAttribute('aria-label', 'みのるんのおまけ');
stage.append(gift);
let activeGift: Surprise | undefined, giftState: GiftState = freshState();
let currentTopic = '', expoDepth = 0, currentGiftCandidates: Surprise[] = [], giftEligible = false;
const giftKey = 'minorun-gifts-' + sessionId;
try {
  const saved = JSON.parse(sessionStorage.getItem(giftKey) ?? 'null');
  if (saved && [saved.turns, saved.lastTurn, saved.lastAt].every(Number.isFinite) && Array.isArray(saved.seen) && saved.seen.every((s: unknown) => typeof s === 'string')) giftState = saved;
} catch { /* 保存できない端末では、この画面の間だけ数える */ }
const saveGifts = () => { try { sessionStorage.setItem(giftKey, JSON.stringify(giftState)); } catch { /* 同上 */ } };
const supportKey = 'minorun-support-last-shown';
function lastSupportShown(): number | null {
  try { return Number(localStorage.getItem(supportKey) ?? '0'); } catch { return null; }
}
function closeGift() {
  activeGift = undefined; gift.hidden = true;
  if (!busy) idle(); relayout();
}
function placeGift(p: { ct: number; ch: number; dockTop: number; side: boolean; ans: boolean; width: number }) {
  if (!activeGift) return;
  const center = p.width / 2 - Math.min(p.width / 6, 110);
  const left = Math.min(p.width / 2 + Math.min(p.width * .07, 45), p.width - 162);
  gift.hidden = false;
  if (!p.ans || has('busy') || (p.side && !activeGift.image)) {
    gift.hidden = true; return;
  }
  const minTop = header.offsetTop + header.offsetHeight + 18;
  // 頭の上へ寄せる。前の配置の高さ制限を外してから、画像の実際の高さを測る。
  gift.style.maxHeight = '';
  const top = p.side ? Math.max(minTop, p.ct - 12 - gift.offsetHeight) : p.ct + 12;
  gift.style.left = (p.side ? p.width - gift.offsetWidth - 18 : left) + 'px'; gift.style.top = top + 'px';
  gift.style.maxHeight = Math.max(60, (p.side ? p.ct - 12 : p.dockTop - 8) - top) + 'px';
  if (p.side) {
    const characterLimit = p.width - p.ch * .75 - 24;
    const giftLimit = p.width - gift.offsetWidth - 40;
    bubble.style.setProperty('--bw', Math.max(190, Math.min(characterLimit, giftLimit)) + 'px');
  }
}
function showGift(candidates: Surprise[], linked?: Surprise) {
  if (!giftEligible || activeGift || busy || has('composing')) return;
  const preview = import.meta.env.DEV ? params.get('gift') : null;
  const now = Date.now();
  const side = stage.classList.contains('side');
  const eligible = candidates.filter(candidate => !side || Boolean(candidate.image));
  const support = !side ? chooseSupport(giftState, lastSupportShown(), now) : undefined;
  const chosen = linked ?? (preview && gifts[preview] ? gifts[preview] : support ?? chooseGift(giftState, eligible, now, Math.random, .4));
  if (!chosen || (side && !chosen.image)) return;
  if (isSupport(chosen.id) && !preview) {
    try { localStorage.setItem(supportKey, String(now)); } catch { return; }
  }
  gift.style.maxHeight = '';
  gift.className = 'surprise' + (chosen.image ? ' photo' : '') + (chosen.preview ? ' link-preview' : '');
  const content = (chosen.image ? `<span class="picture"><img src="${esc(chosen.image)}" alt="" referrerpolicy="no-referrer"></span>` : '') +
    `<h3>${esc(chosen.title)}</h3>` + (chosen.english ? `<strong lang="en">${esc(chosen.english)}</strong>` : '') +
    (chosen.image ? '' : `<p>${esc(chosen.text)}</p>`);
  gift.classList.toggle('linked', Boolean(chosen.source));
  gift.innerHTML = `<button class="dismiss" type="button" aria-label="おまけを閉じる">×</button>` +
    (chosen.source ? `<a class="gift-link" href="${esc(chosen.source)}" target="_blank" rel="noopener noreferrer" aria-label="${esc(chosen.title + '：' + chosen.text)}">${content}</a>` : content);
  gift.querySelector('.dismiss')!.addEventListener('click', closeGift);
  gift.querySelector('img')?.addEventListener('error', closeGift, { once: true });
  gift.querySelector('img')?.addEventListener('load', relayout, { once: true });
  activeGift = chosen; layout();
  if (gift.hidden) { activeGift = undefined; return; }
  if (!chosen.preview) { giftState.seen.push(chosen.id); giftState.lastTurn = giftState.turns; giftState.lastAt = Date.now(); saveGifts(); }
  cancelIdle();
  if (chosen.id === 'thanks') director.phase('thanks');
  else director.phase('answering', true);
}

// ---- 下の操作部：質問の候補と「質問する」を1つの並びに。注意書きは入力を開いたときだけ入力欄の上に出す ----
// 最初は用意した質問からランダムに出し、2問目からはエージェントが回答と並行して作った候補を出す
let lastText = '';
// via：候補がどこから来たか（ai＝回答のあとに Haiku が作った候補、starter＝用意した質問）。監視で自由入力と見分けるために記録へ残す
function renderSuggest(list: string[], retry = false, via: Via = 'starter') {
  suggest.innerHTML = '';
  const add = (t: string, cls: string, fn: (b: HTMLButtonElement) => void) => {
    const b = document.createElement('button'); b.className = 'chip' + (cls ? ' ' + cls : ''); b.type = 'button'; b.textContent = t;
    b.onclick = () => fn(b); suggest.appendChild(b);
  };
  if (retry) add('もう一度送る', '', (b) => send(lastText, b, lastVia));
  list.forEach((t) => add(t, '', (b) => send(t, b, via)));
  // 一度答えたあとは、続けて聞けることが分かるように「追加で質問」にする
  add(has('started') ? '追加で質問' : '質問する', 'ask', openCompose);
  relayout();
}

function openCompose() {
  layoutViewport.begin(innerWidth, visibleHeight(innerHeight, window.visualViewport?.height));
  closeGift();
  cancelIdle(); director.phase('composing');
  stage.classList.add('composing');
  sendBtn.classList.toggle('on', q.value.trim().length > 0);
  // iOSでキーボードを開くため、タップの処理中にフォーカスする。描画待ちへ移さない。
  q.focus({ preventScroll: true });
  relayout();
}
function closeCompose() { stage.classList.remove('composing'); q.blur(); if (!busy) idle(); relayout(); }
function growInput() { q.style.height = 'auto'; q.style.height = q.scrollHeight + 'px'; relayout(); }
q.addEventListener('input', () => { sendBtn.classList.toggle('on', q.value.trim().length > 0); growInput(); });
q.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (q.value.trim()) send(q.value.trim(), q, 'typed'); } });
q.addEventListener('blur', () => setTimeout(() => { if (has('composing') && !q.value.trim() && document.activeElement !== q) closeCompose(); }, 120));
bindSubmitButton(sendBtn, () => { if (q.value.trim()) send(q.value.trim(), q, 'typed'); });
stage.addEventListener('pointerdown', (e) => { if (has('composing') && !dock.contains(e.target as Node)) closeCompose(); });

// 送った質問が丸い玉に縮み、画面の右側を弧を描いて回り込んで、キャラの頭の上へ飛んでいく
async function fly(text: string, fromEl?: HTMLElement) {
  const from = fromEl ? fromEl.getBoundingClientRect() : { left: innerWidth / 2 - 60, top: innerHeight - 120, width: 120, height: 24 };
  const sx = from.left + from.width / 2, sy = from.top + from.height / 2;
  // ① 文字が中央へ縮みながら消え、同じ場所に玉が膨らむ
  const label = document.createElement('div'); label.className = 'fly'; label.textContent = text.length > 22 ? text.slice(0, 21) + '…' : text;
  document.body.appendChild(label);
  label.style.left = sx - label.offsetWidth / 2 + 'px'; label.style.top = sy - label.offsetHeight / 2 + 'px';
  const ball = document.createElement('div'); ball.className = 'fly-ball';
  document.body.appendChild(ball);
  const R = 11;
  ball.style.left = sx - R + 'px'; ball.style.top = sy - R + 'px';
  label.animate([{ transform: 'scale(1)', opacity: 1 }, { transform: 'scale(.2)', opacity: 0 }],
    { duration: 260, easing: 'cubic-bezier(.5,0,.8,.4)', fill: 'forwards' }).finished.then(() => label.remove());
  await ball.animate([{ transform: 'scale(0)', opacity: 0 }, { transform: 'scale(1)', opacity: 1 }],
    { duration: 220, easing: 'ease-out', fill: 'forwards' }).finished;
  // ② 2次ベジェ曲線の上を進む。終点は雲が出る位置（頭の上）。ふだんは右端を回り、
  // みのるんを右下に寄せた低い画面では雲も右上にあるので、左側を回って右上へ入る
  const t = thought.getBoundingClientRect();
  const ex = t.left + t.width / 2, ey = t.top + t.height / 2, bend = Math.min(innerWidth * 0.22, 140);
  const cx = has('side') ? Math.max(32, Math.min(sx, ex) - bend) : Math.min(innerWidth - 32, Math.max(sx, ex) + bend), cy = (sy + ey) * 0.55;
  const frames: Keyframe[] = [];
  for (let i = 0; i <= 24; i++) {
    const u = i / 24, x = (1 - u) ** 2 * sx + 2 * (1 - u) * u * cx + u * u * ex, y = (1 - u) ** 2 * sy + 2 * (1 - u) * u * cy + u * u * ey;
    frames.push({ transform: `translate(${x - sx}px,${y - sy}px) scale(${1 - 0.35 * u})`, opacity: u > 0.85 ? 1 - (u - 0.85) * 4 : 1 });
  }
  await ball.animate(frames, { duration: 700, easing: 'cubic-bezier(.33,0,.15,1)', fill: 'forwards' }).finished;
  // ③ 頭の上でふわっと消えて、雲（考えごと）に変わる
  const last = frames[frames.length - 1].transform as string;
  ball.animate([{ transform: last, opacity: 0.4 }, { transform: last.replace(/scale\([^)]*\)/, 'scale(1.8)'), opacity: 0 }],
    { duration: 220, easing: 'ease-out', fill: 'forwards' }).finished.then(() => ball.remove());
}

// ---- /api/chat：断られたときの文は確認役が返す（理由ごとに画面の場面を変える） ----
async function postChat(body: Record<string, unknown>, signal?: AbortSignal) {
  const response = await fetch('/api/chat', {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', 'x-anon-id': anonId, ...(await authHeader()),
      'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': sessionId },
    body: JSON.stringify(body),
  });
  if (body.warmup && response.status === 403) {
    const data = await response.clone().json().catch(() => ({}));
    if (data.error === 'banned' && data.message) showBanned(data.message);
  }
  return response;
}
// 画面を離れた時刻。アプリの切り替えや画面ロックの間は通信が止まるので、記録に「離れていたか」を添えて回線の不調と分ける
let hiddenAt = document.hidden ? Date.now() : 0;
document.addEventListener('visibilitychange', () => { if (document.hidden) hiddenAt = Date.now(); });
// 記録は順番に送り、復旧通知が最初の通知より先に保存されるのを防ぐ。
let reporting = Promise.resolve();
function reportIncident(incident: Incident) {
  const hidden = document.hidden || hiddenAt >= incident.at;
  reporting = reporting.then(async () => {
    await postChat({ client_error: { ...incident, hidden } }, AbortSignal.timeout(5000));
  }).catch(() => {});
}
const transport = new ChatTransport(postChat, reportIncident);
async function* chat(body: Record<string, unknown>): AsyncGenerator<AgentEvent> {
  if (params.get('limit')) throw new Refused(429, 'daily_limit', '');
  await startup;
  const res = await transport.question(body);
  const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader();
  let buf = '', completed = false;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += value;
      let i: number;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data:')) continue;
          let event: AgentEvent;
          try { event = JSON.parse(line.slice(5).trim()) as AgentEvent; } catch { continue; }
          if (event.type === 'done') completed = true;
          if (event.type === 'error') transport.failure(body, 'agent');
          yield event;
        }
      }
    }
    if (!completed) throw new Error('stream ended before done');
  } catch (e) {
    // 終わりの合図まで届いていれば回答は揃っている。その後の切断は失敗に数えない
    if (completed) return;
    transport.failure(body, 'stream');
    throw e;
  } finally { reader.releaseLock(); }
}

const DAILY_LIMIT = '今日はたくさんお話ししたので、続きは明日にしましょう。日本時間0時（ラスベガス7時）に、またお話しできるようにしておきますね。';
// この会話で話した話題（エージェントが付ける見出し）。上限に達したときのシェア文に使う
const topicsSeen: string[] = [];
const DAILY_LIMIT_GUEST = DAILY_LIMIT + '\n\n右上のアイコンから Google でログインすると、もっとお話しできるようになります。';
let busy = false, turnVersion = 0;
type Via = 'typed' | 'ai' | 'starter' | 'link';
let lastVia: Via = 'typed';
async function send(text: string, fromEl?: HTMLElement, via: Via = 'link') {
  if (busy) return;
  const requestVersion = ++turnVersion;
  closeGift(); giftEligible = false; currentGiftCandidates = [];
  cancelIdle(); busy = true; lastText = text; lastVia = via;
  stage.classList.add('busy', 'started'); greet.classList.remove('on');
  bubble.classList.remove('on'); stage.classList.remove('answering', 'composing');
  q.blur(); q.value = ''; q.style.height = 'auto'; sendBtn.classList.remove('on');
  $('#thoughtText').textContent = text; layout(); drawCloud();
  await fly(text, fromEl);
  thought.classList.add('on'); director.phase('thinking', true);

  const tid = crypto.randomUUID().replace(/-/g, '').slice(0, 20);  // この質問の番号（会話の記録と通信エラーを結び付ける）
  let raw = '', seeking = '', opened = false, failed = false, refused: Refused | null = null, next: string[] = [], limitShare = '', topic = '';
  // 地図は本文より先に届くので吹き出しの先頭に、照合の結果は末尾に置く
  const figures: HTMLElement[] = [], extras: HTMLElement[] = [];
  let followingUp = false, showingMap = false;
  let linkPreview: LinkPreview | undefined;
  const render = (caret: boolean) => {
    answer.innerHTML = '';
    figures.forEach((el) => answer.appendChild(el));
    // 流している途中は、書きかけの出典 [ラベル](URL を隠す（届ききったらラベルになる）
    answer.insertAdjacentHTML('beforeend', md(caret ? raw.replace(/\s*\[[^\]\n]*(\]\([^)\s]*)?$/, '') : raw));
    const last = answer.lastElementChild;
    if (seeking && (caret || followingUp)) answer.insertAdjacentHTML('beforeend', `<p class="seeking">${esc(seeking)}${caret ? '<span class="caret"></span>' : ''}</p>`);
    else if (caret && last && last.tagName !== 'FIGURE') (last.tagName === 'UL' || last.tagName === 'OL' ? last.lastElementChild! : last).insertAdjacentHTML('beforeend', '<span class="caret"></span>');
    extras.forEach((el) => answer.appendChild(el));
    layout();
  };
  const open = async () => {
    if (opened) return;
    opened = true;
    doing.classList.remove('on'); thought.classList.remove('on');
    answer.innerHTML = ''; answer.scrollTop = 0; stage.classList.add('answering'); layout(); bubble.classList.add('on');
  };
  const settle = () => {
    clearTimeout(settleTimer);
    settleTimer = window.setTimeout(() => {
      if (!busy && !activeGift && !has('composing') && requestVersion === turnVersion && !answer.querySelector('img:not([data-ready])')) idle();
    }, 1400);
  };

  try {
    for await (const ev of chat({ prompt: text, tid, via })) {
      if (ev.type === 'topic') {
        topic = ev.heading;
        if (ev.heading && !topicsSeen.includes(ev.heading)) topicsSeen.push(ev.heading);
      } else if (ev.type === 'tool') {
        if (!opened) { director.tool(ev.name); doing.textContent = ev.label; doing.classList.add('on'); }
        // 答えの途中で追加で調べ始めたら、吹き出しの末尾に何を調べているかを出す（次の本文が届いたら消す）
        else { director.tool(ev.name); if (!followingUp) seeking = ev.label; render(true); }
      } else if (ev.type === 'follow_up') {
        followingUp = true; seeking = ev.text; await open(); director.phase('following'); render(true);
      } else if (ev.type === 'replace') {
        if (!ev.text.trim()) continue;
        bubble.classList.remove('on');
        await wait(180);
        raw = ev.text; seeking = ''; followingUp = false;
        linkPreview = undefined;
        answer.scrollTop = 0; director.answer(ev.text, showingMap); render(false); bubble.classList.add('on');
      } else if (ev.type === 'text') {
        raw += ev.text;
        if (!ev.text.trim()) { if (opened) render(true); continue; }
        await open();
        if (!failed && !followingUp) director.answer(ev.text, showingMap);
        seeking = ''; render(true);
      } else if (ev.type === 'map') {
        showingMap = /(?:地図|マップ|map)/i.test(ev.alt + ' ' + ev.caption);
        const imageReady = director.prepareImage();
        director.answer(raw, showingMap);
        const fig = document.createElement('figure');
        fig.innerHTML = `<img src="${esc(ev.src)}" alt="${esc(ev.alt)}" width="${ev.width}" height="${ev.height}"><figcaption>${esc(ev.caption)}・タップで拡大</figcaption>`;
        fig.querySelector('img')!.addEventListener('click', () => openZoom(ev));
        fig.querySelector('img')!.addEventListener('load', relayout);
        // 表示前から読み込み、画素の展開が済んでから回答姿勢へ移る。失敗時も待ち続けない。
        const picture = fig.querySelector('img')!;
        void picture.decode().catch(() => {}).then(() => {
          picture.dataset.ready = 'true'; imageReady(); relayout();
          if (!busy && !failed && requestVersion === turnVersion) settle();
        });
        figures.push(fig); if (opened) render(true);
      } else if (ev.type === 'catalog_update') {
        const box = document.createElement('div'); box.className = 'update';
        box.innerHTML = `<b>最新の公式カタログで変更あり（${esc(ev.at)}）</b><ul>${ev.lines.map((l) => `<li>${esc(l)}</li>`).join('')}</ul>`;
        extras.push(box); render(false);
      } else if (ev.type === 'preview') {
        linkPreview = ev;
      } else if (ev.type === 'suggest') {
        next = ev.items;
      } else if (ev.type === 'error') {
        failed = true; director.phase('error'); await open(); raw += (raw ? '\n\n' : '') + ev.message; render(false);
      }
    }
  } catch (e) {
    refused = e instanceof Refused ? e : new Refused(0, 'network', 'いまうまくつながりませんでした。電波の良いところで、もう一度送ってください。');
  }

  if (refused) {
    failed = true; director.phase('error');
    if (refused.reason === 'banned') {
      // 利用停止：候補と入力を消し、確認役が返した文をそのまま出す
      stage.classList.add('limited');
      await open(); raw = refused.message; render(false);
    } else if (refused.reason === 'daily_limit') {
      // 上限はお知らせではなく、みのるんの返事として吹き出しで伝える（下の候補と入力は消す）
      stage.classList.add('limited');
      await open(); raw = profile() ? DAILY_LIMIT : DAILY_LIMIT_GUEST; render(false);
      // 例：#みのるんエージェント にバッジの受け取りやホテル選びのことなどたくさん質問できて大満足！みんなも使ってみてね。（改行して URL）
      limitShare = `#みのるんエージェント に${topicsSeen.slice(-2).join('や') || 're:Invent'}のことなどたくさん質問できて大満足！みんなも使ってみてね。\n${location.origin}/`;
    } else if (followingUp && raw) {
      seeking = '追加で調べようとしましたが、うまくいきませんでした。'; render(false);
    } else if (refused.reason === 'network' && raw.trim()) {
      // 回答の途中で通信が切れた。届いた分は消さず、途中までかもしれないことを添える
      const note = document.createElement('p'); note.className = 'seeking';
      note.textContent = '通信が途切れたため、回答が途中で止まっているかもしれません。';
      extras.push(note); render(false);
    } else {
      await open(); raw = refused.message; answer.classList.add('oops'); render(false);
    }
  }
  render(false); answer.classList.remove('oops');
  if (limitShare) {
    const share = document.createElement('a'); share.className = 'share'; share.target = '_blank'; share.rel = 'noopener';
    share.href = 'https://x.com/intent/post?text=' + encodeURIComponent(limitShare);
    share.textContent = 'Xでシェア'; answer.appendChild(share); layout();
  }
  if (!failed && raw) {
    const share = document.createElement('a'); share.className = 'share'; share.target = '_blank'; share.rel = 'noopener';
    // 例：#みのるんエージェント に「バッジはどこで受け取る？」を質問したよ！みんなも使ってみてね。（改行して URL）
    const asked = text.length > 40 ? text.slice(0, 39) + '…' : text;
    share.href = 'https://x.com/intent/post?text=' + encodeURIComponent(`#みのるんエージェント に「${asked}」を質問したよ！みんなも使ってみてね。\n${location.origin}/`);
    share.textContent = 'Xでシェア';
    // 最後の行：右にシェア
    const row = document.createElement('div'); row.className = 'foot';
    row.append(share); answer.appendChild(row); layout();
  }
  if (failed) director.phase('error');
  else settle();
  busy = false; stage.classList.remove('busy');
  if (!has('limited')) renderSuggest(next.length ? next : pickStarters(3, [text]), failed && refused?.reason !== 'too_long', next.length ? 'ai' : 'starter');
  if (!failed && raw) {
    giftState.turns++; saveGifts();
    const isExpo = /expo|展示|ブース/i.test(topic + ' ' + text);
    expoDepth = isExpo ? expoDepth + 1 : 0;
    currentTopic = topic;
    currentGiftCandidates = candidatesFor(text, currentTopic, expoDepth);
    giftEligible = figures.length === 0 && !answer.querySelector('img, figure, table');
    layout();
    const sources = Array.from(answer.querySelectorAll<HTMLAnchorElement>('a.src')).map(a => a.href);
    const linked = bookGift(raw, sources) || (linkPreview && previewGift(linkPreview, sources)) || characterGift(text + '\n' + raw);
    if (linked && giftEligible) {
      // 画素が用意できてから貼る。別の質問や入力に移ったら古い画像を差し込まない。
      const image = new Image(); image.referrerPolicy = 'no-referrer'; image.src = linked.image!;
      void Promise.race([image.decode(), wait(2500).then(() => { throw new Error('image timeout'); })])
        .then(() => { if (requestVersion === turnVersion) showGift([], linked); })
        .catch(() => {});
    } else showGift(currentGiftCandidates);
  }
  relayout();
}

// 会話ごとに新しい入れ物（microVM）が立ち上がるのに数秒かかるので、ページを開いた時点で先に起こしておく
async function warmUp() {
  await transport.warmup();
}
function showBanned(message: string) {
  closeGift(); cancelIdle(); turnVersion++;
  stage.classList.add('limited', 'started', 'answering'); stage.classList.remove('composing', 'busy');
  greet.classList.remove('on'); thought.classList.remove('on'); doing.classList.remove('on');
  suggest.innerHTML = '';
  answer.innerHTML = md(message); answer.scrollTop = 0;
  bubble.classList.add('on'); director.phase('error'); layout();
}

// ---- ログイン：時計の右のアイコン。ログインすると上限が緩くなり、前の会話の好みや事実を覚える ----
const meBtn = $('#me'), mePop = $('#mepop');
const PERSON = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><circle cx="12" cy="8.5" r="3.6" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M4.8 19.5c1.3-3.3 4-5 7.2-5s5.9 1.7 7.2 5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';
const GOOGLE = '<svg viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>';
async function authHeader(): Promise<Record<string, string>> {
  const t = await accessToken();
  return t ? { 'x-access-token': t } : {};
}
function renderMe() {
  const p = profile();
  meBtn.innerHTML = PERSON;
  meBtn.classList.toggle('in', !!p);
  meBtn.setAttribute('aria-label', p ? 'ログイン中' : 'ログイン');
  mePop.innerHTML = p
    ? `<p class="lead">ログイン中です。1日に話せる回数が増え、話した内容を覚えて答えます。</p><button class="out" id="logout" type="button">ログアウト</button>`
    : `<p class="lead">ログインすると、1日に話せる回数が増えたり、話した内容を覚えてもらえるようになります。</p>
       <button class="gbtn" id="glogin" type="button">${GOOGLE}<span>Google でログイン</span></button>`;
  $('#glogin')?.addEventListener('click', () => login());
  $('#logout')?.addEventListener('click', () => logout());
}
const toggleMe = (on: boolean) => { mePop.classList.toggle('on', on); meBtn.setAttribute('aria-expanded', String(on)); };
meBtn.addEventListener('click', (e) => { e.stopPropagation(); toggleMe(!mePop.classList.contains('on')); });
document.addEventListener('pointerdown', (e) => { if (!mePop.contains(e.target as Node) && e.target !== meBtn && !meBtn.contains(e.target as Node)) toggleMe(false); });

director.phase('greeting');
restTimer = window.setTimeout(() => { if (!busy && !has('composing')) director.phase('resting'); }, 30_000);
renderSuggest(pickStarters(3));
fitViewport();
layout(); requestAnimationFrame(() => requestAnimationFrame(() => stage.classList.add('ready')));
// Google から戻ってきたときは、先にトークンを受け取ってから先読みする（ログインした人として数えるため）
const startup = handleRedirect().catch(() => {}).then(() => { renderMe(); return warmUp(); });
// 開いて少ししてから、左上にさりげなくあいさつ。最初の質問を送ったら消す
setTimeout(() => { if (!has('started')) greet.classList.add('on'); }, 1500);

const ask = params.get('ask');
if (ask) setTimeout(() => send(ask), 800);
if (params.get('compose')) setTimeout(openCompose, 800);

// 開発時に採用済みの全素材を同じ画面で点検する。
if (import.meta.env.DEV && params.get('pose') && POSES.includes(params.get('pose') as Pose)) {
  cancelIdle(); pose(params.get('pose') as Pose, 'idle');
}

export {};
