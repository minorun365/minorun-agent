"""追加調査後の本文を画面と同じ規則で検査する。"""
import unittest
from run import judge

class RunnerTests(unittest.TestCase):
    def test_replacement_is_the_answer(self):
        events=[{'type':'text','text':'検索前の回答'}, {'type':'replace','text':'検索後の回答'}, {'type':'done'}]
        ng, _, info=judge({},events,200)
        self.assertEqual(ng,[])
        self.assertEqual(info['text'],'検索後の回答')
