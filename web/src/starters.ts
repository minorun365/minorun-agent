// 最初に出す質問の例。自動生成する knowledge/starters.json と、選定した starters-curated.json を
// ビルド時に読み込む。ナレッジベースの外のツールや最新情報を調べる問いはここで管理する。
import { EN } from './i18n';
import byTopic from '../../knowledge/starters.json';
import curated from '../../knowledge/starters-curated.json';

const TOOLS = [
  're:Inventまであと何日？',
  'AgentCoreのセッションはいつある？',
  '会場同士はどのくらい離れてる？',
];

// 問いの切り口を分け、一般的な準備の問いに埋もれず選ばれるようにする。
const DISCOVERY = [
  [
    '最近のAWSで注目の更新は？',
    'Bedrockは最近何が変わった？',
    'AgentCoreの最新機能は？',
    '最近の更新を学べるセッションは？',
    '生成AI以外で注目のAWS更新は？',
  ],
  [
    '初参加におすすめのJamは？',
    'GameDayでおすすめは？',
    'AIのチョークトークで狙い目は？',
    '設計を議論できるセッションは？',
    '手を動かして学べるセッションは？',
  ],
  [
    '月曜のお昼前後で狙い目のセッションは？',
    '火曜の昼前に聞けるセッションは？',
    '水曜の昼食後におすすめのセッションは？',
    '木曜のお昼前後で狙い目のセッションは？',
    '金曜の午前におすすめのセッションは？',
  ],
];

// 話題ごとの束。話題を先に選ぶので、事実の少ない話題（新しく足した話題）も同じ確率で出る
// 手で選んだ問いは自動生成と分けて保持し、同じ話題へ合流する。
// 生成器がまだ扱っていない話題も選択対象にする。
const generated: Record<string, { questions: string[] }> = byTopic;
const selected: Record<string, string[]> = curated;
export const TOPIC_GROUPS = Object.fromEntries(
  [...new Set([...Object.keys(generated), ...Object.keys(selected)])].map((topic) => [
    topic, [...new Set([...(generated[topic]?.questions ?? []), ...(selected[topic] ?? [])])],
  ]),
);
// 英語の画面で出す問い。自動生成の束は日本語なので使わず、ここで話題ごとに持つ（2026-11-24）
export const GROUPS_EN: string[][] = [
  ['How many days until re:Invent?', 'When are the AgentCore sessions?', 'How far apart are the venues?'],
  ["What's new in AWS lately?", 'What changed in Bedrock recently?', "What are AgentCore's latest features?"],
  ['Which Jam is good for first-timers?', 'Any GameDay you recommend?', 'Which hands-on sessions should I try?'],
  ['Good sessions on Wednesday afternoon?', 'Any sessions worth catching Monday morning?'],
  ['When are the keynotes?', 'How do I pick up my badge?'],
  ['How do I get around between venues?', 'What should I pack for Las Vegas?'],
  ['Where can I grab a quick lunch?', 'Any tips for eating in Las Vegas?'],
  ['How can I meet Minorun at re:Invent?', 'What community events are there?'],
  ['How was this app built?'],
];
export const GROUPS: string[][] = EN ? GROUPS_EN : [TOOLS, ...DISCOVERY, ...Object.values(TOPIC_GROUPS)]
  .filter((g) => g.length);
export const STARTERS = GROUPS.flat();

// ランダムな比較関数でsortすると先頭側に偏るため、全位置を等確率で交換する。
const shuffle = <T,>(list: T[]) => {
  const out = [...list];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
};

// 技術の話題。話題を均等に選ぶと技術の束が50束中8束しかなく、初回の3問に技術の問いが
// 1つも無いことが約6割あった（2026-11-24 みのるん指摘）。3問のうち1問は必ずここから選ぶ。
export const TECH_TOPICS = [
  'tech-agents', 'tech-bedrock', 'tech-ai-work', 'tech-output', 'kag-tech',
  'recent-updates', 'history-announcements', 'about-this-app',
];
const TECH_GROUPS: string[][] = EN ? [] : [DISCOVERY[0], ...TECH_TOPICS.map((t) => TOPIC_GROUPS[t] ?? [])]
  .filter((g) => g.length);

// 特に推したい催し（KAG の re:Cap）の問い。開催日まで、初回の3問の2回に1回はここから1問を入れる
// （2026-11-24 みのるん指示「結構プッシュしたいイベント」）。問いは事前回答のある after-recap の用意した質問
export const PUSH_QUESTIONS = [
  'KAGのre:Capでは何を話す？', 'KAGのre:Capは誰でも参加できる？',
  'KAGのre:Capに懇親会はある？', 'みのるんの帰国後の登壇は？',
];
export const PUSH_UNTIL = Date.parse('2026-12-17T21:00:00+09:00');
export const PUSH_RATE = 0.5;

// 別々の話題から1問ずつ n 問選ぶ。exclude（直前に聞いた問いなど）は除く。
// 2問以上のときは、推したい催しの問い（期間中・確率 PUSH_RATE）と技術の束から1問ずつを先に選んでから
// 残りを全体から選び、並びは混ぜる
export function pickStarters(n: number, exclude: string[] = [], now = Date.now()): string[] {
  const out: string[] = [];
  const used = new Set<string[]>();
  if (n >= 2 && !EN && now < PUSH_UNTIL && Math.random() < PUSH_RATE) {
    const q = shuffle(PUSH_QUESTIONS).find((s) => !exclude.includes(s) && STARTERS.includes(s));
    const g = q ? GROUPS.find((x) => x.includes(q)) : undefined;
    if (q && g) { out.push(q); used.add(g); }
  }
  const take = (groups: string[][]) => {
    for (const g of shuffle(groups)) {
      if (used.has(g)) continue;
      const q = shuffle(g).find((s) => !exclude.includes(s));
      if (q) { out.push(q); used.add(g); }
      if (out.length >= n) break;
    }
  };
  if (n >= 2 && TECH_GROUPS.length) {
    const tech = shuffle(TECH_GROUPS);
    take([tech[0]]);
    if (!out.length) take(tech.slice(1, 2));
  }
  take(GROUPS);
  return shuffle(out);
}
