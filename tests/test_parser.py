"""Testes do interpretador de links/perfis do lote e da leitura da saída do gallery-dl."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import profiles  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILS.append(name)


def one(token, hp=None):
    srcs, probs = profiles.parse_input(token, hp, 50)
    return (srcs[0].kind, srcs[0].platform, srcs[0].value) if srcs else ("erro", probs)


print("[links de vídeo]")
check("reel", one("https://www.instagram.com/reel/C9xYz12AbCd/")[:2] == ("video", "instagram"))
check("reels/ID", one("https://www.instagram.com/reels/C9xYz12AbCd/")[:2] == ("video", "instagram"))
check("post /p/", one("instagram.com/p/C9xYz12AbCd/?igsh=x")[:2] == ("video", "instagram"))
check("perfil/reel/ID", one("https://www.instagram.com/fulano/reel/C9xYz12AbCd/")[:2] == ("video", "instagram"))
check("tiktok vídeo", one("https://www.tiktok.com/@beltrana/video/7430000000000000001")[:2] == ("video", "tiktok"))
check("tiktok foto", one("https://www.tiktok.com/@beltrana/photo/7430000000000000001")[:2] == ("video", "tiktok"))
check("tiktok curto", one("https://vm.tiktok.com/ZMabc123/")[:2] == ("video", "tiktok"))

print("[perfis]")
check("tiktok perfil", one("https://www.tiktok.com/@beltrana") == ("profile", "tiktok", "beltrana"))
check("tiktok perfil ?lang", one("https://www.tiktok.com/@beltrana?lang=pt-BR") == ("profile", "tiktok", "beltrana"))
check("ig perfil", one("https://www.instagram.com/fulano.ssa/") == ("profile", "instagram", "fulano.ssa"))
check("ig aba reels", one("https://instagram.com/fulano.ssa/reels/") == ("profile", "instagram", "fulano.ssa"))
check("@ tiktok", one("@beltrana", "tiktok") == ("profile", "tiktok", "beltrana"))
check("@ instagram com ponto", one("@fulano.ssa", "instagram") == ("profile", "instagram", "fulano.ssa"))
check("nome solto com ponto", one("fulano.ssa", "instagram") == ("profile", "instagram", "fulano.ssa"))
r = one("@beltrana")
check("@ sem plataforma pede escolha", r[0] == "erro" and "escolha" in r[1][0], r)

print("[recusas]")
check("youtube", one("https://youtube.com/watch?v=1")[0] == "erro")
check("ig explore", one("https://www.instagram.com/explore/")[0] == "erro")
check("ig stories", one("https://www.instagram.com/stories/fulano/")[0] == "erro")
check("domínio falso", one("https://instagram.com.evil.com/fulano")[0] == "erro")
check("nome inválido", one("@nome!!", "tiktok")[0] == "erro")
check("nome longo demais (IG > 30)", one("@" + "a" * 31, "instagram")[0] == "erro")

print("[texto misturado]")
text = """
https://www.tiktok.com/@beltrana
@fulano.ssa, https://www.instagram.com/reel/AAA111/
https://www.instagram.com/reel/AAA111/
lixo://x  https://youtube.com/x
"""
srcs, probs = profiles.parse_input(text, "instagram", 50)
check("3 fontes, duplicata removida", [s.kind for s in srcs] == ["profile", "profile", "video"],
      [(s.kind, s.value) for s in srcs])
check("2 problemas apontados", len(probs) == 2, probs)
srcs, probs = profiles.parse_input(" ".join(f"@p{i}xx" for i in range(30)), "tiktok", 20)
check("limite de fontes", len(srcs) == 20 and any("Só cabem 20" in p for p in probs))

print("[saída do gallery-dl]")
sample = [
    [2, {"username": "fulano"}],
    [3, "https://cdn/a.mp4", {"post_shortcode": "R1", "shortcode": "R1", "video_url": "https://cdn/a.mp4",
                              "description": "reel 1"}],
    [3, "https://cdn/a2.mp4", {"post_shortcode": "R1", "video_url": "https://cdn/a2.mp4"}],   # mesmo post
    [3, "https://cdn/b.jpg", {"post_shortcode": "P2", "video_url": None}],                    # foto
    [3, "https://cdn/c.mp4", {"post_shortcode": "F3", "video_url": "https://cdn/c.mp4",
                              "product_type": "feed"}],                                         # vídeo do feed
    [3, "https://cdn/d.mp4", {"shortcode": "R4", "video_url": "https://cdn/d.mp4", "product_type": "clips"}],
    [-1, {"error": "HttpError", "message": "429 Too Many Requests"}],
]
found, errors = profiles.parse_gallery_dl_json(json.dumps(sample))
check("só Reels, sem fotos/feed/duplicatas", [f.url for f in found] == [
    "https://www.instagram.com/reel/R1/", "https://www.instagram.com/reel/R4/"], [f.url for f in found])
check("erros capturados", errors and "429" in errors[0])
check("lixo não quebra", profiles.parse_gallery_dl_json("não é json") == ([], ["resposta inválida do gallery-dl"]))

print("\n" + ("TUDO CERTO" if not FAILS else f"{len(FAILS)} FALHA(S): {FAILS}"))
sys.exit(1 if FAILS else 0)
