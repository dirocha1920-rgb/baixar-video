"""
Revisão de criativos com IA (Google Gemini).

O Gemini "assiste" ao vídeo inteiro, com imagem e som: lê o texto na tela, ouve a
narração e olha o que aparece. Recebe também o texto do anúncio e as regras de
Meta Ads, TikTok Ads, Kwai Ads e da lei brasileira, e devolve um laudo em JSON.

Configuração (variáveis de ambiente no Render):
  GEMINI_API_KEY      chave criada no Google AI Studio (obrigatória para ligar a IA)
  GEMINI_MODEL        modelo preferido (padrão: gemini-3.8-flash; se não existir, tenta outros)
  REVIEW_ACCESS_CODE  senha opcional: só quem souber consegue usar a IA
  REVIEWS_PER_DAY     análises por pessoa por dia (padrão 15)
  AI_DAILY_LIMIT      análises por dia no servidor todo (padrão 150) — controla o gasto
  REVIEW_MAX_MB       tamanho máximo do arquivo (padrão 100)
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import date

log = logging.getLogger("vdl.ai")

BASE = os.getenv("GEMINI_BASE", "https://generativelanguage.googleapis.com").rstrip("/")
FALLBACK_MODELS = ["gemini-3.8-flash", "gemini-3-flash", "gemini-2.5-flash"]


class ReviewError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def api_key() -> str:
    return (os.getenv("GEMINI_API_KEY") or "").strip()


def access_code() -> str:
    return (os.getenv("REVIEW_ACCESS_CODE") or "").strip()


def max_bytes() -> int:
    return _int("REVIEW_MAX_MB", 100) * 1024 * 1024


# ---------------------------------------------------------------------------
# Limite diário (em memória; zera à meia-noite e quando o servidor reinicia)
# ---------------------------------------------------------------------------
class DailyCounter:
    def __init__(self):
        self.day = date.today()
        self.total = 0
        self.per_ip: dict[str, int] = {}
        self.lock = threading.Lock()

    def take(self, ip: str) -> None:
        with self.lock:
            if date.today() != self.day:
                self.day, self.total, self.per_ip = date.today(), 0, {}
            if self.total >= _int("AI_DAILY_LIMIT", 150):
                raise ReviewError("O limite de análises com IA de hoje foi atingido. Volte amanhã.", 429)
            if self.per_ip.get(ip, 0) >= _int("REVIEWS_PER_DAY", 15):
                raise ReviewError("Você atingiu o limite de análises com IA de hoje. Volte amanhã.", 429)
            self.total += 1
            self.per_ip[ip] = self.per_ip.get(ip, 0) + 1

    def give_back(self, ip: str) -> None:
        with self.lock:
            self.total = max(0, self.total - 1)
            if self.per_ip.get(ip):
                self.per_ip[ip] -= 1

    def remaining(self, ip: str) -> int:
        with self.lock:
            if date.today() != self.day:
                return _int("REVIEWS_PER_DAY", 15)
            return max(0, min(_int("REVIEWS_PER_DAY", 15) - self.per_ip.get(ip, 0), _int("AI_DAILY_LIMIT", 150) - self.total))


COUNTER = DailyCounter()


# ---------------------------------------------------------------------------
# Regras enviadas para a IA (pesquisa de outubro de 2026)
# ---------------------------------------------------------------------------
RULES = """
Você é um revisor sênior de anúncios para Meta Ads (Facebook/Instagram), TikTok Ads e Kwai Ads, no Brasil, em outubro de 2026.
Seu trabalho é prever reprovações e riscos ANTES do anúncio subir. Seja rigoroso, objetivo e prático. Responda em português do Brasil.

COMO ANALISAR
- Assista ao vídeo inteiro: ouça a narração e as músicas, leia todo texto que aparece na tela (legendas queimadas, cartazes, telas de celular, preço) e observe as imagens.
- Analise também o texto do anúncio enviado (legenda, título, descrição, nome) e o tipo de produto informado.
- Considere promessas IMPLÍCITAS, não só palavras exatas (ex.: "mudou minha vida financeira" mostrando dinheiro = promessa de ganho).
- Para cada problema, diga o momento (MM:SS) quando estiver no vídeo, onde está (fala, texto na tela, imagem, som, legenda do anúncio), qual regra viola e como corrigir com uma alternativa concreta.
- Não invente problemas. Se algo é incerto (ex.: se a música é licenciada), marque como "aviso" e explique o que conferir.
- Gravidade: "erro" = reprovação provável; "aviso" = risco ou depende de autorização/contexto; "dica" = melhora a aprovação ou a performance.

REGRAS COMUNS ÀS TRÊS PLATAFORMAS (e lei brasileira)
- Proibido: promessa de resultado garantido, cura, "100%", "sem risco"; ganho financeiro garantido ou dinheiro fácil; produtos ilegais, drogas, armas, falsificados/réplicas, tabaco e cigarro eletrônico (vape é proibido no Brasil pela Anvisa); conteúdo adulto ou sexual, inclusive sugestivo, e produtos para desejo sexual; discurso de ódio, violência explícita, conteúdo chocante; exploração ou sexualização de menores; golpes e pirâmides.
- Publicidade enganosa ou abusiva é ilegal pelo Código de Defesa do Consumidor (art. 37): preço, desconto, escassez ("só hoje", contador) e depoimentos precisam ser verdadeiros e representativos.
- Saúde: medicamento de receita não pode ser anunciado ao público (Anvisa, RDC 96/2008); suplemento não pode alegar efeito terapêutico ou cura; "aprovado pela Anvisa", "clinicamente comprovado" exigem prova.
- Uso de rosto, voz ou nome de famoso sem autorização é proibido (inclusive deepfake ou voz clonada por IA).
- Crianças: não usar criança para vender a crianças, nem linguagem de apelo infantil para produto adulto (ECA/CONANDA).
- Apostas (bets) no Brasil: só operadoras autorizadas pela SPA/MF. Desde 17/07/2026 TODA peça precisa de uma destas frases, horizontal e legível, ocupando pelo menos 10% da peça: "Ministério da Fazenda adverte: Apostar pode causar dependência" / "Ministério da Fazenda adverte: Apostar faz você perder dinheiro" / "Ministério da Fazenda adverte: Aposta não é investimento". Também é obrigatório indicar proibição para menores de 18 anos. É proibido sugerir ganho fácil, aposta como renda ou investimento, mostrar prêmios ganhos, incentivar aposta excessiva ou mirar menores (Portaria Interministerial 73/2026, Portaria SPA 1.231/2024).
- Política: TikTok e Kwai não aceitam anúncio político; a Meta suspendeu anúncios de política, eleições e temas sociais no Brasil desde 30/09/2026. Qualquer candidato, partido, eleição ou pedido de voto = erro.

META ADS
- Atributos pessoais: proibido afirmar ou insinuar característica pessoal de quem vê (saúde física ou mental, peso, dívidas/situação financeira, idade, religião, orientação sexual, etnia, deficiência). Ex. reprovado: "Você tem diabetes?", "Cansado de ser endividado?", "Sua barriga...". Correto: falar do tema, não da pessoa.
- Saúde e bem-estar (revisão de 22/07/2026): proibido alegar cura de doenças incuráveis (diabetes, herpes, tireoide, psoríase, câncer, autismo, Alzheimer, Parkinson, ELA, HIV); proibido dizer que o resultado vem só de um produto vestível; proibido clickbait de saúde (promessa de resultado em prazo sem ressalva); proibido dizer que alguém é inferior pela aparência; imagem de gordura pinçada é proibida; antes e depois só é reprovado se vier com alegação proibida; emagrecimento e ganho de peso: público 18+. Clareamento de pele e produtos sexuais seguem proibidos.
- Enganoso: "notícia" falsa ou formato de matéria, contador falso, preço "de/por" inflado, isca (bait and switch), depoimento fabricado.
- Técnica: Stories/Reels 9:16 com zona segura (evitar texto nos 14% de cima, 35% de baixo e 6% das laterais; 40% embaixo se houver aviso legal); Feed 4:5 ou 1:1; áudio AAC estéreo 128 kbps recomendado; H.264.
- Música protegida por direitos autorais pode ser silenciada ou reprovada.

TIKTOK ADS
- Formato e funcionalidade (abr/2026): o anúncio precisa ter ÁUDIO de boa qualidade; o vídeo deve ser dinâmico: imagem estática não pode ser o elemento principal nem ocupar mais de 50% do vídeo; pessoa segurando celular mostrando imagem parada é reprovado no Brasil; duração de 5 a 60 s pela política; sem QR code; texto sem erros de ortografia, sem caixa alta, pontuação, números ou símbolos excessivos no meio das palavras; sem botões, gestos ou textos de função que não existe ("arraste para cima", botão de play falso, botão de fechar falso); imagens nítidas, sem marca d'água de terceiros borrada ou escondida.
- Idioma: fala, legendas e página de destino em português para o Brasil.
- Enganoso (abr/2026): sem termos absolutos ("o melhor", "número 1 do TikTok") e sem prometer efeito exagerado ("pernas finas na hora", "dinheiro em 10 segundos"); antes e depois que distorça resultado é proibido; preço/desconto do anúncio deve bater com a página; comparação com concorrente só com prova.
- IA: conteúdo realista gerado ou muito alterado por IA (pessoa fazendo/dizendo o que não fez, voz clonada, cena sintética) precisa de rótulo de IA.
- Peso e imagem corporal (jun/2026): sem promessa irreal de emagrecimento ou ganho de massa, sem "fácil/garantido", sem dizer que o produto sozinho funciona sem dieta/exercício, sem envergonhar o corpo, sem corpo ideal, sem dizer que a aparência traz sucesso ou felicidade; público 18+.
- Apostas: só com certificação do TikTok e licença local, com avisos de risco obrigatórios e sem atingir menores.
- Zona segura In-Feed (1080x1920): evitar elementos importantes nos 160 px de cima, 440 px de baixo e 80 px laterais.

KWAI ADS
- In-Feed: vídeo vertical 9:16, MP4 (H.264), 720p recomendado, de 5 s a 10 min (15 a 60 s performa melhor), até 100 MB, mínimo 20 fps, vídeo a partir de 516 kbps, COM SOM em AAC a partir de 64 kbps.
- Proibido: política, conteúdo adulto, promessas absolutas (cura, ganho garantido), golpes, conteúdo enganoso; categorias de finanças, jogos e cripto exigem verificação; marca no vídeo deve ser a mesma do cadastro e da página.
- Evite marca d'água de TikTok/Reels, conteúdo copiado e música sem licença.
- Linguagem em português, de preferência estilo UGC (pessoa real falando).
"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "resumo": {"type": "STRING", "description": "2 a 4 frases com o veredito geral e os pontos principais"},
        "transcricao": {"type": "STRING", "description": "o que é falado no vídeo, resumido se for longo; vazio se não houver fala"},
        "texto_na_tela": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "textos que aparecem no vídeo/imagem"},
        "plataformas": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "plataforma": {"type": "STRING", "enum": ["meta", "tiktok", "kwai"]},
                    "veredito": {"type": "STRING", "enum": ["reprovado", "risco", "aprovado"]},
                    "problemas": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "gravidade": {"type": "STRING", "enum": ["erro", "aviso", "dica"]},
                                "momento": {"type": "STRING", "description": "MM:SS ou vazio"},
                                "onde": {"type": "STRING", "enum": ["fala", "texto na tela", "imagem", "som", "legenda do anúncio", "geral"]},
                                "o_que": {"type": "STRING"},
                                "regra": {"type": "STRING"},
                                "como_corrigir": {"type": "STRING"},
                            },
                            "required": ["gravidade", "onde", "o_que", "regra", "como_corrigir"],
                        },
                    },
                },
                "required": ["plataforma", "veredito", "problemas"],
            },
        },
        "sugestao_legenda": {"type": "STRING", "description": "uma versão do texto do anúncio que passaria nas regras, ou vazio"},
    },
    "required": ["resumo", "plataformas"],
}

PLATFORM_NAMES = {"meta": "Meta Ads", "tiktok": "TikTok Ads", "kwai": "Kwai Ads"}
PLACEMENTS = {"vertical": "Stories e Reels", "feed": "Feed", "infeed": "In-Feed", "spark": "Spark Ads"}


def build_prompt(platforms: dict, sector: str, texts: dict, specs: str) -> str:
    chosen = [f"{PLATFORM_NAMES[p]} ({PLACEMENTS.get(v, v)})" for p, v in platforms.items() if v and p in PLATFORM_NAMES]
    lines = [
        "Revise este criativo de anúncio.",
        "Plataformas: " + (", ".join(chosen) or "Meta Ads, TikTok Ads e Kwai Ads") + ".",
        "Tipo de produto informado: " + (sector or "não informado") + ".",
    ]
    labels = {"primary": "Texto principal/legenda", "headline": "Título", "description": "Descrição", "name": "Nome de exibição"}
    for key, label in labels.items():
        value = (texts.get(key) or "").strip()
        if value:
            lines.append(f"{label}: \"{value[:2200]}\"")
    if specs:
        lines.append("Dados técnicos já medidos pelo site (não repita esses itens técnicos, foque no conteúdo): " + specs[:800])
    lines.append("Inclua em 'plataformas' somente as plataformas escolhidas. Responda apenas com o JSON pedido.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Chamadas à API do Gemini (só biblioteca padrão)
# ---------------------------------------------------------------------------
def _request(method: str, url: str, headers: dict | None = None, data=None, timeout: int = 120):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


def _api_error(status: int, body: bytes) -> ReviewError:
    try:
        message = json.loads(body.decode("utf-8", "replace")).get("error", {}).get("message", "")
    except Exception:  # noqa: BLE001
        message = ""
    log.warning("gemini %s: %s", status, message[:300])
    if status in (401, 403):
        return ReviewError("A chave da IA (GEMINI_API_KEY) foi recusada pelo Google. Confira a chave no painel do Render.", 502)
    if status == 429:
        return ReviewError("A cota da IA no Google acabou por agora (limite do plano). Tente de novo em alguns minutos.", 503)
    if status == 400 and "location" in message.lower():
        return ReviewError("O Google não liberou a API do Gemini para a região deste servidor.", 502)
    return ReviewError("A IA não conseguiu analisar este arquivo agora. Tente de novo em instantes.", 502)


def upload_file(path: str, mime: str, key: str) -> dict:
    size = os.path.getsize(path)
    status, headers, body = _request("POST", f"{BASE}/upload/v1beta/files", {
        "x-goog-api-key": key,
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(size),
        "X-Goog-Upload-Header-Content-Type": mime,
        "Content-Type": "application/json",
    }, json.dumps({"file": {"display_name": "criativo"}}).encode(), timeout=60)
    upload_url = {k.lower(): v for k, v in headers.items()}.get("x-goog-upload-url")
    if status >= 300 or not upload_url:
        raise _api_error(status, body)
    with open(path, "rb") as fh:
        status, _, body = _request("POST", upload_url, {
            "Content-Length": str(size),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        }, fh, timeout=600)
    if status >= 300:
        raise _api_error(status, body)
    return json.loads(body)["file"]


def wait_active(file: dict, key: str, limit: float = 180) -> dict:
    started = time.time()
    while file.get("state") == "PROCESSING":
        if time.time() - started > limit:
            raise ReviewError("O Google demorou demais para processar o vídeo. Tente de novo.", 504)
        time.sleep(2)
        status, _, body = _request("GET", f"{BASE}/v1beta/{file['name']}", {"x-goog-api-key": key}, timeout=30)
        if status >= 300:
            raise _api_error(status, body)
        file = json.loads(body)
    if file.get("state") == "FAILED":
        raise ReviewError("O Google não conseguiu ler este vídeo. Tente exportar de novo em MP4 (H.264).", 422)
    return file


def delete_file(file: dict, key: str) -> None:
    try:
        _request("DELETE", f"{BASE}/v1beta/{file['name']}", {"x-goog-api-key": key}, timeout=20)
    except Exception:  # noqa: BLE001 - o Google apaga sozinho em 48 h
        pass


def generate(parts: list, key: str) -> tuple[dict, str]:
    preferred = (os.getenv("GEMINI_MODEL") or "").strip()
    models = [m for m in [preferred] + FALLBACK_MODELS if m]
    seen = []
    payload = {
        "system_instruction": {"parts": [{"text": RULES}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "responseSchema": SCHEMA},
    }
    last_error = None
    for model in models:
        if model in seen:
            continue
        seen.append(model)
        status, _, body = _request("POST", f"{BASE}/v1beta/models/{model}:generateContent", {
            "x-goog-api-key": key, "Content-Type": "application/json",
        }, json.dumps(payload).encode(), timeout=300)
        if status == 404 or (status == 400 and b"not found" in body.lower()):
            last_error = _api_error(status, body)
            continue  # modelo não existe para esta chave: tenta o próximo
        if status == 429 and model != models[-1]:
            last_error = _api_error(status, body)
            continue  # sem cota neste modelo (ex.: plano grátis): tenta outro
        if status >= 300:
            raise _api_error(status, body)
        data = json.loads(body)
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text), model
        except (KeyError, IndexError, ValueError):
            reason = (data.get("promptFeedback") or {}).get("blockReason") or (data.get("candidates") or [{}])[0].get("finishReason")
            if reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
                raise ReviewError("A própria IA bloqueou a análise por considerar o conteúdo sensível. Esse criativo provavelmente seria reprovado nas plataformas.", 422)
            raise ReviewError("A IA respondeu num formato inesperado. Tente de novo.", 502)
    raise last_error or ReviewError("Nenhum modelo de IA disponível para esta chave.", 502)


def clean_report(report: dict, platforms: dict) -> dict:
    """Garante a estrutura esperada pela página, mesmo se a IA variar."""
    out = {
        "resumo": str(report.get("resumo") or "")[:1500],
        "transcricao": str(report.get("transcricao") or "")[:4000],
        "texto_na_tela": [str(t)[:300] for t in (report.get("texto_na_tela") or [])][:40],
        "sugestao_legenda": str(report.get("sugestao_legenda") or "")[:1000],
        "plataformas": [],
    }
    wanted = [p for p, v in platforms.items() if v] or ["meta", "tiktok", "kwai"]
    for item in report.get("plataformas") or []:
        p = str(item.get("plataforma") or "").lower()
        if p not in wanted or any(x["plataforma"] == p for x in out["plataformas"]):
            continue
        problems = []
        for pr in (item.get("problemas") or [])[:30]:
            sev = pr.get("gravidade") if pr.get("gravidade") in ("erro", "aviso", "dica") else "aviso"
            problems.append({k: str(pr.get(k) or "")[:600] for k in ("momento", "onde", "o_que", "regra", "como_corrigir")} | {"gravidade": sev})
        verdict = item.get("veredito") if item.get("veredito") in ("reprovado", "risco", "aprovado") else "risco"
        if any(pr["gravidade"] == "erro" for pr in problems):
            verdict = "reprovado"
        out["plataformas"].append({"plataforma": p, "veredito": verdict, "problemas": problems})
    return out


def review(path: str, filename: str, content_type: str, platforms: dict, sector: str, texts: dict, specs: str) -> dict:
    key = api_key()
    if not key:
        raise ReviewError("A análise com IA ainda não foi ligada neste servidor (falta a GEMINI_API_KEY).", 503)
    mime = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if not (mime.startswith("video/") or mime.startswith("image/")):
        raise ReviewError("Envie um vídeo ou uma imagem.", 400)
    if mime == "video/quicktime" and filename.lower().endswith(".m4v"):
        mime = "video/mp4"
    started = time.time()
    file = upload_file(path, mime, key)
    try:
        file = wait_active(file, key)
        parts = [{"file_data": {"mime_type": mime, "file_uri": file["uri"]}}, {"text": build_prompt(platforms, sector, texts, specs)}]
        report, model = generate(parts, key)
    finally:
        delete_file(file, key)
    result = clean_report(report, platforms)
    result["modelo"] = model
    result["segundos"] = round(time.time() - started, 1)
    log.info("revisão IA ok (%s) em %.1fs", model, result["segundos"])
    return result
