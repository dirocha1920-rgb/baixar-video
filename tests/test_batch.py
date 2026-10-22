"""
Teste de ponta a ponta do download em lote, sem acessar as plataformas.
Simula perfis (TikTok e Reels do Instagram), um post de foto, um perfil inexistente,
e verifica downloads, ZIP, cancelamento e limites.

Uso:  MEDIA_DIR=/caminho/com/os/videos python tests/test_batch.py
"""
import io
import json
import os
import socket
import sys
import threading
import time
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
MEDIA = Path(os.environ["MEDIA_DIR"])
os.environ.update({"RATE_LIMIT_PER_MIN": "1000", "DOWNLOADS_PER_HOUR": "1000", "TRUST_PROXY_HOPS": "1",
                   "BATCH_JOBS_PER_HOUR": "100"})

import yt_dlp  # noqa: E402
import downloader as dl  # noqa: E402
import profiles  # noqa: E402
import main  # noqa: E402


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


MP = free_port()
httpd = ThreadingHTTPServer(("127.0.0.1", MP), partial(SimpleHTTPRequestHandler, directory=str(MEDIA)))
httpd.RequestHandlerClass.log_message = lambda *a: None
threading.Thread(target=httpd.serve_forever, daemon=True).start()
M = f"http://127.0.0.1:{MP}"


def fmt(fid, file, **kw):
    d = {"format_id": fid, "url": f"{M}/{file}", "ext": "mp4", "protocol": "http",
         "filesize": (MEDIA / file).stat().st_size}
    d.update(kw)
    return d


def fake_info(url):
    vid = url.rstrip("/").split("/")[-1]
    if "tiktok" in url:
        return {"id": vid, "title": "t", "description": f"vídeo {vid}", "uploader_id": "beltrana",
                "extractor": "TikTok", "extractor_key": "TikTok", "webpage_url": url,
                "formats": [fmt("h264_720p", "tt720_h264.mp4", width=720, height=1280, vcodec="h264", acodec="aac"),
                            fmt("bytevc1_1080p", "tt1080_hevc.mp4", width=1080, height=1920, vcodec="h265",
                                acodec="aac")]}
    return {"id": vid, "title": "t", "description": f"reel {vid}", "uploader_id": "fulano.ssa",
            "extractor": "Instagram", "extractor_key": "Instagram", "webpage_url": url,
            "formats": [fmt("dash-1080v", "v1080_avc.mp4", width=1080, height=1920, vcodec="avc1.640028",
                            acodec="none"),
                        fmt("dash-audio", "audio.m4a", ext="m4a", vcodec="none", acodec="mp4a.40.2")]}


def fake_extract(url, s):
    if "FOTO" in url:
        raise dl.UserError(dl.friendly_error(Exception("There is no video in this post")))
    with yt_dlp.YoutubeDL({"quiet": True}) as y:
        return dl.strip_selection(y.sanitize_info(y.process_ie_result(fake_info(url), download=False)))


LISTED = []


def fake_list_profile(src, limit, s):
    LISTED.append((src.platform, src.value, limit))
    if src.value == "naoexiste":
        raise dl.UserError("perfil não encontrado")
    if src.platform == "tiktok":
        return [profiles.Found(url=f"https://www.tiktok.com/@{src.value}/video/7{n:018d}") for n in range(1, 9)][:limit]
    return [profiles.Found(url=f"https://www.instagram.com/reel/R{n}/", title=f"Reel {n}") for n in range(1, 3)] + \
           [profiles.Found(url="https://www.instagram.com/reel/FOTO1/")]


dl.extract = fake_extract
profiles.list_profile = fake_list_profile
main.BATCH.bs.pause_instagram = (0.0, 0.0)
main.BATCH.bs.pause_tiktok = (0.0, 0.0)

import uvicorn  # noqa: E402

AP = free_port()
server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=AP, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
A = f"http://127.0.0.1:{AP}"
for _ in range(50):
    try:
        requests.get(A + "/health", timeout=1); break
    except Exception:
        time.sleep(0.1)

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(name)


def as_ip(ip):
    return {"X-Forwarded-For": ip}


def wait(job_id, ip, until=("done", "cancelled", "failed"), timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = requests.get(f"{A}/api/batch/{job_id}", headers=as_ip(ip)).json()
        if d["status"] in until:
            return d
        time.sleep(0.3)
    return d


print("\n[config e reconhecimento]")
c = requests.get(A + "/api/config").json()["batch"]
check("config expõe limites", c["max_per_profile"] == 50 and "instagram_profiles" in c, c)
text = "@beltrana\nhttps://www.instagram.com/fulano.ssa/\nhttps://www.tiktok.com/@beltrana/video/7000000000000000099\n" \
       "https://youtube.com/x"
r = requests.post(A + "/api/batch/check", json={"text": text}).json()
check("sem rede escolhida pede escolha", r["needs_platform"] is True)
r = requests.post(A + "/api/batch/check", json={"text": text, "handle_platform": "tiktok"}).json()
check("reconhece 3 fontes e 1 problema", len(r["sources"]) == 3 and len(r["problems"]) == 1, r)

print("\n[lote completo: perfil TikTok + perfil Instagram (Reels) + link + perfil inexistente]")
body = {"text": text + "\n@naoexiste", "handle_platform": "tiktok", "per_profile": 5, "quality": "max"}
r = requests.post(A + "/api/batch", json=body, headers=as_ip("10.0.0.1"))
check("lote criado", r.status_code == 201, r.text[:100])
job = r.json()["id"]
r2 = requests.post(A + "/api/batch", json=body, headers=as_ip("10.0.0.1"))
check("2º lote simultâneo do mesmo IP recusado", r2.status_code == 429, r2.json().get("detail"))
d = wait(job, "10.0.0.1")
c = d["counts"]
print("  fontes:", [(s["label"], s["status"], s["found"], s["message"]) for s in d["sources"]])
check("status done", d["status"] == "done", d["status"])
check("limite por perfil respeitado (5 do TikTok)", ("tiktok", "beltrana", 5) in LISTED, LISTED)
check("perfil Instagram listado", any(p == "instagram" and v == "fulano.ssa" for p, v, _ in LISTED))
check("total 5 + 3 + 1 = 9 itens", c["total"] == 9, c)
check("8 baixados, 1 pulado (foto)", c["done"] == 8 and c["skipped"] == 1 and c["error"] == 0, c)
foto = [i for i in d["items"] if "FOTO" in i["url"]][0]
check("foto marcada como 'não é vídeo'", foto["status"] == "skipped" and "não é vídeo" in foto["message"])
err_src = [s for s in d["sources"] if s["label"] == "@naoexiste"][0]
check("perfil inexistente explicado", err_src["status"] == "error" and "não encontrado" in err_src["message"])
check("tamanho total informado", d["total_size"] > 0 and d["expires_at"])

print("\n[entrega]")
first = [i for i in d["items"] if i["status"] == "done"][0]
r = requests.get(f"{A}/api/batch/{job}/file/{first['index']}")
check("arquivo individual", r.ok and r.headers["content-type"] == "video/mp4" and len(r.content) == first["size"],
      r.headers.get("content-disposition"))
r = requests.get(f"{A}/api/batch/{job}/file/{foto['index']}")
check("item pulado não tem arquivo", r.status_code == 404)
r = requests.get(f"{A}/api/batch/{job}/file/999")
check("índice inexistente -> 404", r.status_code == 404)
r = requests.get(f"{A}/api/batch/{job}/zip")
zf = zipfile.ZipFile(io.BytesIO(r.content))
names = zf.namelist()
check("zip válido com 8 vídeos", r.ok and len(names) == 8 and zf.testzip() is None, names[:3])
check("zip sem compressão (rápido)", all(i.compress_type == zipfile.ZIP_STORED for i in zf.infolist()))
check("tamanhos no zip batem", sum(i.file_size for i in zf.infolist()) == d["total_size"])
check("nomes numerados e únicos", len(set(names)) == 8 and names[0].startswith("001_"), names[:2])
check("modo máximo: TikTok veio em HEVC 1080p", any("1080p" in n and "tiktok" in n for n in names))
r = requests.get(f"{A}/api/batch/naoexiste123")
check("lote desconhecido -> 404", r.status_code == 404)

print("\n[modo compatível]")
body = {"text": "@beltrana", "handle_platform": "tiktok", "per_profile": 2, "quality": "compat"}
job2 = requests.post(A + "/api/batch", json=body, headers=as_ip("10.0.0.2")).json()["id"]
d2 = wait(job2, "10.0.0.2")
check("compatível: TikTok veio em 720p H.264",
      d2["counts"]["done"] == 2 and all("720p" in i["filename"] for i in d2["items"]),
      [i["filename"] for i in d2["items"]])

print("\n[cancelamento]")
main.BATCH.bs.pause_tiktok = (0.6, 0.6)
body = {"text": "@beltrana", "handle_platform": "tiktok", "per_profile": 8}
job3 = requests.post(A + "/api/batch", json=body, headers=as_ip("10.0.0.3")).json()["id"]
t0 = time.time()
while time.time() - t0 < 30:
    s = requests.get(f"{A}/api/batch/{job3}").json()
    if s["counts"]["done"] >= 2:
        break
    time.sleep(0.1)
requests.post(f"{A}/api/batch/{job3}/cancel")
d3 = wait(job3, "10.0.0.3")
check("cancelado no meio", d3["status"] == "cancelled" and 2 <= d3["counts"]["done"] < 8, d3["counts"])
r = requests.get(f"{A}/api/batch/{job3}/zip")
check("zip parcial disponível após cancelar", r.ok and len(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
      == d3["counts"]["done"])
r = requests.post(A + "/api/batch", json=body, headers=as_ip("10.0.0.3"))
check("após cancelar, pode criar outro lote", r.status_code == 201)
wait(r.json()["id"], "10.0.0.3")
main.BATCH.bs.pause_tiktok = (0.0, 0.0)

print("\n[validação e limites]")
r = requests.post(A + "/api/batch", json={"text": "https://youtube.com/x"}, headers=as_ip("10.0.0.4"))
check("só links inválidos -> 400 com explicação", r.status_code == 400, r.json()["detail"])
r = requests.post(A + "/api/batch", data="xx", headers={**as_ip("10.0.0.4"), "Content-Type": "application/json"})
check("corpo inválido -> 400", r.status_code == 400)
main.BATCH_JOBS_PER_HOUR = 1
r1 = requests.post(A + "/api/batch", json={"text": "@a1b2c3", "handle_platform": "tiktok", "per_profile": 1},
                   headers=as_ip("10.0.0.5"))
wait(r1.json()["id"], "10.0.0.5")
r2 = requests.post(A + "/api/batch", json={"text": "@a1b2c3", "handle_platform": "tiktok", "per_profile": 1},
                   headers=as_ip("10.0.0.5"))
check("limite de lotes por hora", r1.status_code == 201 and r2.status_code == 429, r2.json().get("detail"))
r = requests.post(A + "/api/batch", json={"text": "@zz9", "handle_platform": "tiktok", "per_profile": 9999},
                  headers=as_ip("10.0.0.6"))
d6 = wait(r.json()["id"], "10.0.0.6")
check("per_profile acima do teto vira 50", ("tiktok", "zz9", 50) in LISTED)

leftovers = [p for p in Path(main.SETTINGS.tmp_root).glob("vdl_*")]
check("sem pastas temporárias de download sobrando", not leftovers, leftovers[:2])

print("\n" + ("TUDO CERTO" if not FAILS else f"{len(FAILS)} FALHA(S): {FAILS}"))
server.should_exit = True
sys.exit(1 if FAILS else 0)
