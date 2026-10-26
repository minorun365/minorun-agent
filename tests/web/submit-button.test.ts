import assert from 'node:assert/strict';
import test from 'node:test';
import { bindSubmitButton } from '../../web/src/submit-button';

const finger = (x = 20, y = 20, identifier = 1) => ({ clientX: x, clientY: y, identifier });
function setup() {
  const button = new EventTarget();
  let sent = 0;
  bindSubmitButton(button as unknown as HTMLElement, () => { sent++; });
  return {
    count: () => sent,
    fire(type: string, values: Record<string, unknown> = {}) {
      const event = new Event(type, { cancelable: true });
      Object.assign(event, values);
      button.dispatchEvent(event);
      return event;
    },
  };
}
test('1タップで指を離した時に送り、フォーカス移動と互換clickの二重送信を防ぐ', () => {
  const s = setup();
  assert.ok(s.fire('touchstart', { touches: [finger()] }).defaultPrevented);
  assert.equal(s.count(), 0);
  assert.ok(s.fire('touchend', { touches: [], changedTouches: [finger()] }).defaultPrevented);
  assert.equal(s.count(), 1);
  s.fire('click', { detail: 1 });
  assert.equal(s.count(), 1);
});
test('ドラッグして戻った場合とタッチ中断では送らない', () => {
  for (const cancel of ['touchmove', 'touchcancel']) {
    const s = setup();
    s.fire('touchstart', { touches: [finger()] });
    s.fire(cancel, { touches: [finger(50)] });
    s.fire('touchend', { touches: [], changedTouches: [finger()] });
    s.fire('click', { detail: 1 });
    assert.equal(s.count(), 0);
  }
});
test('複数の指と移動通知のない大きな指の移動では送らない', () => {
  const s = setup();
  s.fire('touchstart', { touches: [finger(), finger(20, 20, 2)] });
  s.fire('touchend', { touches: [], changedTouches: [finger()] });
  assert.equal(s.count(), 0);
  s.fire('touchstart', { touches: [finger()] });
  s.fire('touchend', { touches: [], changedTouches: [finger(100)] });
  assert.equal(s.count(), 0);
});
test('タッチの後もキーボード、支援技術、マウスで送信できる', () => {
  const s = setup();
  s.fire('touchstart', { touches: [finger()] });
  s.fire('touchcancel');
  s.fire('click', { detail: 0 });
  assert.equal(s.count(), 1);
  assert.ok(s.fire('pointerdown', { pointerType: 'mouse', isPrimary: true, button: 0 }).defaultPrevented);
  s.fire('click', { detail: 1 });
  assert.equal(s.count(), 2);
});
