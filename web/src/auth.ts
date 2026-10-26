// Google でログイン（Cognito の Hosted UI。認可コード＋PKCE を自前で扱い、ライブラリは足さない）
// ログインした人は質問の上限が緩くなり、前の会話の好みや事実をエージェントが覚える。
// ID は infra/scripts/auth.py が作った値（infra/.auth.json）を、ビルド時に VITE_AUTH_DOMAIN・VITE_AUTH_CLIENT_ID で渡す。秘密の値ではない
// 個人情報は受け取らない：Google へは openid だけを頼むので、名前・メールアドレス・画像はどこにも残らない

const ENV = (import.meta as unknown as { env: Record<string, string | undefined> }).env;
const AUTH = { domain: ENV.VITE_AUTH_DOMAIN ?? '', clientId: ENV.VITE_AUTH_CLIENT_ID ?? '' };
const KEY = 'minorun-auth';

interface Tokens { access: string; id: string; refresh: string; exp: number }
export interface Profile { signedIn: true }

const redirectUri = () => location.origin + '/';
const b64url = (bytes: ArrayBuffer | Uint8Array) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const claims = (jwt: string) => { try { return JSON.parse(atob(jwt.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))); } catch { return {}; } };
// 本人確認の範囲（aws.cognito.signin.user.admin）が無い古い証明書は捨てる（2026-10-25 以前にログインした分。これが無いと上限が緩まない）
const read = (): Tokens | null => {
  try {
    const t: Tokens | null = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (t && !String(claims(t.access).scope || '').includes('aws.cognito.signin.user.admin')) { localStorage.removeItem(KEY); return null; }
    return t;
  } catch { return null; }
};
const save = (t: Tokens | null) => { try { t ? localStorage.setItem(KEY, JSON.stringify(t)) : localStorage.removeItem(KEY); } catch { /* 保存できなくても動く */ } };

export async function login() {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const state = b64url(crypto.getRandomValues(new Uint8Array(16)));
  const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
  sessionStorage.setItem('minorun-pkce', JSON.stringify({ verifier, state }));
  const q = new URLSearchParams({
    identity_provider: 'Google', response_type: 'code', client_id: AUTH.clientId, redirect_uri: redirectUri(),
    scope: 'openid aws.cognito.signin.user.admin', code_challenge_method: 'S256', code_challenge: challenge, state,
  });
  location.assign(`https://${AUTH.domain}/oauth2/authorize?${q}`);
}

export function logout() {
  save(null);
  const q = new URLSearchParams({ client_id: AUTH.clientId, logout_uri: redirectUri() });
  location.assign(`https://${AUTH.domain}/logout?${q}`);
}

async function token(body: Record<string, string>): Promise<Tokens | null> {
  const res = await fetch(`https://${AUTH.domain}/oauth2/token`, {
    method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: AUTH.clientId, ...body }),
  });
  if (!res.ok) return null;
  const d = await res.json();
  return { access: d.access_token, id: d.id_token, refresh: d.refresh_token ?? body.refresh_token ?? '', exp: Date.now() + (d.expires_in - 60) * 1000 };
}

// Google から戻ってきたとき（?code=…&state=…）に、コードをトークンに替えて URL を元に戻す
export async function handleRedirect() {
  const p = new URLSearchParams(location.search);
  const code = p.get('code'), state = p.get('state');
  if (!code) return;
  const saved = JSON.parse(sessionStorage.getItem('minorun-pkce') || '{}');
  sessionStorage.removeItem('minorun-pkce');
  history.replaceState(null, '', location.pathname);
  if (!state || state !== saved.state) return;
  save(await token({ grant_type: 'authorization_code', code, redirect_uri: redirectUri(), code_verifier: saved.verifier }));
}

// 質問のたびに呼ぶ。期限が近ければ取り直し、取り直せなければログアウト扱いにする
export async function accessToken(): Promise<string | null> {
  let t = read();
  if (!t) return null;
  if (Date.now() > t.exp) {
    t = t.refresh ? await token({ grant_type: 'refresh_token', refresh_token: t.refresh }) : null;
    save(t);
  }
  return t?.access ?? null;
}

export function profile(): Profile | null {
  return read() ? { signedIn: true } : null;
}
