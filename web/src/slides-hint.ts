// スライド作成を見つけてもらうための小さな入口（2026-11-15 みのるん指示）。常設のボタンは足さず、
// ①入力欄にURLが入ったら「この記事をスライドにする」の札 ②入力欄の薄い字 ③質問候補にときどき1つ、で知らせる。

import { EN, tr } from './i18n';

// 質問を主役にし、資料の添付などは使ううちに気づくサブ機能として薄い字には並べない（2026-11-26 みのるん指示）
export const PLACEHOLDER = tr('質問、または記事のURL', 'Ask me anything');
export const SLIDES_PLACEHOLDER = '記事のURLを入力';
export const SLIDES_STARTER = 'URLをスライドに要約して！';
export const SLIDES_REQUEST = 'この記事をスライドにして';
/** 質問候補に混ぜる割合（3〜4回に1回） */
export const STARTER_RATE = 0.3;

const URL_RE = /https?:\/\/[^\s<>"'）」]+\.[^\s<>"'）」]+/;

/** 入力にURLがあり、まだスライドを頼んでいないときに札を出す */
export function wantsSlidesHint(text: string): boolean {
  // スライドは日本語で作るので、英語の画面では入口を出さない
  if (EN) return false;
  return URL_RE.test(text) && !/スライド|資料|パワポ/.test(text);
}

/** 札を押したとき・URL待ちで送ったときの文。URLの後ろに頼み方を足す */
export function slidesRequest(text: string): string {
  const t = text.trim();
  return /スライド|資料|パワポ/.test(t) ? t : `${t}\n${SLIDES_REQUEST}`;
}

/** 質問候補へ、ときどき SLIDES_STARTER を1つ混ぜる（最後の1つと入れ替える）。ログインしていない人にも出し、押せばそのまま頼める */
export function withSlidesStarter(list: string[], rand: () => number = Math.random): string[] {
  if (EN || !list.length || list.includes(SLIDES_STARTER) || rand() >= STARTER_RATE) return list;
  return [...list.slice(0, -1), SLIDES_STARTER];
}

// 回答のあとの札（2026-11-15 みのるん決定）。検索で調べた説明の回答のあとに、次の質問の候補と並べて1つだけ出す。常設のボタンは足さない。
export const OFFER_CHIP = 'この回答をスライドに！';
export const OFFER_REQUEST = 'この回答をスライドにして';

/** 候補の先頭に札を置く（候補は3つまでのまま。最後の1つと入れ替える） */
export function withOfferChip(list: string[]): string[] {
  if (EN) return list;
  const rest = list.filter((t) => t !== OFFER_CHIP && t !== SLIDES_STARTER);
  const size = Math.max(1, Math.min(list.length, 3));
  return [OFFER_CHIP, ...rest.slice(0, size - 1)];
}
