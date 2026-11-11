.PHONY: sync check check-fast lint typecheck test hooks fetch import-mirror api worker front front-dev up up-voice down logs deploy
COMPOSE = docker compose --env-file .env -f deploy/compose.yml
COMPOSE_PROD = $(COMPOSE) -f deploy/compose.prod.yml
# Профиль voice: адрес службы синтеза речи для api задаётся отдельным файлом, см. deploy/compose.voice.yml.
COMPOSE_VOICE = $(COMPOSE) -f deploy/compose.voice.yml
# Каталоги данных создаются от пользователя хоста до первого запуска, иначе Docker создаст их от root и контейнеры (uid 1000) не смогут писать.
DATA_DIRS = data/raw data/assessments data/mirror
# На VPS не копируются: окружения и ключи (.env, .env.llm, .env.prod; .env.prod кладётся отдельно как .env), git, кеши, архивы,
# локальные исключения клона (.git/info/exclude), зависимости и сборка фронтенда и весь каталог data/ (сырые данные,
# расчёты, данные Space-Track, модели, логи): data/ на сервере свой, и --delete его не трогает.
# Из data/ на сервер идёт только открытое зеркало data/mirror, отдельным rsync без --delete (цель deploy).
RSYNC_EXCLUDE = --exclude .venv --exclude .git --include .env.example --exclude '.env*' \
	--exclude __pycache__ --exclude '.*_cache' --exclude '*.zip' --exclude /data/ --exclude frontend/node_modules --exclude frontend/dist \
	$(if $(wildcard .git/info/exclude),--exclude-from=.git/info/exclude)
# Зависимости и сборка фронтенда на VPS не копируются: их делают стадии frontend-deps и frontend образа.
# Команда bun для целей front и front-dev: make front BUN=~/.bun/bin/bun, если bun не в PATH.
BUN ?= bun
# Sha кода для образа: короткий HEAD, суффикс -dirty при незакоммиченных изменениях в src/ и config/ (см. src/evarisk/version.py).
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo nogit)$(shell git status --porcelain -- src config 2>/dev/null | grep -q . && echo -dirty)
sync:
	uv sync --frozen || uv sync
lint:
	uv run ruff format --check src tests scripts
	uv run ruff check src tests scripts
typecheck:
	uv run mypy
test:
	uv run pytest
check-fast: lint typecheck
	uv run python scripts/check_honesty.py
	uv run python scripts/check_docs.py --staged
	uv run pytest -m "not e2e"
check: lint typecheck
	uv run python scripts/check_honesty.py
	uv run python scripts/check_docs.py --all
	uv run pytest
hooks:
	cp scripts/hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
fetch:
	uv run python scripts/mirror_archives.py $(if $(START),--start $(START)) $(if $(END),--end $(END))
	uv run python scripts/import_mirror.py
import-mirror:
	uv run python scripts/import_mirror.py
api:
	uv run uvicorn evarisk.api.app:app --reload --port 8000
worker:
	uv run python -m evarisk.worker
# Сборка фронтенда в frontend/dist; make api раздаёт её с «/», старую страницу web/ с «/dev».
front:
	cd frontend && $(BUN) install --frozen-lockfile && $(BUN) run build
front-dev:
	cd frontend && $(BUN) run dev
up:
	mkdir -p $(DATA_DIRS)
	GIT_SHA=$(GIT_SHA) $(COMPOSE) up -d --build
# То же с необязательным сервисом синтеза речи (профиль voice); EVARISK_TTS_URL=http://tts:8010 ставит compose.voice.yml.
up-voice:
	mkdir -p $(DATA_DIRS) data/tts
	GIT_SHA=$(GIT_SHA) $(COMPOSE_VOICE) --profile voice up -d --build
down:
	$(COMPOSE_VOICE) --profile voice down
logs:
	$(COMPOSE) logs -f --tail=100
# make deploy HOST=user@host: проект копируется в ~/evarisk на VPS, зеркало data/mirror дописывается к зеркалу сервера
# (файлы, докачанные воркером на сервере, не удаляются), локальный .env.prod (если есть) становится там .env,
# затем сборка, запуск с deploy/compose.prod.yml и импорт зеркала data/mirror в сырое хранилище.
# Каталоги data/ на сервере отдаются uid 1000 (пользователь app в образе), если деплой идёт от root.
deploy:
	@test -n "$(HOST)" || { echo "использование: make deploy HOST=user@host"; exit 2; }
	rsync -az --delete $(RSYNC_EXCLUDE) ./ $(HOST):~/evarisk/
	test ! -d data/mirror || rsync -az data/mirror $(HOST):~/evarisk/data/
	@test ! -f .env.prod || rsync -az .env.prod $(HOST):~/evarisk/.env
	ssh $(HOST) 'cd ~/evarisk && test -f .env || { echo "нет ~/evarisk/.env: скопируйте .env.example в .env и заполните"; exit 2; }; \
	  mkdir -p data/raw data/assessments data/mirror \
	  && { test "$$(id -u)" != 0 || { chown 1000:1000 data data/raw data/assessments && chown -R 1000:1000 data/mirror; }; } \
	  && GIT_SHA=$(GIT_SHA) docker compose --env-file .env -f deploy/compose.yml -f deploy/compose.prod.yml up -d --build \
	  && docker compose --env-file .env -f deploy/compose.yml -f deploy/compose.prod.yml exec -T api python scripts/import_mirror.py --mirror /data/mirror'
