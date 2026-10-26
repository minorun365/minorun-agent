"""回答画像の選択・配信素材・出典の整合性。"""
import json
import unittest
from pathlib import Path

import tools

ROOT = Path(__file__).resolve().parents[2]


class AnswerImages(unittest.TestCase):
    def setUp(self):
        self.previous = tools.Turn.current
        tools.Turn.current = tools.Turn()

    def tearDown(self):
        tools.Turn.current = self.previous

    def show(self, image_id):
        return getattr(tools.show_map, '_tool_func', tools.show_map)(image_id)

    def test_every_image_has_matching_deployed_file_and_metadata(self):
        canonical_path = ROOT / 'knowledge/assets/maps/index.json'
        if canonical_path.is_file():
            canonical = json.loads(canonical_path.read_text())
            self.assertEqual(canonical['images'], tools.MAPS)
        self.assertEqual(len({m['id'] for m in tools.MAPS}), len(tools.MAPS))
        for m in tools.MAPS:
            with self.subTest(image=m['id']):
                result = self.show(m['id'])
                self.assertTrue(result['shown'])
                self.assertEqual(result['year'], m['year'])
                self.assertEqual(tools.Turn.current.maps[-1]['source_url'], m['page_url'])
                public = ROOT / 'web/public/assets/maps' / m['file']
                # 原本では全配信素材を検査。公開版は知識由来の画像を別途用意する。
                if canonical_path.is_file() or m['id'] == 'architecture':
                    self.assertTrue(public.is_file())
                original = ROOT / 'knowledge/assets/maps' / m['file']
                if original.is_file():
                    self.assertEqual(original.read_bytes(), public.read_bytes())
                self.assertIn(m['id'], tools.show_map.tool_spec['description'])

    def test_unknown_id_does_not_show_unrelated_campus_map(self):
        self.assertFalse(self.show('not-a-real-image')['shown'])
        self.assertEqual(tools.Turn.current.maps, [])

    def test_repeat_image_is_emitted_once(self):
        self.show('architecture')
        self.show('architecture')
        self.assertEqual(len(tools.Turn.current.maps), 1)

    def test_withdrawn_photos_cannot_be_shown_or_deployed(self):
        for image_id in ('venetian-room-2024', 'dining-2024', 'lounge-2024', 'expo-swag-2024', 'minorun-speaking-2024'):
            with self.subTest(image=image_id):
                self.assertFalse(self.show(image_id)['shown'])
                self.assertNotIn(image_id, tools.show_map.tool_spec['description'])
                self.assertFalse((ROOT / 'web/public/assets/maps' / (image_id + '.jpg')).exists())
        self.assertEqual(tools.Turn.current.maps, [])
