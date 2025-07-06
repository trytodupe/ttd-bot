FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:0.7.19 /uv /bin/uv

WORKDIR /app

ENV TZ=Asia/Shanghai

ENV UV_COMPILE_BYTECODE=1
ENV UV_FROZEN=1
ENV UV_LINK_MODE=copy
ENV ALL_PROXY=http://172.28.96.1:7890

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
  --mount=type=cache,target=/var/lib/apt,sharing=locked \
  apt-get update && apt-get install -y git

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --no-dev

COPY ./docker/gunicorn_conf.py /gunicorn_conf.py
COPY ./docker/start.sh /start.sh
RUN chmod +x /start.sh

ENV APP_MODULE=main:app
ENV MAX_WORKERS=1

COPY bot.py ./docker/main.py ./docker/prestart.sh /app/
COPY src /app/src/
RUN chmod +x /app/prestart.sh

CMD ["uv", "run", "--no-dev", "/start.sh"]
