// スライド作成中に別の質問をしたとき、見出しの下に固定する進み具合の帯（2026-11-24 みのるん選定のD案）。
// 吹き出しは1つしかないので、次の質問で作成中のカードが消える。そのカードの位置から帯へ移る動きで見せ、
// 帯は status.json を3秒おきに見て進み具合を出し、できたら表紙と「開く」に変わる。

import { tr } from './i18n';
import { GIVE_UP_MS, stepOf, type SlidesStatus } from './slides-card';

/** 帯は幅が狭いので、スライド作成機能の detail（「書き出して余白を測っています（2回目）」など）は出さず、
 *  段階ごとの短い言葉だけにする。スマホ幅で末尾が「…」で切れないよう、日本語は9字・英語は1〜2語まで（2026-11-24 みのるん指示） */
const STRIP_LINES = [tr('記事を読んでいます', 'Reading'), tr('構成を考えています', 'Outlining'), tr('書き出しています', 'Writing'), tr('仕上げています', 'Polishing')];
export function stripLine(s: SlidesStatus | null): string {
  const n = s ? stepOf(s.stage) : -1;
  if (n === 0 && s?.kind === 'topic') return tr('出典を読んでいます', 'Reading sources');
  return n >= 0 && n < 4 ? STRIP_LINES[n] : tr('順番を待っています', 'In line');
}

export type StripKind = 'working' | 'done' | 'error';
export interface StripView { kind: StripKind; pct: number; line: string; title?: string }

/** status.json を帯の見え方へ直す。棒の長さは4段階のどこまで来たか（受付待ちでも少しは伸ばす） */
export function stripView(s: SlidesStatus | null): StripView {
  if (s?.stage === 'done') return { kind: 'done', pct: 100, line: tr('スライドができました！', 'Your slides are ready!'), title: s.title };
  if (s?.stage === 'error') return { kind: 'error', pct: 0, line: tr('スライドを作れませんでした', "The slides couldn't be made") };
  const n = s ? stepOf(s.stage) : -1;
  return { kind: 'working', pct: n < 0 ? 6 : Math.round(((n + 0.5) / 4) * 100), line: stripLine(s) };
}

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
const OK_BASE = /^\/s\/[A-Za-z0-9]{8,32}\/$/;

export class SlidesStrip {
  readonly el: HTMLElement;
  base = '';
  private timer = 0;
  private started = 0;
  private kind: StripKind | '' = '';
  constructor(host: HTMLElement, private onChange: () => void, private onOpen: (base: string) => void, private fetcher: typeof fetch = fetch.bind(window)) {
    this.el = document.createElement('div');
    this.el.className = 'slides-strip';
    this.el.hidden = true;
    this.el.setAttribute('role', 'status');
    host.appendChild(this.el);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && this.kind === 'working') void this.tick(); });
  }

  get visible() { return !this.el.hidden; }
  get working() { return this.visible && this.kind === 'working'; }

  /** 作成中のカードから帯へ移す。from はカードの画面上の位置（そこから帯の位置へ縮みながら動く） */
  show(base: string, from?: DOMRect) {
    if (!OK_BASE.test(base)) return;
    this.base = base; this.kind = ''; this.started = Date.now();
    this.el.hidden = false;
    this.paint({ kind: 'working', pct: 6, line: tr('作成中', 'Making') });
    this.onChange();
    if (from && from.width && typeof this.el.animate === 'function') {
      const to = this.el.getBoundingClientRect();
      if (to.width && to.height) {
        const dx = from.left + from.width / 2 - (to.left + to.width / 2), dy = from.top + from.height / 2 - (to.top + to.height / 2);
        this.el.animate([
          { transform: `translate(${dx}px,${dy}px) scale(${from.width / to.width},${from.height / to.height})`, opacity: 0.4, borderRadius: '14px' },
          { transform: 'none', opacity: 1 },
        ], { duration: 520, easing: 'cubic-bezier(.2,.8,.2,1)' });
      }
    }
    clearTimeout(this.timer);
    void this.tick();
  }

  hide() {
    clearTimeout(this.timer);
    this.el.hidden = true; this.base = ''; this.kind = '';
    this.onChange();
  }

  private async tick() {
    clearTimeout(this.timer);
    const base = this.base;
    if (!base) return;
    let s: SlidesStatus | null = null;
    try { const r = await this.fetcher(base + 'status.json', { cache: 'no-store' }); if (r.ok) s = await r.json() as SlidesStatus; } catch { /* 通信の揺れは次の回に任せる */ }
    if (base !== this.base) return;
    let v = stripView(s);
    if (v.kind === 'working' && Date.now() - this.started > GIVE_UP_MS) v = { kind: 'error', pct: 0, line: tr('時間がかかりすぎたため止めました', 'This took too long') };
    this.paint(v);
    if (v.kind === 'working') this.timer = window.setTimeout(() => void this.tick(), 3000);
  }

  private paint(v: StripView) {
    const changed = v.kind !== this.kind;
    this.kind = v.kind;
    this.el.className = 'slides-strip ' + v.kind;
    const close = `<button class="x" type="button" aria-label="${tr('閉じる', 'Close')}">×</button>`;
    if (v.kind === 'done') {
      this.el.innerHTML = `<a class="go" href="${esc(this.base)}" target="_blank" rel="noopener"><img src="${esc(this.base)}og.jpg" alt="" width="56" height="32"><span class="t">${esc(v.line)}</span><span class="open">${tr('開く', 'Open')}</span></a>${close}`;
      this.el.querySelector('a')!.addEventListener('click', () => { this.onOpen(this.base); this.hide(); });
    } else if (v.kind === 'error') {
      this.el.innerHTML = `<span class="t">${esc(v.line)}</span>${close}`;
    } else {
      this.el.innerHTML = `<span class="t">${tr('スライド作成中', 'Making slides')}</span><span class="g"><i style="width:${v.pct}%"></i></span><span class="s">${esc(v.line)}</span>`;
    }
    this.el.querySelector('.x')?.addEventListener('click', () => { if (this.kind === 'done') this.onOpen(this.base); this.hide(); });
    if (changed) this.onChange();
  }
}
