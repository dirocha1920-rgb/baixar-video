#!/bin/sh
# Inicia o servidor. Antes, atualiza o yt-dlp: Instagram e TikTok mudam com
# frequência e as correções saem em versões novas. Desligue com AUTO_UPDATE_YTDLP=0.
set -e

if [ "${AUTO_UPDATE_YTDLP:-1}" = "1" ]; then
  echo "Atualizando yt-dlp e gallery-dl..."
  timeout 120 pip install --no-cache-dir --quiet --upgrade "yt-dlp[default,curl-cffi]" gallery-dl \
    || echo "Não foi possível atualizar agora; seguindo com as versões instaladas."
fi

mkdir -p "${TMP_DIR:-/tmp/vdl}"
cd /app/server
# Um único processo: os limites por IP e o cache ficam em memória
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" --timeout-keep-alive 10 --no-server-header
