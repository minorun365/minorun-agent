"""ログイン（Google のみ）の Cognito を作る・揃える（何度流しても同じ状態になる）。

ユーザープール・Hosted UI のドメイン・Google の接続・アプリクライアントを作り、infra/.auth.json に書く。
個人情報は持たない（2026-10-25 みのるん指示）：Google へは openid だけを頼み、受け取るのは本人を区別する番号（sub）だけ。
名前・メールアドレス・画像は Cognito に残らない。
Google の client secret は SSM の SecureString（/minorun-agent/google-oauth-client-secret）から読み、
CDK の記録に平文で残さないためにスクリプトで作っている。Google の OAuth クライアントは Google Cloud で先に作り、
ID を環境変数 GOOGLE_CLIENT_ID で渡す。新しいドメインを使うときは、その承認済みリダイレクト URI に
https://<DOMAIN>.auth.us-west-2.amazoncognito.com/oauth2/idpresponse を1行足す（Console でしか足せない）。

  uv run --with boto3 python infra/scripts/auth.py
"""

import json
import os
from pathlib import Path

import boto3

REGION = "us-west-2"
POOL_NAME = "minorun-agent-users"
DOMAIN = "minorun-agent"
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
SECRET_PARAM = "/minorun-agent/google-oauth-client-secret"
APP_URLS = [os.environ.get("APP_URL", "https://agent.example.com/"), "http://localhost:5199/", "http://127.0.0.1:5199/"]

session = boto3.Session(region_name=REGION)
idp = session.client("cognito-idp")


def pool_id():
    for page in idp.get_paginator("list_user_pools").paginate(MaxResults=60):
        for p in page["UserPools"]:
            if p["Name"] == POOL_NAME:
                return p["Id"]
    r = idp.create_user_pool(
        PoolName=POOL_NAME,
        # Google 以外の入口は使わない。自分での登録（パスワード）は閉じ、Google から来た人だけを作る。
        # メールアドレスを名前に使わず、属性も持たない
        AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
        UsernameConfiguration={"CaseSensitive": False},
        AccountRecoverySetting={"RecoveryMechanisms": [{"Priority": 1, "Name": "admin_only"}]},
        DeletionProtection="ACTIVE",
        UserPoolTags={"Project": "minorun-agent"},
    )
    return r["UserPool"]["Id"]


def ensure_domain(pid):
    d = idp.describe_user_pool_domain(Domain=DOMAIN)["DomainDescription"]
    if not d.get("UserPoolId"):
        idp.create_user_pool_domain(Domain=DOMAIN, UserPoolId=pid, ManagedLoginVersion=1)
    elif d["UserPoolId"] != pid:
        raise SystemExit(f"ドメイン {DOMAIN} は別のプールが使っている")


def ensure_google(pid):
    secret = session.client("ssm").get_parameter(Name=SECRET_PARAM, WithDecryption=True)["Parameter"]["Value"]
    details = {"client_id": GOOGLE_CLIENT_ID, "client_secret": secret, "authorize_scopes": "openid"}
    mapping = {"username": "sub"}
    try:
        idp.describe_identity_provider(UserPoolId=pid, ProviderName="Google")
        idp.update_identity_provider(UserPoolId=pid, ProviderName="Google", ProviderDetails=details, AttributeMapping=mapping)
    except idp.exceptions.ResourceNotFoundException:
        idp.create_identity_provider(UserPoolId=pid, ProviderName="Google", ProviderType="Google",
                                     ProviderDetails=details, AttributeMapping=mapping)


def ensure_client(pid):
    conf = dict(
        UserPoolId=pid, ClientName="minorun-agent-web",
        RefreshTokenValidity=30, AccessTokenValidity=60, IdTokenValidity=60,
        TokenValidityUnits={"RefreshToken": "days", "AccessToken": "minutes", "IdToken": "minutes"},
        ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH"],
        SupportedIdentityProviders=["Google"],
        CallbackURLs=APP_URLS, LogoutURLs=APP_URLS,
        AllowedOAuthFlows=["code"],
        # aws.cognito.signin.user.admin は Cognito の中だけの範囲（個人情報は含まない）。Lambda インターセプターが GetUser で本人か確かめるのに要る
        AllowedOAuthScopes=["openid", "aws.cognito.signin.user.admin"],
        AllowedOAuthFlowsUserPoolClient=True, PreventUserExistenceErrors="ENABLED", EnableTokenRevocation=True,
    )
    for c in idp.list_user_pool_clients(UserPoolId=pid, MaxResults=60)["UserPoolClients"]:
        if c["ClientName"] == conf["ClientName"]:
            idp.update_user_pool_client(ClientId=c["ClientId"], **conf)
            return c["ClientId"]
    return idp.create_user_pool_client(GenerateSecret=False, **conf)["UserPoolClient"]["ClientId"]


def main():
    pid = pool_id()
    ensure_domain(pid)
    ensure_google(pid)
    cid = ensure_client(pid)
    out = {"userPoolId": pid, "clientId": cid, "domain": f"{DOMAIN}.auth.{REGION}.amazoncognito.com",
           "issuer": f"https://cognito-idp.{REGION}.amazonaws.com/{pid}",
           "googleRedirectUri": f"https://{DOMAIN}.auth.{REGION}.amazoncognito.com/oauth2/idpresponse"}
    Path(__file__).resolve().parent.parent.joinpath(".auth.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
