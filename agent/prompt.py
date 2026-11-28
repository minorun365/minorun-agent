"""システムプロンプト。変わらない部分（キャッシュする）と、会話ごとに変わる部分（これまでの話題の要約）に分ける。"""
from pathlib import Path

from datetime import datetime, timedelta, timezone

BASE = (Path(__file__).with_name("base.local.txt").read_text().strip() if Path(__file__).with_name("base.local.txt").exists() else "")


# 期間限定で紹介する催し。固定部分（BASE）はキャッシュするので、期限で消える文は毎回作るこちらへ置く
FLYING_RECAP_END = datetime(2026, 12, 3, 13, 0, tzinfo=timezone(timedelta(hours=9)))
FLYING_RECAP = (
    "# 期間限定で紹介する催し\n"
    "- KAG が現地12月2日（水）19時〜20時（日本時間12月3日（木）12時〜13時）に、ラスベガスから YouTube Live で"
    "「AWS re:Invent 2026 宇宙最速のフライングRecap with KAG」を生配信します（視聴無料。前半は私が AI まわりの発表を解説し、"
    "後半は現地の KAG メンバーで雑談しながら解説。申し込みは KAG の connpass https://kddi-agile.connpass.com/event/414397/ ）。"
    "紹介するのは、質問そのものが現地からの生配信・日本からキーノートや発表をどう追うか・re:Cap・KAG のイベントを尋ねているときだけで、1文で紹介して申し込みのリンクを添えます。個々の新発表やサービスの機能、ニュース、そのほかの質問の回答には付け足しません。"
    "12/17（木）の re:Cap と同じ回答で紹介するときは2つを1文にまとめ、同じ会話で一度紹介したら、聞かれない限り繰り返しません。"
)


def promos(now=None):
    """期限内の期間限定の催し。期限を過ぎたら空にする（デプロイし直さなくても消える）。"""
    now = now or datetime.now(timezone.utc)
    return [FLYING_RECAP] if now < FLYING_RECAP_END else []


def dynamic(shelf, current, memories=None, now=None):
    """これまでの話題の要約（前の話題の要約）と、いまの話題の見出し。ログインした人なら、前の会話から覚えていること。"""
    lines = promos(now)
    if memories:
        lines.append("# この人について前の会話から覚えていること\n答えに関係するときだけ自然に生かします（「覚えています」とわざわざ言わない）。")
        lines += [f"- {m}" for m in memories]
    if current:
        lines.append(f"# いまの話題\n{current}")
    if shelf:
        lines.append("# これまでの話題（要約）\n利用者が「さっきの話」と言ったら、ここを手がかりにします。")
        for item in shelf[-8:]:
            lines.append(f"- {item['heading']}：{item['summary']}")
    if not current and not shelf:
        lines.append("# いまの話題\n（会話の始まり）")
    return "\n".join(lines)
