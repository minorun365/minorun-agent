"""独自ドメイン（環境変数 APP_DOMAIN）の証明書と DNS をそろえる（何度流しても同じ状態になる）。

証明書はデプロイ先のアカウント（AWS_PROFILE）の us-east-1、ドメインは Route 53 のホストゾーン（HOSTED_ZONE_ID）にある。
ホストゾーンが別のアカウントにあるときは DNS_PROFILE にそのプロファイルを入れる。CDK の1つのスタックでは
アカウントをまたげないので、ここで API を直接呼ぶ。

  uv run --with boto3 python infra/scripts/domain.py cert    # 証明書を取り、検証の CNAME を入れ、infra/.edge.json を書く
  uv run --with boto3 python infra/scripts/domain.py alias   # MinorunWeb のデプロイ後、ドメインを CloudFront へ向ける

.edge.json には WAF の ARN（MinorunEdge が SSM に置く）も写す。MinorunWeb はこのファイルを読んで付ける。
"""

import json
import os
import sys
import time
from pathlib import Path

import boto3

DOMAIN = os.environ["APP_DOMAIN"]
ZONE_ID = os.environ["HOSTED_ZONE_ID"]
CLOUDFRONT_ZONE_ID = "Z2FDTNDATAQYW2"  # CloudFront の別名レコードの固定値
WEB_ACL_PARAM = "/minorun-agent/edge/web-acl-arn"
EDGE_FILE = Path(__file__).resolve().parent.parent / ".edge.json"

app = boto3.Session()
acm = app.client("acm", region_name="us-east-1")
route53 = boto3.Session(profile_name=os.environ.get("DNS_PROFILE")).client("route53")


def upsert(name, rtype, **record):
    route53.change_resource_record_sets(HostedZoneId=ZONE_ID, ChangeBatch={"Changes": [{
        "Action": "UPSERT", "ResourceRecordSet": {"Name": name, "Type": rtype, **record}}]})


def find_cert():
    for page in acm.get_paginator("list_certificates").paginate(
            CertificateStatuses=["PENDING_VALIDATION", "ISSUED"]):
        for c in page["CertificateSummaryList"]:
            if c["DomainName"] == DOMAIN:
                return c["CertificateArn"]
    return None


def cert():
    arn = find_cert()
    if not arn:
        arn = acm.request_certificate(DomainName=DOMAIN, ValidationMethod="DNS",
                                      Tags=[{"Key": "Project", "Value": "minorun-agent"}])["CertificateArn"]
        print("requested", arn)
    # 検証用の CNAME は発行の直後には出てこないことがある
    for _ in range(30):
        desc = acm.describe_certificate(CertificateArn=arn)["Certificate"]
        rr = (desc.get("DomainValidationOptions") or [{}])[0].get("ResourceRecord")
        if rr:
            break
        time.sleep(2)
    else:
        raise SystemExit("検証の CNAME が出てこない")
    if desc["Status"] != "ISSUED":
        upsert(rr["Name"], "CNAME", TTL=300, ResourceRecords=[{"Value": rr["Value"]}])
        print("validation CNAME", rr["Name"])
        acm.get_waiter("certificate_validated").wait(CertificateArn=arn, WaiterConfig={"Delay": 15, "MaxAttempts": 60})
    print("certificate ISSUED", arn)

    web_acl = app.client("ssm", region_name="us-east-1").get_parameter(Name=WEB_ACL_PARAM)["Parameter"]["Value"]
    out = {"domain": DOMAIN, "certificateArn": arn, "webAclArn": web_acl}
    EDGE_FILE.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))


def alias():
    cf = app.client("cloudfront")
    dist = None
    for page in cf.get_paginator("list_distributions").paginate():
        for d in page.get("DistributionList", {}).get("Items", []):
            if DOMAIN in (d.get("Aliases", {}).get("Items") or []):
                dist = d
    if not dist:
        raise SystemExit(f"{DOMAIN} を別名に持つ CloudFront が無い。先に MinorunWeb をデプロイする")
    # 別アカウントの CloudFront でも、別名レコードは CloudFront の固定のゾーン ID で向けられる
    target = {"HostedZoneId": CLOUDFRONT_ZONE_ID, "DNSName": dist["DomainName"], "EvaluateTargetHealth": False}
    for rtype in ("A", "AAAA"):
        upsert(DOMAIN, rtype, AliasTarget=target)
    print("alias", DOMAIN, "->", dist["DomainName"], dist["Id"])


if __name__ == "__main__":
    {"cert": cert, "alias": alias}[sys.argv[1] if len(sys.argv) > 1 else "cert"]()
