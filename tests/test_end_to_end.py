"""
Teste de ponta a ponta sem acessar Instagram/TikTok.

Sobe um servidor HTTP local servindo vídeos gerados pelo ffmpeg, simula as
listas de formatos que o yt-dlp devolve para cada plataforma e roda a API real
(seleção de qualidade, download, junção vídeo+áudio, MP3, erros e limites).

Uso:
  MEDIA_DIR=/caminho/com/os/videos python tests/test_end_to_end.py
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
MEDIA = Path(os.environ["MEDIA_DIR"])

os.environ.update({"RATE_LIMIT_PER_MIN": "1000", "DOWNLOADS_PER_HOUR": "1000", "TRUST_PROXY_HOPS": "0"})

import yt_dlp  # noqa: E402
import downloader as dl  # noqa: E402
import main  # noqa: E402


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


MEDIA_PORT = free_port()
httpd = ThreadingHTTPServer(("127.0.0.1", MEDIA_PORT),
                            partial(SimpleHTTPRequestHandler, directory=str(MEDIA)))
httpd.RequestHandlerClass.log_message = lambda *a: None
threading.Thread(target=httpd.serve_forever, daemon=True).start()
M = f"http://127.0.0.1:{MEDIA_PORT}"


def fmt(fid, file, **kw):
    base = {"format_id": fid, "url": f"{M}/{file}", "ext": "mp4", "protocol": "http",
            "filesize": (MEDIA / file).stat().st_size}
    base.update(kw)
    return base


# Parecido com o que o yt-dlp devolve para um Reel: MP4 progressivo (sem codec
# declarado) + DASH com vídeo e áudio separados
INSTAGRAM = {
    "id": "IGTEST123", "title": "Video by fulano", "description": "Pôr do sol em Itapuã 🌅",
    "uploader_id": "fulano.ssa", "duration": 3, "extractor": "Instagram", "extractor_key": "Instagram",
    "webpage_url": "https://www.instagram.com/reel/IGTEST123/",
    "formats": [
        fmt("8", "prog720.mp4", width=720, height=1280),
        fmt("dash-720v", "v720_avc.mp4", width=720, height=1280, vcodec="avc1.4d401f", acodec="none", tbr=1500),
        fmt("dash-1080v", "v1080_avc.mp4", width=1080, height=1920, vcodec="avc1.640028", acodec="none", tbr=3500),
        fmt("dash-audio", "audio.m4a", ext="m4a", vcodec="none", acodec="mp4a.40.2", abr=128),
    ],
}

# Parecido com o TikTok: tudo combinado; inclui versão com marca d'água e bytevc2
TIKTOK = {
    "id": "7300000000000000001", "title": "dança", "description": "coreografia nova #axé",
    "uploader_id": "beltrana", "uploader": "beltrana", "duration": 3,
    "extractor": "TikTok", "extractor_key": "TikTok",
    "webpage_url": "https://www.tiktok.com/@beltrana/video/7300000000000000001",
    "formats": [
        fmt("download", "tt720_h264.mp4", width=720, height=1280, format_note="Download video, watermarked",
            preference=-2, vcodec="h264", acodec="aac"),
        fmt("h264_540p", "tt540_h264.mp4", width=576, height=1024, vcodec="h264", acodec="aac", tbr=900),
        fmt("h264_720p", "tt720_h264.mp4", width=720, height=1280, vcodec="h264", acodec="aac", tbr=1600),
        fmt("bytevc1_1080p", "tt1080_hevc.mp4", width=1080, height=1920, vcodec="h265", acodec="aac", tbr=2500),
        fmt("bytevc2_1080p", "tt1080_hevc.mp4", width=1080, height=1920, vcodec="bytevc2", acodec="aac",
            tbr=2000, preference=-100),
    ],
}

FAKES = {"instagram": INSTAGRAM, "tiktok": TIKTOK}
EXTRACT_CALLS = []


def fake_extract(url, s):
    EXTRACT_CALLS.append(url)
    if "PRIVATE" in url:
        raise dl.UserError(dl.friendly_error(Exception("This content may be inappropriate: login required")))
    if "PHOTO" in url:
        raise dl.UserError(dl.friendly_error(Exception("There is no video in this post")))
    info = json.loads(json.dumps(FAKES[dl.platform_of(url)]))
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:     # ordena/normaliza como o yt-dlp real
        info = ydl.process_ie_result(info, download=False)
        return dl.strip_selection(ydl.sanitize_info(info))


dl.extract = fake_extract

import uvicorn  # noqa: E402

API_PORT = free_port()
server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=API_PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(50):
    try:
        requests.get(f"http://127.0.0.1:{API_PORT}/health", timeout=1); break
    except Exception:
        time.sleep(0.1)
A = f"http://127.0.0.1:{API_PORT}"

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(name)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,codec_name,width,height", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    return json.loads(out or "{}").get("streams", [{"codec_type": "?", "codec_name": "?"}])


def faststart(path):
    data = Path(path).read_bytes()[:200000]
    return 0 <= data.find(b"moov") < data.find(b"mdat")


TMP = Path(os.environ.get("OUT_DIR", "/tmp/vdl_test_out")); TMP.mkdir(parents=True, exist_ok=True)


def get_file(url, quality, name):
    r = requests.get(f"{A}/api/download", params={"url": url, "quality": quality}, timeout=120)
    p = TMP / name
    if r.ok:
        p.write_bytes(r.content)
    return r, p


print("\n[saúde e páginas]")
r = requests.get(f"{A}/health"); check("health", r.ok and r.json()["status"] == "ok", r.json().get("yt_dlp"))
r = requests.get(f"{A}/"); check("página inicial", r.ok and "data-vdl" in r.text)
r = requests.get(f"{A}/embed"); check("página embed", r.ok and "vdl:height" in r.text)
r = requests.get(f"{A}/tool.js"); check("tool.js servido", r.ok and "javascript" in r.headers["content-type"])

print("\n[Instagram]")
ig = "https://www.instagram.com/reel/IGTEST123/?igsh=abc"
r = requests.post(f"{A}/api/info", json={"url": ig}); d = r.json()
print("  opções:", [(o["label"], o["codec"], o["filesize"]) for o in d["options"]])
check("info ok", r.ok and d["platform"] == "instagram" and d["uploader"] == "fulano.ssa")
check("melhor opção é 1080p", d["options"][0]["key"] == "1080")
check("tem 720p e só áudio", [o["key"] for o in d["options"]] == ["1080", "720", "audio"])
check("tamanho 1080p = vídeo+áudio",
      d["options"][0]["filesize"] == (MEDIA / "v1080_avc.mp4").stat().st_size + (MEDIA / "audio.m4a").stat().st_size)

calls_before = len(EXTRACT_CALLS)
r, p = get_file(ig, "1080", "ig1080.mp4")
check("download 1080p", r.ok, r.headers.get("content-disposition"))
s = probe(p)
v = [x for x in s if x["codec_type"] == "video"][0]
check("vídeo 1080x1920 H.264", (v["width"], v["height"], v["codec_name"]) == (1080, 1920, "h264"))
check("tem áudio AAC (DASH foi juntado)", any(x["codec_type"] == "audio" and x["codec_name"] == "aac" for x in s))
check("faststart (moov antes de mdat)", faststart(p))
check("cache evitou 2ª consulta à plataforma", len(EXTRACT_CALLS) == calls_before)
check("nome do arquivo", "instagram_fulano.ssa_IGTEST123_1080p.mp4" in r.headers.get("content-disposition", ""))

r, p = get_file(ig, "720", "ig720.mp4"); s = probe(p)
v = [x for x in s if x["codec_type"] == "video"][0]
check("download 720p", r.ok and v["height"] == 1280 and any(x["codec_type"] == "audio" for x in s))

r, p = get_file(ig, "audio", "ig.mp3"); s = probe(p)
check("só áudio vira MP3", r.ok and r.headers["content-type"] == "audio/mpeg" and s[0]["codec_name"] == "mp3"
      and not any(x["codec_type"] == "video" for x in s))

r, p = get_file(ig, "lixo", "igdefault.mp4"); s = probe(p)
check("qualidade inválida cai na melhor", r.ok and [x for x in s if x["codec_type"] == "video"][0]["height"] == 1920)

print("\n[TikTok]")
tt = "https://www.tiktok.com/@beltrana/video/7300000000000000001"
r = requests.post(f"{A}/api/info", json={"url": tt}); d = r.json()
print("  opções:", [(o["label"], o["codec"]) for o in d["options"]])
keys = [o["key"] for o in d["options"]]
check("ordem 1080/720/576/áudio", keys == ["1080", "720", "576", "audio"], keys)
check("1080p marcado como HEVC", d["options"][0]["codec"] == "HEVC")
check("720p é H.264 (sem marca)", d["options"][1]["codec"] is None)

r, p = get_file(tt, "1080", "tt1080.mp4"); s = probe(p)
v = [x for x in s if x["codec_type"] == "video"][0]
check("1080p baixa HEVC (não bytevc2)", r.ok and v["codec_name"] == "hevc" and v["height"] == 1920)
r, p = get_file(tt, "720", "tt720.mp4")
check("720p sem marca d'água (formato h264_720p)", r.ok and p.read_bytes() == (MEDIA / "tt720_h264.mp4").read_bytes()
      or probe(p)[0]["codec_name"] == "h264")
r, p = get_file(tt, "audio", "tt.mp3"); s = probe(p)
check("TikTok só áudio (extraído do vídeo)", r.ok and s[0]["codec_name"] == "mp3")

print("\n[erros e validação]")
r = requests.post(f"{A}/api/info", json={"url": "https://youtube.com/watch?v=1"})
check("recusa outro site", r.status_code == 400, r.json()["detail"])
r = requests.post(f"{A}/api/info", json={"url": "https://instagram.com.evil.com/reel/x"})
check("recusa domínio falso", r.status_code == 400)
r = requests.post(f"{A}/api/info", json={"url": "file:///etc/passwd"})
check("recusa file://", r.status_code == 400)
r = requests.post(f"{A}/api/info", json={"url": "www.instagram.com/reel/IGTEST123/"})
check("aceita link sem https://", r.ok)
r = requests.post(f"{A}/api/info", json={"url": "https://www.instagram.com/p/PRIVATE/"})
check("privado -> mensagem clara", r.status_code == 422 and "privado" in r.json()["detail"], r.json()["detail"][:60])
r = requests.post(f"{A}/api/info", json={"url": "https://www.instagram.com/p/PHOTO/"})
check("só foto -> mensagem clara", r.status_code == 422 and "foto" in r.json()["detail"])
r = requests.post(f"{A}/api/info", data="isso não é json", headers={"Content-Type": "application/json"})
check("corpo inválido -> 400", r.status_code == 400)

print("\n[expiração dos links da plataforma]")
dl.extract = fake_extract
CACHED = main.CACHE.get(ig)
broken = json.loads(json.dumps(CACHED))
for f in broken["formats"]:
    f["url"] = f"{M}/nao-existe-mais.mp4"
main.CACHE.put(ig, broken)
n = len(EXTRACT_CALLS)
r, p = get_file(ig, "1080", "ig_retry.mp4")
check("link expirado -> extrai de novo e baixa", r.ok and len(EXTRACT_CALLS) == n + 1 and probe(p))

print("\n[limites]")
main.RATE_LIMIT = 3
main.LIMITER = main.RateLimiter()
codes = [requests.post(f"{A}/api/info", json={"url": "https://youtube.com/x"}).status_code for _ in range(5)]
check("limite por minuto -> 429", codes[-1] == 429, codes)
main.RATE_LIMIT = 1000

leftover = list(Path(main.SETTINGS.tmp_root).glob("vdl_*"))
time.sleep(0.5)
leftover = [d for d in Path(main.SETTINGS.tmp_root).glob("vdl_*") if d.exists()]
check("pastas temporárias apagadas", not leftover, leftover)

print("\n" + ("TUDO CERTO" if not FAILS else f"{len(FAILS)} FALHA(S): {FAILS}"))
server.should_exit = True
sys.exit(1 if FAILS else 0)
