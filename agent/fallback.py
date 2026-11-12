"""回答のモデルを Claude API 優先で呼び、使えないときは Bedrock へ切り替える。

Claude API の費用は Max プランに付く月 $100 の API クレジットで払う。クレジットが尽きる・期限が切れる・
キーが無効になると Claude API は 402 / 400（残高不足）/ 401 / 403 を返すので、この入れ物（Runtime の
プロセス）では以後 Claude API を呼ばない。混雑（429・529・5xx・通信の失敗）はその1回だけ Bedrock へ回し、
少し待ってから Claude API へ戻す。ID 連携の交換に失敗したとき（401 等）も待っても直らない側に入れる。

切り替えは、1回のモデル呼び出しで最初の出来事を返す前に失敗したときだけ行う。途中まで返した後の失敗は、
これまでの Bedrock と同じく質問全体の失敗として扱う（返しかけの文に別のモデルの続きを足さない）。
"""

import json
import os
import re
import time

import anthropic
import httpx
from strands.models.model import Model
from strands.types.exceptions import ContextWindowOverflowException, ModelThrottledException

from settings import REGION

# Claude API へは ID 連携（Workload Identity Federation）で入る。実行ロールで STS から短い期限の証明書を
# もらい、Claude API の通行証と交換する（キーを持たない）。ID は Console の Workload identity で作ったもの
RULE_ID = os.environ.get("CLAUDE_WIF_RULE_ID", "")
ORGANIZATION_ID = os.environ.get("CLAUDE_WIF_ORGANIZATION_ID", "")
SERVICE_ACCOUNT_ID = os.environ.get("CLAUDE_WIF_SERVICE_ACCOUNT_ID", "") or None
ENABLED = os.environ.get("ANTHROPIC_PRIMARY", "on") == "on" and bool(RULE_ID and ORGANIZATION_ID)
RETRY_AFTER_BUSY = 60  # 混雑で Bedrock へ回したあと、Claude API へ戻すまでの秒数
# 最初の出来事が届くまでの待ち。Claude API は応答中も ping を送るので、読み取りの間隔は60秒あれば足りる
TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=5.0)


class _State:
    """この入れ物で Claude API を使えるか。会話ごとに入れ物が分かれるので、プロセスの中だけで持つ。"""

    disabled_reason = None if ENABLED else "switched_off"
    busy_until = 0.0


def _is_permanent(error):
    """クレジット切れ・キーの失効など、待っても直らない失敗か。"""
    if isinstance(error, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return True
    # ID 連携の交換の失敗（ルールの不一致・取り消しなど）。交換先の障害（HTTP 5xx）だけは一時的とみなす
    if isinstance(error, anthropic.WorkloadIdentityError):
        return not re.search(r"HTTP 5\d\d", str(error))
    # STS が証明書を出さない（権限・アカウント設定の取り消し）
    if type(error).__name__ == "ClientError" and "GetWebIdentityToken" in str(error):
        return True
    if isinstance(error, anthropic.APIStatusError):
        if error.status_code == 402 or getattr(error, "type", None) == "billing_error":
            return True
        # 残高不足は 400 invalid_request_error で返る（型では見分けられないので文面で判定する）
        if error.status_code == 400 and "credit balance" in str(error).lower():
            return True
    return False


def _is_transient(error):
    """混雑・一時的な障害か（Bedrock へ回せば答えられる見込みがある）。"""
    if isinstance(error, (ModelThrottledException, anthropic.APIConnectionError, anthropic.APITimeoutError,
                          anthropic.RateLimitError, anthropic.InternalServerError, httpx.HTTPError)):
        return True
    return isinstance(error, anthropic.APIStatusError) and error.status_code >= 500


def _log(event, **fields):
    print(json.dumps({"model_route": event, **fields}), flush=True)


def _identity_token():
    """実行ロールの身元を示す証明書（JWT）を STS から受け取る。SDK が通行証の更新のたびに呼ぶ。"""
    import boto3
    sts = boto3.client("sts", region_name="us-east-1")  # GetWebIdentityToken は地域の窓口だけで受け付ける
    return sts.get_web_identity_token(Audience=["https://api.anthropic.com"], SigningAlgorithm="RS256",
                                      DurationSeconds=900)["WebIdentityToken"]


def credentials():
    return anthropic.WorkloadIdentityCredentials(
        identity_token_provider=_identity_token, federation_rule_id=RULE_ID,
        organization_id=ORGANIZATION_ID, service_account_id=SERVICE_ACCOUNT_ID)


class FallbackModel(Model):
    """primary（Claude API）を先に呼び、使えなければ fallback（Bedrock）で答える。

    primary は最初に使うときに作る（使わない入れ物で認証の準備をしない）。
    """

    def __init__(self, make_primary, fallback):
        self._make_primary = make_primary
        self._primary = None
        self.fallback = fallback

    # Strands が設定を読むときは Bedrock 側を見せる（切り替わっても会話の作り直しが起きないように）
    def update_config(self, **model_config):
        self.fallback.update_config(**model_config)

    def get_config(self):
        return self.fallback.get_config()

    def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        return self.fallback.structured_output(output_model, prompt, system_prompt=system_prompt, **kwargs)

    def _use_primary(self):
        if _State.disabled_reason:
            return False
        if time.monotonic() < _State.busy_until:
            return False
        if self._primary is None:
            try:
                self._primary = self._make_primary(credentials())
            except Exception as e:  # 認証の準備に失敗したら、この入れ物では Bedrock だけで答える
                _State.disabled_reason = "credentials_unavailable"
                _log("disabled", reason="credentials_unavailable", detail=type(e).__name__)
                return False
        return True

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        if self._use_primary():
            started = False
            try:
                async for event in self._primary.stream(messages, tool_specs, system_prompt, **kwargs):
                    started = True
                    yield event
                return
            except ContextWindowOverflowException:
                raise  # 会話の要約で直す失敗。Bedrock でも同じく溢れる
            except Exception as e:
                if started:
                    raise
                if _is_permanent(e):
                    _State.disabled_reason = type(e).__name__
                    _log("disabled", reason=type(e).__name__, detail=str(e)[:300])
                elif _is_transient(e):
                    _State.busy_until = time.monotonic() + RETRY_AFTER_BUSY
                    _log("busy", reason=type(e).__name__, detail=str(e)[:300])
                else:
                    # 想定外の失敗（要求の形の不一致など）も、利用者には Bedrock で答える。原因はログで追う
                    _State.busy_until = time.monotonic() + RETRY_AFTER_BUSY
                    _log("unexpected", reason=type(e).__name__, detail=str(e)[:300])
        async for event in self.fallback.stream(messages, tool_specs, system_prompt, **kwargs):
            yield event


def anthropic_model(model_class, model_id, max_tokens, effort, cache_config):
    """Claude API 用の部品を作る関数を返す。設定は Bedrock 側と揃える（考える量・上限・キャッシュ）。"""
    def make(creds):
        return model_class(
            client_args={"credentials": creds, "max_retries": 0, "timeout": TIMEOUT},
            model_id=model_id, max_tokens=max_tokens, cache_config=cache_config,
            params={"output_config": {"effort": effort}})
    return make

