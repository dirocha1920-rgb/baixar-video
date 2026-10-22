FROM python:3.12-slim

# ffmpeg junta vídeo + áudio do Instagram e converte para MP3
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Usuário sem privilégios, dono do ambiente Python (permite atualizar o yt-dlp ao iniciar)
RUN useradd --create-home --uid 10001 app \
 && python -m venv /opt/venv && chown -R app:app /opt/venv \
 && mkdir -p /tmp/vdl && chown app:app /tmp/vdl
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WEB_DIR=/app/web \
    TMP_DIR=/tmp/vdl \
    PORT=8000

WORKDIR /app
COPY --chown=app:app server/requirements.txt server/requirements.txt
USER app
RUN pip install --no-cache-dir -r server/requirements.txt

COPY --chown=app:app server/ server/
COPY --chown=app:app web/ web/
COPY --chown=app:app deploy/entrypoint.sh /app/entrypoint.sh

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)" || exit 1

ENTRYPOINT ["sh", "/app/entrypoint.sh"]
