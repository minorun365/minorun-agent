// Marp が書き出した HTML（bare テンプレート）を開き、全スライドの余白を測って PNG に焼く。
// 使い方: node render.mjs <deck.html> <出力フォルダ>   標準出力に測定結果の JSON を1行で出す。
// 余白の下限は個人テーマの measure.mjs と同じ（左右72px・下64px・見出しとの間36px）
import { createRequire } from 'module';
import fs from 'fs';
import path from 'path';

const require = createRequire(process.env.MARP_MODULES || '/usr/local/lib/node_modules/@marp-team/marp-cli/node_modules/');
const puppeteer = require('puppeteer-core');
const [html, outDir] = process.argv.slice(2);
const MIN = { side: 72, bottom: 64, head: 36 };

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH || '/usr/lib/chromium/chromium',
  headless: true,
  timeout: 60000,
  dumpio: !!process.env.RENDER_DEBUG,
  // Lambda は /tmp しか書けず、共有メモリも小さい。クラッシュ報告の仕組みは書き込み先が無いと起動を止めるので外す
  args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--no-zygote', '--single-process',
    '--disable-gpu', '--disable-crash-reporter', '--disable-breakpad', '--no-first-run', '--no-default-browser-check',
    '--user-data-dir=/tmp/chrome', '--disk-cache-dir=/tmp/chrome-cache', '--font-render-hinting=none'],
  env: { ...process.env, HOME: '/tmp', XDG_CONFIG_HOME: '/tmp/.config', XDG_CACHE_HOME: '/tmp/.cache' },
});
try {
  const page = await browser.newPage();
  // 記事の中身から作った HTML なので、手元のファイル以外へは一切つながせない
  await page.setRequestInterception(true);
  page.on('request', (r) => (r.url().startsWith('file://') || r.url().startsWith('data:') ? r.continue() : r.abort()));
  // 画面で拡大して見られるので2倍の解像度で焼く（測定は CSS の px なので倍率に左右されない）
  await page.setViewport({ width: 1280, height: 720, deviceScaleFactor: 2 });
  await page.goto('file://' + path.resolve(html), { waitUntil: 'load' });
  await page.addStyleTag({ content: 'body{margin:0;background:#fff} svg[data-marpit-svg]{display:block;width:1280px;height:720px;margin:0}' });
  await page.evaluate(() => document.fonts.ready);
  const rows = await page.evaluate((MIN) => [...document.querySelectorAll('section')].map((sec, n) => {
    const sr = sec.getBoundingClientRect(); const k = 1280 / sr.width;
    const rel = (r) => ({ l: (r.left - sr.left) * k, r: (sr.right - r.right) * k, t: (r.top - sr.top) * k, b: (sr.bottom - r.bottom) * k });
    const h1 = sec.querySelector('h1');
    const kids = [...sec.children].filter((e) => !['H1', 'HEADER', 'FOOTER'].includes(e.tagName));
    const plain = !['top', 'crosshead', 'end'].some((c) => sec.classList.contains(c));
    const flow = plain ? kids : [...sec.children].filter((e) => e.tagName !== 'HEADER');
    const rs = flow.map((e) => rel(e.getBoundingClientRect()));
    if (!rs.length) return { n: n + 1, bad: [] };
    const box = { l: Math.min(...rs.map((r) => r.l)), r: Math.min(...rs.map((r) => r.r)), t: Math.min(...rs.map((r) => r.t)), b: Math.min(...rs.map((r) => r.b)) };
    const head = h1 && kids.length && plain ? box.t - (720 - rel(h1.getBoundingClientRect()).b) : null;
    const over = [...sec.querySelectorAll('*')].filter((e) => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).display !== 'inline').length;
    // 見出しが2行に折れたか（1行の高さの1.6倍を超えたら折れている）
    const wrapped = h1 && plain ? h1.getBoundingClientRect().height * k > parseFloat(getComputedStyle(h1).fontSize) * 1.3 * 1.6 : false;
    const bad = [box.l < MIN.side && '左の余白が足りない', box.r < MIN.side && '右にはみ出している', box.b < MIN.bottom && '下にはみ出している',
      head !== null && head < MIN.head && '見出しと本文が近すぎる', over && '横にはみ出している要素がある', wrapped && '見出しが2行に折れている'].filter(Boolean);
    // カード（流れ・ステップ）の文が、書いた改行のほかに勝手に折り返していないか（「を」だけが次の行に残るような崩れ）
    const cardWrap = [...sec.querySelectorAll('.flow b, .flow span, .steps span')].some((e) => {
      const range = document.createRange(); range.selectNodeContents(e);
      const tops = new Set([...range.getClientRects()].filter((r) => r.width > 0).map((r) => Math.round(r.top / 4)));
      return tops.size > e.querySelectorAll('br').length + 1;
    });
    if (cardWrap) bad.push('カードの文が書いた改行のほかに折り返している（字数を減らす）');
    // 箇条書きが1項目だけのスライドは、中身が薄く、画面の中ほどに1行だけ浮いて見える（2026-11-21 の通しテストで見つけた）
    if (plain && [...sec.querySelectorAll(':scope > ul')].some((u) => u.children.length === 1)) bad.push('箇条書きが1項目だけ（2〜4項目にするか、リード文に入れる）');
    // 右下の挿絵（img.ill）が本文の文字・表・カードに重なっていないか（8px の遊びを見る）
    const ills = [...sec.querySelectorAll('img.ill')].map((e) => e.getBoundingClientRect());
    if (ills.length) {
      // 段落や箇条書きの枠は文字の長さに関係なく横いっぱいに広がるので、枠ではなく文字そのものの範囲で測る
      // （枠で測ると短い行でも必ず重なり判定になり、挿絵を1枚も置けなくなっていた。2026-11-21 の通しテストで見つけた）
      const blocks = [];
      for (const e of sec.querySelectorAll('h1, li, td, th, .steps > div, .flow > div, .prompt, p')) {
        if (e.querySelector('img.ill') || !e.textContent.trim()) continue;
        if (e.matches('.steps > div, .flow > div, .prompt, td, th')) { blocks.push(e.getBoundingClientRect()); continue; }
        const range = document.createRange(); range.selectNodeContents(e);
        blocks.push(...[...range.getClientRects()].filter((q) => q.width > 0 && q.height > 0));
      }
      const hit = ills.some((a) => blocks.some((b) => a.left < b.right - 8 && b.left < a.right - 8 && a.top < b.bottom - 8 && b.top < a.bottom - 8));
      if (hit) bad.push('挿絵が本文に重なっている（文字を減らすか、挿絵を外す）');
    }
    // 表紙の題は2行まで、右の丸い飾り（左から900pxより右）に文字を重ねない
    if (sec.classList.contains('top') && h1) {
      const range = document.createRange(); range.selectNodeContents(h1);
      const rects = [...range.getClientRects()].map(rel);
      const lines = new Set(rects.map((r) => Math.round(r.t / 10))).size;
      if (lines > 2) bad.push('表紙の題が3行以上に折れている');
      if (rects.some((r) => 1280 - r.r > 900)) bad.push('表紙の題が右の飾りに重なっている');
    }
    return { n: n + 1, bad };
  }), MIN);
  fs.mkdirSync(outDir, { recursive: true });
  const svgs = await page.$$('svg[data-marpit-svg]');
  for (let i = 0; i < svgs.length; i++) {
    await svgs[i].screenshot({ path: path.join(outDir, `${String(i + 1).padStart(2, '0')}.png`) });
  }
  process.stdout.write(JSON.stringify({ pages: svgs.length, ng: rows.filter((r) => r.bad.length) }) + '\n');
} finally {
  await browser.close();
}
