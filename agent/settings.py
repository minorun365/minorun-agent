"""環境変数と定数。値は CDK（infra/lib/agent-stack.ts）が Runtime に渡す。"""

import datetime
import os

REGION = os.environ.get("AWS_REGION", "us-west-2")
MODEL_MAIN = os.environ.get("MODEL_MAIN", "global.anthropic.claude-sonnet-5-5")
MODEL_AUX = os.environ.get("MODEL_AUX", "global.anthropic.claude-haiku-5-5")
# 回答の考える量（low / medium）。待ち時間と答えの質で決める
MAIN_EFFORT = os.environ.get("MAIN_EFFORT", "low")
TOOLS_GATEWAY_URL = os.environ.get("TOOLS_GATEWAY_URL", "")
KNOWLEDGE_BUCKET = os.environ.get("KNOWLEDGE_BUCKET", "")
MEMORY_ID = os.environ.get("MEMORY_ID", "")
LOG_TABLE = os.environ.get("LOG_TABLE", "")
# 回答に添える画像（会場マップ）の置き場。画面と同じ配信元からの相対パス
ASSET_BASE = os.environ.get("ASSET_BASE", "/assets")

JST = datetime.timezone(datetime.timedelta(hours=9))
# 会期中のラスベガスは太平洋標準時（夏時間は 11/1 に終わっている）
LAS_VEGAS = datetime.timezone(datetime.timedelta(hours=-8))
CONFERENCE_DAYS = [datetime.date(2026, 11, 30) + datetime.timedelta(days=i) for i in range(5)]

# 1回の質問で道具を呼べる回数の上限（無限に調べ続けないように）
MAX_TOOL_CALLS = 8

# 検索結果をモデルへ渡す件数。知識ベースは上位6件で引用元の95%を保つ（2026-10-26 実測）
KB_RESULTS = int(os.environ.get("KB_RESULTS", "6"))
WEB_RESULTS = int(os.environ.get("WEB_RESULTS", "4"))
