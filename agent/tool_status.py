"""検索中に表示する短い案内。質問の原文は画面へ転記しない。"""

import re


# 具体的な話題を先に照合し、人名・会社名などの広い話題は後に置く。
KNOWLEDGE_TOPICS = tuple((re.compile(pattern, re.I), label) for pattern, label in (
    (r"このアプリ|このエージェント|みのるんエージェント", "アプリの仕組み"),
    (r"agentcore|strands|bedrock|claude\s*code|\bmcp\b|生成ai|aiエージェント", "AIの開発ノウハウ"),
    (r"登壇|執筆|記事の書き方|技術発信", "登壇・執筆のノウハウ"),
    (r"キーノート|基調講演|keynote", "キーノート"),
    (r"ワークショップ|チョークトーク|chalk\s*talk|gameday|\bjam\b|ハンズオン", "体験型セッション"),
    (r"セッション|講演|session", "セッションの選び方"),
    (r"シャトル|モノレール|uber|lyft|タクシー|移動|交通", "現地の移動"),
    (r"ホテル|宿泊|hotel", "ホテル選び"),
    (r"機内|飛行機|フライト|航空|乗り継ぎ|時差ぼけ", "フライト・機内の過ごし方"),
    (r"入国|esta|パスポート|ビザ", "渡航の準備"),
    (r"持ち物|荷物|服装|靴|気温|天気|防寒", "持ち物・服装"),
    (r"esim|simカード|wi.?fi|通信|充電|コンセント", "通信・充電"),
    (r"ドラッグストア|薬局|薬|体調|病院", "現地の健康・薬"),
    (r"食事|朝食|昼食|夕食|ランチ|レストラン|ご飯|コーヒー", "食事・休憩"),
    (r"買い物|お土産|ショッピング|スーパー", "現地の買い物"),
    (r"re:play|パーティ|party|交流|懇親|ミートアップ", "交流・イベント"),
    (r"jaws|コミュニティ|community", "コミュニティの活動"),
    (r"バッジ|受付|チェックイン|badge", "受付・バッジ"),
    (r"費用|予算|料金|チケット|参加費|申し込|申込|来年", "参加の準備・費用"),
    (r"会場|キャンパス|venetian|ベネチアン|マンダレイ|wynn|ウィン", "会場の情報"),
    (r"英語|通訳|翻訳|言語", "英語・言葉の準備"),
    (r"kag|kddi|アジャイル開発センター", "KAGの公開情報"),
    (r"みのるん|御田|minorun|著書|出演|プロフィール", "みのるんの活動"),
    (r"aws|クラウド|lambda|ec2|s3|dynamodb", "AWSの技術情報"),
    (r"ラスベガス|las\s*vegas|観光|治安|チップ", "ラスベガスの過ごし方"),
))


def knowledge_label(name: str, question: str, current: str = "") -> str:
    """現在の質問を優先し、省略された続きの質問では直前の話題も使う。"""
    category = next((label for text in (question, current)
                     for pattern, label in KNOWLEDGE_TOPICS if pattern.search(text)), None)
    if category:
        prefix = "過去の資料で" if name == "search_past" else "ナレッジベースで"
        return f"{prefix}{category}を調べています"
    return "過去の資料を調べています" if name == "search_past" else "ナレッジベースで参加情報を調べています"
