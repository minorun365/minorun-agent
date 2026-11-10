"""添付画像：本人の置き場だけを読む・JPEG だけ通す・読んだら消す・控えには印だけを残す。"""
import io
import json
import unittest
from unittest.mock import MagicMock, patch

import attachments

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
KEY = "up/" + "a" * 16 + "/" + "b" * 32 + ".jpg"


def s3_with(data):
    s3 = MagicMock()
    s3.get_object.return_value = {"ContentLength": len(data), "Body": io.BytesIO(data)}
    return s3


class OwnedKeyTests(unittest.TestCase):
    def test_only_own_prefix_is_accepted(self):
        self.assertEqual(attachments.owned_key(KEY, "a" * 16), KEY)
        self.assertIsNone(attachments.owned_key(KEY, "c" * 16))
        self.assertIsNone(attachments.owned_key("sessions/catalog.json", "a" * 16))
        self.assertIsNone(attachments.owned_key("up/" + "a" * 16 + "/../x.jpg", "a" * 16))
        self.assertIsNone(attachments.owned_key("", "a" * 16))


class LoadTests(unittest.TestCase):
    def load(self, s3):
        with patch.object(attachments, "UPLOAD_BUCKET", "bucket"), patch.object(attachments, "_s3", s3):
            return attachments.load(KEY)

    def test_jpeg_is_returned_and_deleted(self):
        s3 = s3_with(JPEG)
        self.assertEqual(self.load(s3), JPEG)
        s3.delete_object.assert_called_once_with(Bucket="bucket", Key=KEY)

    def test_non_jpeg_is_refused_but_still_deleted(self):
        s3 = s3_with(b"<svg onload=alert(1)>")
        self.assertIsNone(self.load(s3))
        s3.delete_object.assert_called_once()

    def test_too_large_is_refused(self):
        s3 = MagicMock()
        s3.get_object.return_value = {"ContentLength": attachments.MAX_BYTES + 1, "Body": io.BytesIO(b"")}
        self.assertIsNone(self.load(s3))

    def test_missing_object_is_refused(self):
        s3 = MagicMock()
        s3.get_object.side_effect = RuntimeError("NoSuchKey")
        self.assertIsNone(self.load(s3))

    def test_without_bucket_nothing_is_read(self):
        s3 = s3_with(JPEG)
        with patch.object(attachments, "UPLOAD_BUCKET", ""), patch.object(attachments, "_s3", s3):
            self.assertIsNone(attachments.load(KEY))
        s3.get_object.assert_not_called()


class StripTests(unittest.TestCase):
    def test_image_becomes_placeholder_and_original_is_kept(self):
        messages = [
            {"role": "user", "content": [{"text": "これは何？"}, attachments.block(JPEG)]},
            {"role": "assistant", "content": [{"text": "案内板です"}]},
        ]
        stripped = attachments.strip(messages)
        self.assertEqual(stripped[0]["content"], [{"text": "これは何？"}, {"text": attachments.PLACEHOLDER}])
        self.assertIs(stripped[1], messages[1])
        self.assertIn("image", messages[0]["content"][1])  # 会話の入れ物の中の画像は残る
        json.dumps(stripped, ensure_ascii=False)  # 控えとして保存できる形になっている

    def test_without_image_same_list_is_returned(self):
        messages = [{"role": "user", "content": [{"text": "こんにちは"}]}]
        self.assertIs(attachments.strip(messages), messages)


if __name__ == "__main__":
    unittest.main()
