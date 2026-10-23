# Ligar a "Análise com IA" do Checador de criativos

A IA usada é o **Google Gemini**. Ela assiste ao vídeo inteiro com imagem e som: ouve a narração, lê o texto na tela e olha as cenas. Depois devolve um laudo para Meta, TikTok e Kwai.

**Por que o Gemini:** é o provedor que analisa vídeo **com o áudio** numa única chamada. Ele também tem um **plano grátis**, sem cartão, que serve para testar.

São 3 passos, cerca de 10 minutos.

---

## 1. Criar a chave da IA (Google AI Studio)

1. Entre em **https://aistudio.google.com** com uma conta Google.
2. Clique em **Get API key** (ou "Criar chave de API").
3. Clique em **Create API key**. Se ele perguntar por um projeto, deixe criar um novo.
4. Copie a chave, que é um texto longo começando com `AIza…`.
   - **Trate como senha:** não mande para ninguém e não cole no GitHub.

## 2. Colocar a chave no Render

1. Abra **https://dashboard.render.com** e clique no serviço **baixar-video**.
2. No menu da esquerda, clique em **Environment** e depois em **Add Environment Variable**:

   | Key | Value |
   |---|---|
   | `GEMINI_API_KEY` | a chave que você copiou |
   | `REVIEW_ACCESS_CODE` | *(opcional)* uma senha para só seus amigos usarem a IA |
   | `REVIEWS_PER_DAY` | `15` (análises por pessoa por dia) |
   | `AI_DAILY_LIMIT` | `150` (análises por dia no total) |

3. Clique em **Save, rebuild and deploy**.

## 3. Atualizar os arquivos do servidor no GitHub

No repositório **baixar-video**:
1. Abra a pasta `server`, clique em **Add file**, depois em **Upload files**, e envie `ai_review.py`, `main.py` e `requirements.txt` desta pasta.
2. Volte para a primeira tela do repositório e envie `render.yaml` e este guia.
3. Clique em **Commit changes**. O Render atualiza sozinho em alguns minutos (acompanhe em **Events/Logs**). Quando aparecer `IA: ligada` nos logs, está pronto.

Por último, no repositório **do site**, envie os arquivos novos do site (**tool-checador.js**, **style.css** e **config.js**).

---

## Usando

No **Checador de criativos**, cada criativo ganha o botão **"Analisar conteúdo com IA"**. A análise leva de 20 a 90 segundos e traz:
- veredito por plataforma;
- cada problema com o **momento do vídeo** (ex.: `00:07`), onde ele está (fala, texto na tela, imagem), a regra que viola e como corrigir;
- uma sugestão de texto para o anúncio que passaria nas regras;
- o que a IA ouviu e leu no vídeo.

Se você definiu `REVIEW_ACCESS_CODE`, a página pede essa senha uma vez e guarda no navegador.

---

## Custos e privacidade

- **Plano grátis:** não precisa de cartão, mas tem limite de uso por minuto e por dia. **No plano grátis o Google pode usar o que é enviado para melhorar os produtos dele.** Para testar com criativos de exemplo, tudo bem.
- **Plano pago:** para criativos de clientes ou conteúdo sigiloso, ative o faturamento no AI Studio. No plano pago o Google não usa seus dados para treinar.
  - Quanto custa: pela tabela de outubro de 2026, o Gemini Flash cobra cerca de US$ 0,75 por milhão de tokens de entrada. Um vídeo de 30 segundos fica perto de **US$ 0,02, uns 10 centavos de real**. Confira a tabela atual antes, porque os preços mudam (há aumento previsto para janeiro de 2027).
- **Controle de gasto:** os limites `REVIEWS_PER_DAY` e `AI_DAILY_LIMIT` evitam surpresa. No Google Cloud você também pode criar um alerta de orçamento.
- **O que acontece com o arquivo:** o vídeo vai para o servidor, que o envia ao Google só durante a análise e pede para apagar logo em seguida. O servidor não guarda cópia.

## Se algo der errado

| Mensagem | O que fazer |
|---|---|
| "ainda não foi ligada (falta a GEMINI_API_KEY)" | Confira o passo 2 e espere o deploy terminar |
| "A chave da IA foi recusada" | Copie a chave de novo no AI Studio, sem espaços |
| "A cota da IA no Google acabou por agora" | Limite do plano grátis: espere alguns minutos ou ative o faturamento |
| "limite de análises com IA de hoje" | São os seus limites (`REVIEWS_PER_DAY` / `AI_DAILY_LIMIT`); aumente se quiser |
| "A própria IA bloqueou a análise" | O conteúdo foi considerado sensível; provavelmente as plataformas também vão reprovar |

## Trocar o modelo (avançado)

O padrão é `gemini-3.8-flash`. Se ele não existir para a sua chave, o servidor tenta `gemini-3-flash` e depois `gemini-2.5-flash` sozinho. Para fixar outro modelo, crie a variável `GEMINI_MODEL` no Render.
