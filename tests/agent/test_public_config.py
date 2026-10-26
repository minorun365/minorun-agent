"""実行設定の欠落を検出する。"""
import unittest
from unittest.mock import patch
import app
import prompt
import topics
import research

class Configuration(unittest.TestCase):
    def test_missing_configuration_stops(self):
        with patch.object(prompt, "BASE", ""):
            with self.assertRaises(RuntimeError):
                app._require_configuration()
    def test_configured_values_are_accepted(self):
        with patch.object(prompt, "BASE", "test answer"), patch.object(research, "CHECK", "test check"), patch.object(topics, "JUDGE", "test judge"), patch.object(topics, "NEXT", "test next"), patch.object(topics, "SUMMARY", "test summary"), patch.object(topics, "SUMMARY_GROUNDING", "test grounding"):
            app._require_configuration()
