"""監視ページ（https://agent.minoruonda.com/admin/）にログインできる唯一の利用者を作る。

  uv run --with boto3 python infra/scripts/admin_user.py

User Pool minorun-agent-admin は自分で登録できない設定なので、ここで環境変数 ADMIN_EMAIL の1人だけを作る。
ログインはメールのワンタイムコード（かパスキー）で、パスワードは使わない。Cognito はパスワードを外せないので、
状態を「確認済み」にするためだけに、誰にも渡さない長いランダムなパスワードを入れて捨てる。何度流しても同じ結果になる。
"""

import os
import secrets

import boto3

EMAIL = os.environ["ADMIN_EMAIL"]
POOL_NAME = "minorun-agent-admin"

idp = boto3.Session(region_name="us-west-2").client("cognito-idp")
pool = next(p["Id"] for p in idp.list_user_pools(MaxResults=60)["UserPools"] if p["Name"] == POOL_NAME)
try:
    idp.admin_create_user(UserPoolId=pool, Username=EMAIL, MessageAction="SUPPRESS",
                          UserAttributes=[{"Name": "email", "Value": EMAIL}, {"Name": "email_verified", "Value": "true"}])
    print("作成した")
except idp.exceptions.UsernameExistsException:
    print("もう居る")
idp.admin_set_user_password(UserPoolId=pool, Username=EMAIL, Password=secrets.token_urlsafe(32) + "Aa1!", Permanent=True)
users = idp.list_users(UserPoolId=pool)["Users"]
print(pool, [(u["Username"], u["UserStatus"]) for u in users])
