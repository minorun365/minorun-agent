"""挿絵（いらすとやの画像一式）を、非公開の S3（s3://<SLIDES_BUCKET>/ill/）から入れ物の /tmp/ill へ取り込む。

挿絵の画像はいらすとやの規約（素材の再配布の禁止）に合わせて公開リポジトリに入れていないので、使うときに取り込む。
使い方: python3 /app/bin/illust.py    取り込んだ枚数を出す。2回目からは何もしない
挿絵の作り直しは slides/tools/build_illust.py。
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3

DIR = Path(os.environ.get("ILL_DIR", "/tmp/ill"))


def ensure():
    if (DIR / "catalog.tsv").exists():
        return len((DIR / "catalog.tsv").read_text().splitlines()) - 1
    bucket = os.environ["SLIDES_BUCKET"]
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-west-2"))
    DIR.mkdir(parents=True, exist_ok=True)
    keys, kwargs = [], {"Bucket": bucket, "Prefix": "ill/"}
    while True:
        page = s3.list_objects_v2(**kwargs)
        keys += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(".png")]
        if not page.get("IsTruncated"):
            break
        kwargs["ContinuationToken"] = page["NextContinuationToken"]
    with ThreadPoolExecutor(16) as pool:
        list(pool.map(lambda k: s3.download_file(bucket, k, str(DIR / k[4:])), keys))
    tmp = DIR / "catalog.tsv.part"
    s3.download_file(bucket, "ill/catalog.tsv", str(tmp))
    tmp.rename(DIR / "catalog.tsv")  # 目録は最後に置く（途中で止まったら次の呼び出しで取り直す）
    return len((DIR / "catalog.tsv").read_text().splitlines()) - 1


if __name__ == "__main__":
    try:
        print(f"illustrations ready: {ensure()} illustrations in {DIR}")
    except Exception as e:
        sys.exit(f"挿絵を取り込めませんでした（{type(e).__name__}）。挿絵なしで進めてください")
