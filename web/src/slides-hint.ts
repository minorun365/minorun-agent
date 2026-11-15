// スライド作成を見つけてもらうための小さな入口（2026-11-15 みのるん指示）。常設のボタンは足さず、
// ①入力欄にURLが入ったら「この記事をスライドにする」の札 ②入力欄の薄い字 ③質問候補にときどき1つ、で知らせる。

export const PLACEHOLDER = '質問、または記事のURL';
export const SLIDES_PLACEHOLDER = '記事のURLを入力';
export const SLIDES_STARTER = 'URLをスライドに要約して！';
export const SLIDES_REQUEST = 'この記事をスライドにして';
/** 質問候補に混ぜる割合（3〜4回に1回） */
export const STARTER_RATE = 0.3;

const URL_RE = /https?:\/\/[^\s<>"'）」]+\.[^\s<>"'）」]+/;

/** 入力にURLがあり、まだスライドを頼んでいないときに札を出す */
export function wantsSlidesHint(text: string): boolean {
  return URL_RE.test(text) && !/スライド|資料|パワポ/.test(text);
}

/** 札を押したとき・URL待ちで送ったときの文。URLの後ろに頼み方を足す */
export function slidesRequest(text: string): string {
  const t = text.trim();
  return /スライド|資料|パワポ/.test(t) ? t : `${t}\n${SLIDES_REQUEST}`;
}

/** 質問候補へ、ときどき SLIDES_STARTER を1つ混ぜる（最後の1つと入れ替える）。ログイン前の人にも出し、押したらログインを案内する */
export function withSlidesStarter(list: string[], rand: () => number = Math.random): string[] {
  if (!list.length || list.includes(SLIDES_STARTER) || rand() >= STARTER_RATE) return list;
  return [...list.slice(0, -1), SLIDES_STARTER];
}
