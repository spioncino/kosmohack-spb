#!/bin/sh
# Локальный API и воркер по pid-файлам. Использование: scripts/dev_server.sh start|stop|status|restart|worker-start|worker-stop|tts-start|tts-stop|tunnel-start|tunnel-stop|tunnel-url [port]
# Оба процесса загружают код при старте: после изменения кода нужен restart, иначе задания из очереди выполняет старый воркер.
set -e
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$0")/.."
PORT="${2:-8000}"
PIDFILE="data/api.pid"
LOG="data/api.log"

stop_pidfile() {
  if [ -f "$1" ] && kill "$(cat "$1")" 2>/dev/null; then
    pid="$(cat "$1")"; rm -f "$1"
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    echo "$2 остановлен"
  else
    echo "$2 не запущен"
  fi
}

case "$1" in
  start)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then echo "уже запущен, pid $(cat "$PIDFILE")"; exit 0; fi
    mkdir -p data
    setsid nohup uv run uvicorn evarisk.api.app:app --host 0.0.0.0 --port "$PORT" > "$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do sleep 2; curl -sS -m 3 "localhost:$PORT/health" >/dev/null 2>&1 && { echo "запущен, pid $(cat "$PIDFILE"), порт $PORT"; exit 0; }; done
    echo "не поднялся, см. $LOG"; tail -20 "$LOG"; exit 1;;
  stop)
    stop_pidfile "$PIDFILE" "api";;
  status)
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null && curl -sS -m 3 "localhost:$PORT/health" || echo "не запущен"
    [ -f data/worker.pid ] && kill -0 "$(cat data/worker.pid)" 2>/dev/null && echo " воркер pid $(cat data/worker.pid)" || echo " воркер не запущен"
    [ -f data/tts.pid ] && kill -0 "$(cat data/tts.pid)" 2>/dev/null && echo " синтез речи pid $(cat data/tts.pid)" || echo " синтез речи не запущен"
    [ -f data/tunnel.pid ] && kill -0 "$(cat data/tunnel.pid)" 2>/dev/null && echo " туннель pid $(cat data/tunnel.pid): $("$SELF" tunnel-url)" || echo " туннель не запущен";;
  restart)
    "$SELF" worker-stop
    "$SELF" stop "$PORT"
    "$SELF" start "$PORT"
    "$SELF" worker-start;;
  worker-start)
    if [ -f data/worker.pid ] && kill -0 "$(cat data/worker.pid)" 2>/dev/null; then echo "воркер уже запущен"; exit 0; fi
    mkdir -p data
    setsid nohup uv run python -m evarisk.worker > data/worker.log 2>&1 < /dev/null &
    echo $! > data/worker.pid; echo "воркер запущен, pid $(cat data/worker.pid)";;
  worker-stop)
    stop_pidfile data/worker.pid "воркер";;
  tts-start)
    if [ -f data/tts.pid ] && kill -0 "$(cat data/tts.pid)" 2>/dev/null; then echo "синтез речи уже запущен"; exit 0; fi
    [ -x tts/.venv/bin/uvicorn ] || { echo "нет окружения tts/.venv, см. tts/README.md"; exit 1; }
    mkdir -p data
    setsid nohup tts/.venv/bin/uvicorn server:app --app-dir tts --host 127.0.0.1 --port 8010 > data/tts.log 2>&1 < /dev/null &
    echo $! > data/tts.pid; echo "синтез речи запущен, pid $(cat data/tts.pid), порт 8010";;
  tts-stop)
    stop_pidfile data/tts.pid "синтез речи";;
  tunnel-start)
    # Быстрый туннель Cloudflare: временный публичный адрес *.trycloudflare.com к локальному API, без учётной записи.
    # Адрес меняется при каждом запуске; авторизации у сервиса нет, адрес даётся только своим.
    # Протокол http2 задан явно: в сетях с закрытым исходящим UDP 7844 туннель по QUIC не соединяется.
    if [ -f data/tunnel.pid ] && kill -0 "$(cat data/tunnel.pid)" 2>/dev/null; then echo "туннель уже запущен"; "$SELF" tunnel-url; exit 0; fi
    BIN="$(command -v cloudflared || echo "$HOME/.local/bin/cloudflared")"
    [ -x "$BIN" ] || { echo "нет cloudflared: github.com/cloudflare/cloudflared/releases, файл cloudflared-linux-amd64 в ~/.local/bin"; exit 1; }
    mkdir -p data; : > data/tunnel.log
    setsid nohup "$BIN" tunnel --no-autoupdate --protocol http2 --url "http://localhost:$PORT" > data/tunnel.log 2>&1 < /dev/null &
    echo $! > data/tunnel.pid
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do sleep 2; grep -q 'trycloudflare.com' data/tunnel.log && break; done
    "$SELF" tunnel-url;;
  tunnel-stop)
    stop_pidfile data/tunnel.pid "туннель";;
  tunnel-url)
    grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' data/tunnel.log 2>/dev/null | tail -1 || echo "адреса туннеля нет, см. data/tunnel.log";;
  *) echo "start|stop|status|restart|worker-start|worker-stop|tts-start|tts-stop|tunnel-start|tunnel-stop|tunnel-url [port]"; exit 2;;
esac
