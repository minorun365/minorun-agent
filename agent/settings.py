"""環境変数と定数。値は CDK（infra/lib/agent-stack.ts）が Runtime に渡す。"""

import datetime
import os
from zoneinfo import ZoneInfo

REGION = os.environ.get("AWS_REGION", "us-west-2")
MODEL_MAIN = os.environ.get("MODEL_MAIN", "global.anthropic.claude-sonnet-5-5")
# 回答は Claude API のこのモデルを先に使う（使えないときは MODEL_MAIN の Bedrock。agent/fallback.py）
MODEL_MAIN_API = os.environ.get("MODEL_MAIN_API", "claude-sonnet-5-5")
MODEL_AUX = os.environ.get("MODEL_AUX", "global.anthropic.claude-haiku-5-5")
MODEL_AUX_API = os.environ.get("MODEL_AUX_API", "claude-haiku-5-5")
# 回答の考える量（low / medium）。待ち時間と答えの質で決める
MAIN_EFFORT = os.environ.get("MAIN_EFFORT", "low")
TOOLS_GATEWAY_URL = os.environ.get("TOOLS_GATEWAY_URL", "")
KNOWLEDGE_BUCKET = os.environ.get("KNOWLEDGE_BUCKET", "")
MEMORY_ID = os.environ.get("MEMORY_ID", "")
LOG_TABLE = os.environ.get("LOG_TABLE", "")
# 回答に添える画像（会場マップ）の置き場。画面と同じ配信元からの相対パス
ASSET_BASE = os.environ.get("ASSET_BASE", "/assets")
# 質問に添える画像の一時置き場（空なら添付を受け付けない）
UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET", "")

JST = datetime.timezone(datetime.timedelta(hours=9))
# 会期前後も使うため、夏時間の切り替えを日付から判定する。
LAS_VEGAS = ZoneInfo("America/Los_Angeles")
CONFERENCE_DAYS = [datetime.date(2026, 11, 30) + datetime.timedelta(days=i) for i in range(5)]

# 1回の質問でツールを呼べる回数の上限（無限に調べ続けないように）
MAX_TOOL_CALLS = 8

# 検索結果をモデルへ渡す件数。ナレッジベースは上位6件で引用元の95%を保つ（2026-10-26 実測）
KB_RESULTS = int(os.environ.get("KB_RESULTS", "6"))
WEB_RESULTS = int(os.environ.get("WEB_RESULTS", "4"))

# URL からスライドを作る機能（2026-11-15 みのるん決定：1人1日2本・全体で1日50本。11/21 からログインしていない人も匿名IDで1日1本・同じIPから1日3本）
# 2026-11-25 みのるん決定で上げた：ログインした人1日5本・全体1日200本・ログインなし1日2本・同じIPから1日30本（会場の Wi-Fi は大勢が同じ出口を使うため）
SLIDES_RUNTIME = os.environ.get("SLIDES_RUNTIME", "")
SLIDES_TABLE = os.environ.get("SLIDES_TABLE", "")
SLIDES_BUCKET = os.environ.get("SLIDES_BUCKET", "")
SLIDES_USER_DAILY = int(os.environ.get("SLIDES_USER_DAILY", "5"))
SLIDES_TOTAL_DAILY = int(os.environ.get("SLIDES_TOTAL_DAILY", "200"))
SLIDES_GUEST_DAILY = int(os.environ.get("SLIDES_GUEST_DAILY", "2"))
SLIDES_IP_DAILY = int(os.environ.get("SLIDES_IP_DAILY", "30"))
# 回数を数えない利用者（ログインした人のハッシュ。カンマ区切り）。テストでよく作る運営者だけを入れる
SLIDES_UNLIMITED = frozenset(u for u in os.environ.get("SLIDES_UNLIMITED", "").split(",") if u)
