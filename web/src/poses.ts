// 採用済みの絵を、同じ役割の中で選ぶ。お辞儀は採用対象外。
export const POSES = ['idle', 'think', 'think2', 'book', 'pc', 'phone', 'catalog', 'idea',
  'watch', 'calendar', 'page', 'archive', 'listen', 'note', 'tablet', 'compass',
  'explain', 'thumb', 'coffee', 'wave', 'map', 'magnify', 'clap'] as const;
export type Pose = typeof POSES[number];
export const PHASE_POSES = {
  greeting: ['wave', 'listen'], idle: ['idle', 'listen'], composing: ['idle', 'listen'],
  thinking: ['think', 'think2', 'note'], answering: ['explain', 'idea'],
  following: ['note'], thanks: ['clap', 'thumb'], error: ['idle'],
  resting: ['idle', 'listen', 'coffee'], map: ['map', 'compass'],
} as const satisfies Record<string, readonly Pose[]>;
export type Phase = keyof typeof PHASE_POSES;
export const TOOL_POSES: Record<string, readonly Pose[]> = {
  now: ['watch', 'think', 'think2'], search_sessions: ['catalog', 'calendar', 'tablet'],
  search_2026: ['book', 'page', 'magnify'], search_past: ['book', 'page', 'archive'],
  search_web: ['pc', 'phone', 'magnify', 'tablet'], search_recent: ['pc', 'phone', 'magnify', 'tablet'],
  search_whats_new: ['pc', 'phone', 'tablet'], search_aws_blog: ['book', 'page', 'tablet'],
  get_announcements: ['phone', 'tablet'], show_map: ['book', 'tablet', 'note'],
};

export class PosePicker {
  private previous = new Map<string, Pose>();
  constructor(private random: () => number = Math.random) {}
  pick(key: string, candidates: readonly Pose[], unavailable: ReadonlySet<string> = new Set()): Pose {
    const ready = candidates.filter(p => !unavailable.has(p));
    const different = ready.filter(p => p !== this.previous.get(key));
    const choices = different.length ? different : ready;
    const selected = choices.length ? choices[Math.floor(this.random() * choices.length)] : 'idle';
    this.previous.set(key, selected);
    return selected;
  }
}

// 本文の断片ごとの再抽選を防ぎ、後から始まったツールは最大600msにまとめる。
// 返答・エラーへの移行は、保留中のツールの姿勢を取り消して即座に反映する。
export class PoseDirector {
  private key = '';
  private lastToolAt = -Infinity;
  private pending: ReturnType<typeof setTimeout> | undefined;
  private imageGeneration = 0;
  private pendingImages = 0;
  private pendingAnswer: Phase | undefined;
  constructor(private show: (pose: Pose, state: string) => void,
    private picker = new PosePicker(), private unavailable: ReadonlySet<string> = new Set()) {}
  private cancel() { clearTimeout(this.pending); this.pending = undefined; }
  private apply(key: string, candidates: readonly Pose[]) {
    this.key = key;
    this.show(this.picker.pick(key, candidates, this.unavailable), key);
  }
  phase(phase: Phase, force = false) {
    this.cancel();
    this.imageGeneration++; this.pendingImages = 0; this.pendingAnswer = undefined;
    if (force || this.key !== phase) this.apply(phase, PHASE_POSES[phase]);
  }
  // 空白・画像準備・ツールの結果を、回答本文の開始として扱わない。
  answer(text: string, showingMap = false): boolean {
    if (!text.trim()) return false;
    if (this.pendingImages) {
      this.pendingAnswer = showingMap ? 'map' : 'answering';
      return false;
    }
    this.phase(showingMap ? 'map' : 'answering');
    return true;
  }
  // URLの到着と画像の表示完了は別。本文が先に届いても画像の準備を続ける。
  prepareImage(): () => void {
    this.tool('show_map');
    this.pendingImages++;
    const generation = this.imageGeneration;
    let finished = false;
    return () => {
      if (finished || generation !== this.imageGeneration) return;
      finished = true;
      if (--this.pendingImages === 0 && this.pendingAnswer) this.phase(this.pendingAnswer);
    };
  }
  tool(name: string) {
    this.cancel();
    this.pendingAnswer = undefined;
    const apply = () => {
      this.pending = undefined; this.lastToolAt = Date.now();
      this.apply('tool:' + name, TOOL_POSES[name] ?? PHASE_POSES.thinking);
    };
    const remaining = 600 - (Date.now() - this.lastToolAt);
    if (remaining > 0 && this.key.startsWith('tool:')) this.pending = setTimeout(apply, remaining);
    else apply();
  }
}
