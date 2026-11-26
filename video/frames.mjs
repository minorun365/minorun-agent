// 台本（story.json）の1行ごとに frame.html を描いて PNG に焼く。
// 使い方: node frames.mjs <story.json> <frame.html> <出力フォルダ>   標準出力に焼いた枚数の JSON を1行で出す。
import { createRequire } from 'module';
import fs from 'fs';
import path from 'path';

const require = createRequire(process.env.MARP_MODULES || '/usr/local/lib/node_modules/@marp-team/marp-cli/node_modules/');
const puppeteer = require('puppeteer-core');
const [storyPath, framePath, outDir] = process.argv.slice(2);
const story = JSON.parse(fs.readFileSync(storyPath, 'utf8'));
fs.mkdirSync(outDir, { recursive: true });

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH || '/usr/lib/chromium/chromium',
  headless: true,
  timeout: 120000,
  args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--no-zygote', '--single-process',
    '--disable-gpu', '--disable-crash-reporter', '--disable-breakpad', '--no-first-run', '--no-default-browser-check',
    '--user-data-dir=/tmp/chrome-video', '--font-render-hinting=none', '--allow-file-access-from-files'],
  env: { ...process.env, HOME: '/tmp', XDG_CONFIG_HOME: '/tmp/.config', XDG_CACHE_HOME: '/tmp/.cache' },
});
let n = 0;
try {
  const page = await browser.newPage();
  // 利用者の資料から作った台本なので、手元のファイル以外へは一切つながせない
  await page.setRequestInterception(true);
  page.on('request', (r) => (r.url().startsWith('file://') || r.url().startsWith('data:') ? r.continue() : r.abort()));
  await page.setViewport({ width: 1280, height: 720, deviceScaleFactor: 1.5 });
  await page.goto('file://' + path.resolve(framePath), { waitUntil: 'load' });
  await page.evaluate((s) => { window.STORY = s; }, story);
  await page.evaluate(() => document.fonts.ready);
  const total = story.scenes.reduce((a, sc) => a + sc.lines.reduce((b, l) => b + (l.sec || 1), 0), 0);
  let at = 0;
  for (let si = 0; si < story.scenes.length; si++) {
    for (let li = 0; li < story.scenes[si].lines.length; li++) {
      await page.evaluate((a, b, c) => window.show(a, b, c), si, li, at / total);
      at += story.scenes[si].lines[li].sec || 1;
      await page.screenshot({ path: path.join(outDir, `f${String(n).padStart(3, '0')}.png`) });
      n++;
    }
  }
} finally {
  await browser.close();
}
console.log(JSON.stringify({ frames: n }));
