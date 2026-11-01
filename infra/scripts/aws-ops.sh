#!/bin/bash
# クラウドのセッションから AWS を触るときの入口。.github/workflows/aws-ops.yml を起動し、終わるまで待ってログを出す。
# VM に AWS の鍵は無い。ワークフローが OIDC で読み取り＋モデル呼び出しの権限を得て実行する（デプロイはできない）。
#
#   bash infra/scripts/aws-ops.sh eval grounding --summary
#   bash infra/scripts/aws-ops.sh script check_runtime
#   bash infra/scripts/aws-ops.sh aws logs tail /aws/bedrock-agentcore/runtimes/... --since 1h
#
# クラウドのトークンはワークフローを起動できない（dispatch は 403）。そこで、今の HEAD に依頼ファイル
# .aws-ops/request.json を足したコミットを作業ツリーに触れずに作り、使い捨てのブランチ aws-ops/... へ push する。
# 未 push・未コミットの変更は載らない（載るのはコミット済みの HEAD）。ブランチはワークフローが最後に消す。
# gh の GraphQL はクラウドで使えないので REST だけで書く。
set -euo pipefail
cd "$(dirname "$0")/../.."   # どこから打っても、このリポジトリに対して依頼する

task=${1:?eval / script / aws のどれか}
shift
name=""
if [ "$task" != aws ]; then
  name=${1:?ファイル名（拡張子なし）}
  shift
fi
args="$*"

repo=$(git remote get-url origin | sed -E 's#^(https://[^/]+/|git@[^:]+:|.*/git/)##; s#\.git$##' | grep -oE '[^/]+/[^/]+$')
branch="aws-ops/$(date -u +%Y%m%d-%H%M%S)-$$"

# 依頼ファイルを足したツリーを、一時的な index で組み立てる（作業ツリーと本来の index には触れない）
request=$(TASK="$task" NAME="$name" ARGS="$args" python3 -c \
  'import json, os; print(json.dumps({k: os.environ[k.upper()] for k in ("task", "name", "args")}, ensure_ascii=False))')
blob=$(printf '%s\n' "$request" | git hash-object -w --stdin)
tmp_index=$(mktemp)
trap 'rm -f "$tmp_index"' EXIT
GIT_INDEX_FILE=$tmp_index git read-tree HEAD
GIT_INDEX_FILE=$tmp_index git update-index --add --cacheinfo "100644,$blob,.aws-ops/request.json"
tree=$(GIT_INDEX_FILE=$tmp_index git write-tree)
commit=$(git commit-tree "$tree" -p HEAD -m "aws-ops: $task $name $args")
git push -q origin "$commit:refs/heads/$branch"
echo "依頼しました: $branch（$task $name $args）"

run_id=""
for _ in $(seq 1 30); do
  run_id=$(gh api "repos/$repo/actions/workflows/aws-ops.yml/runs?branch=$branch&event=push&per_page=1" --jq '.workflow_runs[0].id // empty')
  [ -n "$run_id" ] && break
  sleep 4
done
[ -n "$run_id" ] || { echo "実行が始まりません。GitHub の Actions 画面を確かめてください" >&2; exit 1; }
echo "実行中: https://github.com/$repo/actions/runs/$run_id"

job_id=""
for _ in $(seq 1 360); do
  read -r job_id status conclusion < <(gh api "repos/$repo/actions/runs/$run_id/jobs" \
    --jq '.jobs[] | select(.name == "run") | "\(.id) \(.status) \(.conclusion // "-")"')
  [ "${status:-}" = completed ] && break
  sleep 10
done

# ログは productionresultssaN.blob.core.windows.net から配られる（環境の許可ドメインに入れてある）
# 実行の手前（依頼の検査・認証・準備）で落ちたときは目印の行が無いので、末尾とエラー行を出す
# 新しい gh は制御文字入りの出力を --allow-escape-sequences 無しでは出さない。古い gh はこの旗を知らない
logs() { gh api --allow-escape-sequences "$@" 2>/dev/null || gh api "$@" 2>/dev/null; }
if log=$(logs "repos/$repo/actions/jobs/$job_id/logs"); then
  log=$(sed -E 's/^[0-9T:.-]+Z //' <<<"$log")
  if grep -qE 'aws-ops: (eval|script|aws) を開始' <<<"$log"; then
    # 次のステップ（成果物の保存・後片付け）の見出しで止める
    awk '/aws-ops: (eval|script|aws) を開始/{on=1} on && /^##\[group\]Run /{exit} on' <<<"$log" | tail -n 400
  else
    echo "（実行の手前で止まりました。エラー行と末尾を出します）"
    grep -E '##\[error\]' <<<"$log" || true
    tail -n 30 <<<"$log"
  fi
else
  echo "ログを取得できませんでした。GitHub のアプリで上の URL を開いてください" >&2
fi
echo "結果: $conclusion"
[ "$conclusion" = success ]
