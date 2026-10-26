// XのiOSアプリは「Twitter for iPhone/iPad」を名乗る。
// 高さだけでは、ツールバーがあるEdgeとXのアプリ内ブラウザを区別できない。
export function useSideLayout(width: number, height: number, composing: boolean, userAgent: string): boolean {
  if (composing || height >= 680) return false;
  const xInApp = /\bTwitter for (?:iPhone|iPad)\b/i.test(userAgent);
  return width > height || xInApp;
}
