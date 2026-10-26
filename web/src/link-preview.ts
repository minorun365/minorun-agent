import type { Surprise } from './surprises';

export interface LinkPreview { url: string; image: string; title: string; label: string }
const paths: Record<string, RegExp> = {
  'qiita.com': /^\/minorun365(?:\/items\/[a-f0-9]+)?\/?$/,
  'speakerdeck.com': /^\/minorun365(?:\/[a-zA-Z0-9_-]+)?\/?$/,
  'github.com': /^\/minorun365(?:\/[a-zA-Z0-9_.-]+)?\/?$/,
  'minoruonda.com': /^\/$/, 'www.minoruonda.com': /^\/$/,
};
const titles: Record<string, string> = { 'qiita.com': '私の記事', 'speakerdeck.com': '私の登壇資料', 'github.com': '公開コード' };
// KAGの公式キャラクター「カグカグ」の話をした回答には、紹介記事のサムネイルを貼る（みのるん指示）。
const KAGKAG = {
  url: 'https://note.kddi-agdc.com/n/n2a781e331dd0',
  image: 'https://assets.st-note.com/production/uploads/images/143826704/rectangle_large_type_2_7b91c9650159330829e4a87cc5e7fbdb.jpeg?fit=bounds&quality=85&width=1280',
  title: 'KAGのゆるキャラ『カグカグ』を紹介します！',
};
export function characterGift(text: string): Surprise | undefined {
  if (!/カグカグ|kagkag/i.test(text)) return;
  return { id: 'preview:kagkag', title: 'KAGの公式キャラ', text: KAGKAG.title, image: KAGKAG.image,
    source: KAGKAG.url, sourceLabel: '記事を読む', preview: true, pose: 'pc' };
}
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
