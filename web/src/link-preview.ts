import type { Surprise } from './surprises';

export interface LinkPreview { url: string; image: string; title: string; label: string }
const paths: Record<string, RegExp> = {
  'qiita.com': /^\/minorun365(?:\/items\/[a-f0-9]+)?\/?$/,
  'speakerdeck.com': /^\/minorun365(?:\/[a-zA-Z0-9_-]+)?\/?$/,
  'github.com': /^\/minorun365(?:\/[a-zA-Z0-9_.-]+)?\/?$/,
  'minoruonda.com': /^\/$/, 'www.minoruonda.com': /^\/$/,
};
const titles: Record<string, string> = { 'qiita.com': '私の記事', 'speakerdeck.com': '私の登壇資料', 'github.com': '公開コード' };
export function previewGift(preview: LinkPreview, sources: string[]): Surprise | undefined {
  try {
    const url = new URL(preview.url), image = new URL(preview.image);
    if (url.protocol !== 'https:' || url.username || url.password || url.port || !paths[url.hostname]?.test(url.pathname)) return;
    if (image.protocol !== 'https:' || image.username || image.password || !preview.title.trim()) return;
    // 最終的に画面へ出した出典だけを対象にする。追加調査で消えたリンクは出さない。
    if (!sources.some(source => {
      const cited = new URL(source); cited.search = ''; cited.hash = '';
      return cited.href === url.href;
    })) return;
    return { id: 'preview:' + url.href, title: titles[url.hostname] ?? '私のホームページ', text: preview.title,
      image: image.href, source: url.href, sourceLabel: preview.label, preview: true, pose: 'pc' };
  } catch { return; }
}
