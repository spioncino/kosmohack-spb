# Необязательный сервис синтеза речи для сводки экипажу: Silero TTS v4_ru на CPU.
# Модель (40 МБ) лежит в томе /data/tts и загружается при первом старте, если её там нет.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TTS_MODEL_PATH=/data/tts/v4_ru.pt

WORKDIR /app
COPY tts/requirements.txt ./
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install --no-cache-dir numpy fastapi "uvicorn[standard]"
COPY tts/server.py tts/page.html ./

RUN useradd --uid 1000 --user-group --create-home app && mkdir -p /data/tts && chown -R app:app /data
USER app

EXPOSE 8010
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8010"]
