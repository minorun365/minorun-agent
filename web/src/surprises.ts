// 回答のおまけ。本人の体験は公開資料で確認した文だけを使う。
export interface Surprise { id: string; title: string; text: string; english?: string; source?: string; sourceLabel?: string; image?: string; preview?: boolean; pose: 'idea' | 'pc' }
const slides = 'https://speakerdeck.com/minorun365/';
export const gifts: Record<string, Surprise> = {
  'support-github': { id: 'support-github', title: '応援してね', text: 'GitHub Sponsors で応援してもらえるとうれしいです！', source: 'https://github.com/sponsors/minorun365', pose: 'pc' },
  'support-x': { id: 'support-x', title: '応援してね', text: 'X のサブスクで応援してもらえるとうれしいです！', source: 'https://x.com/minorun365/subs', pose: 'pc' },
  experience: { id: 'experience', title: '私の失敗談', text: '2024年、ワーナーのキーノートにレシーバーなしで臨んだら、英語が聞き取れませんでした。', source: slides + 'mi-zhao-bedrockergare-invent-2024deguo-gosita5ri-jian-woshao-jie', pose: 'idea' },
  english: { id: 'english', title: '通ですね！', text: '短いデモを見せてもらえますか？', english: 'Could you show me a quick demo?', pose: 'idea' },
  slide: { id: 'slide', title: '2025年の登壇資料より', text: 're:Invent エージェントの開発秘話', image: 'https://files.speakerdeck.com/presentations/e51d54203aca4064a54d1a33be539437/slide_0.jpg', source: slides + 'awsnoxin-ji-neng-wohuruhuo-yong-sita-re-inventeziento-kai-fa-mi-hua', pose: 'pc' },
  thanks: { id: 'thanks', title: 'うれしいです！', text: 'ありがとうございます。', pose: 'pc' },
};
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
export function chooseGift(state: GiftState, candidates: Surprise[], now: number, random: () => number = Math.random, chance = .4): Surprise | undefined {
  if (state.turns < 1 || state.seen.length >= 3 || state.turns - state.lastTurn < 2 || (state.seen.length > 0 && now - state.lastAt < 60_000)) return;
  const available = candidates.filter(g => !state.seen.includes(g.id));
  // 初回は外れ続けても4回答目で出会える。以後は通常の抽選を続ける。
  if (!available.length || (random() >= chance && !(state.seen.length === 0 && state.turns >= 4))) return;
  return available[Math.floor(random() * available.length)];
}
export function candidatesFor(question: string, topic: string, expoDepth: number): Surprise[] {
  const context = question + ' ' + topic;
  const result: Surprise[] = [];
  if (/キーノート|基調講演|通訳|レシーバー|keynote/i.test(context)) result.push(gifts.experience);
  if (/expo|展示|ブース/i.test(context) && expoDepth >= 2) result.push(gifts.english);
  if (/(2025|去年|昨年|開発秘話)/.test(context) && /エージェント|agent/i.test(context)) result.push(gifts.slide);
  return result.length ? result : [gifts.slide];
}
