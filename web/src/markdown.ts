// 本文の整形：先に HTML を無害化してから、太字・箇条書き・出典ラベルだけを付ける ----
export const esc = (t: string) => t.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
// 出典は [ラベル](URL) で届く。URL だけが届いたときは、ドメインから短いラベルを作る
const SITES: [RegExp, string][] = [
  [/(^|\.)aws\.amazon\.com$|(^|\.)amazon\.com$|awsstatic|(^|\.)aws$/, 'AWS公式'], [/(^|\.)(x|twitter)\.com$/, 'X'],
  [/qiita\.com$/, 'Qiita'], [/zenn\.dev$/, 'Zenn'], [/note\.com$/, 'note'], [/classmethod\.jp$/, 'DevelopersIO'],
  [/speakerdeck\.com$/, 'Speaker Deck'], [/docswell\.com$/, 'Docswell'], [/connpass\.com$/, 'connpass'],
  [/hatenablog|hatena\.ne\.jp$/, 'はてなブログ'], [/github\.com$/, 'GitHub'], [/youtube\.com$|youtu\.be$/, 'YouTube'],
];
function siteLabel(url: string) {
  let host = '';
  try { host = new URL(url.replace(/&amp;/g, '&')).hostname.replace(/^www\./, ''); } catch { return 'リンク'; }
  return SITES.find(([re]) => re.test(host))?.[1] ?? host.split('.').slice(-2, -1)[0] ?? host;
}
const srcTag = (url: string, label: string) => `<a class="src" href="${url}" target="_blank" rel="noopener">${label}</a>`;
// モデルが付けたラベルが別のサイト名（URL は DevelopersIO なのに「Speaker Deck」など）や、
// 言い訳を含む長い名前なら、URL のドメインから作り直す
const KNOWN = new Map(SITES.map(([, l]) => [l.replace(/\s/g, '').toLowerCase(), l]));
function srcLabel(label: string, url: string) {
  if (label.length > 14 || /ではなく|じゃなく/.test(label)) return siteLabel(url);
  const named = KNOWN.get(label.replace(/\s/g, '').toLowerCase());
  if (!named) return label;
  // AWS のイベント・Builder Center など aws を含むドメインは「AWS公式」のままでよい
  if (named === 'AWS公式' && /aws/i.test(url)) return label;
  const actual = siteLabel(url);
  return named === actual ? label : actual;
}
export function inline(t: string) {
  const keep: string[] = [];
  const hold = (h: string) => `\u0000${keep.push(h) - 1}\u0000`;
  return esc(t)
    .replace(/\[([^\]\n]{1,30})\]\((https?:\/\/[^\s)]+)\)/g, (_, l: string, u: string) => hold(srcTag(u, srcLabel(l, u))))
    .replace(/https?:\/\/[^\s<>()（）「」、。*`\u0000]+/g, (u) => hold(srcTag(u, siteLabel(u))))
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    // 「出典:」などの前置きは要らない（ラベルだけを出す）
    .replace(/(出典|参考|ソース)[:：]\s*(?=\u0000)/g, '')
    .replace(/\u0000(\d+)\u0000/g, (_, i: string) => keep[+i]);
}
// 出典だけの行（「出典」の見出しや、リンクだけの箇条書き）は独立させず、直前の文の末尾へ寄せる
const LINK = /\[[^\]\n]{1,30}\]\(https?:\/\/[^\s)]+\)|https?:\/\/[^\s<>()（）「」、。*`]+/g;
function inlineSources(t: string) {
  const out: string[] = [];
  for (const line of t.split('\n')) {
    const links = line.match(LINK) ?? [];
    const rest = line.replace(LINK, '').replace(/^[\s\-・*#]*|[\s、,・]*$/g, '').replace(/^\**(出典|参考|ソース|参考リンク)\**[:：]?\**$/, '');
    if (/^\**(出典|参考|ソース|参考リンク)\**[:：]?\**$/.test(line.replace(/^[\s#\-*]+/, '').trim())) continue;
    if (links.length && !rest) {
      // 直前の空でない行に付ける（空行は飛ばす）
      let i = out.length - 1;
      while (i >= 0 && !out[i].trim()) i--;
      if (i >= 0) { out[i] += ' ' + links.join(' '); continue; }
    }
    out.push(line);
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trimEnd();
}
export function md(t: string) {
  t = inlineSources(t);
  if ((t.match(/\*\*/g) || []).length % 2) t += '**';
  // 1行ずつ読む。「- 」「1. 」で始まる行は新しい項目、続く行はその項目の続き、空行で区切る
  const out: string[] = [];
  let para: string[] = [], list: { tag: 'ul' | 'ol'; items: string[] } | null = null;
  const flushPara = () => { if (para.length) out.push('<p>' + para.join('<br>') + '</p>'); para = []; };
  const flushList = () => { if (list) out.push(`<${list.tag}>` + list.items.map((i) => `<li>${i}</li>`).join('') + `</${list.tag}>`); list = null; };
  for (const rawLine of t.split('\n')) {
    const line = rawLine.trim();
    const bullet = line.match(/^[-・*]\s+(.*)$/), num = line.match(/^\d+[.．)]\s+(.*)$/);
    if (!line) { flushPara(); flushList(); continue; }
    if (bullet || num) {
      const tag = bullet ? 'ul' : 'ol';
      flushPara();
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push(inline((bullet ?? num)![1]));
    } else if (list) {
      list.items[list.items.length - 1] += '<br>' + inline(line);
    } else {
      para.push(inline(line.replace(/^#+\s*/, '')));
    }
  }
  flushPara(); flushList();
  return out.join('');
}

