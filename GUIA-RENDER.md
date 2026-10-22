# Colocar o "Baixar vídeo" no ar pelo Render (grátis)

São 3 partes, cerca de 20 minutos no total. Você não precisa instalar nada no computador.

- **Parte 1:** subir esta pasta num repositório novo no GitHub.
- **Parte 2:** criar o servidor no Render, que é grátis.
- **Parte 3:** ligar o seu site da Vercel nesse servidor.

---

## Parte 1: GitHub (repositório novo)

1. Entre em **github.com**, clique no **+** (canto superior direito) e depois em **New repository**.
2. Em **Repository name**, coloque `baixar-video`. Marque **Private**, se quiser que ninguém veja o código; o Render funciona do mesmo jeito. Clique em **Create repository**.
3. Na página que abrir, clique no link **uploading an existing file**.
4. No seu computador, abra o zip e entre na pasta `video-downloader`. Selecione **tudo o que está dentro dela** e arraste para a página do GitHub. São as pastas `server`, `web`, `deploy`, `tests`, `cookies` e os arquivos `Dockerfile`, `render.yaml` e os outros.
   - Arraste o **conteúdo** da pasta, não a pasta `video-downloader` em si. O arquivo `render.yaml` precisa ficar na "raiz" do repositório, logo na primeira tela.
   - No Mac, arquivos que começam com ponto (como `.dockerignore`) ficam escondidos. Tudo bem se ele não for: ele não é obrigatório.
5. Espere os arquivos carregarem e clique em **Commit changes**.

---

## Parte 2: Render (o servidor)

1. Entre em **render.com** e clique em **Get Started**. Faça o cadastro com **GitHub**: é o jeito mais fácil, porque ele já enxerga seus repositórios. Não pede cartão.
2. No painel, clique em **New +** e depois em **Blueprint**.
3. Escolha o repositório `baixar-video`. Se ele não aparecer, clique em **Configure GitHub** e libere o acesso a ele.
4. O Render lê o arquivo `render.yaml` sozinho e pergunta um valor:
   - **ALLOWED_ORIGINS:** cole o endereço do seu site na Vercel, por exemplo `https://ferramentas-web.vercel.app`. É isso que autoriza o seu site a usar o servidor.
5. Clique em **Apply** (ou **Deploy Blueprint**). A primeira montagem leva de 5 a 10 minutos. Você acompanha em **Logs**. Quando aparecer **Live** (verde), está no ar.
6. Na página do serviço, copie o endereço que aparece no topo, algo como `https://baixar-video-xxxx.onrender.com`.
   - Teste: abra esse endereço no navegador. A própria página da ferramenta deve aparecer.

---

## Parte 3: ligar o seu site da Vercel

### 3.1 Enviar os arquivos novos

No repositório do seu site no GitHub (o mesmo que a Vercel usa), abra a pasta onde estão os arquivos do site. Clique em **Add file**, depois em **Upload files**, e arraste estes 5 arquivos:

- `tool-baixar-video.js` (novo)
- `config.js` (novo)
- `index.html`
- `style.css`
- `vercel.json`

Os que já existem são substituídos sozinhos, então não precisa apagar nada antes. Clique em **Commit changes**. A Vercel atualiza o site em cerca de 1 minuto.

### 3.2 Colocar o endereço do servidor (escolha um jeito)

- **Jeito A (vale para todo mundo):** no GitHub, abra o arquivo `config.js`, clique no lápis (**Edit**) e cole o endereço do Render entre as aspas:
  ```js
  baixarVideoServidor: 'https://baixar-video-xxxx.onrender.com',
  ```
  Depois clique em **Commit changes**.
- **Jeito B (só no seu navegador, bom para testar):** deixe o `config.js` como está. Abra o site, vá em **Baixar vídeo**, cole o endereço no campo que aparece e clique em **Conectar**.

---

## Usando

1. Abra o seu site e clique em **Baixar vídeo** no menu.
2. Se o servidor estava parado, aparece **"Ligando o servidor…"**. No plano grátis isso leva até 1 minuto, só no primeiro uso depois de um tempo parado. Quando ficar **"Servidor pronto"**, pode usar.
3. Cole o link de um TikTok ou Reel, escolha a qualidade e clique em **Baixar vídeo**.
4. Na aba **Vários vídeos**, cole vários links ou perfis (um por linha) e baixe tudo num `.zip`.

---

## Se algo der errado

| O que aparece | O que fazer |
|---|---|
| "O servidor respondeu, mas não liberou este site" | No Render, abra o serviço e vá em **Environment**. Em `ALLOWED_ORIGINS`, coloque exatamente o endereço que a mensagem mostra e clique em **Save Changes**. Espere reiniciar e clique em **Conectar** de novo. |
| "Não consegui falar com o servidor" | Confira o endereço copiado do Render e se o serviço está **Live**. |
| Fica muito tempo em "Ligando o servidor…" | Normal na primeira vez do dia. Se passar de 2 minutos, olhe os **Logs** no Render. |
| Instagram: "Não consegui acessar esse vídeo" (com vídeo público) | O Instagram às vezes bloqueia servidores. Veja "Cookies do Instagram" abaixo. TikTok costuma funcionar direto. |
| Lote de perfil do Instagram pede login | Também precisa dos cookies (abaixo). |

### Cookies do Instagram (opcional)

Use uma **conta secundária**, não a principal: baixar muito pode fazer o Instagram limitar a conta.

1. No Chrome, entre no Instagram com essa conta. Instale a extensão **"Get cookies.txt LOCALLY"** e exporte os cookies do instagram.com. Vai baixar um arquivo de texto.
2. No Render, abra o serviço, vá em **Environment** e role até **Secret Files**. Clique em **Add Secret File**:
   - **Filename:** `instagram.txt`
   - **Contents:** cole todo o conteúdo do arquivo exportado.
3. Ainda em **Environment**, adicione uma variável:
   - **Key:** `COOKIES_FILE`
   - **Value:** `/etc/secrets/instagram.txt`
4. Clique em **Save Changes**. O servidor reinicia sozinho.

Os cookies vencem de tempos em tempos. Quando o Instagram voltar a recusar, exporte de novo e atualize o Secret File.

---

## Limites do plano grátis

- O servidor **dorme** depois de 15 minutos sem uso e acorda no próximo acesso, em até cerca de 1 minuto.
- São **5 GB de tráfego por mês**, o que dá para algumas centenas de vídeos.
- Para não gastar banda à toa, cada perfil baixa no máximo 25 vídeos e cada lote no máximo 50.

## Quando quiser sair do teste

O mesmo projeto roda numa VPS paga (veja a seção "Opção A: VPS" no `README.md`), sem limite de sono e com o Instagram mais estável. Se o endereço do servidor mudar para um domínio seu (ex.: `baixar.seusite.com.br`), troque o endereço no `config.js` e acrescente esse domínio em `connect-src`, no `index.html` e no `vercel.json` do site, ao lado de `https://*.onrender.com`.

## Uso responsável

Baixe só vídeos seus ou com permissão de quem criou. Os termos do Instagram e do TikTok restringem baixar conteúdo de outras pessoas.
