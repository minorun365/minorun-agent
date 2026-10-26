FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.14-slim-bookworm

WORKDIR /app
# print を即座に CloudWatch Logs へ流す
ENV PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py *.json ./

EXPOSE 8080
# トレースを出すため OpenTelemetry の計測を付けて起動する（オンライン評価と GenAI Observability はこのトレースを読む）
CMD ["opentelemetry-instrument", "python", "app.py"]
