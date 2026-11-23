// 画面の言語（日本語・英語）。既定は日本語で、見出しの下のスイッチで英語にできる（2026-11-24 みのるん選定のB案）。
// 選んだ言語はこのブラウザに覚える。切り替えたときは読み込み直すので、各モジュールは読み込み時の言語で文言を決めてよい。
// 検証用に ?lang=en / ?lang=ja で開くと、覚えた言語より優先する（Simulator はタップできないため）。

export type Lang = 'ja' | 'en';
const KEY = 'minorun-lang';

export function readLang(): Lang {
  try {
    const p = new URLSearchParams(location.search).get('lang');
    if (p === 'en' || p === 'ja') return p;
    return localStorage.getItem(KEY) === 'en' ? 'en' : 'ja';
  } catch { return 'ja'; }
}

export const LANG: Lang = readLang();
export const EN = LANG === 'en';
/** 日本語と英語の文言から、いまの言語の方を返す */
export const tr = (ja: string, en: string) => (EN ? en : ja);

/** 言語を覚えて、読み込み直す（?lang= は外す。会話IDはタブに残るので、同じ会話のまま続く） */
export function switchLang(next: Lang) {
  try { localStorage.setItem(KEY, next); } catch { /* 覚えられない端末では、この読み込みの間だけ */ }
  const url = new URL(location.href);
  url.searchParams.delete('lang');
  if (next === 'en') url.searchParams.set('lang', 'en');
  location.replace(url.toString());
}
