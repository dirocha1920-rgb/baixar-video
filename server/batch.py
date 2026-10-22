"""
Download em lote.

Um "lote" recebe vários links e/ou perfis, lista os vídeos, baixa um por vez
(para não chamar atenção das plataformas) e entrega tudo num ZIP ou arquivo
por arquivo. Os arquivos ficam no servidor só até o lote expirar.
"""
from __future__ import annotations

import logging
import os
import random
import secrets
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue

import downloader as dl
import profiles

log = logging.getLogger("vdl.batch")


@dataclass
class BatchSettings:
    max_sources: int = 20             # links/perfis por lote
    max_per_profile: int = 50         # vídeos por perfil (teto da escolha do visitante)
    max_items: int = 150              # vídeos por lote
    max_total_mb: int = 3000          # tamanho máximo somado do lote
    min_free_mb: int = 1024           # espaço livre mínimo em disco para continuar
    ttl_minutes: int = 60             # quanto tempo os arquivos ficam disponíveis depois de pronto
    workers: int = 1                  # lotes processados ao mesmo tempo
    pause_instagram: tuple = (2.5, 5.0)
    pause_tiktok: tuple = (1.0, 2.5)


@dataclass
class Item:
    index: int
    url: str
    platform: str
    source: str                       # "@perfil" ou "link"
    title: str = ""
    status: str = "pending"           # pending | downloading | done | skipped | error
    message: str = ""
    filename: str = ""
    path: str = ""
    size: int = 0

    def public(self) -> dict:
        return {"index": self.index, "url": self.url, "platform": self.platform, "source": self.source,
                "title": self.title, "status": self.status, "message": self.message,
                "filename": self.filename, "size": self.size}


@dataclass
class SourceState:
    label: str
    platform: str
    kind: str
    status: str = "pending"           # pending | listing | ok | error
    found: int = 0
    message: str = ""


@dataclass
class Job:
    id: str
    ip: str
    sources: list
    quality: str
    per_profile: int
    workdir: Path
    created: float = field(default_factory=time.time)
    status: str = "queued"            # queued | listing | downloading | done | cancelled | failed
    message: str = ""
    items: list = field(default_factory=list)
    source_states: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    finished: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def public(self, queue_position: int | None = None) -> dict:
        with self.lock:
            items = [i.public() for i in self.items]
            sources = [vars(s).copy() for s in self.source_states]
        done = [i for i in items if i["status"] == "done"]
        return {
            "id": self.id,
            "status": self.status,
            "message": self.message,
            "queue_position": queue_position,
            "quality": self.quality,
            "sources": sources,
            "problems": self.problems,
            "items": items,
            "counts": {
                "total": len(items),
                "done": len(done),
                "skipped": sum(1 for i in items if i["status"] == "skipped"),
                "error": sum(1 for i in items if i["status"] == "error"),
                "pending": sum(1 for i in items if i["status"] in ("pending", "downloading")),
            },
            "total_size": sum(i["size"] for i in done),
        }


class BatchManager:
    def __init__(self, settings: dl.Settings, cache: dl.InfoCache, bs: BatchSettings, root: str):
        self.s, self.cache, self.bs = settings, cache, bs
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.queue: Queue[str] = Queue()
        self.waiting: list[str] = []
        self.lock = threading.Lock()
        self._started = False

    # -- ciclo de vida -------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for d in self.root.glob("job_*"):            # restos de uma execução anterior
            shutil.rmtree(d, ignore_errors=True)
        for n in range(max(1, self.bs.workers)):
            threading.Thread(target=self._worker, name=f"batch-{n}", daemon=True).start()
        threading.Thread(target=self._janitor, name="batch-janitor", daemon=True).start()

    def create(self, ip: str, sources: list, problems: list, quality: str, per_profile: int) -> Job:
        job_id = secrets.token_urlsafe(12)
        workdir = self.root / f"job_{job_id}"
        workdir.mkdir(parents=True)
        per_profile = max(1, min(int(per_profile or 10), self.bs.max_per_profile))
        job = Job(id=job_id, ip=ip, sources=sources, quality=quality if quality in ("max", "compat") else "max",
                  per_profile=per_profile, workdir=workdir, problems=problems)
        job.source_states = [SourceState(label=s.label, platform=s.platform, kind=s.kind) for s in sources]
        with self.lock:
            self.jobs[job_id] = job
            self.waiting.append(job_id)
        self.queue.put(job_id)
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def position(self, job_id: str) -> int | None:
        with self.lock:
            return self.waiting.index(job_id) + 1 if job_id in self.waiting else None

    def active_jobs_for(self, ip: str) -> int:
        return sum(1 for j in self.jobs.values()
                   if j.ip == ip and j.status in ("queued", "listing", "downloading"))

    def cancel(self, job: Job) -> None:
        job.cancel.set()
        if job.status == "queued":
            with self.lock:
                if job.id in self.waiting:
                    self.waiting.remove(job.id)
            self._finish(job, "cancelled", "Lote cancelado.")

    # -- processamento -------------------------------------------------------
    def _worker(self) -> None:
        while True:
            job_id = self.queue.get()
            with self.lock:
                if job_id in self.waiting:
                    self.waiting.remove(job_id)
            job = self.jobs.get(job_id)
            if not job or job.cancel.is_set():
                continue
            try:
                self._run(job)
            except Exception:  # noqa: BLE001
                log.exception("lote %s falhou", job_id)
                self._finish(job, "failed", "O lote parou por um erro inesperado no servidor.")

    def _run(self, job: Job) -> None:
        # 1) monta a lista de vídeos
        job.status = "listing"
        for src, st in zip(job.sources, job.source_states):
            if job.cancel.is_set():
                return self._finish(job, "cancelled", "Lote cancelado.")
            if len(job.items) >= self.bs.max_items:
                st.status, st.message = "error", f"limite de {self.bs.max_items} vídeos por lote atingido"
                continue
            if src.kind == "video":
                self._add_item(job, src.value, src.platform, "link")
                st.status, st.found = "ok", 1
                continue
            st.status = "listing"
            try:
                room = min(job.per_profile, self.bs.max_items - len(job.items))
                found = profiles.list_profile(src, room, self.s)
                for f in found:
                    self._add_item(job, f.url, src.platform, src.label, f.title)
                st.status, st.found = "ok", len(found)
            except dl.UserError as e:
                st.status, st.message = "error", e.message
            except Exception as e:  # noqa: BLE001
                log.warning("falha listando %s: %s", src.label, e)
                st.status, st.message = "error", "não foi possível listar o perfil"

        if not job.items:
            return self._finish(job, "failed", "Nenhum vídeo encontrado para baixar.")

        # 2) baixa um por vez
        job.status = "downloading"
        total_bytes = 0
        last_platform = None
        for item in job.items:
            if job.cancel.is_set():
                return self._finish(job, "cancelled", "Lote cancelado. Os vídeos já baixados continuam disponíveis.")
            free_mb = shutil.disk_usage(self.root).free / 1e6
            if free_mb < self.bs.min_free_mb:
                self._stop_rest(job, "o servidor está sem espaço livre")
                break
            if total_bytes >= self.bs.max_total_mb * 1e6:
                self._stop_rest(job, f"o lote passou de {self.bs.max_total_mb} MB")
                break
            if last_platform is not None:                       # pausa entre vídeos
                lo, hi = self.bs.pause_instagram if item.platform == "instagram" else self.bs.pause_tiktok
                if job.cancel.wait(random.uniform(lo, hi)):
                    continue
            last_platform = item.platform
            self._download_item(job, item)
            total_bytes += item.size

        done = sum(1 for i in job.items if i.status == "done")
        if job.cancel.is_set():
            return self._finish(job, "cancelled", "Lote cancelado. Os vídeos já baixados continuam disponíveis.")
        if done == 0:
            return self._finish(job, "failed", "Nenhum vídeo pôde ser baixado.")
        self._finish(job, "done", "")

    def _add_item(self, job: Job, url: str, platform: str, source: str, title: str = "") -> None:
        with job.lock:
            if any(i.url == url for i in job.items):
                return
            job.items.append(Item(index=len(job.items), url=url, platform=platform, source=source, title=title))

    def _stop_rest(self, job: Job, reason: str) -> None:
        with job.lock:
            for i in job.items:
                if i.status == "pending":
                    i.status, i.message = "skipped", reason

    def _download_item(self, job: Job, item: Item) -> None:
        with job.lock:
            item.status = "downloading"
        try:
            res = dl.download(item.url, job.quality, self.s, self.cache)
        except dl.UserError as e:
            with job.lock:
                no_video = "não tem vídeo" in e.message or "foto" in e.message
                item.status = "skipped" if no_video else "error"
                item.message = "não é vídeo (post de foto)" if no_video else e.message
            return
        except Exception:  # noqa: BLE001
            log.exception("erro baixando %s", item.url)
            with job.lock:
                item.status, item.message = "error", "erro inesperado"
            return
        try:
            name = self._unique_name(job, f"{item.index + 1:03d}_{res.filename}")
            dest = job.workdir / name
            shutil.move(str(res.path), dest)
            with job.lock:
                item.status, item.filename, item.path = "done", name, str(dest)
                item.size = dest.stat().st_size
                item.message = ""
        finally:
            shutil.rmtree(res.workdir, ignore_errors=True)

    @staticmethod
    def _unique_name(job: Job, name: str) -> str:
        base, ext = os.path.splitext(name)
        n, candidate = 1, name
        while (job.workdir / candidate).exists():
            n += 1
            candidate = f"{base}_{n}{ext}"
        return candidate

    def _finish(self, job: Job, status: str, message: str) -> None:
        job.status, job.message = status, message
        job.finished = time.time()
        with job.lock:
            for i in job.items:
                if i.status in ("pending", "downloading"):
                    i.status, i.message = "skipped", "cancelado" if status == "cancelled" else "não processado"
        log.info("lote %s %s: %d/%d vídeos", job.id, status,
                 sum(1 for i in job.items if i.status == "done"), len(job.items))

    def expires_at(self, job: Job) -> float | None:
        return job.finished + self.bs.ttl_minutes * 60 if job.finished else None

    def _janitor(self) -> None:
        while True:
            time.sleep(30)
            now = time.time()
            for job_id, job in list(self.jobs.items()):
                exp = self.expires_at(job)
                if exp and now > exp:
                    shutil.rmtree(job.workdir, ignore_errors=True)
                    self.jobs.pop(job_id, None)

    # -- entrega -------------------------------------------------------------
    def file_path(self, job: Job, index: int) -> Item | None:
        with job.lock:
            if 0 <= index < len(job.items):
                item = job.items[index]
                if item.status == "done" and item.path and Path(item.path).is_file():
                    return item
        return None

    def zip_stream(self, job: Job, chunk: int = 1024 * 1024):
        """
        Gera o ZIP aos pedaços, direto dos arquivos (sem duplicar no disco).
        Sem compressão: vídeo já é comprimido, então o ZIP sai rápido e do mesmo tamanho.
        """
        with job.lock:
            files = [(i.filename, i.path) for i in job.items if i.status == "done" and i.path]

        class Sink:
            def __init__(self):
                self.buf = bytearray()

            def write(self, b):
                self.buf += b
                return len(b)

            def flush(self):
                pass

            def take(self):
                out = bytes(self.buf)
                self.buf.clear()
                return out

        sink = Sink()
        with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for name, path in files:
                if not Path(path).is_file():
                    continue
                zinfo = zipfile.ZipInfo.from_file(path, arcname=name)
                zinfo.compress_type = zipfile.ZIP_STORED
                with open(path, "rb") as src, zf.open(zinfo, "w", force_zip64=True) as dst:
                    while True:
                        data = src.read(chunk)
                        if not data:
                            break
                        dst.write(data)
                        if len(sink.buf) >= chunk:
                            yield sink.take()
                yield sink.take()
        tail = sink.take()
        if tail:
            yield tail
