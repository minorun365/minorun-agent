// ログインの往復で画面のやり取りが消えないように、Google へ行く直前に最後の質問と回答を控えておき、
// 戻ってきたら吹き出しへ描き直す。スライドの入口からログインした人は、頼もうとしていたことを続けて送る。
// 控えはタブごとの sessionStorage に置く（会話IDと同じ置き場なので、別のタブや別の会話へは漏れない）。

export type LastTurn = { q: string; a: string; next: string[]; offer: boolean; via: 'ai' | 'starter' };
// offer＝回答をスライドに、url＝入力した URL をスライドに、compose＝URL を入れる入力欄を開く
export type Pending = { kind: 'offer' } | { kind: 'url'; text: string } | { kind: 'compose' };
export type Carryover = { turn: LastTurn | null; pending: Pending | null; at: number };

const KEY = 'minorun-login-carryover';
const MAX_AGE = 30 * 60 * 1000; // ログインに30分以上かかった控えは古いとみなす

export function saveCarryover(storage: Storage, turn: LastTurn | null, pending: Pending | null, now = Date.now()) {
  if (!turn && !pending) { try { storage.removeItem(KEY); } catch { /* 保存できなくても動く */ } return; }
  try { storage.setItem(KEY, JSON.stringify({ turn, pending, at: now } satisfies Carryover)); } catch { /* 同上 */ }
}

/** 控えを取り出して消す（1回だけ描き直す）。壊れている・古い控えは捨てる */
export function takeCarryover(storage: Storage, now = Date.now()): Carryover | null {
  let raw: string | null = null;
  try { raw = storage.getItem(KEY); storage.removeItem(KEY); } catch { return null; }
  if (!raw) return null;
  try {
    const c = JSON.parse(raw) as Carryover;
    if (typeof c?.at !== 'number' || now - c.at > MAX_AGE || now < c.at) return null;
    const turn = c.turn && typeof c.turn.q === 'string' && typeof c.turn.a === 'string' && c.turn.a.trim()
      ? { q: c.turn.q, a: c.turn.a, next: Array.isArray(c.turn.next) ? c.turn.next.filter((t) => typeof t === 'string').slice(0, 6) : [],
          offer: !!c.turn.offer, via: c.turn.via === 'ai' ? 'ai' as const : 'starter' as const }
      : null;
    const p = c.pending;
    const pending: Pending | null = p?.kind === 'offer' ? { kind: 'offer' }
      : p?.kind === 'compose' ? { kind: 'compose' }
      : p?.kind === 'url' && typeof p.text === 'string' && p.text.trim() ? { kind: 'url', text: p.text.slice(0, 1000) }
      : null;
    // 回答をスライドにする頼みは、元の回答が無いと続けられない
    if (pending?.kind === 'offer' && !turn) return null;
    return turn || pending ? { turn, pending, at: c.at } : null;
  } catch { return null; }
}
