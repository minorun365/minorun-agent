// URL から作るスライドのカード。受け付けた直後に置き、工房が書く status.json を数秒おきに見て、
// 「記事を読む → 構成を考える → スライドを焼く → 仕上げ」の段階を進め、できたら表紙のサムネイルに差し替える。

export interface SlidesStatus { stage: string; message?: string; title?: string; pages?: number; viewer?: string; thumb?: string }

export const STEPS = ['記事を読む', '構成を考える', 'スライドを焼く', '仕上げ'] as const;
const WAIT_LINES = ['記事を読んでいます', '構成を考えています', 'スライドを焼いています', '余白を測って仕上げています'];

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
    + `<div class="slides-bar"><span></span></div><figcaption class="slides-line">受け付けました。少しお待ちください</figcaption>`;
  const line = fig.querySelector('.slides-line') as HTMLElement;
  const started = Date.now();
  let stopped = false;
  const show = (s: SlidesStatus) => {
    const n = stepOf(s.stage);
    fig.querySelectorAll<HTMLElement>('.step').forEach((el, i) => {
      el.classList.toggle('done', i < n); el.classList.toggle('now', i === n);
    });
    if (n >= 0 && n < 4) line.textContent = WAIT_LINES[n] + '…';
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

// ---- 作ったスライドの一覧（ログインのメニューの中）と、作成中の知らせ（画面の上） ----
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

/** メニューの「作ったスライド」。1件ずつ status.json を見て、完成・作成中・失敗を出し分ける */
export function slidesList(items: SlidesItem[], fetcher: typeof fetch = fetch.bind(window)): HTMLElement {
  const box = document.createElement('div');
  box.className = 'myslides';
  if (!items.length) return box;
  box.innerHTML = `<p class="mh">作ったスライド</p><ul></ul>`;
  const ul = box.querySelector('ul')!;
  for (const it of items) {
    const li = document.createElement('li');
    li.innerHTML = `<span class="t">確かめています…</span><span class="d">${when(it.at)}</span>`;
    ul.appendChild(li);
    void statusOf(it.base, fetcher).then((s) => {
      if (s?.stage === 'done') {
        li.innerHTML = `<a href="${esc(it.base)}" target="_blank" rel="noopener"><img src="${esc(it.base)}og.jpg" alt="" width="64" height="36"><span class="t">${esc(s.title || 'スライド')}</span></a><span class="d">${when(it.at)}</span>`;
      } else if (s?.stage === 'error') {
        li.innerHTML = `<span class="t mut">作れませんでした</span><span class="d">${when(it.at)}</span>`;
      } else if (s && maybeWorking(it)) {
        li.innerHTML = `<span class="t">作成中…</span><span class="d">${when(it.at)}</span>`;
      } else li.remove();
    });
  }
  return box;
}

/** 開き直したときに作成中のものがあれば、画面の上に知らせを出し、できたら「できました」に替える */
export function slidesNotice(item: SlidesItem, fetcher: typeof fetch = fetch.bind(window)): HTMLElement {
  const el = document.createElement('a');
  el.className = 'slides-note'; el.target = '_blank'; el.rel = 'noopener';
  el.innerHTML = '<i></i><span>スライドを作成中です…</span>';
  const span = el.querySelector('span')!;
  const tick = async () => {
    const s = await statusOf(item.base, fetcher);
    if (s?.stage === 'done') {
      el.classList.add('ready'); el.href = item.base;
      span.textContent = `スライドができました！タップで開く（${s.title || 'スライド'}）`;
      return;
    }
    if (s?.stage === 'error') { el.classList.add('failed'); span.textContent = 'スライドを作れませんでした'; setTimeout(() => el.remove(), 8000); return; }
    const n = s ? stepOf(s.stage) : -1;
    if (n >= 0 && n < 4) span.textContent = `スライドを作成中です…（${WAIT_LINES[n]}）`;
    if (maybeWorking(item)) setTimeout(tick, 3000); else el.remove();
  };
  void tick();
  return el;
}
