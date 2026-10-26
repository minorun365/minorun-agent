// 最初に出す質問の例。自動生成する knowledge/starters.json と、選定した starters-curated.json を
// ビルド時に読み込む。ナレッジベースの外のツールや最新情報を調べる問いはここで管理する。
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
export const GROUPS: string[][] = [TOOLS, ...DISCOVERY, ...Object.values(TOPIC_GROUPS)]
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

// 別々の話題から1問ずつ n 問選ぶ。exclude（直前に聞いた問いなど）は除く
export function pickStarters(n: number, exclude: string[] = []): string[] {
  const out: string[] = [];
  for (const g of shuffle(GROUPS)) {
    const q = shuffle(g).find((s) => !exclude.includes(s));
    if (q) out.push(q);
    if (out.length >= n) break;
  }
  return out;
}
