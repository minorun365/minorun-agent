// XのiOSアプリは「Twitter for iPhone/iPad」を名乗る。
// 高さだけでは、ツールバーがあるEdgeとXのアプリ内ブラウザを区別できない。
const X_IN_APP = /\bTwitter for (?:iPhone|iPad)\b/i;

// XのiOSアプリ内ブラウザは、キーボードの上に浮く操作バー（∧ ∨ ✓）を見えている高さに含めない。
// 見えている範囲の下端に置くと入力欄が操作バーの下に潜るので、その分だけ持ち上げる（11/26 みのるんのスクショ）。
export const X_KEYBOARD_BAR = 64;
export function keyboardBarLift(keyboardShown: boolean, userAgent: string): number {
  return keyboardShown && X_IN_APP.test(userAgent) ? X_KEYBOARD_BAR : 0;
}

export function useSideLayout(width: number, height: number, composing: boolean, userAgent: string): boolean {
  if (composing || height >= 680) return false;
  const xInApp = X_IN_APP.test(userAgent);
  return width > height || xInApp;
}
