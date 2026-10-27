/** 同じ会話の先読みを待ち、未実行と分かる起動競合だけを再試行する。 */
export type Incident = {
  id: string; at: number; phase: 'warmup' | 'question'; kind: 'session_busy' | 'http' | 'network' | 'timeout' | 'stream' | 'agent';
  status: number; state: 'retrying' | 'recovered' | 'failed'; attempts: number; tid?: string; hidden?: boolean;
};
export class TransportError extends Error {
  constructor(public status: number, public reason: string, message: string) { super(message); }
}
const BUSY = '会話の準備に時間がかかっています。少し待って、もう一度送ってください。';
const GENERIC = 'いまうまくつながりませんでした。少し時間をおいて、もう一度送ってください。';
const REFUSALS = new Set(['banned', 'daily_limit', 'per_minute', 'too_long', 'bad_id', 'stopped']);
// 先読みは1回8秒で打ち切り、もう1回だけ送り直す。サーバーが返し終えていても応答が届かず止まることがある（10/27（火）に3件。
// いずれも Runtime は0.3秒で返し終えていた）。1回目で入れ物は起きているので、送り直しはすぐ返る。
const WARMUP_TIMEOUT = 8_000;
const WARMUP_RESENDS = 1;
export class ChatTransport {
  private warming?: Promise<Response | undefined>;
  constructor(
    private post: (body: Record<string, unknown>, signal?: AbortSignal) => Promise<Response>,
    private report: (incident: Incident) => void,
    private sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms)),
    private warmupTimeout = WARMUP_TIMEOUT,
  ) {}
  warmup(): Promise<Response | undefined> {
    // Promise を共有する。先読みを呼び直しても同じ会話の起動要求は増えない。
    return this.warming ??= this.request({ warmup: true }, 'warmup').catch(() => undefined);
  }
  async question(body: Record<string, unknown>): Promise<Response> {
    await this.warming;
    return this.request(body, 'question');
  }
  failure(body: Record<string, unknown>, kind: Incident['kind']) {
    try {
      this.report({ id: crypto.randomUUID().replaceAll('-', ''), at: Date.now(), phase: 'question', kind,
        status: 0, state: 'failed', attempts: 1, tid: String(body.tid || '') });
    } catch { /* 記録の失敗で回答を止めない */ }
  }
  private async request(body: Record<string, unknown>, phase: Incident['phase']) {
    const id = crypto.randomUUID().replaceAll('-', '');
    const at = Date.now();
    const retries = phase === 'warmup' ? 2 : 3;
    const tid = typeof body.tid === 'string' ? body.tid : undefined;
    let trouble: Incident['kind'] | undefined, troubleStatus = 0, resends = 0;
    const emit = (kind: Incident['kind'], status: number, state: Incident['state'], attempts: number) => {
      try { this.report({ id, at, phase, kind, status, state, attempts, tid }); } catch { /* 記録の失敗で回答を止めない */ }
    };
    for (let attempt = 1; ; attempt++) {
      // 先読みは要求ごとに時間を区切る（質問は回答に時間がかかるので区切らない）
      const signal = phase === 'warmup' ? AbortSignal.timeout(this.warmupTimeout) : undefined;
      let res: Response;
      try {
        res = await this.post(body, signal);
        // 先読みはヘッダーではなく、先読み処理の終了まで待つ。ヘッダーの後に止まっても同じ時間で区切る
        if (phase === 'warmup' && res.ok) await res.clone().arrayBuffer();
      } catch (e) {
        const kind = signal?.aborted ? 'timeout' : 'network';
        // 質問は処理済みかもしれないので送り直さない。先読みは何度届いても同じ結果になる
        const again = phase === 'warmup' && resends < WARMUP_RESENDS;
        emit(kind, 0, again ? 'retrying' : 'failed', attempt);
        if (!again) throw e;
        resends++; trouble = kind; troubleStatus = 0;
        await this.sleep(300);
        continue;
      }
      if (res.ok && res.body) {
        if (trouble) emit(trouble, troubleStatus, 'recovered', attempt);
        return res;
      }
      const data = await res.json().catch(() => ({})) as { error?: string; message?: string; __type?: string };
      const errorType = res.headers.get('x-amzn-errortype') || data.__type || data.error || '';
      const busy = res.status === 409 && (errorType.split(':')[0].endsWith('RetryableConflictException') ||
        data.message === 'Session operation in progress, please retry');
      if (busy) {
        trouble = 'session_busy'; troubleStatus = 409;
        emit('session_busy', 409, attempt <= retries ? 'retrying' : 'failed', attempt);
        if (attempt <= retries) {
          await this.sleep(500 * 2 ** (attempt - 1) + Math.floor(Math.random() * 150));
          continue;
        }
        throw new TransportError(409, 'session_busy', BUSY);
      }
      if (!REFUSALS.has(data.error || '') || trouble) emit('http', res.status, 'failed', attempt);
      throw new TransportError(res.status, data.error || 'http',
        REFUSALS.has(data.error || '') && data.message ? data.message : GENERIC);
    }
  }
}
