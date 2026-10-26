/** 同じ会話の先読みを待ち、未実行と分かる起動競合だけを再試行する。 */
export type Incident = {
  id: string; at: number; phase: 'warmup' | 'question'; kind: 'session_busy' | 'http' | 'network' | 'stream' | 'agent';
  status: number; state: 'retrying' | 'recovered' | 'failed'; attempts: number; tid?: string;
};
export class TransportError extends Error {
  constructor(public status: number, public reason: string, message: string) { super(message); }
}
const BUSY = '会話の準備に時間がかかっています。少し待って、もう一度送ってください。';
const GENERIC = 'いまうまくつながりませんでした。少し時間をおいて、もう一度送ってください。';
const REFUSALS = new Set(['banned', 'daily_limit', 'per_minute', 'too_long', 'bad_id', 'stopped']);
export class ChatTransport {
  private warming?: Promise<Response | undefined>;
  constructor(
    private post: (body: Record<string, unknown>, signal?: AbortSignal) => Promise<Response>,
    private report: (incident: Incident) => void,
    private sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms)),
  ) {}
  warmup(): Promise<Response | undefined> {
    // Promise を共有する。先読みを呼び直しても同じ会話の起動要求は増えない。
    return this.warming ??= (async () => {
      try {
        const res = await this.request({ warmup: true }, 'warmup', AbortSignal.timeout(12_000));
        // ヘッダーの到着ではなく、先読み処理の終了まで待つ。
        await res.arrayBuffer();
        return res;
      } catch { return undefined; }
    })();
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
  private async request(body: Record<string, unknown>, phase: Incident['phase'], signal?: AbortSignal) {
    const id = crypto.randomUUID().replaceAll('-', '');
    const at = Date.now();
    const retries = phase === 'warmup' ? 2 : 3;
    const tid = typeof body.tid === 'string' ? body.tid : undefined;
    let conflict = false;
    const emit = (kind: Incident['kind'], status: number, state: Incident['state'], attempts: number) => {
      try { this.report({ id, at, phase, kind, status, state, attempts, tid }); } catch { /* 記録の失敗で回答を止めない */ }
    };
    for (let attempt = 1; ; attempt++) {
      let res: Response;
      try { res = await this.post(body, signal); }
      catch (e) { emit('network', 0, 'failed', attempt); throw e; }
      if (res.ok && res.body) {
        if (conflict) emit('session_busy', 409, 'recovered', attempt);
        return res;
      }
      const data = await res.json().catch(() => ({})) as { error?: string; message?: string; __type?: string };
      const errorType = res.headers.get('x-amzn-errortype') || data.__type || data.error || '';
      const busy = res.status === 409 && (errorType.split(':')[0].endsWith('RetryableConflictException') ||
        data.message === 'Session operation in progress, please retry');
      if (busy) {
        conflict = true;
        emit('session_busy', 409, attempt <= retries ? 'retrying' : 'failed', attempt);
        if (attempt <= retries) {
          await this.sleep(500 * 2 ** (attempt - 1) + Math.floor(Math.random() * 150));
          if (signal?.aborted) throw signal.reason;
          continue;
        }
        throw new TransportError(409, 'session_busy', BUSY);
      }
      if (!REFUSALS.has(data.error || '') || conflict) emit('http', res.status, 'failed', attempt);
      throw new TransportError(res.status, data.error || 'http',
        REFUSALS.has(data.error || '') && data.message ? data.message : GENERIC);
    }
  }
}
