"""エージェントのツール。ナレッジベースと Web 検索はツールの Gateway（MCP・IAM 認証）越しに呼ぶ。
鮮度の問いだけは Brave を直接呼ぶ（キーは AgentCore Identity の保管庫 brave から読む）。

ツールの結果はモデルが読みやすい形に整えて返し、画面へ出す情報（地図・照合の予約）は Turn に積む。
"""

import datetime
import email.utils
import html
import json
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import boto3

from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from strands import tool
from strands.tools.mcp.mcp_client import MCPClient

import catalog
from knowledge_context import compact_sources, mark_fragments
from settings import (ASSET_BASE, CONFERENCE_DAYS, JST, KB_RESULTS, KNOWLEDGE_BUCKET, LAS_VEGAS, REGION,
                      TOOLS_GATEWAY_URL, WEB_RESULTS)

_mcp = None
_mcp_lock = threading.Lock()


def mcp():
    global _mcp
    with _mcp_lock:
        if _mcp is None:
            client = MCPClient(lambda: aws_iam_streamablehttp_client(
                endpoint=TOOLS_GATEWAY_URL, aws_region=REGION, aws_service="bedrock-agentcore"))
            client.start()
            _mcp = client
        return _mcp


class Turn:
    """1回の質問の間だけ持つ情報（画面へ出すもの・裏の照合）。"""

    current = None

    def __init__(self):
        self.maps = []
        self.verify = None
        self.tool_calls = 0
        self.tools_used = []
        self.x_calls = 0
        self.x_usage = []
        # 保管庫の鍵を読むための通行証。Runtime が質問ごとにヘッダーで渡す
        self.workload_token = None


def _call(name, args):
    result = mcp().call_tool_sync(f"t-{name}", name, args)
    text = "".join(c.get("text", "") for c in result.get("content", []))
    if result.get("status") != "success":
        raise RuntimeError(text[:300])
    return json.loads(text) if text.startswith("{") else {"text": text}


def _kb(target, query):
    data = _call(f"{target}___Retrieve", {"retrievalQuery": {"text": query}})
    rows = []
    # Gateway は8件返す。上位だけを渡し、毎問のトークンを減らす
    for r in data.get("retrievalResults", [])[:KB_RESULTS]:
        rows.append({"year": int(r.get("metadata", {}).get("year", 0)),
                     "text": r.get("content", {}).get("text", "")[:1500]})
    return rows


@tool
def now() -> dict:
    """いまの日本時間とラスベガス時間（会期中は17時間差）、re:Invent 2026 の会期の何日目かを返す。"""
    jst = datetime.datetime.now(JST)
    lv = jst.astimezone(LAS_VEGAS)
    day = lv.date()
    if day < CONFERENCE_DAYS[0]:
        phase = f"開幕（ラスベガス時間 11/30（月））まであと {(CONFERENCE_DAYS[0] - day).days} 日"
    elif day > CONFERENCE_DAYS[-1]:
        phase = "会期（11/30〜12/4）は終わっています"
    else:
        phase = f"会期 {CONFERENCE_DAYS.index(day) + 1} 日目"
    week = "月火水木金土日"
    return {"japan": f"{jst:%Y-%m-%d}（{week[jst.weekday()]}） {jst:%H:%M}",
            "las_vegas": f"{lv:%Y-%m-%d}（{week[lv.weekday()]}） {lv:%H:%M}（太平洋標準時）",
            "conference": phase,
            "conference_days": "11/30（月）〜12/4（金）、ラスベガス時間"}


@tool
def search_sessions(query: str = "", day: str = "", venue: str = "", session_type: str = "",
                    start_after: str = "", start_before: str = "") -> dict:
    """re:Invent 2026 の公式セッションカタログ（全件の控え）を検索する。
    参加者と会う方法・みのるんの参加スタイルや連絡先は search_2026 で調べる。

    Args:
        query: 英語のキーワード（例: "AgentCore", "serverless", "keynote"）かセッションコード（例: "KEY001"）。登壇者名や会社名も可
        day: ラスベガス時間の日付 YYYY-MM-DD（2026-11-30〜2026-12-04）。絞らないなら空
        venue: 会場名の一部（MGM Grand / Venetian / Wynn / Caesars Forum / Caesars Palace）。絞らないなら空
        session_type: 形式の一部（Breakout / Chalk talk / Workshop / Builders / Lightning / Code talk / Keynote / Gamified learning / Featured experience）。絞らないなら空
        start_after: この時刻以降に始まる回だけ（ラスベガス時間 HH:MM。「昼食後」なら "13:00"、「夕方」なら "16:00"）。絞らないなら空
        start_before: この時刻より前に始まる回だけ（ラスベガス時間 HH:MM。「午前」なら "12:00"）。絞らないなら空
    """
    retrieved, total, hits = catalog.search(query, day, venue, session_type,
                                            start_after=start_after, start_before=start_before)
    turn = Turn.current
    if turn is not None and hits and turn.verify is None:
        turn.verify = catalog.start_verify([h["code"] for h in hits], query)
    return {"snapshot_time_jst": retrieved[:16].replace("T", " "), "matched": total,
            "note": "時刻はラスベガス時間。jst_start は日本時間",
            "sessions": [catalog.view(h) for h in hits]}


@tool
def search_2026(query: str) -> dict:
    """2026年の re:Invent の情報（公式サイトの案内と、2026年に向けた参加者の記事）をナレッジベースから探す。会場・移動・食事・持ち物・参加の実務はまずこれ。
    みのるん本人が公開したプロフィール・現地の過ごし方・よくいる場所・連絡先も含む。「みのるんと会うには？」はまずこれで探す。

    Args:
        query: 探したい内容を日本語の自然な文で（例: バッジの受け取り場所）
    """
    return {"year": "2026年の情報", **compact_sources(mark_fragments(_kb("kb-2026", query)))}


@tool
def search_past(query: str) -> dict:
    """2025年以前の re:Invent の参加記・資料をナレッジベースから探す。答えでは必ず「◯年は〜だった」と年を付けて過去の参考として使う。

    Args:
        query: 探したい内容を日本語の自然な文で
    """
    return {"year": "2025年以前の情報（過去の参考）", **compact_sources(mark_fragments(_kb("kb-past", query)))}


@tool
def search_web(query: str) -> dict:
    """Web を検索する。ナレッジベースに無いこと、最新の発表やニュースを調べるときに使う。

    Args:
        query: 検索語（日本語でも英語でも可）
    """
    data = _call("web___WebSearch", {"query": query, "maxResults": WEB_RESULTS})
    return {"results": [{"title": r.get("title"), "url": r.get("url"), "published": r.get("publishedDate"),
                         "text": (r.get("text") or "")[:700]} for r in data.get("results", [])]}


_brave_key = None


def _brave(query, freshness="pw"):
    global _brave_key
    if _brave_key is None:
        from bedrock_agentcore.services.identity import IdentityClient
        token = Turn.current.workload_token if Turn.current else None
        if not token:
            raise RuntimeError("workload token が無い")
        _brave_key = IdentityClient(REGION).get_api_key(provider_name="brave", agent_identity_token=token)
    params = {"q": query, "country": "JP", "search_lang": "jp", "ui_lang": "ja-JP", "count": WEB_RESULTS}
    if freshness:
        params["freshness"] = freshness
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request("https://api.search.brave.com/res/v1/web/search?" + q,
                                 headers={"Accept": "application/json", "X-Subscription-Token": _brave_key})
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.load(res)


@tool
def search_recent(query: str) -> dict:
    """直近1週間に公開された Web の情報を探す（Brave Search）。「今週」「最新」「さっき発表された」「当日の変更」など鮮度が要る問いに使う。見つからなければ search_web で期間を外して探し直す。

    Args:
        query: 検索語（日本語でも英語でも可）
    """
    data = _brave(query)
    return {"period": "直近1週間", "results": [
        {"title": r.get("title"), "url": r.get("url"), "age": r.get("age") or r.get("page_age"),
         "text": (r.get("description") or "")[:500]} for r in data.get("web", {}).get("results", [])]}


@tool
def search_japanese(query: str) -> dict:
    """期間を絞らず日本語の公開Web情報を探す。人物の名前・通称・ブログ・XのIDを探すとき、ナレッジベースで答えが足りなければ使う。

    Args:
        query: 人物名やキーワード。所属や活動分野が分かれば含める
    """
    data = _brave(query, freshness="")
    return {"results": [{"title": r.get("title"), "url": r.get("url"),
                         "text": (r.get("description") or "")[:700]}
                        for r in data.get("web", {}).get("results", [])]}


def _x_search(operation, args):
    turn = Turn.current
    if turn is None or turn.x_calls >= 2:
        return {"status": "turn_limit", "results": []}
    turn.x_calls += 1
    data = _call("x___" + operation, args)
    turn.x_usage.append({"operation": operation, "status": data.get("status"),
                        "estimated_usd": data.get("estimated_usd"),
                        "reserved_usd": data.get("reserved_usd")})
    return data


@tool
def search_x_users(query: str) -> dict:
    """Xの公開プロフィールを検索する。人物名・通称で日本語Web検索し、公開リンクから見つけた@IDで照会する。同名の候補は同一人物と決めつけない。

    Args:
        query: @から始まる正確なユーザーID（名前や通称はsearch_japaneseで先に調べる）
    """
    return _x_search("users", {"query": query})


@tool
def search_x_posts(query: str, hours: int = 24) -> dict:
    """Xの最新の公開投稿をキーワードで検索する。会期中の現地の様子・速報・最新の話題や、利用者がXの投稿を求めたときに使う。既定は過去24時間、新しい順で最大10件。公式情報と体験談を区別し、投稿時刻とURLを添えて答える。

    Args:
        query: キーワード。例: 'reInvent lang:ja -is:retweet'。確認済みの投稿者はfrom:ID、話題の別名はORで指定できる
        hours: 遡る時間（1〜167）。今日の状況には24、直近数日なら72など。7日より古い投稿は対象外
    """
    return _x_search("posts", {"query": query, "hours": hours})


# AWS What's New の RSS（直近100件・約10日分）。10分だけ手元に持つ
WHATS_NEW = "https://aws.amazon.com/about-aws/whats-new/recent/feed/"
_feed = {"at": 0.0, "items": []}


def _whats_new_items():
    if time.time() - _feed["at"] > 600:
        req = urllib.request.Request(WHATS_NEW, headers={"User-Agent": "minorun-agent/1.0"})
        with urllib.request.urlopen(req, timeout=10) as res:
            root = ET.fromstring(res.read())
        items = []
        for it in root.iter("item"):
            body = html.unescape(re.sub(r"<[^>]+>", " ", it.findtext("description") or ""))
            when = email.utils.parsedate_to_datetime(it.findtext("pubDate"))
            items.append({"title": it.findtext("title") or "", "url": it.findtext("link") or "",
                          "published_jst": when.astimezone(JST).strftime("%Y-%m-%d %H:%M"),
                          "categories": [c.text.split("/")[-1] for c in it.findall("category") if c.text],
                          "detail": re.sub(r"\s+", " ", body).strip()})
        _feed.update(at=time.time(), items=items)
    return _feed["items"]


@tool
def search_whats_new(query: str = "") -> dict:
    """AWS 公式の What's New（新機能・アップデートの発表）を、直近約10日分から探して技術的な詳細つきで返す。「最近のアップデート」「◯◯の新機能」「今週発表されたもの」を聞かれたときに使う。

    Args:
        query: 英語のキーワード（例: "Bedrock", "AgentCore", "Lambda"）。空なら新しい順に返す
    """
    items = _whats_new_items()
    words = [w.lower() for w in re.split(r"[\s,、]+", query) if w]
    if words:
        def score(i):
            text = (i["title"] + " " + " ".join(i["categories"]) + " " + i["detail"]).lower()
            return sum(3 if w in i["title"].lower() else 1 for w in words if w in text)
        items = sorted((i for i in items if score(i)), key=score, reverse=True)
    picked = items[:6]
    return {"source": "AWS What's New（公式）", "covers": f"直近{len(_feed['items'])}件",
            "matched": len(items), "items": [{**i, "detail": i["detail"][:1200]} for i in picked]}


# AWS ブログ全体（英語の各ブログと日本語ブログ）。aws.amazon.com のブログ一覧と同じ検索 API を新しい順で引き、上位2件は本文も読む
BLOG_SEARCH = "https://aws.amazon.com/api/dirs/items/search"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "minorun-agent/1.0"})
    with urllib.request.urlopen(req, timeout=10) as res:
        return res.read().decode("utf-8", "replace")


def _blog_body(url):
    page = _get(url)
    m = re.search(r'<section class="blog-post-content[^>]*>(.*?)</section>', page, re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1) if m else ""))
    return re.sub(r"\s+", " ", text).strip()[:2000]


@tool
def search_aws_blog(query: str, japanese: bool = False) -> dict:
    """AWS 公式ブログ（News Blog・Machine Learning Blog などの全ブログ、または日本語ブログ）を新しい順に検索する。新機能の詳しい解説、会期中の発表のまとめ（re:Invent の Top announcements など）、日本語の「週刊AWS」を探すときに使う。上位2件は本文の冒頭も返す。

    Args:
        query: キーワード（英語のブログなら英語。例: "re:Invent 2026 announcements", "AgentCore"）
        japanese: 日本語ブログ（aws.amazon.com/jp/blogs）を探すなら true
    """
    q = urllib.parse.urlencode({"item.directoryId": "blog-posts", "sort_by": "item.additionalFields.createdDate",
                                "sort_order": "desc", "size": 6, "item.locale": "ja_JP" if japanese else "en_US",
                                "q": query, "q_operator": "AND"})
    data = json.loads(_get(f"{BLOG_SEARCH}?{q}"))
    posts = []
    for n, it in enumerate(data.get("items", [])):
        f = it["item"]["additionalFields"]
        post = {"title": html.unescape(f.get("title", "")), "url": f.get("link"),
                "published_jst": datetime.datetime.fromisoformat(f["createdDate"].replace("Z", "+00:00")).astimezone(JST).strftime("%Y-%m-%d %H:%M"),
                "excerpt": html.unescape(re.sub(r"<[^>]+>", " ", f.get("postExcerpt", "")))[:400]}
        if n < 2:
            try:
                post["body"] = _blog_body(post["url"])
            except Exception:
                pass
        posts.append(post)
    return {"source": "AWS 公式ブログ" + ("（日本語）" if japanese else ""), "matched": data.get("metadata", {}).get("totalHits"),
            "posts": posts}


# 速報は S3 の1ファイル（knowledge/tools/announce.py が書く）。ナレッジベースの取り込みを待たずに出せるよう直接読み、1分だけ手元に持つ
_news = {"at": 0.0, "items": []}


@tool
def get_announcements() -> dict:
    """みのるんが現地から足した速報（新しい発表・混雑・会場や時間の変更など）を新しい順に返す。会期中の「いまどうなってる？」「混んでる？」「何か変更あった？」や、当日の状況を聞かれたときに使う。"""
    if time.time() - _news["at"] > 60:
        try:
            body = boto3.client("s3", region_name=REGION).get_object(
                Bucket=KNOWLEDGE_BUCKET, Key="announcements/latest.json")["Body"].read()
            _news["items"] = json.loads(body).get("items", [])
        except Exception as e:
            # まだ速報が1件も無いと、S3 は「ファイルが無い」ではなく AccessDenied を返す（一覧の権限を付けていないため）
            if "NoSuchKey" not in str(e) and "AccessDenied" not in str(e):
                raise
            _news["items"] = []
        _news["at"] = time.time()
    items = sorted(_news["items"], key=lambda i: i.get("at", ""), reverse=True)[:10]
    return {"note": "時刻は日本時間。みのるんが現地で確かめて書いた速報", "items": items} if items else \
        {"items": [], "note": "いまは速報がありません"}


with open(__import__("pathlib").Path(__file__).with_name("maps.json")) as f:
    MAPS = json.load(f)["images"]


def map_payload(map_id):
    """画面へ送る画像の情報（未登録の ID なら None）。"""
    m = next((m for m in MAPS if m["id"] == map_id), None)
    return m and {"id": m["id"], "src": f"{ASSET_BASE}/maps/{m['file']}", "alt": m["alt"], "caption": m["caption"],
                  "width": m["width"], "height": m["height"], "source_url": m["page_url"]}


def show_map(map_id: str = "campus-map-2026") -> dict:
    """質問に合う登録済みの画像を回答へ添える。写真は過去の体験の参考として年を明示する。

    Args:
        map_id: 下の画像一覧にあるID。質問に直接関係する画像を1枚選ぶ。
    """
    m = next((m for m in MAPS if m["id"] == map_id), None)
    if m is None:
        return {"shown": False, "error": "unknown_image", "available_ids": [m["id"] for m in MAPS]}
    shown = map_payload(map_id)
    if Turn.current is not None and all(x["id"] != m["id"] for x in Turn.current.maps):
        Turn.current.maps.append(shown)
    return {"shown": True, "year": m["year"], "caption": m["caption"], "covers": m["covers"], "not_covers": m["not_covers"],
            "source_url": m["page_url"]}


# 索引とツールの説明を一致させ、新しい画像をモデルから選べるようにする。
image_catalog_description = "\n画像一覧：\n" + "\n".join(
    f"- {m['id']}：{m['caption']}。使用場面：{'／'.join(m['show_when'])}。注意：{m['not_covers']}"
    for m in MAPS)
show_map = tool(description=show_map.__doc__.split("Args:")[0].strip() + image_catalog_description)(show_map)

ALL = [now, search_sessions, search_2026, search_past, search_web, search_recent, search_japanese, search_x_users, search_x_posts, search_whats_new, search_aws_blog, get_announcements, show_map]
