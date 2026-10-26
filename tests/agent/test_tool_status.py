import unittest

from tool_status import knowledge_label


class ToolStatus(unittest.TestCase):
    def test_specific_subject_before_person_or_company(self):
        for question, expected in (
            ("みのるんのAgentCoreの記事は？", "AIの開発ノウハウ"),
            ("KAGの登壇ノウハウは？", "登壇・執筆のノウハウ"),
            ("ホテルから会場へのシャトルは？", "現地の移動"),
            ("ホテルはどこがおすすめ？", "ホテル選び"),
            ("機内で快適に過ごすには？", "フライト・機内の過ごし方"),
            ("JAWS-UGについて教えて", "コミュニティの活動"),
        ):
            with self.subTest(question=question):
                self.assertEqual(knowledge_label("search_2026", question),
                                 f"ナレッジベースで{expected}を調べています")

    def test_follow_up_uses_topic_but_new_question_takes_priority(self):
        self.assertIn("食事・休憩", knowledge_label("search_2026", "おすすめは？", "食事"))
        self.assertIn("ホテル選び", knowledge_label("search_2026", "ホテルは？", "食事"))

    def test_past_information_is_not_always_an_attendee_report(self):
        self.assertEqual(knowledge_label("search_past", "KAGについて"),
                         "過去の資料でKAGの公開情報を調べています")

    def test_unknown_input_never_echoes_the_question(self):
        question = "private@example.com <script>alert(1)</script>"
        self.assertEqual(knowledge_label("search_2026", question), "ナレッジベースで参加情報を調べています")
        self.assertEqual(knowledge_label("search_past", "続きは？"), "過去の資料を調べています")
