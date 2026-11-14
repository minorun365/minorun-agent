# スライド工房（Lambda のコンテナ）。Marp CLI と Chromium で焼き、Python が段取りをする
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.13-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium nodejs npm fonts-dejavu-core curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g @marp-team/marp-cli@4.5.1 && npm cache clean --force

# 書体：Zen Kaku Gothic New（見出し）、Zen Maru Gothic（本文）、Inter（数字と英字の飾り）。どれも SIL OFL
RUN mkdir -p /usr/share/fonts/truetype/web && cd /usr/share/fonts/truetype/web \
    && for w in Regular Medium Bold; do curl -fsSLo ZenKakuGothicNew-$w.ttf "https://github.com/google/fonts/raw/main/ofl/zenkakugothicnew/ZenKakuGothicNew-$w.ttf"; done \
    && for w in Regular Bold; do curl -fsSLo ZenMaruGothic-$w.ttf "https://github.com/google/fonts/raw/main/ofl/zenmarugothic/ZenMaruGothic-$w.ttf"; done \
    && curl -fsSLo Inter.ttf "https://github.com/google/fonts/raw/main/ofl/inter/Inter%5Bopsz,wght%5D.ttf" \
    && fc-cache -f

ENV PYTHONUNBUFFERED=1 CHROME_PATH=/usr/lib/chromium/chromium MARP_MODULES=/usr/local/lib/node_modules/@marp-team/marp-cli/node_modules/ HOME=/tmp
WORKDIR /var/task
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py *.mjs ./
COPY theme/ ./theme/

ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["handler.handler"]
