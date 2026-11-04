"""イメージ掃除の残す判定。使用中を消すと本番が次の起動で落ちるので、判定だけを検査する。"""
import datetime
import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.modules.setdefault("boto3", types.ModuleType("boto3"))
spec = importlib.util.spec_from_file_location("prune_images", ROOT / "infra/scripts/prune_images.py")
prune = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prune)


def image(digest, day, tags=()):
    return {"imageDigest": digest, "imageTags": list(tags), "imageSizeInBytes": 1,
            "imagePushedAt": datetime.datetime(2026, 10, day)}


class SelectTest(unittest.TestCase):
    images = [image("sha256:a", 1, ["old-used"]), image("sha256:b", 2), image("sha256:c", 3), image("sha256:d", 4),
              image("sha256:e", 5), image("sha256:f", 6, ["other-app"])]

    def test_keeps_newest_and_used(self):
        repo = "000000000000.dkr.ecr.us-west-2.amazonaws.com/test-assets"
        keep, missing = prune.select(self.images, {f"{repo}:old-used", f"{repo}@sha256:b"})
        self.assertEqual(missing, [])
        self.assertEqual(keep, {"sha256:a", "sha256:b", "sha256:d", "sha256:e", "sha256:f"})

    def test_reports_missing_reference(self):
        repo = "000000000000.dkr.ecr.us-west-2.amazonaws.com/test-assets"
        _, missing = prune.select(self.images, {f"{repo}:gone"})
        self.assertEqual(missing, [f"{repo}:gone"])


if __name__ == "__main__":
    unittest.main()
