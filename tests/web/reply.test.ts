import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

// 回答への「返信する」と返信先の帯（2026-11-23 みのるん選定のA案）。
// 前の回答に続けて聞けると気づかない人がいるため、吹き出しの下端と入力欄の上で伝える
const main = readFileSync(new URL('../../web/src/main.ts', import.meta.url), 'utf8');
const html = readFileSync(new URL('../../web/index.html', import.meta.url), 'utf8');
const css = readFileSync(new URL('../../web/src/style.css', import.meta.url), 'utf8');

test('答えた吹き出しの下端の行に、シェアより前に「返信する」を置き、押すと入力欄を開く', () => {
  assert.match(main, /row\.append\(replyButton\(\), share\)/);
  assert.match(main, /function replyButton\(\)[\s\S]*?b\.onclick = openCompose;/);
});

test('ログインの往復で描き直した回答にも「返信する」と返信先を出す', () => {
  const restore = main.slice(main.indexOf('function restoreCarryover'), main.indexOf('function resumeSlides'));
  assert.match(restore, /replyButton\(\)/);
  assert.match(restore, /setReplyTo\(turn\.q\)/);
});

test('返信先の帯は答えたあとだけ。失敗した回では消す', () => {
  assert.match(main, /setReplyTo\(!failed && raw\.trim\(\) \? text : null\)/);
  assert.match(html, /id="replyTo" hidden/);
});

test('スライドのURL待ち・画像の見本・スライドの札が出ている間は、帯を隠して高さにも数えない', () => {
  assert.match(main, /const reply = comp && !replyTo\.hidden && !slidesMode && shot\.hidden && chip\.hidden && docChip\.hidden;/);
  assert.match(main, /\(reply \? replyTo\.offsetHeight \+ 14 : 0\)/);
});

test('矢印は絵文字にしない（iPhone で青い四角の絵文字になる）', () => {
  const strip = html.slice(html.indexOf('id="replyTo"'), html.indexOf('</div>', html.indexOf('id="replyTo"')));
  assert.doesNotMatch(strip, /↩/);
  assert.match(css, /\.replyto\.on\{opacity:1/);
});
