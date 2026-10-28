"""人物カードの文ごとに、誰の話かを書く。

ナレッジベースは行の途中でも断片を切るので、主語を前の文に頼ると
「陶芸が好き。」だけが検索に届き、回答者が「私」のことと読み違える。
"""
import json
import re
import unittest
from pathlib import Path

PROFILE = Path(__file__).resolve().parents[2] / "knowledge/sources/profile"


def sentences(text):
    for s in re.split(r"(?<=。)", text):
        s = s.strip()
        if s and not re.fullmatch(r"https?://\S+", s):
            yield s


class PersonSubjects(unittest.TestCase):
    def test_every_sentence_names_the_person(self):
        missing = []
        for path in sorted(PROFILE.glob("pro-person-*.json")):
            card = json.loads(path.read_text())
            name = re.split(r"[（(]", card["title"])[0].removesuffix("さん")
            for fact in card["facts"]:
                missing += [f"{path.name}: {s}" for s in sentences(fact["text"]) if name not in s]
        self.assertEqual(missing, [], "主語の無い文：" + "\n".join(missing))

    def test_detects_a_sentence_without_the_person(self):
        self.assertEqual([s for s in sentences("しらかばさんは架空。陶芸が好き。 https://x.com/s")
                          if "しらかば" not in s], ["陶芸が好き。"])


if __name__ == "__main__":
    unittest.main()
