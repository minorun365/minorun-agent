# スライド作成機能（AgentCore Runtime のコンテナ。2026-11-16 に Lambda から移した）。Marp CLI と Chromium で焼き、Python が段取りをする。
# 同じイメージを AgentCore Harness の入れ物にも使う（graphviz と diagrams のアイコンは、エージェントが bin/arch.py でアーキテクチャ図を描くため）
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.13-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium nodejs npm fonts-dejavu-core curl ca-certificates graphviz \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g @marp-team/marp-cli@4.5.1 && npm cache clean --force

# 書体：Noto Sans JP（見出しと本文。かなを詰める palt を持つ可変書体）、Inter（数字と英字の飾り）。どれも SIL OFL
RUN mkdir -p /usr/share/fonts/truetype/web && cd /usr/share/fonts/truetype/web \
    && curl -fsSLo NotoSansJP.ttf "https://github.com/google/fonts/raw/main/ofl/notosansjp/NotoSansJP%5Bwght%5D.ttf" \
    && curl -fsSLo Inter.ttf "https://github.com/google/fonts/raw/main/ofl/inter/Inter%5Bopsz,wght%5D.ttf" \
    && fc-cache -f

ENV PYTHONUNBUFFERED=1 CHROME_PATH=/usr/lib/chromium/chromium MARP_MODULES=/usr/local/lib/node_modules/@marp-team/marp-cli/node_modules/ HOME=/tmp
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py *.mjs ./
COPY theme/ ./theme/
# Harness のエージェントが使う道具（焼いて測る・図を描く・できたものを渡す）とスキル。Harness も同じイメージで動く（2026-11-21）。
# 挿絵（いらすとやの画像一式）は規約上リポジトリに入れず、使うときに bin/illust.py が非公開の S3 から /tmp/ill へ取り込む
COPY bin/ ./bin/
COPY skills/ ./skills/

# AgentCore Runtime の決まり：8080 番で /invocations と /ping を受ける
EXPOSE 8080
CMD ["python", "app.py"]
