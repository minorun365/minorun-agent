"""イメージ掃除の残す判定。使用中を消すと本番が次の起動で落ちるので、判定だけを検査する。"""
import datetime
import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.modules.setdefault("boto3", types.ModuleType("boto3"))
spec = importlib.util.spec_from_file_location("prune_images", ROOT / "infra/lambda/prune-images/prune_images.py")
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


class DroppableTest(unittest.TestCase):
    now = datetime.datetime(2026, 10, 20)
    images = [image("sha256:used", 1), image("sha256:old", 2), image("sha256:recent", 15)]

    def test_drops_only_old_and_unused(self):
        drop = prune.droppable(self.images, {"sha256:used"}, self.now)
        self.assertEqual([i["imageDigest"] for i in drop], ["sha256:old"])

    def test_keeps_images_within_age_limit_even_if_unused(self):
        drop = prune.droppable(self.images, {self.images[0]["imageDigest"]}, self.now, min_age_days=30)
        self.assertEqual(drop, [])


class HandlerTest(unittest.TestCase):
    def test_dry_run_does_not_apply(self):
        seen = {}
        original_run, original_boto = prune.run, prune.boto3
        prune.boto3 = types.SimpleNamespace(Session=lambda: None)
        prune.run = lambda session, apply: (seen.update(apply=apply) or {"drop": 0}, [])
        try:
            prune.handler({"dry_run": True}, None)
            self.assertFalse(seen["apply"])
            prune.handler({}, None)
            self.assertTrue(seen["apply"])
        finally:
            prune.run, prune.boto3 = original_run, original_boto


if __name__ == "__main__":
    unittest.main()
