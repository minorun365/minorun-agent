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
