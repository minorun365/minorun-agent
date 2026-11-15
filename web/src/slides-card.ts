// スライドのカード（記事のURLから作るものと、調べてまとめるもの）。受け付けた直後に置き、工房が書く status.json を数秒おきに見て、
// 「記事を読む → 構成を考える → スライドを焼く → 仕上げ」の段階を進め、できたら表紙のサムネイルに差し替える。

export interface SlidesStatus { stage: string; kind?: 'article' | 'topic'; message?: string; title?: string; pages?: number; viewer?: string; thumb?: string }

export const STEPS = ['記事を読む', '構成を考える', 'スライドを焼く', '仕上げ'] as const;
const WAIT_LINES = ['記事を読んでいます', '構成を考えています', 'スライドを焼いています', '余白を測って仕上げています'];
/** 調べてまとめるときは、最初の段階を「出典を読む」と言い分ける */
export const stepLabel = (i: number, kind?: string) => (i === 0 && kind === 'topic' ? '出典を読む' : STEPS[i]);
export const waitLine = (i: number, kind?: string) => (i === 0 && kind === 'topic' ? '出典を読んでいます' : WAIT_LINES[i]);

/** 工房の段階を、画面の4段階のどこまで来たかへ直す（-1 は受付待ち） */
export function stepOf(stage: string): number {
  return ({ queued: -1, reading: 0, writing: 1, rendering: 2, checking: 3, fixing: 3, done: 4 } as Record<string, number>)[stage] ?? -1;
}

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));

/** 公開先は自分のサイトの /s/<番号>/ だけ（status.json の中身をそのまま信じない） */
export function safeViewer(base: string, s: SlidesStatus): { viewer: string; thumb: string } {
  return { viewer: base, thumb: base + 'og.jpg' + (s.pages ? `?v=${s.pages}` : '') };
}

export function slidesCard(base: string, onChange: () => void, fetcher: typeof fetch = fetch.bind(window)): HTMLElement {
  const fig = document.createElement('figure');
  fig.className = 'slides-card working';
  fig.innerHTML = `<div class="slides-steps">${STEPS.map((s, i) => `<span class="step" data-i="${i}"><i></i>${s}</span>`).join('')}</div>`
    + `<div class="slides-bar"><span></span></div><figcaption class="slides-line">受け付けました。閉じても続きます</figcaption>`;
  const line = fig.querySelector('.slides-line') as HTMLElement;
  const started = Date.now();
  let stopped = false;
  const show = (s: SlidesStatus) => {
    const n = stepOf(s.stage);
    fig.querySelectorAll<HTMLElement>('.step').forEach((el, i) => {
      el.classList.toggle('done', i < n); el.classList.toggle('now', i === n);
      if (i === 0) el.lastChild!.textContent = stepLabel(0, s.kind);
    });
    if (n >= 0 && n < 4) line.textContent = waitLine(n, s.kind) + '…';
  };
  const finish = (s: SlidesStatus) => {
    stopped = true;
    const { viewer, thumb } = safeViewer(base, s);
    fig.className = 'slides-card ready';
    fig.innerHTML = `<a href="${esc(viewer)}" target="_blank" rel="noopener"><img src="${esc(thumb)}" alt="${esc(s.title || 'スライドの表紙')}" width="1200" height="675"></a>`
      + `<figcaption><b>できました！</b>タップでめくれます${s.pages ? `（全${s.pages}ページ）` : ''}</figcaption>`;
    fig.querySelector('img')!.addEventListener('load', onChange);
    onChange();
  };
  const fail = (message: string) => {
    stopped = true;
    fig.className = 'slides-card failed';
    fig.innerHTML = `<figcaption>${esc(message)}</figcaption>`;
    onChange();
  };
  const tick = async () => {
    if (stopped) return;
    try {
      const r = await fetcher(base + 'status.json', { cache: 'no-store' });
      if (r.ok) {
        const s = await r.json() as SlidesStatus;
        if (s.stage === 'done') return finish(s);
        if (s.stage === 'error') return fail(s.message || 'スライドを作れませんでした。');
        show(s);
      }
    } catch { /* 通信の揺れは次の回に任せる */ }
    if (Date.now() - started > 12 * 60_000) return fail('時間がかかりすぎたため止めました。少し時間をおいて、もう一度頼んでください。');
    setTimeout(tick, 3000);
  };
  setTimeout(tick, 1500);
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

/** 作り始めて15分以内なら、まだ作っている途中の可能性がある */
export const maybeWorking = (item: SlidesItem, now = Date.now()) => now / 1000 - item.at < 15 * 60;

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
      const tag = `<em class="kind">${s!.kind === 'topic' ? '調べて' : '記事'}</em>`;
      rows.push(`<li><a href="${esc(it.base)}" target="_blank" rel="noopener"><img src="${esc(it.base)}og.jpg" alt="" width="64" height="36"><span class="t">${isNew}${tag}${esc(s!.title || 'スライド')}</span></a>${d}</li>`);
    } else if (kind === 'error') {
      rows.push(`<li><span class="t mut">作れませんでした</span>${d}</li>`);
    } else if (kind === 'working') {
      const n = stepOf(s!.stage);
      const step = n >= 0 && n < 4 ? waitLine(n, s!.kind) : '順番を待っています';
      rows.push(`<li class="working"><span class="ph"><i></i></span><span class="t"><b>作成中…</b><small>${step}</small></span>${d}</li>`);
    } else if (kind === 'checking') {
      rows.push(`<li><span class="t mut">確かめています…</span>${d}</li>`);
    }
  }
  if (rows.length) box.innerHTML = `<p class="mh">作ったスライド</p><ul>${rows.join('')}</ul>`;
  return box;
}

const SEEN_KEY = 'minorun-slides-seen';
const readSeen = (): string[] => { try { return JSON.parse(localStorage.getItem(SEEN_KEY) || '[]'); } catch { return []; } };

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
    this.onChange(); // 15分を過ぎて作成中から外れたときも印を引き直す
  }
}
