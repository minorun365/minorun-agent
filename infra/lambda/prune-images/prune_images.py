"""cdkd のイメージ置き場（ECR）から、どこからも使っていない古いイメージを消す。

週1回 EventBridge から起動する。手元で試すときは infra/scripts/prune_images.py を使う。
この置き場は同じアカウントの別アプリと共用なので、期間で消すライフサイクルルールは使えない
（しばらく更新していないアプリの使用中イメージまで消える）。全 Runtime・全 Lambda・ECS の参照を
毎回読み直し、参照されているもの・新しい3枚・作って7日以内のもの（デプロイの途中を巻き込まないため）を残す。
読み取りや削除に失敗したら例外で止め、Lambda のエラーとして警報メールで知らせる。
"""
import datetime
import json

import boto3

REGION = "us-west-2"
REPO = None  # 実行時にアカウントIDから決める
KEEP_NEWEST = 3
MIN_AGE_DAYS = 7
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json")


def pages(client, method, key, **params):
    token_key = {"list_functions": ("Marker", "NextMarker")}.get(method, ("nextToken", "nextToken"))
    while True:
        res = getattr(client, method)(**params)
        yield from res.get(key, [])
        token = res.get(token_key[1])
        if not token:
            return
        params[token_key[0]] = token


def used_refs(session):
    """使用中のイメージURI（タグでもダイジェストでも）を集める。読めなければ例外で止める。"""
    refs = set()
    control = session.client("bedrock-agentcore-control", region_name=REGION)
    for runtime in pages(control, "list_agent_runtimes", "agentRuntimes"):
        rid = runtime["agentRuntimeId"]
        versions = {control.get_agent_runtime(agentRuntimeId=rid)["agentRuntimeVersion"]}
        for endpoint in pages(control, "list_agent_runtime_endpoints", "runtimeEndpoints", agentRuntimeId=rid):
            versions |= {v for v in (endpoint.get("liveVersion"), endpoint.get("targetVersion")) if v}
        for version in versions:
            detail = control.get_agent_runtime(agentRuntimeId=rid, agentRuntimeVersion=version)
            refs.add(detail["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"])
    lam = session.client("lambda", region_name=REGION)
    for fn in pages(lam, "list_functions", "Functions"):
        if fn.get("PackageType") == "Image":
            code = lam.get_function(FunctionName=fn["FunctionName"])["Code"]
            refs |= {code[k] for k in ("ImageUri", "ResolvedImageUri") if code.get(k)}
    ecs = session.client("ecs", region_name=REGION)
    for arn in pages(ecs, "list_task_definitions", "taskDefinitionArns", status="ACTIVE"):
        task = ecs.describe_task_definition(taskDefinition=arn)["taskDefinition"]
        refs |= {c["image"] for c in task["containerDefinitions"]}
    return {r for r in refs if REPO in r}


def select(images, refs, newest=KEEP_NEWEST):
    """残すダイジェストを決める。images は describe_images の行、refs はイメージURI。"""
    keep = {i["imageDigest"] for i in sorted(images, key=lambda i: i["imagePushedAt"], reverse=True)[:newest]}
    missing = []
    for ref in refs:
        tail = ref.split("/", 1)[-1]
        if "@" in tail:
            digest = tail.split("@", 1)[1]
            hits = [i for i in images if i["imageDigest"] == digest]
        else:
            tag = tail.rsplit(":", 1)[-1]
            hits = [i for i in images if tag in i.get("imageTags", [])]
        if hits:
            keep |= {i["imageDigest"] for i in hits}
        else:
            missing.append(ref)
    return keep, missing


def with_children(ecr, images, keep):
    """複数アーキテクチャの目次を残すなら、その中身のイメージも残す。"""
    by_digest = {i["imageDigest"]: i for i in images}
    targets = [d for d in keep if by_digest[d].get("imageManifestMediaType") in INDEX_TYPES]
    result = set(keep)
    for i in range(0, len(targets), 100):
        res = ecr.batch_get_image(repositoryName=REPO, imageIds=[{"imageDigest": d} for d in targets[i:i + 100]])
        if res.get("failures"):
            raise RuntimeError(res["failures"])
        for image in res["images"]:
            result |= {m["digest"] for m in json.loads(image["imageManifest"]).get("manifests", [])}
    return result


def droppable(images, keep, now, min_age_days=MIN_AGE_DAYS):
    """消してよいイメージ。残す判定に入らず、作ってから一定の日数を過ぎたものだけ。"""
    limit = now - datetime.timedelta(days=min_age_days)
    return [i for i in images if i["imageDigest"] not in keep and i["imagePushedAt"] < limit]


def run(session, apply):
    global REPO
    account = session.client("sts").get_caller_identity()["Account"]
    REPO = f"cdkd-container-assets-{account}-{REGION}"
    ecr = session.client("ecr", region_name=REGION)
    images = list(pages(ecr, "describe_images", "imageDetails", repositoryName=REPO))
    refs = used_refs(session)
    keep, missing = select(images, refs)
    if missing:
        raise RuntimeError(f"参照先が置き場に見つからないので止めます: {missing}")
    keep = with_children(ecr, images, keep)
    drop = droppable(images, keep, datetime.datetime.now(datetime.timezone.utc))
    gb = lambda rows: round(sum(i["imageSizeInBytes"] for i in rows) / 1024 ** 3, 2)
    result = {"apply": apply, "images": len(images), "images_gb": gb(images), "used_refs": len(refs),
              "keep": len(images) - len(drop), "drop": len(drop), "drop_gb": gb(drop)}
    if apply:
        for i in range(0, len(drop), 100):
            res = ecr.batch_delete_image(repositoryName=REPO, imageIds=[{"imageDigest": d["imageDigest"]} for d in drop[i:i + 100]])
            if res.get("failures"):
                raise RuntimeError(f"削除に失敗しました: {res['failures']}")
    return result, sorted(refs)


def handler(event, _context):
    """{"dry_run": true} で起動すると、消す数を出すだけで消さない。"""
    result, _ = run(boto3.Session(), not (event or {}).get("dry_run"))
    print(json.dumps(result, ensure_ascii=False))
    return result
