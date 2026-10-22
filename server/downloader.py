"""
Núcleo de download: extração com yt-dlp, escolha de qualidade e download.

Separado do servidor web para poder ser testado sem rede.
"""
from __future__ import annotations

import base64
import copy
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

# Domínios aceitos (inclui subdomínios: www., m., vm., vt. ...)
ALLOWED_HOSTS = ("instagram.com", "instagr.am", "tiktok.com")

# Codecs que tocam em praticamente qualquer aparelho/programa
COMPATIBLE_VCODECS = ("avc1", "h264")
CODEC_LABELS = {"h265": "HEVC", "hevc": "HEVC", "hvc1": "HEVC", "hev1": "HEVC",
                "bytevc1": "HEVC", "vp9": "VP9", "vp09": "VP9", "av01": "AV1", "av1": "AV1"}


class UserError(Exception):
    """Erro com mensagem pronta para mostrar ao visitante."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# Validação de link
# ---------------------------------------------------------------------------
def validate_url(url: str) -> str:
    url = (url or "").strip()
    if len(url) > 2048:
        raise UserError("Link longo demais.", 400)
    if url and "://" not in url:
        url = "https://" + url
    try:
        p = urlparse(url)
    except ValueError:
        raise UserError("Link inválido.", 400)
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not any(
        host == h or host.endswith("." + h) for h in ALLOWED_HOSTS
    ):
        raise UserError("Cole um link do Instagram ou do TikTok.", 400)
    return url


def platform_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return "tiktok" if host.endswith("tiktok.com") else "instagram"


# ---------------------------------------------------------------------------
# Opções do yt-dlp
# ---------------------------------------------------------------------------
@dataclass
class Settings:
    cookies_file: str | None = None
    max_filesize_mb: int = 300
    proxy: str | None = None
    tmp_root: str = field(default_factory=tempfile.gettempdir)


def base_opts(s: Settings) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "max_filesize": s.max_filesize_mb * 1024 * 1024,
        "cachedir": False,
    }
    if s.cookies_file and Path(s.cookies_file).is_file():
        opts["cookiefile"] = s.cookies_file
    if s.proxy:
        opts["proxy"] = s.proxy
    return opts


def friendly_error(e: Exception) -> str:
    msg = str(e).lower()
    if any(k in msg for k in ("private", "login", "log in", "cookies", "rate-limit",
                              "rate limit", "not available", "unavailable", "429")):
        return ("Não consegui acessar esse vídeo. Ele pode ser privado, ter sido apagado "
                "ou a plataforma bloqueou o acesso temporariamente. Tente de novo em alguns minutos.")
    if "no video" in msg or "there is no video" in msg or "no formats" in msg \
            or "requested format is not available" in msg:
        return "Esse post não tem vídeo (pode ser só foto)."
    if "unsupported url" in msg:
        return "Esse tipo de link não é suportado. Use o link de um post, reel ou vídeo."
    if "larger than max-filesize" in msg or "max_filesize" in msg or "file is larger" in msg:
        return "Esse vídeo é grande demais para baixar por aqui."
    if "timed out" in msg or "timeout" in msg:
        return "A plataforma demorou demais para responder. Tente novamente."
    return "Não foi possível obter o vídeo. Confira o link e tente de novo."


# ---------------------------------------------------------------------------
# Extração + cache curto (evita consultar a plataforma duas vezes)
# ---------------------------------------------------------------------------
class InfoCache:
    def __init__(self, ttl: int = 600, max_items: int = 256):
        self.ttl, self.max_items = ttl, max_items
        self._data: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def get(self, url: str) -> dict | None:
        with self._lock:
            item = self._data.get(url)
            if item and time.time() - item[0] < self.ttl:
                return item[1]
            self._data.pop(url, None)
            return None

    def put(self, url: str, info: dict) -> None:
        with self._lock:
            if len(self._data) >= self.max_items:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                self._data.pop(oldest, None)
            self._data[url] = (time.time(), info)


def pick_video_entry(info: dict) -> dict:
    """Carrosséis/playlists: devolve o primeiro item que tem vídeo."""
    if info.get("_type") in ("playlist", "multi_video") or info.get("entries") is not None:
        for entry in info.get("entries") or []:
            if entry and any(_has_video(f) for f in entry.get("formats") or [entry]):
                return entry
        raise UserError("Esse post não tem vídeo (pode ser só foto).")
    if not any(_has_video(f) for f in info.get("formats") or [info]):
        raise UserError("Esse post não tem vídeo (pode ser só foto).")
    return info


# Campos que o yt-dlp preenche ao ESCOLHER um formato. Ficam fora do cache para que
# cada download (1080p, 720p, áudio...) faça a sua própria escolha do zero.
_SELECTION_KEYS = {
    "format", "format_id", "format_note", "ext", "protocol", "url", "manifest_url", "fragments",
    "width", "height", "resolution", "fps", "vcodec", "acodec", "vbr", "abr", "tbr", "asr",
    "filesize", "filesize_approx", "dynamic_range", "audio_channels", "aspect_ratio",
    "stretched_ratio", "language", "requested_formats", "requested_downloads",
    "requested_subtitles", "_has_drm", "epoch", "_version",
}


def strip_selection(info: dict) -> dict:
    if not info.get("formats"):
        return info  # vídeo com um único formato: os campos de cima SÃO o formato
    return {k: v for k, v in info.items() if k not in _SELECTION_KEYS}


def extract(url: str, s: Settings) -> dict:
    """Consulta a plataforma e devolve o info-dict do vídeo (sem baixar)."""
    try:
        with yt_dlp.YoutubeDL(base_opts(s)) as ydl:
            info = ydl.extract_info(url, download=False)
            entry = pick_video_entry(info)
            return strip_selection(ydl.sanitize_info(entry))
    except UserError:
        raise
    except (DownloadError, ExtractorError) as e:
        raise UserError(friendly_error(e))


# ---------------------------------------------------------------------------
# Escolha de qualidade
# ---------------------------------------------------------------------------
def _has_video(f: dict) -> bool:
    vc = f.get("vcodec")
    if vc == "none":
        return False
    # Formatos sem codec declarado (ex.: MP4 progressivo do Instagram) contam como vídeo
    return bool(f.get("width") or f.get("height") or (vc and vc != "none"))


def _is_audio_only(f: dict) -> bool:
    return f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")


def _has_audio(f: dict) -> bool:
    ac = f.get("acodec")
    return ac != "none"  # None = desconhecido; formatos progressivos costumam ter áudio


def _codec_family(f: dict) -> str | None:
    vc = (f.get("vcodec") or "").lower().split(".")[0]
    return vc or None


def _codec_label(f: dict) -> str | None:
    fam = _codec_family(f)
    if fam is None or fam in COMPATIBLE_VCODECS:
        return None
    return CODEC_LABELS.get(fam, fam.upper())


def _compat_rank(f: dict) -> int:
    fam = _codec_family(f)
    if fam in COMPATIBLE_VCODECS:
        return 2
    if fam is None:
        return 1
    return 0


def _res(f: dict) -> int:
    """Resolução 'p' (lado menor): um Reel 1080x1920 é 1080p."""
    dims = [d for d in (f.get("width"), f.get("height")) if d]
    return min(dims) if dims else 0


def _usable(f: dict) -> bool:
    if not _has_video(f):
        return False
    if (f.get("preference") or 0) <= -100:          # TikTok bytevc2: quase nada reproduz
        return False
    note = (f.get("format_note") or "").lower()
    if "watermark" in note:                          # versões com marca d'água
        return False
    if (f.get("protocol") or "").startswith(("f4m", "rtmp")):
        return False
    return True


def _size(*fmts: dict) -> int | None:
    total = 0
    for f in fmts:
        sz = f.get("filesize") or f.get("filesize_approx")
        if not sz:
            return None
        total += int(sz)
    return total


def build_options(info: dict) -> list[dict]:
    """
    Lista de qualidades para o visitante escolher, da maior para a menor,
    mais a opção "só áudio". Cada opção traz o seletor de formato do yt-dlp.

    Regras:
      - nunca usa versões com marca d'água nem codecs que quase nada reproduz;
      - uma opção por resolução; dentro da mesma resolução prefere H.264
        (abre em qualquer aparelho) e depois a ordem de qualidade do yt-dlp;
      - vídeo sem áudio (DASH do Instagram) é combinado com o melhor áudio.
    """
    formats = info.get("formats") or [info]
    # yt-dlp já entrega 'formats' ordenado do pior para o melhor
    ranked = list(enumerate(formats))
    audio = [f for _, f in ranked if _is_audio_only(f)]
    best_audio = None
    if audio:
        m4a = [f for f in audio if f.get("ext") in ("m4a", "mp4") or
               (f.get("acodec") or "").startswith("mp4a")]
        best_audio = (m4a or audio)[-1]

    best_by_res: dict[int, tuple[tuple, dict]] = {}
    for idx, f in ranked:
        if not _usable(f):
            continue
        needs_audio = f.get("acodec") == "none"
        if needs_audio and not best_audio:
            continue
        key = (_compat_rank(f), idx)
        r = _res(f)
        if r not in best_by_res or key > best_by_res[r][0]:
            best_by_res[r] = (key, f)

    options = []
    for r in sorted(best_by_res, reverse=True):
        f = best_by_res[r][1]
        if f.get("acodec") == "none":
            selector = f"{f['format_id']}+{best_audio['format_id']}"
            size = _size(f, best_audio)
        else:
            selector = f["format_id"]
            size = _size(f)
        codec = _codec_label(f)
        label = f"{r}p" if r else "Original"
        options.append({
            "key": str(r) if r else "orig",
            "label": label,
            "codec": codec,
            "width": f.get("width"),
            "height": f.get("height"),
            "filesize": size,
            "format": selector,
            "kind": "video",
        })

    if not options:
        raise UserError("Não encontrei uma versão desse vídeo que dê para baixar.")

    # Remove resoluções muito baixas quando há opções melhores (ruído na interface)
    if len(options) > 4:
        options = options[:4]

    audio_src = best_audio
    options.append({
        "key": "audio",
        "label": "Só áudio",
        "codec": "MP3",
        "width": None,
        "height": None,
        "filesize": None,
        "format": audio_src["format_id"] if audio_src else options[0]["format"],
        "kind": "audio",
    })
    return options


def public_info(info: dict, url: str, thumb_data: str | None) -> dict:
    opts = build_options(info)
    return {
        "platform": platform_of(url),
        "id": info.get("id"),
        "title": (info.get("description") or info.get("title") or "").strip()[:300],
        "uploader": info.get("uploader_id") or info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration"),
        "thumbnail": thumb_data,
        "options": [{k: o[k] for k in ("key", "label", "codec", "width", "height", "filesize", "kind")}
                    for o in opts],
    }


# ---------------------------------------------------------------------------
# Miniatura embutida (as CDNs do Instagram/TikTok bloqueiam exibir em outros sites)
# ---------------------------------------------------------------------------
THUMB_HOST_SUFFIXES = ("cdninstagram.com", "fbcdn.net", "tiktokcdn.com", "tiktokcdn-us.com",
                       "tiktokcdn-eu.com", "ibyteimg.com", "byteimg.com", "muscdn.com")
THUMB_MAX_BYTES = 600 * 1024


def fetch_thumbnail(info: dict, s: Settings) -> str | None:
    url = info.get("thumbnail")
    thumbs = info.get("thumbnails") or []
    candidates = [url] + [t.get("url") for t in reversed(thumbs)]
    for u in candidates:
        if not u:
            continue
        p = urlparse(u)
        host = (p.hostname or "").lower()
        if p.scheme != "https" or not any(host == h or host.endswith("." + h) for h in THUMB_HOST_SUFFIXES):
            continue
        try:
            with yt_dlp.YoutubeDL(base_opts(s)) as ydl:
                resp = ydl.urlopen(u)
                ctype = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0]
                if not ctype.startswith("image/"):
                    continue
                data = resp.read(THUMB_MAX_BYTES + 1)
                if len(data) > THUMB_MAX_BYTES:
                    continue
                return f"data:{ctype};base64," + base64.b64encode(data).decode()
        except Exception:  # noqa: BLE001 - miniatura é opcional
            continue
    return None


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
@dataclass
class DownloadResult:
    path: Path
    filename: str
    media_type: str
    workdir: str


def _safe(text: str, limit: int = 40) -> str:
    keep = "".join(c if c.isalnum() or c in "-_." else "_" for c in (text or ""))
    keep = "_".join(filter(None, keep.split("_")))
    return keep[:limit] or "video"


def _download_opts(s: Settings, workdir: str, option: dict) -> dict:
    opts = base_opts(s) | {
        "outtmpl": os.path.join(workdir, "%(id)s.%(ext)s"),
        "format": option["format"],
        "paths": {"temp": workdir},
        "overwrites": True,
    }
    if option["kind"] == "audio":
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "0",   # VBR de melhor qualidade (~245 kbps)
        }]
    else:
        opts["merge_output_format"] = "mp4"
        # Faz o MP4 abrir/tocar imediatamente em celulares e players web
        opts["postprocessor_args"] = {"merger+ffmpeg_o": ["-movflags", "+faststart"]}
        # Se vier outro contêiner (ex.: webm), remuxa para mp4 sem perder qualidade
        opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]
    return opts


def choose_option(options: list[dict], quality: str) -> dict:
    """
    quality: chave de uma opção ("1080", "audio"...), "max" (maior resolução) ou
    "compat" (maior resolução em H.264, que abre em qualquer aparelho).
    """
    if quality == "compat":
        h264 = [o for o in options if o["kind"] == "video" and not o["codec"]]
        if h264:
            return h264[0]
    return next((o for o in options if o["key"] == quality), options[0])


def download(url: str, quality: str, s: Settings, cache: InfoCache) -> DownloadResult:
    info = cache.get(url)
    fresh = info is None
    if fresh:
        info = extract(url, s)
        cache.put(url, info)

    options = build_options(info)
    option = choose_option(options, quality)

    workdir = tempfile.mkdtemp(prefix="vdl_", dir=s.tmp_root)
    try:
        try:
            _run_download(info, s, workdir, option)
        except (DownloadError, ExtractorError, OSError):
            if fresh:
                raise
            # Links da plataforma expiraram: extrai de novo e tenta mais uma vez
            info = extract(url, s)
            cache.put(url, info)
            options = build_options(info)
            option = choose_option(options, quality)
            shutil.rmtree(workdir, ignore_errors=True)
            os.makedirs(workdir, exist_ok=True)
            _run_download(info, s, workdir, option)

        files = [f for f in Path(workdir).iterdir()
                 if f.is_file() and not f.name.endswith((".part", ".ytdl", ".temp"))]
        if not files:
            raise UserError("O download não gerou nenhum arquivo. Tente novamente.")
        path = max(files, key=lambda f: f.stat().st_size)
    except UserError:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    except (DownloadError, ExtractorError, OSError) as e:
        shutil.rmtree(workdir, ignore_errors=True)
        raise UserError(friendly_error(e))
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise

    who = _safe(info.get("uploader_id") or info.get("uploader") or "", 30)
    tag = "audio" if option["kind"] == "audio" else option["label"]
    parts = [platform_of(url), who, _safe(str(info.get("id") or "video"), 30), tag]
    filename = "_".join(p for p in parts if p) + path.suffix
    media_type = "audio/mpeg" if path.suffix == ".mp3" else "video/mp4"
    return DownloadResult(path=path, filename=filename, media_type=media_type, workdir=workdir)


def _run_download(info: dict, s: Settings, workdir: str, option: dict) -> None:
    # Cópia profunda: o yt-dlp altera o dict durante o download, e o original fica no cache
    with yt_dlp.YoutubeDL(_download_opts(s, workdir, option)) as ydl:
        ydl.process_ie_result(copy.deepcopy(info), download=True)


def cleanup_stale(tmp_root: str, older_than: int = 3600) -> None:
    """Remove pastas temporárias esquecidas (ex.: servidor reiniciado no meio de um download)."""
    now = time.time()
    for d in Path(tmp_root).glob("vdl_*"):
        try:
            if d.is_dir() and now - d.stat().st_mtime > older_than:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
