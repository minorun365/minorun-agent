// 普通の取得で本文を読めなかったページを、AgentCore Browser（AWS 側の使い捨てブラウザ）で開いて本文を抜く。
// 使い方: WS_URL=... WS_HEADERS='{"Authorization":...}' node read_browser.mjs <URL>   標準出力に {url, title, text} を1行で出す
import { createRequire } from 'module';

const require = createRequire(process.env.MARP_MODULES || '/usr/local/lib/node_modules/@marp-team/marp-cli/node_modules/');
const puppeteer = require('puppeteer-core');
const [target] = process.argv.slice(2);
const browser = await puppeteer.connect({ browserWSEndpoint: process.env.WS_URL, headers: JSON.parse(process.env.WS_HEADERS || '{}'), protocolTimeout: 60000 });
try {
  const page = await browser.newPage();
  await page.goto(target, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForNetworkIdle({ idleTime: 800, timeout: 8000 }).catch(() => {});
  const out = await page.evaluate(() => {
    // 記事の本体らしい場所から読む。無ければ本文全体から、ナビやフッターを除いて読む
    const pick = document.querySelector('article') || document.querySelector('main') || document.body;
    const clone = pick.cloneNode(true);
    clone.querySelectorAll('nav, header, footer, aside, script, style, noscript, form, [role=navigation]').forEach((e) => e.remove());
    const text = clone.innerText.replace(/\n{3,}/g, '\n\n').trim();
    return { url: location.href, title: document.title, text };
  });
  process.stdout.write(JSON.stringify({ ...out, text: out.text.slice(0, 12000) }) + '\n');
} finally {
  await browser.disconnect();
}
