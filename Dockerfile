# Один образ для api и worker: команда воркера задаётся в deploy/compose.yml через command.
# Стадии frontend-deps и frontend собирают фронтенд (Vue 3 + Vite, каталог frontend/) через bun внутри образа:
# bun и node на хосте и VPS не нужны. Слой зависимостей пересобирается только при изменении package.json или bun.lock.
FROM oven/bun:1 AS frontend-deps

WORKDIR /app/frontend
COPY frontend/package.json frontend/bun.lock* ./
RUN bun install --frozen-lockfile

FROM frontend-deps AS frontend

COPY frontend/ ./
RUN bun run build

# Стадия builder собирает .venv: aacgmv2 в uv.lock есть только как sdist с C-расширением, ему нужен gcc.
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Зависимости ставятся до копирования исходников: слой пересобирается только при изменении pyproject.toml или uv.lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Пакет evarisk собирается hatchling из src/evarisk и ставится в .venv обычной установкой, без editable-ссылки на /app/src.
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Итоговый образ без компилятора и bun: интерпретатор, готовый .venv, конфиги, сборка фронтенда, старая страница и скрипты.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY src ./src
COPY config ./config
COPY web ./web
COPY scripts ./scripts
# Документы для раздела «Документация» страницы (GET /manual): README, CONTRIBUTING и docs/*.md.
COPY README.md CONTRIBUTING.md ./
COPY docs ./docs
# api раздаёт /app/frontend/dist с «/» (Settings.frontend_dist по умолчанию frontend/dist относительно /app), web/ с «/dev».
COPY --from=frontend /app/frontend/dist ./frontend/dist

# Процессы работают от пользователя app (uid 1000). /app остаётся у root и только читается;
# /data монтируется из compose, каталоги в образе нужны для запуска без монтирования; /data/mirror это
# зеркало архивов для докачки по запросу (D59), его пишет воркер. Все пути записи по умолчанию ведут в /data,
# включая SQLite: без этого `docker run` образа без compose падал с PermissionError на data/ внутри /app (D88).
RUN useradd --uid 1000 --user-group --create-home app \
    && mkdir -p /data/raw /data/assessments /data/mirror \
    && chown -R app:app /data
USER app

# В образе нет git: sha кода передаётся аргументом сборки GIT_SHA (make up и make deploy берут его из git rev-parse,
# суффикс -dirty при незакоммиченных изменениях в src/ и config/) и попадает в algorithm_version результатов.
# EVARISK_DATABASE_URL по умолчанию SQLite в /data для запуска без compose; compose подставляет Postgres.
ARG GIT_SHA=nogit
ENV PATH="/app/.venv/bin:$PATH" \
    EVARISK_RAW_ROOT=/data/raw \
    EVARISK_ASSESSMENTS_ROOT=/data/assessments \
    EVARISK_MIRROR_ROOT=/data/mirror \
    EVARISK_DATABASE_URL=sqlite:////data/evarisk.db \
    EVARISK_GIT_SHA=$GIT_SHA

EXPOSE 8000
CMD ["uvicorn", "evarisk.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
