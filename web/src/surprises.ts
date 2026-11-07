// 回答のおまけ。本人の体験は公開資料で確認した文だけを使う。
import { bookGifts, githubGifts, qiitaGifts } from './topic-gifts';
export interface Surprise { id: string; title: string; text: string; english?: string; source?: string; sourceLabel?: string; image?: string; preview?: boolean; cover?: boolean; pose: 'idea' | 'pc' }
const slides = 'https://speakerdeck.com/minorun365/';
export const gifts: Record<string, Surprise> = {
  'support-github': { id: 'support-github', title: '応援してね', text: 'GitHub Sponsors で応援してもらえるとうれしいです！', source: 'https://github.com/sponsors/minorun365', pose: 'pc' },
  'support-x': { id: 'support-x', title: '応援してね', text: 'X のサブスクで応援してもらえるとうれしいです！', source: 'https://x.com/minorun365/subs', pose: 'pc' },
  experience: { id: 'experience', title: '私の失敗談', text: '2024年、ワーナーのキーノートにレシーバーなしで臨んだら、英語が聞き取れませんでした。', source: slides + 'mi-zhao-bedrockergare-invent-2024deguo-gosita5ri-jian-woshao-jie', pose: 'idea' },
  english: { id: 'english', title: '通ですね！', text: '短いデモを見せてもらえますか？', english: 'Could you show me a quick demo?', pose: 'idea' },
  slide: { id: 'slide', title: '2025年の登壇資料より', text: 're:Invent エージェントの開発秘話', image: 'https://files.speakerdeck.com/presentations/e51d54203aca4064a54d1a33be539437/slide_0.jpg', source: slides + 'awsnoxin-ji-neng-wohuruhuo-yong-sita-re-inventeziento-kai-fa-mi-hua', pose: 'pc' },
  thanks: { id: 'thanks', title: 'うれしいです！', text: 'ありがとうございます。', pose: 'pc' },
};
// 話題に合ったときだけ貼る本人の登壇資料。表紙は本人のSpeaker Deckのもの（11/7（土）に表紙を確認）。
const deck = (id: string, year: number, text: string, slug: string, image: string): Surprise =>
  ({ id, title: `${year}年の登壇資料より`, text, image: 'https://files.speakerdeck.com/presentations/' + image, source: slides + slug, pose: 'pc' });
export const decks: { gift: Surprise; when: RegExp }[] = [
  { when: /過ごし|1日の流れ|スケジュール|初参加|初めて|ラスベガス|ベガス|何日|現地の様子/,
    gift: deck('deck-5days', 2024, 'Bedrockerがre:Invent 2024で過ごした5日間', 'mi-zhao-bedrockergare-invent-2024deguo-gosita5ri-jian-woshao-jie', '39a681821f7845e4a55bc73613ddf1df/slide_0_1742430611.jpg') },
  { when: /発表|アップデート|アプデ|新機能|新サービス|キーノート|基調講演|keynote/i,
    gift: deck('deck-2025-recap', 2025, 're:Invent 2025速報 AIビルダー向けアップデート', 'aws-re-invent-2025su-bao-aibirudaxiang-keatupudetowomatometejie-shuo', '7d4467dcec6942c88ca8948b4e90340d/slide_0.jpg') },
  { when: /予選落ち|re:Invent ?の?(前|直前)|事前の?発表|プレ.?インベント|pre:?invent/i,
    gift: deck('deck-pre-2024', 2024, 're:Invent 2024 予選落ちのBedrockアプデ', 'aws-re-invent-2024-yu-xuan-luo-tinobedrockapudewomatometejie-shuo', 'da1aab3c8d8b435a8234f679e72e8b60/slide_0_1742430605.jpg') },
  { when: /登壇|スピーカー|講演|セッションで話/,
    gift: deck('deck-gbl206', 2024, 're:Invent 2024で登壇したセッション（GBL206-JA）', 'aws-re-invent-2024deng-tan-zi-liao-gbl206-ja-unleashing-the-power-of-generative-ai-on-aws-for-your-business', '55d148ce573e450ea112114a4d384e62/slide_0_1742430609.jpg') },
  { when: /RAG|ナレッジベース|Knowledge ?Bases?/i,
    gift: deck('deck-multi-rag', 2024, 're:Inventの新機能でマルチRAGエージェントを作った話', 're-inventdefa-biao-sareta-bedrocknoxin-ji-neng-wose-shi-tutemarutiragezientonikuraudoxuan-ding-sasetemitajian', '909829d1f39145ea9b99ca20f151f333/slide_0_1742430622.jpg') },
  { when: /Hero|ヒーロー|みのるん(さん)?(って|は|の経歴)|経歴|キャリア|社外/i,
    gift: deck('deck-hero', 2024, 'なんで、私がAWS Heroに!?', 'nande-si-gaaws-heroni-she-wai-noguang-ishi-jie-ni-bu-ta-michu-sou', 'eb8b715472fc4babb32509150d425f84/slide_0_1742430558.jpg') },
  { when: /AgentCore/i,
    gift: deck('deck-agentcore', 2026, '2026年春のAgentCoreアプデ 細かいやつ全部まとめ', '2026nian-chun-noagentcoreapude-xi-kaiyatuquan-bu-matome', 'aae68244d29e46da9c6d0c3a3b2b8170/slide_0.jpg') },
  { when: /Strands/i,
    gift: deck('deck-strands', 2025, 'StrandsとBedrock AgentCoreでAIエージェント構築に入門', 'madajian-nihe-u-strandstobedrock-agentcoredeaiezientogou-zhu-niru-men-siyou', 'dfd9a2dd8dcf48588df16338fbcb954c/slide_0.jpg') },
  { when: /Claude ?Code|クロード/i,
    gift: deck('deck-claude-code', 2026, 'Claude Codeを「使うほど育つ」AI秘書にするノウハウ', 'claude-code-o-tsukau-hodo-sodatsu-ai-hisho-ni-suru-nouhau', '6913c0168dab48018a923872ae9c9a9f/slide_0.jpg') },
  { when: /A2A|エージェント(同士|間)|マルチエージェント/i,
    gift: deck('deck-a2a', 2026, 'やさしいA2A入門', 'yasasiia2aru-men', '95ba0b88325745168cede57a1832e48b/slide_0.jpg') },
  { when: /Summit|サミット|バズ|広まる|人気の?アプリ/i,
    gift: deck('deck-summit', 2026, '「勝手に広まる」人気AIエージェントを爆速で作ろう！', 'sheng-shou-niguang-maru-ren-qi-ai-ezientowobao-su-dezuo-rou-aws-summit-japan-2026jiang-yan-zi-liao', 'a5bf566483bf4f239d2e85c033744122/slide_0.jpg') },
  { when: /エージェントとは|エージェントって|エージェント(を|の)?入門|初心者|はじめて作/,
    gift: deck('deck-agent-intro', 2025, 'AIエージェント入門', 'aiezientoru-men', 'eb4047eef8fc4d3aa89dba6fd2771799/slide_0_1742430647.jpg') },
  { when: /OSS|オープンソース|GitHub|英語で(登壇|発表)/i,
    gift: deck('deck-builder-loop', 2026, 'The Agent Builder Loop from Daily Work to OSS', 'the-agent-builder-loop-from-daily-work-to-oss', '7d2227a4147343458b1412b93f045820/slide_0.jpg') },
  { when: /Kiro/i,
    gift: deck('deck-kiro', 2026, 'Kiro Crewで「みのるん式」仕事術やってみた', 'kiro-crew-de-minoru-nshiki-shigotojutsu-ya-te-mita', '62c08ae39989491cb3525bf5ca0dac49/slide_0.jpg') },
];
export interface GiftState { turns: number; lastTurn: number; lastAt: number; seen: string[] }
export const freshState = (): GiftState => ({ turns: 0, lastTurn: -3, lastAt: 0, seen: [] });
export const isSupport = (id: string): boolean => id === 'support' || id === 'support-github' || id === 'support-x';
export const SUPPORT_INTERVAL = 7 * 24 * 60 * 60 * 1000;
// 支援案内には初回の強制表示を適用せず、保存不可・破損時も控える。
export function chooseSupport(state: GiftState, lastShown: number | null, now: number, random: () => number = Math.random): Surprise | undefined {
  if (lastShown === null || !Number.isFinite(lastShown) || lastShown < 0 || state.turns < 5 || state.seen.some(isSupport)) return;
  if (lastShown > 0 && now - lastShown < SUPPORT_INTERVAL) return;
  if (state.seen.length >= 3 || state.turns - state.lastTurn < 2 || (state.seen.length > 0 && now - state.lastAt < 60_000)) return;
  if (random() >= .1) return;
  return random() < .5 ? gifts['support-github'] : gifts['support-x'];
}
// 1会話で貼る上限。話題に合う資料を増やしたので、3枚から6枚へ増やした（11/7（土）みのるん指示）。
export const GIFT_LIMIT = 6;
export function chooseGift(state: GiftState, candidates: Surprise[], now: number, random: () => number = Math.random, chance = .6): Surprise | undefined {
  if (state.turns < 1 || state.seen.length >= GIFT_LIMIT || state.turns - state.lastTurn < 2 || (state.seen.length > 0 && now - state.lastAt < 60_000)) return;
  const available = candidates.filter(g => !state.seen.includes(g.id));
  // 初回は外れ続けても4回答目で出会える。以後は通常の抽選を続ける。
  if (!available.length || (random() >= chance && !(state.seen.length === 0 && state.turns >= 4))) return;
  return available[Math.floor(random() * available.length)];
}
// 手元確認の ?gift=ID で、話題の付箋も呼び出せるようにする。
export const findGift = (id: string): Surprise | undefined =>
  gifts[id] ?? [...decks, ...qiitaGifts, ...githubGifts, ...bookGifts].find(g => g.gift.id === id)?.gift;
export function candidatesFor(question: string, topic: string, expoDepth: number): Surprise[] {
  const context = question + ' ' + topic;
  const result: Surprise[] = [];
  if (/キーノート|基調講演|通訳|レシーバー|keynote/i.test(context)) result.push(gifts.experience);
  if (/expo|展示|ブース/i.test(context) && expoDepth >= 2) result.push(gifts.english);
  if ((/(2025|去年|昨年|開発秘話)/.test(context) && /エージェント|agent/i.test(context)) || /このアプリ|このエージェント|みのるんエージェント|どうやって作/.test(context)) result.push(gifts.slide);
  for (const { gift, when } of [...decks, ...qiitaGifts, ...githubGifts, ...bookGifts]) if (when.test(context)) result.push(gift);
  // 質問に合う付箋が無い回では出さない。既定の1枚へ落とすと無関係な資料が貼られる。
  return result;
}
