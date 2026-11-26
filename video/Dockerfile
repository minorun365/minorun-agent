# 資料からずんだもんの解説動画を作る機能（AgentCore Runtime のコンテナ。2026-11-26）。
# 読み上げの VOICEVOX ENGINE を同じ入れ物の中で動かす（公式イメージから本体だけを写す。ENGINE は Ubuntu 24.04 で焼かれているので、glibc が同じ世代の trixie を土台にする）。
# 声のモデルは、ずんだもんノーマル（id 3）が入った 0.vvm だけを残す（全部入れると圧縮後2.7GB で、Runtime の2GB を超えるため）
FROM --platform=linux/arm64 voicevox/voicevox_engine:cpu-latest AS voicevox
USER root
RUN find /opt/voicevox_engine/model -name '*.vvm' ! -name '0.vvm' -delete

FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.13-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium nodejs npm ffmpeg poppler-utils libreoffice-impress libreoffice-core fonts-dejavu-core curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# 1コマずつ焼く Chromium の操作は puppeteer-core（Chromium は上で入れたものを使う）
RUN mkdir -p /opt/node && cd /opt/node && npm install puppeteer-core@24 && npm cache clean --force
# 書体：Noto Sans JP（字幕と図。PowerPoint を PDF にするときの日本語の書体にもなる）、Inter（番号と英字の飾り。みのるんグラスと同じ）。どれも SIL OFL
RUN mkdir -p /usr/share/fonts/truetype/web && cd /usr/share/fonts/truetype/web \
    && curl -fsSLo NotoSansJP.ttf "https://github.com/google/fonts/raw/main/ofl/notosansjp/NotoSansJP%5Bwght%5D.ttf" \
    && curl -fsSLo Inter.ttf "https://github.com/google/fonts/raw/main/ofl/inter/Inter%5Bopsz,wght%5D.ttf" \
    && fc-cache -f
COPY --from=voicevox /opt/voicevox_engine /opt/voicevox_engine

ENV PYTHONUNBUFFERED=1 CHROME_PATH=/usr/bin/chromium MARP_MODULES=/opt/node/node_modules/ HOME=/tmp VOICEVOX_RUN=/opt/voicevox_engine/run
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py *.mjs *.html ./

EXPOSE 8080
CMD ["python", "app.py"]
