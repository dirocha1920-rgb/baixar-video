"""
Entrada do download em lote: interpreta o texto colado pelo visitante e lista
os vídeos de perfis.

  - TikTok: vídeos do perfil, via yt-dlp (normalmente sem login).
  - Instagram: SOMENTE a aba Reels do perfil, via gallery-dl (exige cookies de login).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

import downloader as dl

IG_USER_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
TT_USER_RE = re.compile(r"^[A-Za-z0-9._-]{2,24}$")
# Primeiros segmentos do Instagram que não são nome de perfil
IG_RESERVED = {"p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct",
               "about", "developer", "legal", "web", "challenge", "s", "share", "api", "graphql"}


class InputError(Exception):
    pass


@dataclass
class Source:
    kind: str        # "video" | "profile"
    platform: str    # "tiktok" | "instagram"
    value: str       # URL do vídeo ou nome do perfil
    label: str       # como aparece para o visitante

    def as_dict(self) -> dict:
        return {"kind": self.kind, "platform": self.platform, "value": self.value, "label": self.label}


# ---------------------------------------------------------------------------
# Interpretação do texto colado
# ---------------------------------------------------------------------------
def classify(token: str, handle_platform: str | None) -> Source:
    t = token.strip().strip(",;")
    if not t:
        raise InputError("vazio")

    # @perfil ou perfil "solto" (nomes do Instagram podem ter ponto: fulano.ssa)
    low = t.lower()
    looks_like_url = "/" in t or ":" in t or any(d in low for d in ("instagram.com", "instagr.am", "tiktok.com"))
    if t.startswith("@") or not looks_like_url:
        name = t.lstrip("@")
        if handle_platform not in ("tiktok", "instagram"):
            raise InputError(f"{t}: escolha se os @ são do TikTok ou do Instagram")
        rx = TT_USER_RE if handle_platform == "tiktok" else IG_USER_RE
        if not rx.match(name):
            raise InputError(f"{t}: nome de perfil inválido")
        return Source("profile", handle_platform, name, f"@{name}")

    url = dl.validate_url(t)  # levanta UserError se não for Instagram/TikTok
    p = urlparse(url)
    host = (p.hostname or "").lower()
    parts = [s for s in p.path.split("/") if s]

    if host.endswith("tiktok.com"):
        if host.startswith(("vm.", "vt.")) or (parts and parts[0] == "t"):
            return Source("video", "tiktok", url, "link curto do TikTok")
        if parts and parts[0].startswith("@"):
            name = parts[0][1:]
            if len(parts) >= 3 and parts[1] in ("video", "photo"):
                return Source("video", "tiktok", url, f"vídeo de @{name}")
            if len(parts) == 1 and TT_USER_RE.match(name):
                return Source("profile", "tiktok", name, f"@{name}")
        if len(parts) >= 2 and parts[0] in ("embed", "share") or "video" in parts:
            return Source("video", "tiktok", url, "vídeo do TikTok")
        raise InputError(f"{t}: link do TikTok não reconhecido")

    # Instagram
    if not parts:
        raise InputError(f"{t}: link do Instagram sem perfil nem post")
    first = parts[0].lower()
    if first in ("p", "reel", "reels", "tv") and len(parts) >= 2:
        return Source("video", "instagram", url, f"{'post' if first == 'p' else 'Reel'} {parts[1][:14]}")
    if first not in IG_RESERVED:
        if len(parts) >= 3 and parts[1] in ("p", "reel", "reels", "tv"):
            return Source("video", "instagram", url, f"Reel de @{parts[0]}")
        if IG_USER_RE.match(parts[0]) and (len(parts) == 1 or parts[1] in ("reels", "")):
            return Source("profile", "instagram", parts[0], f"@{parts[0]}")
    raise InputError(f"{t}: link do Instagram não reconhecido (use o link do perfil, de um Reel ou de um post)")


def parse_input(text: str, handle_platform: str | None, max_sources: int) -> tuple[list[Source], list[str]]:
    tokens = [t for t in re.split(r"[\s,;]+", text or "") if t.strip()]
    sources, problems, seen = [], [], set()
    for tok in tokens:
        try:
            src = classify(tok, handle_platform)
        except InputError as e:
            problems.append(str(e))
            continue
        except dl.UserError:
            problems.append(f"{tok}: não é um link do Instagram nem do TikTok")
            continue
        key = (src.kind, src.platform, src.value.lower())
        if key in seen:
            continue
        seen.add(key)
        sources.append(src)
    if len(sources) > max_sources:
        problems.append(f"Só cabem {max_sources} links ou perfis por lote; o restante foi ignorado.")
        sources = sources[:max_sources]
    return sources, problems


# ---------------------------------------------------------------------------
# Listagem de perfis
# ---------------------------------------------------------------------------
@dataclass
class Found:
    url: str
    title: str = ""


def _profile_error(e: Exception, platform: str) -> str:
    msg = str(e).lower()
    if "private" in msg:
        return "o perfil é privado"
    if "does not have any videos" in msg:
        return "o perfil não tem vídeos"
    if "unable to extract secondary user id" in msg or "404" in msg or "not found" in msg:
        return "perfil não encontrado"
    if "login" in msg or "429" in msg or "rate" in msg:
        return f"o {'TikTok' if platform == 'tiktok' else 'Instagram'} bloqueou a consulta agora; tente mais tarde"
    return "não foi possível listar os vídeos do perfil"


def list_tiktok_profile(username: str, limit: int, s: dl.Settings) -> list[Found]:
    opts = dl.base_opts(s) | {"extract_flat": "in_playlist", "playlistend": limit, "lazy_playlist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.tiktok.com/@{username}", download=False)
    except (DownloadError, ExtractorError) as e:
        raise dl.UserError(_profile_error(e, "tiktok"))
    out = []
    for entry in (info.get("entries") or [])[:limit]:
        url = (entry or {}).get("url") or (entry or {}).get("webpage_url")
        if url:
            out.append(Found(url=url, title=(entry.get("description") or entry.get("title") or "")[:120]))
    if not out:
        raise dl.UserError("nenhum vídeo público encontrado no perfil")
    return out


def gallery_dl_bin() -> str | None:
    return shutil.which(os.getenv("GALLERY_DL_BIN", "gallery-dl"))


def parse_gallery_dl_json(raw: str) -> tuple[list[Found], list[str]]:
    """
    Interpreta a saída de `gallery-dl -j`: uma lista JSON de mensagens.
      [3, url, dados]   -> arquivo (Message.Url)
      [-1, {erro}]      -> erro
    Mantém só vídeos (video_url preenchido) e um item por post.
    """
    found, errors, seen = [], [], set()
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return [], ["resposta inválida do gallery-dl"]
    for msg in data if isinstance(data, list) else []:
        if not isinstance(msg, list) or not msg:
            continue
        if msg[0] == -1 and len(msg) > 1 and isinstance(msg[1], dict):
            errors.append(f"{msg[1].get('error', '')}: {msg[1].get('message', '')}")
            continue
        if msg[0] != 3 or len(msg) < 3 or not isinstance(msg[2], dict):
            continue
        kw = msg[2]
        if not kw.get("video_url"):
            continue  # foto
        product = (kw.get("product_type") or "clips").lower()
        if product not in ("clips", "reels"):
            continue  # vídeo comum do feed, não é Reel
        code = kw.get("post_shortcode") or kw.get("shortcode")
        if not code or code in seen:
            continue
        seen.add(code)
        caption = kw.get("description") or kw.get("caption") or ""
        found.append(Found(url=f"https://www.instagram.com/reel/{code}/", title=str(caption)[:120]))
    return found, errors


def list_instagram_reels(username: str, limit: int, s: dl.Settings) -> list[Found]:
    exe = gallery_dl_bin()
    if not exe:
        raise dl.UserError("o servidor não tem o gallery-dl instalado (necessário para perfis do Instagram)")
    if not (s.cookies_file and os.path.isfile(s.cookies_file)):
        raise dl.UserError("o Instagram só mostra a lista de Reels com login; "
                           "configure os cookies no servidor (veja o README)")
    cmd = [exe, "-j", "--range", f"1-{limit}", "--cookies", s.cookies_file,
           "-o", "extractor.instagram.videos=true", "-o", "extractor.instagram.previews=false"]
    if s.proxy:
        cmd += ["--proxy", s.proxy]
    cmd.append(f"https://www.instagram.com/{username}/reels/")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        raise dl.UserError("o Instagram demorou demais para responder; tente mais tarde")
    found, errors = parse_gallery_dl_json(proc.stdout)
    if found:
        return found[:limit]
    blob = " ".join(errors + [proc.stderr or ""]).lower()
    if "notfound" in blob or "not found" in blob or "404" in blob:
        raise dl.UserError("perfil não encontrado")
    if "private" in blob:
        raise dl.UserError("o perfil é privado")
    if any(k in blob for k in ("login", "401", "403", "challenge", "redirect", "auth")):
        raise dl.UserError("o Instagram recusou o login; os cookies podem ter expirado (veja o README)")
    if "429" in blob or "rate" in blob:
        raise dl.UserError("o Instagram limitou as consultas agora; tente mais tarde")
    if proc.returncode == 0:
        raise dl.UserError("o perfil não tem Reels")
    raise dl.UserError("não foi possível listar os Reels do perfil")


def list_profile(src: Source, limit: int, s: dl.Settings) -> list[Found]:
    if src.platform == "tiktok":
        return list_tiktok_profile(src.value, limit, s)
    return list_instagram_reels(src.value, limit, s)
