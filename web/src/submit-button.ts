// iOSではclickを待つ間に入力欄がblurし、キーボードと送信ボタンが移動することがある。
// タッチの既定動作を止め、指を離した時点で送信する。ドラッグや中断では送信しない。
export function bindSubmitButton(button: HTMLElement, submit: () => void) {
  let touch: { id: number; x: number; y: number } | undefined;
  let ignoreTouchClick = false;
  button.addEventListener('pointerdown', (event) => {
    if (event.isPrimary && event.button === 0) event.preventDefault();
    if (event.pointerType === 'mouse') ignoreTouchClick = false;
  });
  button.addEventListener('touchstart', (event) => {
    event.preventDefault();
    ignoreTouchClick = true;
    const first = event.touches[0];
    touch = event.touches.length === 1 ? { id: first.identifier, x: first.clientX, y: first.clientY } : undefined;
  }, { passive: false });
  button.addEventListener('touchmove', (event) => {
    const current = Array.from(event.touches).find((item) => item.identifier === touch?.id);
    if (!current || !touch || Math.hypot(current.clientX - touch.x, current.clientY - touch.y) > 12) touch = undefined;
  }, { passive: true });
  button.addEventListener('touchcancel', () => { touch = undefined; });
  button.addEventListener('touchend', (event) => {
    event.preventDefault();
    const start = touch;
    touch = undefined;
    const end = Array.from(event.changedTouches).find((item) => item.identifier === start?.id);
    if (start && end && event.touches.length === 0 && Math.hypot(end.clientX - start.x, end.clientY - start.y) <= 12) submit();
  }, { passive: false });
  // キーボード・支援技術のclickはそのまま扱い、タッチ後の互換clickだけを除く。
  button.addEventListener('click', (event) => {
    if (!ignoreTouchClick || event.detail === 0) submit();
  });
}
