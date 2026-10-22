# Baixar vídeo do Instagram e TikTok

Ferramenta completa com duas abas:

- **Um vídeo:** o visitante cola o link, vê a prévia, escolhe a qualidade e baixa o MP4 (ou só o áudio em MP3).
- **Vários vídeos:** cola vários links e/ou perfis, um por linha. Vale o link do perfil ou só o `@`. Os vídeos são baixados em sequência e entregues num `.zip` ou um por um.

```
server/            API em Python (Starlette + yt-dlp + ffmpeg)
web/               páginas: index.html (página completa) e embed.html (para colocar no seu site)
deploy/            Caddyfile (HTTPS automático) e script de inicialização
tests/             teste de ponta a ponta (não precisa de internet)
Dockerfile, docker-compose.yml, render.yaml, .env.example
```

## Qualidade

- **Sempre a maior que a plataforma entrega**, sem marca d'água. No Instagram isso costuma ser 1080p. O Instagram guarda vídeo e áudio separados nessa qualidade, e o servidor junta os dois sem recomprimir. No TikTok, costuma ser até 1080p.
- **Nada é recomprimido.** O arquivo final é o mesmo vídeo da plataforma, só reorganizado para abrir na hora em celulares.
- **O visitante pode escolher a resolução.** Quando houver duas versões com a mesma resolução, a ferramenta prefere H.264, que abre em qualquer aparelho.
- **HEVC no TikTok.** Às vezes o 1080p só existe em HEVC. A página avisa que alguns PCs com Windows precisam de uma extensão para abrir e oferece a opção H.264 logo abaixo.
- **Ficam de fora:** versões com marca d'água e o formato "bytevc2" do TikTok, que quase nenhum player abre.
- **Limite natural.** Nenhuma ferramenta passa da qualidade que a plataforma guarda, porque o Instagram e o TikTok comprimem o vídeo quando ele é postado.

## Download em lote

O que dá para colar (misturado, um por linha):

| Entrada | O que baixa |
|---|---|
| `https://www.tiktok.com/@perfil` ou `@perfil` (com "TikTok" escolhido) | Os vídeos mais recentes do perfil |
| `https://www.instagram.com/perfil/` ou `@perfil` (com "Instagram" escolhido) | **Só os Reels** do perfil (a aba Reels) |
| Links de vídeos, Reels ou posts avulsos | Cada um deles |

- **Reels do Instagram, com atenção redobrada.** A lista vem da própria aba Reels do perfil. Além disso, cada item é conferido: posts de foto e vídeos comuns do feed ficam de fora, e um mesmo post nunca entra duas vezes.
- **TikTok.** Posts de fotos (carrossel) não são vídeo e aparecem na lista como "Pulado: não é vídeo".
- **Como os vídeos são baixados.** Um de cada vez, com pausas de alguns segundos entre eles. Isso reduz bastante o risco de a plataforma bloquear o servidor. Um perfil com 50 vídeos leva alguns minutos.
- **Escolhas do visitante.** Quantos vídeos por perfil (até o teto do servidor) e a qualidade. "Máxima" pega a maior disponível. "Compatível" pega a maior em H.264, que abre em qualquer aparelho.
- **Entrega.** Um `.zip` com tudo ou cada vídeo separado. Os arquivos ficam disponíveis por 60 minutos e depois são apagados.
- **Recarregar a página não perde o lote.** O endereço guarda o lote (`#lote=…`).
- **Proteção contra abuso.** Cada visitante tem 1 lote por vez e no máximo 4 por hora. Há também tetos de vídeos, de tamanho e de espaço em disco (veja `.env.example`).

### Perfis do Instagram precisam de login

O Instagram só entrega a lista de Reels de um perfil para quem está logado. Sem os cookies configurados, a ferramenta avisa isso na hora. Links de Reels avulsos e perfis do TikTok continuam funcionando sem login. Para ativar, veja "Configurar os cookies do Instagram" abaixo.

A lista de Reels é feita pelo [gallery-dl](https://github.com/mikf/gallery-dl), porque o extrator de perfis do Instagram no yt-dlp está desativado. O download de cada Reel continua pelo yt-dlp, com a mesma qualidade máxima da aba "Um vídeo".

## Onde hospedar

O servidor precisa rodar Python + ffmpeg sem limite curto de tempo, então **Vercel, Netlify e hospedagem de PHP compartilhada não servem**. Opções que funcionam (preços de outubro de 2026):

| Opção | Custo | Bom para |
|---|---|---|
| **VPS** (Hetzner CX23 ou CAX11, ou outra) | ~€5,50–6/mês | **Recomendado.** Roda a ferramenta e o seu site pessoal juntos, com HTTPS automático. |
| **Oracle Cloud Always Free** | grátis | Funciona bem, mas o cadastro pede cartão e às vezes falta máquina disponível. |
| **Render (plano gratuito)** | grátis | Testar. Dorme após 15 min sem uso (demora cerca de 1 min para acordar) e só tem 5 GB de banda/mês, o que dá umas poucas centenas de vídeos. |

**Sobre o Instagram:** ele bloqueia com frequência pedidos vindos de servidores de datacenter. O TikTok costuma funcionar direto. Se o Instagram começar a falhar, veja "Quando o Instagram bloquear" abaixo.

### Opção A: VPS (recomendado)

1. Crie um servidor Ubuntu e instale o Docker: `curl -fsSL https://get.docker.com | sh`
2. No painel do seu domínio, crie um registro **A** apontando `baixar.seusite.com.br` para o IP do servidor.
3. Envie esta pasta para o servidor e rode:
   ```bash
   cp .env.example .env
   nano .env            # troque DOMINIO pelo seu
   docker compose up -d --build
   ```
4. Abra `https://baixar.seusite.com.br`. O certificado HTTPS é gerado sozinho no primeiro acesso.

**Para hospedar seu site pessoal no mesmo servidor:** coloque os arquivos dele na pasta `meu-site/`, defina `DOMINIO_SITE` no `.env`, tire o `#` do bloco no fim de `deploy/Caddyfile` e rode `docker compose up -d` de novo.

**Manter atualizado:** o yt-dlp se atualiza sozinho cada vez que o app reinicia. Para reiniciar toda madrugada, rode `crontab -e` e adicione esta linha:
```
0 5 * * * cd /caminho/video-downloader && docker compose restart app
```

### Opção B: Render (grátis, para testar)

Passo a passo completo, incluindo como ligar no site da Vercel: veja **GUIA-RENDER.md**.

1. Suba esta pasta para um repositório no GitHub.
2. No Render: **New > Blueprint** e escolha o repositório. Ele lê o `render.yaml` sozinho.
3. Use o endereço `https://….onrender.com` que o Render gerar.

## Colocar no seu site

**Jeito mais simples: um link** para `https://baixar.seusite.com.br`.

**Ferramenta dentro de uma página do seu site:** cole este bloco onde ela deve aparecer, trocando o endereço. A altura se ajusta sozinha.

```html
<iframe id="baixar-video" src="https://baixar.seusite.com.br/embed"
        style="width:100%;max-width:640px;border:0;height:200px"
        allow="clipboard-read; clipboard-write" title="Baixar vídeo"></iframe>
<script>
  addEventListener("message", function (e) {
    if (e.origin === "https://baixar.seusite.com.br" && e.data && e.data.type === "vdl:height")
      document.getElementById("baixar-video").style.height = e.data.height + "px";
  });
</script>
```

Para forçar um tema, use `/embed?theme=light` ou `/embed?theme=dark`. Sem isso, o tema segue o do aparelho do visitante.

**Personalizar:** as cores ficam no topo de `web/tool.css` (`--accent`, `--sun`…). Os textos ficam em `web/index.html` e `web/tool.js`. Troque o link "Início" em `index.html` pelo endereço do seu site.

## Configurar os cookies do Instagram

Os cookies são necessários para listar Reels de perfis e ajudam quando o Instagram bloqueia vídeos avulsos (sintoma: "Não consegui acessar esse vídeo" para vídeos públicos).

1. **Cookies de uma conta secundária.** Não use sua conta principal: baixar muitos Reels pode fazer o Instagram limitar a conta.
   - No navegador, entre no Instagram com essa conta e exporte os cookies em formato Netscape. A extensão "Get cookies.txt LOCALLY" faz isso.
   - Salve como `cookies/instagram.txt` nesta pasta e, no `.env`, tire o `#` de `COOKIES_FILE=/app/cookies/instagram.txt`.
   - Rode `docker compose up -d`. A aba "Vários vídeos" para de mostrar o aviso de login.
   - Os cookies expiram de tempos em tempos (semanas ou meses). Quando a ferramenta disser que o Instagram recusou o login, exporte de novo.
2. **Proxy residencial:** defina `YTDLP_PROXY` no `.env`. É pago, mas costuma resolver bloqueios de vez.

## Configurações (`.env`)

| Variável | Padrão | O que faz |
|---|---|---|
| `RATE_LIMIT_PER_MIN` | 12 | Pedidos por visitante por minuto |
| `DOWNLOADS_PER_HOUR` | 40 | Downloads por visitante por hora |
| `MAX_CONCURRENT_DOWNLOADS` | 3 | Downloads processados ao mesmo tempo (os demais esperam até 45 s) |
| `MAX_FILESIZE_MB` | 300 | Tamanho máximo de vídeo |
| `TRUST_PROXY_HOPS` | 1 | Proxies na frente do app: Caddy = 1, Render = 2. Define de onde vem o IP do visitante. |
| `CLIENT_IP_HEADER` | — | Cabeçalho com o IP real, se o provedor tiver um (ex.: `cf-connecting-ip` com Cloudflare) |
| `COOKIES_FILE` / `YTDLP_PROXY` | — | Veja a seção acima |
| `AUTO_UPDATE_YTDLP` | 1 | Atualiza o yt-dlp e o gallery-dl ao iniciar |
| `BATCH_*` | veja `.env.example` | Tetos do download em lote (vídeos por perfil, por lote, tamanho, tempo de expiração) |

## Rodar no seu computador

Precisa de Python 3.10+ e ffmpeg.
```bash
pip install -r server/requirements.txt
cd server && uvicorn main:app --reload --port 8000
```
Abra `http://localhost:8000`.

**Teste automático** (sem internet; simula Instagram e TikTok com vídeos gerados pelo ffmpeg):
```bash
pip install requests
mkdir -p /tmp/media && cd /tmp/media
ffmpeg -f lavfi -i testsrc2=s=1080x1920:d=3 -c:v libx264 -pix_fmt yuv420p -an v1080_avc.mp4
ffmpeg -f lavfi -i testsrc2=s=720x1280:d=3 -c:v libx264 -pix_fmt yuv420p -an v720_avc.mp4
ffmpeg -f lavfi -i sine=d=3 -c:a aac audio.m4a
ffmpeg -f lavfi -i testsrc2=s=720x1280:d=3 -f lavfi -i sine=d=3 -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest prog720.mp4
ffmpeg -f lavfi -i testsrc2=s=576x1024:d=3 -f lavfi -i sine=d=3 -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest tt540_h264.mp4
cp prog720.mp4 tt720_h264.mp4
ffmpeg -f lavfi -i testsrc2=s=1080x1920:d=3 -f lavfi -i sine=d=3 -c:v libx265 -tag:v hvc1 -c:a aac -shortest tt1080_hevc.mp4
cd - && MEDIA_DIR=/tmp/media python tests/test_end_to_end.py
MEDIA_DIR=/tmp/media python tests/test_batch.py     # lote: perfis, ZIP, cancelamento, limites
python tests/test_parser.py                          # reconhecimento de links, @ e saída do gallery-dl
```

## Uso responsável

Os termos do Instagram e do TikTok restringem baixar conteúdo de outras pessoas, e os vídeos pertencem a quem os criou. A página já avisa que a ferramenta é para baixar vídeos próprios ou com permissão do autor. Os arquivos ficam no servidor só durante o download e são apagados logo depois.
