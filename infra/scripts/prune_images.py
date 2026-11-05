"""イメージ置き場の掃除を手元で試す。本番は Lambda minorun-agent-prune-images が週1回動かす。

  AWS_PROFILE=sandbox uv run --no-project --with boto3 python infra/scripts/prune_images.py          # 消す候補を出すだけ
  AWS_PROFILE=sandbox uv run --no-project --with boto3 python infra/scripts/prune_images.py --apply  # 実際に消す

残す判定は infra/lambda/prune-images/prune_images.py が正本。
"""
import json
import os
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda" / "prune-images"))
import prune_images as core  # noqa: E402


def main(apply):
    result, refs = core.run(boto3.Session(profile_name=os.environ.get("AWS_PROFILE")), apply)
    print(json.dumps(result, ensure_ascii=False))
    for ref in refs:
        print("  使用中:", ref.split("/", 1)[-1][:90])
    print("消しました。" if apply else "消していません。消すときは --apply を付けます。")


if __name__ == "__main__":
    main("--apply" in sys.argv)
