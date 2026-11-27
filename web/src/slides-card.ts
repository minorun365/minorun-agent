// スライドのカード（記事のURLから作るものと、調べてまとめるもの）。受け付けた直後に置き、スライド作成機能が書く status.json を数秒おきに見て、
// 「記事を読む → 構成を考える → スライドを書き出す → 仕上げ」の段階を進め、できたら表紙のサムネイルに差し替える。

import { EN, tr } from './i18n';
const LOGIN = tr('ログイン', 'Log in');

export interface SlidesStatus { stage: string; kind?: 'article' | 'topic'; detail?: string; message?: string; title?: string; pages?: number; viewer?: string; thumb?: string }

export const STEPS = EN ? ['Read', 'Outline', 'Write', 'Polish'] as const : ['記事を読む', '構成を考える', 'スライドを書き出す', '仕上げ'] as const;
const WAIT_LINES = EN ? ['Reading the article', 'Planning the outline', 'Writing the slides', 'Checking the margins and polishing'] : ['記事を読んでいます', '構成を考えています', 'スライドを書き出しています', '余白を測って仕上げています'];
/** 調べてまとめるときは、最初の段階を「出典を読む」と言い分ける */
export const stepLabel = (i: number, kind?: string) => (i === 0 && kind === 'topic' ? tr('出典を読む', 'Sources') : STEPS[i]);
export const waitLine = (i: number, kind?: string) => (i === 0 && kind === 'topic' ? tr('出典を読んでいます', 'Reading the sources') : WAIT_LINES[i]);
/** できるまでの目安。エージェントが焼いて見直すので5〜10分かかる（2026-11-21 の本番で約6.5分） */
export const ETA_LINE = tr('できあがるまで5〜10分ほどかかります', 'Takes about 5–10 minutes');
/** 作成中の一言。スライド作成機能が書く detail（「書き出して余白を測っています（2回目）」など）があればそれを、無ければ段階の言葉を出す。
 *  detail は status.json から来るので、短い日本語の文だけを通す（タグや記号の多い文は捨てる） */
export function progressLine(s: SlidesStatus): string {
  const d = typeof s.detail === 'string' ? s.detail.trim() : '';
  // detail は日本語の文なので、英語のときは段階の言葉を出す
  if (!EN && d && d.length <= 40 && !/[<>{}\[\]`$]/.test(d)) return d;
  const n = stepOf(s.stage);
  return n >= 0 && n < 4 ? waitLine(n, s.kind) : tr('順番を待っています', 'Waiting in line');
}
/** 作成中として見張る長さ。スライド作成機能の1本の上限（60分）より短く、ふだんの所要（5〜10分）より十分長く */
export const WORKING_MINUTES = 40;

/** スライド作成機能の段階を、画面の4段階のどこまで来たかへ直す（-1 は受付待ち） */
export function stepOf(stage: string): number {
  return ({ queued: -1, reading: 0, writing: 1, rendering: 2, checking: 3, fixing: 3, done: 4 } as Record<string, number>)[stage] ?? -1;
}

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));

/** 公開先は自分のサイトの /s/<番号>/ だけ（status.json の中身をそのまま信じない） */
export function safeViewer(base: string, s: SlidesStatus): { viewer: string; thumb: string } {
  return { viewer: base, thumb: base + 'og.jpg' + (s.pages ? `?v=${s.pages}` : '') };
}

/** 作成中のカードを諦めるまでの時間（WORKING_MINUTES と同じ） */
export const GIVE_UP_MS = WORKING_MINUTES * 60_000;

/** ログインしていない人へ、作成中のカードの中で待つ間のログインを勧める（11/21 みのるん選定のA案） */
export interface LoginNudge { icon: string; onClick: () => void }
export const LOGIN_NUDGE_TEXT = tr('ログインしておくと、あとから<br>どの端末でも開けます', 'Log in to open it later<br>from any device');
/** ログインなしの上限に当たったときの案内（同じA案の形。押すとログインし、戻ったら同じ頼みを送り直す） */
export const LOGIN_CONTINUE_TEXT = tr('ログインして戻ると、<br>このスライドを作り始めます', 'Log in and come back<br>to start these slides');
export function loginContinueBox(nudge: LoginNudge): HTMLElement {
  const box = document.createElement('div');
  box.className = 'slides-login';
  box.innerHTML = `<p>${LOGIN_CONTINUE_TEXT}</p><button type="button">${nudge.icon}<span>${LOGIN}</span></button>`;
  box.querySelector('button')!.addEventListener('click', () => nudge.onClick());
  return box;
}

/** 作成中のカードの棒をどこまで伸ばすか（％）。受付待ちでも少しだけ見せ、仕上げでも満タンにはしない */
export function barWidth(n: number): number {
  return [8, 25, 50, 72, 90][Math.max(-1, Math.min(n, 3)) + 1];
}

export function slidesCard(base: string, onChange: () => void, fetcher: typeof fetch = fetch.bind(window), nudge?: LoginNudge): HTMLElement {
  const fig = document.createElement('figure');
  fig.className = 'slides-card working';
  fig.dataset.base = base;
  // 吹き出しの中の文字を「題」と「小さい字」の2段にそろえ、段階の丸は出さない（11/24 みのるん選定のD案。吹き出しが込み合うため）
  fig.innerHTML = `<p class="slides-title">${tr('スライドを作っています', 'Making your slides')}</p><p class="slides-eta">${ETA_LINE}</p>`
    + `<div class="slides-bar"><span style="width:${barWidth(-1)}%"></span></div><figcaption class="slides-line">${tr('いまは', '')}${progressLine({ stage: 'queued' })}</figcaption>`
    + (nudge ? `<div class="slides-login"><p>${LOGIN_NUDGE_TEXT}</p><button type="button">${nudge.icon}<span>${LOGIN}</span></button></div>` : '');
  fig.querySelector('.slides-login button')?.addEventListener('click', () => nudge!.onClick());
  const line = fig.querySelector('.slides-line') as HTMLElement;
  const bar = fig.querySelector('.slides-bar span') as HTMLElement;
  const started = Date.now();
  let stopped = false;
  const show = (s: SlidesStatus) => {
    const n = stepOf(s.stage);
    bar.style.width = barWidth(n) + '%';
    if (n < 4) line.textContent = tr('いまは', '') + progressLine(s);
  };
  const finish = (s: SlidesStatus) => {
    stopped = true;
    const { viewer, thumb } = safeViewer(base, s);
    fig.className = 'slides-card ready';
    fig.innerHTML = `<a href="${esc(viewer)}" target="_blank" rel="noopener"><img src="${esc(thumb)}" alt="${esc(s.title || tr('スライドの表紙', 'Slide cover'))}" width="1200" height="675"></a>`
      + (EN ? `<figcaption><b>Done!</b> Tap to flip through${s.pages ? ` (${s.pages} pages)` : ''}</figcaption>` : `<figcaption><b>できました！</b>タップでめくれます${s.pages ? `（全${s.pages}ページ）` : ''}</figcaption>`);
    fig.querySelector('img')!.addEventListener('load', onChange);
    onChange();
  };
  const fail = (message: string) => {
    stopped = true;
    fig.className = 'slides-card failed';
    fig.innerHTML = `<figcaption>${esc(message)}</figcaption>`;
    onChange();
  };
  // iPhone で画面が消えた・別のアプリへ行った間はタイマーが止まる。戻ってきたらすぐ読み直す
  let timer = 0, reading = false;
  const tick = async () => {
    if (stopped || reading) return;
    reading = true; clearTimeout(timer);
    try {
      const r = await fetcher(base + 'status.json', { cache: 'no-store' });
      if (r.ok) {
        const s = await r.json() as SlidesStatus;
        if (s.stage === 'done') return finish(s);
        if (s.stage === 'error') return fail((EN ? '' : s.message) || tr('スライドを作れませんでした。', "The slides couldn't be made."));
        show(s);
      }
    } catch { /* 通信の揺れは次の回に任せる */ } finally { reading = false; }
    // スライド作成機能は1本60分で打ち切って失敗を書く。ふだんの所要（5〜10分）より十分長く待っても進まないときだけ止める
    if (Date.now() - started > GIVE_UP_MS) return fail(tr('時間がかかりすぎたため止めました。少し時間をおいて、もう一度頼んでください。', 'This took too long, so it was stopped. Please wait a bit and ask again.'));
    timer = window.setTimeout(tick, 3000);
  };
  const onVisible = () => { if (stopped) document.removeEventListener('visibilitychange', onVisible); else if (document.visibilityState === 'visible') void tick(); };
  document.addEventListener('visibilitychange', onVisible);
  timer = window.setTimeout(tick, 1500);
  return fig;
}

// ---- 作ったスライドの一覧（ログインのメニューの中）と、アイコンのバッジ ----
// 作成中の知らせは画面に浮かべず（スマホで吹き出しと重なるため）、ログインのアイコンに小さな印を付け、
// メニューを開いた一覧の中で進み具合を見せる。
export interface SlidesItem { job: string; base: string; at: number }

/** 先読みの返事（SSE）から作ったスライドの一覧を取り出す。自分のサイトの /s/<番号>/ だけを通す */
export function parseSlidesList(sse: string): SlidesItem[] {
  for (const line of sse.split('\n')) {
    if (!line.startsWith('data:')) continue;
    try {
      const ev = JSON.parse(line.slice(5).trim());
      if (ev.type === 'slides_list' && Array.isArray(ev.items)) {
        return ev.items.filter((i: SlidesItem) => typeof i.job === 'string' && /^\/s\/[A-Za-z0-9]{8,32}\/$/.test(i.base) && Number.isFinite(i.at));
      }
    } catch { /* 読めない行は飛ばす */ }
  }
  return [];
}

/** 作り始めて WORKING_MINUTES 分以内なら、まだ作っている途中の可能性がある */
export const maybeWorking = (item: SlidesItem, now = Date.now()) => now / 1000 - item.at < GIVE_UP_MS / 1000;

const when = (at: number) => {
  const d = new Date(at * 1000);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

async function statusOf(base: string, fetcher: typeof fetch): Promise<SlidesStatus | null> {
  try { const r = await fetcher(base + 'status.json', { cache: 'no-store' }); return r.ok ? await r.json() as SlidesStatus : null; } catch { return null; }
}

/** 状態の見え方。undefined はまだ確かめていない、null は読めなかった（古いものは一覧から外す） */
export type RowKind = 'checking' | 'done' | 'error' | 'working' | 'hidden';
export function rowKind(item: SlidesItem, s: SlidesStatus | null | undefined, now = Date.now()): RowKind {
  if (s === undefined) return 'checking';
  if (s?.stage === 'done') return 'done';
  if (s?.stage === 'error') return 'error';
  return s && maybeWorking(item, now) ? 'working' : 'hidden';
}

/** アイコンの印：作成中があれば「作成中」、無くて見ていない完成品があれば「できた」 */
export type Badge = '' | 'working' | 'ready';
export function badgeOf(items: SlidesItem[], status: (job: string) => SlidesStatus | null | undefined, unseen: Set<string>, now = Date.now()): Badge {
  if (items.some((i) => rowKind(i, status(i.job), now) === 'working')) return 'working';
  return items.some((i) => unseen.has(i.job) && status(i.job)?.stage === 'done') ? 'ready' : '';
}

/** メニューの「作ったスライド」。状態は見張り役から受け取り、その場で描く（開き直すたびに読み直してちらつかせない） */
/** メニューの一覧は種類ごとに先頭の COLLAPSED 件だけ出し、残りは「ほか◯件」で開く（2026-11-27 みのるん指示：並べすぎない）。
 * 一覧は状態が変わるたびに描き直すので、開いたかどうかは種類ごとにここで覚える（メニューを閉じたら閉じ直す） */
export const COLLAPSED = 2;
const opened = new Set<string>();
export const closeLists = () => opened.clear();
export function collapsible(box: HTMLElement, kind: string, heading: string, rows: string[]) {
  if (!rows.length) return;
  const rest = rows.length - COLLAPSED;
  const isOpen = () => opened.has(kind);
  const label = () => (isOpen() ? tr('たたむ', 'Show less') : tr(`ほか${rest}件を見る`, `Show ${rest} more`));
  box.classList.toggle('open', isOpen());
  box.innerHTML = `<p class="mh">${heading}</p><ul>${rows.join('')}</ul>`
    + (rest > 0 ? `<button type="button" class="mmore" aria-expanded="${isOpen()}">${label()}</button>` : '');
  const btn = box.querySelector<HTMLButtonElement>('.mmore');
  btn?.addEventListener('click', (e) => {
    e.stopPropagation();
    if (isOpen()) opened.delete(kind); else opened.add(kind);
    box.classList.toggle('open', isOpen());
    btn.textContent = label();
    btn.setAttribute('aria-expanded', String(isOpen()));
  });
}

export function slidesList(items: SlidesItem[], status: (job: string) => SlidesStatus | null | undefined, unseen: Set<string> = new Set(), now = Date.now()): HTMLElement {
  const box = document.createElement('div');
  box.className = 'myslides';
  const rows: string[] = [];
  for (const it of items) {
    const s = status(it.job);
    const kind = rowKind(it, s, now);
    const d = `<span class="d">${when(it.at)}</span>`;
    if (kind === 'done') {
      const isNew = unseen.has(it.job) ? '<em class="new">NEW</em>' : '';
      const tag = `<em class="kind">${s!.kind === 'topic' ? tr('調べて', 'Topic') : tr('記事', 'Article')}</em>`;
      rows.push(`<li><a href="${esc(it.base)}" target="_blank" rel="noopener"><img src="${esc(it.base)}og.jpg" alt="" width="64" height="36"><span class="t">${isNew}${tag}${esc(s!.title || tr('スライド', 'Slides'))}</span></a>${d}</li>`);
    } else if (kind === 'error') {
      rows.push(`<li><span class="t mut">${tr('作れませんでした', 'Failed')}</span>${d}</li>`);
    } else if (kind === 'working') {
      const step = esc(progressLine(s!));
      rows.push(`<li class="working"><span class="ph"><i></i></span><span class="t"><b>${tr('作成中…', 'Making…')}</b><small>${step}</small></span>${d}</li>`);
    } else if (kind === 'checking') {
      rows.push(`<li><span class="t mut">${tr('確かめています…', 'Checking…')}</span>${d}</li>`);
    }
  }
  collapsible(box, 'slides', tr('作ったスライド', 'Your slides'), rows);
  return box;
}

const SEEN_KEY = 'minorun-slides-seen';
const readSeen = (): string[] => { try { return JSON.parse(localStorage.getItem(SEEN_KEY) || '[]'); } catch { return []; } };
/** 吹き出しかメニューで、もう完成を見たスライドか */
export const seenBefore = (job: string) => readSeen().includes(job);

/** 作ったスライドの状態を見張る。作成中のものだけ3秒おきに status.json を見て、変わったら onChange を呼ぶ */
export class SlidesTracker {
  items: SlidesItem[] = [];
  private status = new Map<string, SlidesStatus | null>();
  private polling = new Set<string>();
  /** できたのに、まだメニューで見ていないもの（作り始めて1時間以内だけ） */
  unseen = new Set<string>();
  constructor(private onChange: () => void, private fetcher: typeof fetch = fetch.bind(window)) {}

  get = (job: string) => this.status.get(job);

  set(items: SlidesItem[]) {
    this.items = items;
    for (const it of items) if (!this.status.has(it.job)) void this.poll(it);
  }
  add(item: SlidesItem) {
    this.items = [item, ...this.items.filter((i) => i.job !== item.job)].slice(0, 10);
    this.status.delete(item.job);
    void this.poll(item);
    this.onChange();
  }
  badge(): Badge { return badgeOf(this.items, this.get, this.unseen); }
  /** メニューを開いた・画面のカードで完成を見た。印を消し、次に開き直しても出さない */
  seen(job?: string) {
    const jobs = job ? [job] : [...this.unseen];
    if (!jobs.length) return;
    jobs.forEach((j) => this.unseen.delete(j));
    try { localStorage.setItem(SEEN_KEY, JSON.stringify([...new Set([...jobs, ...readSeen()])].slice(0, 30))); } catch { /* 保存できなくても印が残るだけ */ }
    this.onChange();
  }

  private async poll(it: SlidesItem) {
    if (this.polling.has(it.job)) return;
    this.polling.add(it.job);
    let last = '';
    for (;;) {
      const s = await statusOf(it.base, this.fetcher);
      this.status.set(it.job, s);
      const kind = rowKind(it, s);
      if (kind === 'done' && Date.now() / 1000 - it.at < 60 * 60 && !readSeen().includes(it.job)) this.unseen.add(it.job);
      const key = `${kind}:${s?.stage ?? ''}`;
      if (key !== last) { last = key; this.onChange(); }
      if (kind !== 'working') break;
      await new Promise((r) => setTimeout(r, 3000));
    }
    this.polling.delete(it.job);
    this.onChange(); // WORKING_MINUTES 分を過ぎて作成中から外れたときも印を引き直す
  }
}
