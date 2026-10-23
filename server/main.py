"""
Servidor web: API + páginas do site.

Rotas:
  GET  /                    página completa da ferramenta
  GET  /embed               versão para colocar dentro de outro site (iframe)
  POST /api/info            {"url": "..."} -> título, autor, miniatura, qualidades
  GET  /api/download?url=…&quality=1080   -> arquivo .mp4 (ou .mp3 com quality=audio)
  GET  /api/config          limites atuais (para a interface)

  Lote:
  POST /api/batch/check     {"text", "handle_platform"} -> o que foi reconhecido
  POST /api/batch           {"text", "handle_platform", "per_profile", "quality"} -> {"id"}
  GET  /api/batch/{id}      progresso
  POST /api/batch/{id}/cancel
  GET  /api/batch/{id}/file/{n}   um vídeo do lote
  GET  /api/batch/{id}/zip        todos os vídeos prontos num .zip

  Revisão com IA:
  GET  /api/review/config   se a IA está ligada, se pede senha, quantas análises restam
  POST /api/review          (multipart) arquivo + plataformas + texto -> laudo da IA

  GET  /health              status + versão do yt-dlp
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

import yt_dlp
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

import ai_review
import batch
import downloader as dl
import profiles

log = logging.getLogger("vdl")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# Configuração (variáveis de ambiente)
# ---------------------------------------------------------------------------
def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


WEB_DIR = Path(os.getenv("WEB_DIR", Path(__file__).resolve().parent.parent / "web"))
def _origins(raw: str) -> list[str]:
    """Aceita endereços colados de qualquer jeito: com barra no fim, com caminho, sem https."""
    out = []
    for item in raw.replace(";", ",").replace(" ", ",").split(","):
        item = item.strip().rstrip("/")
        if not item:
            continue
        if "://" not in item:
            item = "https://" + item
        scheme, rest = item.split("://", 1)
        origin = scheme.lower() + "://" + rest.split("/", 1)[0].lower()
        if origin not in out:
            out.append(origin)
    return out


# Sites que podem usar esta API direto do navegador (ex.: o seu site na Vercel)
ALLOWED_ORIGINS = _origins(os.getenv("ALLOWED_ORIGINS", ""))
RATE_LIMIT = _int("RATE_LIMIT_PER_MIN", 12)            # requisições por IP por minuto
DOWNLOADS_PER_HOUR = _int("DOWNLOADS_PER_HOUR", 40)    # downloads por IP por hora
MAX_CONCURRENT = _int("MAX_CONCURRENT_DOWNLOADS", 3)   # downloads simultâneos no servidor
# Quantos proxies confiáveis ficam na frente do app: Caddy = 1, Render = 2, nenhum = 0.
# O IP do visitante é lido nessa posição, contando do fim do X-Forwarded-For.
PROXY_HOPS = _int("TRUST_PROXY_HOPS", 1)
# Alternativa: cabeçalho que o provedor preenche com o IP real (ex.: CF-Connecting-IP)
CLIENT_IP_HEADER = os.getenv("CLIENT_IP_HEADER", "").strip().lower()

def _cookies_path() -> str | None:
    """
    O yt-dlp regrava o arquivo de cookies depois de usar. Se ele estiver num lugar só de
    leitura (ex.: "Secret Files" do Render, em /etc/secrets), usa uma cópia gravável.
    """
    path = os.getenv("COOKIES_FILE") or None
    if not path or not os.path.isfile(path) or os.access(path, os.W_OK):
        return path
    tmp_root = os.getenv("TMP_DIR") or dl.tempfile.gettempdir()
    os.makedirs(tmp_root, exist_ok=True)
    copy_path = os.path.join(tmp_root, "cookies.txt")
    try:
        shutil.copyfile(path, copy_path)
        os.chmod(copy_path, 0o600)
        return copy_path
    except OSError:
        return path


SETTINGS = dl.Settings(
    cookies_file=_cookies_path(),
    max_filesize_mb=_int("MAX_FILESIZE_MB", 300),
    proxy=os.getenv("YTDLP_PROXY") or None,
    tmp_root=os.getenv("TMP_DIR") or dl.tempfile.gettempdir(),
)
CACHE = dl.InfoCache(ttl=_int("INFO_CACHE_SECONDS", 600))
SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT)

BATCH_JOBS_PER_HOUR = _int("BATCH_JOBS_PER_HOUR", 4)  # lotes por IP por hora
BATCH = batch.BatchManager(
    SETTINGS, CACHE,
    batch.BatchSettings(
        max_sources=_int("BATCH_MAX_SOURCES", 20),
        max_per_profile=_int("BATCH_MAX_PER_PROFILE", 50),
        max_items=_int("BATCH_MAX_ITEMS", 150),
        max_total_mb=_int("BATCH_MAX_TOTAL_MB", 3000),
        min_free_mb=_int("BATCH_MIN_FREE_MB", 1024),
        ttl_minutes=_int("BATCH_TTL_MINUTES", 60),
        workers=_int("BATCH_WORKERS", 1),
    ),
    root=os.getenv("BATCH_DIR") or os.path.join(SETTINGS.tmp_root, "batches"),
)


# ---------------------------------------------------------------------------
# Limites por IP (em memória; suficiente para um único servidor)
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self):
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_prune = time.time()

    def allow(self, ip: str, bucket: str, limit: int, window: int) -> bool:
        if limit <= 0:
            return True
        now = time.time()
        with self._lock:
            q = self._hits[(ip, bucket)]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if now - self._last_prune > 600:
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > 3600]:
                    del self._hits[k]
                self._last_prune = now
            return True


LIMITER = RateLimiter()


def client_ip(request: Request) -> str:
    if CLIENT_IP_HEADER:
        direct = (request.headers.get(CLIENT_IP_HEADER) or "").strip()
        if direct:
            return direct
    fwd = request.headers.get("x-forwarded-for")
    if fwd and PROXY_HOPS > 0:
        ips = [p.strip() for p in fwd.split(",") if p.strip()]
        if ips:
            return ips[max(0, len(ips) - PROXY_HOPS)]
    return request.client.host if request.client else "unknown"


def error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)


def check_rate(request: Request) -> JSONResponse | None:
    if not LIMITER.allow(client_ip(request), "req", RATE_LIMIT, 60):
        return error("Muitas tentativas seguidas. Espere um minuto e tente de novo.", 429)
    return None


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "yt_dlp": yt_dlp.version.__version__})


def _info_sync(url: str) -> dict:
    info = CACHE.get(url)
    if info is None:
        info = dl.extract(url, SETTINGS)
        CACHE.put(url, info)
    thumb = dl.fetch_thumbnail(info, SETTINGS)
    return dl.public_info(info, url, thumb)


async def api_info(request: Request) -> Response:
    if (r := check_rate(request)) is not None:
        return r
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return error("Requisição inválida.", 400)
    try:
        url = dl.validate_url(str((body or {}).get("url", "")))
        data = await run_in_threadpool(_info_sync, url)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})
    except dl.UserError as e:
        return error(e.message, e.status)
    except Exception:  # noqa: BLE001
        log.exception("erro inesperado em /api/info")
        return error("Erro inesperado no servidor. Tente novamente.", 500)


def _download_sync(url: str, quality: str) -> dl.DownloadResult:
    if not SLOTS.acquire(timeout=45):
        raise dl.UserError("O servidor está ocupado agora. Tente de novo em instantes.", 503)
    try:
        return dl.download(url, quality, SETTINGS, CACHE)
    finally:
        SLOTS.release()


async def api_download(request: Request) -> Response:
    if (r := check_rate(request)) is not None:
        return r
    ip = client_ip(request)
    if not LIMITER.allow(ip, "dl", DOWNLOADS_PER_HOUR, 3600):
        return error("Você atingiu o limite de downloads por hora. Volte mais tarde.", 429)
    quality = (request.query_params.get("quality") or "").strip()[:12]
    try:
        url = dl.validate_url(request.query_params.get("url", ""))
        started = time.time()
        res = await run_in_threadpool(_download_sync, url, quality)
    except dl.UserError as e:
        return error(e.message, e.status)
    except Exception:  # noqa: BLE001
        log.exception("erro inesperado em /api/download")
        return error("Erro inesperado no servidor. Tente novamente.", 500)

    size = res.path.stat().st_size
    log.info("download ok %s q=%s %.1fMB em %.1fs", dl.platform_of(url), quality or "best",
             size / 1e6, time.time() - started)
    return FileResponse(
        res.path,
        media_type=res.media_type,
        filename=res.filename,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        background=BackgroundTask(shutil.rmtree, res.workdir, ignore_errors=True),
    )


# ---------------------------------------------------------------------------
# Lote
# ---------------------------------------------------------------------------
async def api_config(_: Request) -> JSONResponse:
    bs = BATCH.bs
    return JSONResponse({
        "batch": {
            "max_sources": bs.max_sources,
            "max_per_profile": bs.max_per_profile,
            "max_items": bs.max_items,
            "ttl_minutes": bs.ttl_minutes,
            "instagram_profiles": bool(SETTINGS.cookies_file and os.path.isfile(SETTINGS.cookies_file)
                                       and profiles.gallery_dl_bin()),
        }
    })


async def _json_body(request: Request) -> dict | None:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _handle_platform(body: dict) -> str | None:
    hp = str(body.get("handle_platform") or "").lower()
    return hp if hp in ("tiktok", "instagram") else None


async def api_batch_check(request: Request) -> Response:
    if not LIMITER.allow(client_ip(request), "check", 90, 60):
        return error("Muitas tentativas seguidas. Espere um minuto.", 429)
    body = await _json_body(request)
    if body is None:
        return error("Requisição inválida.", 400)
    text = str(body.get("text", ""))[:20000]
    sources, problems = profiles.parse_input(text, _handle_platform(body), BATCH.bs.max_sources)
    needs_platform = any(p.endswith("escolha se os @ são do TikTok ou do Instagram") for p in problems)
    return JSONResponse({"sources": [s.as_dict() for s in sources], "problems": problems,
                         "needs_platform": needs_platform})


async def api_batch_create(request: Request) -> Response:
    if (r := check_rate(request)) is not None:
        return r
    ip = client_ip(request)
    body = await _json_body(request)
    if body is None:
        return error("Requisição inválida.", 400)
    text = str(body.get("text", ""))[:20000]
    sources, problems = profiles.parse_input(text, _handle_platform(body), BATCH.bs.max_sources)
    if not sources:
        return error(problems[0] if problems else "Cole pelo menos um link ou perfil.", 400)
    if BATCH.active_jobs_for(ip) >= 1:
        return error("Você já tem um lote em andamento. Espere ele terminar ou cancele.", 429)
    if not LIMITER.allow(ip, "batch", BATCH_JOBS_PER_HOUR, 3600):
        return error("Você atingiu o limite de lotes por hora. Volte mais tarde.", 429)
    try:
        per_profile = int(body.get("per_profile") or 10)
    except (TypeError, ValueError):
        per_profile = 10
    job = BATCH.create(ip, sources, problems, str(body.get("quality") or "max"), per_profile)
    log.info("lote %s criado: %d fontes", job.id, len(sources))
    return JSONResponse({"id": job.id}, status_code=201)


def _job_or_404(request: Request):
    job = BATCH.get(request.path_params["job_id"])
    if not job:
        return None, error("Esse lote não existe mais (os arquivos expiram depois de um tempo).", 404)
    return job, None


async def api_batch_status(request: Request) -> Response:
    job, err = _job_or_404(request)
    if err:
        return err
    data = job.public(BATCH.position(job.id))
    data["expires_at"] = BATCH.expires_at(job)
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


async def api_batch_cancel(request: Request) -> Response:
    job, err = _job_or_404(request)
    if err:
        return err
    BATCH.cancel(job)
    return JSONResponse({"ok": True})


async def api_batch_file(request: Request) -> Response:
    job, err = _job_or_404(request)
    if err:
        return err
    try:
        index = int(request.path_params["index"])
    except ValueError:
        return error("Arquivo inválido.", 400)
    item = BATCH.file_path(job, index)
    if not item:
        return error("Esse vídeo não está pronto.", 404)
    media = "audio/mpeg" if item.filename.endswith(".mp3") else "video/mp4"
    return FileResponse(item.path, media_type=media, filename=item.filename,
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


async def api_batch_zip(request: Request) -> Response:
    job, err = _job_or_404(request)
    if err:
        return err
    if not any(i.status == "done" for i in job.items):
        return error("Ainda não há vídeos prontos neste lote.", 404)
    name = f"videos_{time.strftime('%Y-%m-%d_%H%M')}.zip"
    return StreamingResponse(
        BATCH.zip_stream(job), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store",
                 "X-Content-Type-Options": "nosniff"},
    )


# ---------------------------------------------------------------------------
# Revisão de criativos com IA
# ---------------------------------------------------------------------------
REVIEW_SLOTS = threading.BoundedSemaphore(_int("REVIEW_CONCURRENT", 2))


async def api_review_config(request: Request) -> JSONResponse:
    return JSONResponse({
        "enabled": bool(ai_review.api_key()),
        "requires_code": bool(ai_review.access_code()),
        "remaining": ai_review.COUNTER.remaining(client_ip(request)),
        "max_mb": ai_review.max_bytes() // (1024 * 1024),
    }, headers={"Cache-Control": "no-store"})


def _review_sync(path, filename, ctype, platforms, sector, texts, specs):
    if not REVIEW_SLOTS.acquire(timeout=60):
        raise ai_review.ReviewError("Muita gente usando a IA agora. Tente de novo em um minuto.", 503)
    try:
        return ai_review.review(path, filename, ctype, platforms, sector, texts, specs)
    finally:
        REVIEW_SLOTS.release()


async def api_review(request: Request) -> Response:
    if (r := check_rate(request)) is not None:
        return r
    if not ai_review.api_key():
        return error("A análise com IA ainda não foi ligada neste servidor (falta a GEMINI_API_KEY).", 503)
    try:
        form = await request.form(max_files=1, max_fields=10)
    except Exception:  # noqa: BLE001
        return error("Envio inválido.", 400)
    code = ai_review.access_code()
    if code and (form.get("code") or "").strip() != code:
        return error("Senha da análise com IA incorreta.", 403)
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return error("Envie o arquivo do criativo.", 400)
    try:
        platforms = json.loads(form.get("platforms") or "{}")
        texts = json.loads(form.get("texts") or "{}")
        platforms = {k: (str(v)[:20] if v else None) for k, v in platforms.items() if k in ("meta", "tiktok", "kwai")}
        texts = {k: str(v)[:2500] for k, v in texts.items() if k in ("primary", "headline", "description", "name")}
    except (ValueError, AttributeError):
        return error("Dados do anúncio inválidos.", 400)
    sector = str(form.get("sector") or "")[:60]
    specs = str(form.get("specs") or "")[:800]
    ip = client_ip(request)
    tmp_dir = Path(SETTINGS.tmp_root) / "review"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"rv_{int(time.time() * 1000)}_{threading.get_ident()}"
    size, limit = 0, ai_review.max_bytes()
    try:
        with open(tmp, "wb") as out:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    return error(f"Arquivo grande demais para a IA (máximo {limit // 1048576} MB).", 413)
                out.write(chunk)
        try:
            ai_review.COUNTER.take(ip)
        except ai_review.ReviewError as e:
            return error(e.message, e.status)
        try:
            result = await run_in_threadpool(_review_sync, str(tmp), upload.filename or "criativo", upload.content_type or "", platforms, sector, texts, specs)
        except ai_review.ReviewError as e:
            if e.status >= 500:
                ai_review.COUNTER.give_back(ip)
            return error(e.message, e.status)
        except Exception:  # noqa: BLE001
            ai_review.COUNTER.give_back(ip)
            log.exception("erro inesperado em /api/review")
            return error("Erro inesperado na análise. Tente novamente.", 500)
        result["restantes"] = ai_review.COUNTER.remaining(ip)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


class Pages(StaticFiles):
    """Arquivos do site com cabeçalhos de segurança e cache sensato."""

    async def get_response(self, path: str, scope) -> Response:
        resp = await super().get_response(path, scope)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if path.endswith((".css", ".js", ".svg", ".png", ".woff2")):
            resp.headers["Cache-Control"] = "public, max-age=3600"
        else:
            resp.headers["Cache-Control"] = "no-cache"
        return resp


async def embed(_: Request) -> Response:
    return FileResponse(WEB_DIR / "embed.html", headers={"Cache-Control": "no-cache"})


@contextlib.asynccontextmanager
async def lifespan(_app):
    Path(SETTINGS.tmp_root).mkdir(parents=True, exist_ok=True)
    dl.cleanup_stale(SETTINGS.tmp_root, older_than=0)
    BATCH.start()
    log.info("IA: %s", "ligada" if ai_review.api_key() else "desligada (sem GEMINI_API_KEY)")
    log.info("yt-dlp %s | cookies: %s | downloads simultâneos: %d | sites liberados: %s",
             yt_dlp.version.__version__, "sim" if SETTINGS.cookies_file else "não", MAX_CONCURRENT,
             ", ".join(ALLOWED_ORIGINS) or "nenhum (só a própria página)")
    yield


routes = [
    Route("/health", health),
    Route("/api/info", api_info, methods=["POST"]),
    Route("/api/download", api_download, methods=["GET"]),
    Route("/api/config", api_config),
    Route("/api/batch/check", api_batch_check, methods=["POST"]),
    Route("/api/batch", api_batch_create, methods=["POST"]),
    Route("/api/batch/{job_id:str}", api_batch_status),
    Route("/api/batch/{job_id:str}/cancel", api_batch_cancel, methods=["POST"]),
    Route("/api/batch/{job_id:str}/file/{index:int}", api_batch_file),
    Route("/api/batch/{job_id:str}/zip", api_batch_zip),
    Route("/api/review/config", api_review_config),
    Route("/api/review", api_review, methods=["POST"]),
    Route("/embed", embed),
]
if WEB_DIR.is_dir():
    routes.append(Mount("/", app=Pages(directory=WEB_DIR, html=True), name="web"))

middleware = [Middleware(GZipMiddleware, minimum_size=1024)]
if ALLOWED_ORIGINS:
    # Permite que o seu site (em outro domínio, ex.: Vercel) chame a API direto
    middleware.append(Middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        expose_headers=["Content-Disposition", "Content-Length"],
    ))

app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
