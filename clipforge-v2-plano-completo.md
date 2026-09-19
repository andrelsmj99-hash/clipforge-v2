# Clip Forge V2 — Documento Consolidado de Planejamento

> Projeto novo (não reaproveita código do ClipForge antigo). Objetivo: conectar redes sociais, baixar vídeos de canais, editar usando templates do Canva, agendar e publicar em massa — tudo rodando localmente, dividido em módulos independentes.

**Status geral:** arquitetura e módulos fechados. Módulos YouTube e Templates (Canva) planejados por completo. O Kwai foi removido do escopo (era a plataforma mais incerta: sem extractor no yt-dlp, sem agendador nativo, sem lib de automação madura). Descoberta importante: TikTok e Instagram têm agendamento nativo real (via TikTok Studio e Meta Business Suite/app) — até essa automação ser implementada, o Publisher/DueScanner serve de fallback pra elas. O Downloader foi consolidado em cima do yt-dlp (biblioteca Python) pra YouTube, TikTok e Instagram. Demais módulos (Conectores/Agendamento das outras plataformas, Editor, Publisher) ainda não detalhados individualmente.

---

## 1. Arquitetura geral

**Padrão: worker-based com fila de jobs local + conectores plugáveis.**

```
┌─────────────┐
│   UI (fina) │  ← só dispara comandos e lê status, nenhuma lógica pesada
└──────┬──────┘
       │ escreve/lê
┌──────▼──────────────────────┐
│  Fila de jobs (SQLite)       │  ← único ponto de acoplamento entre módulos
│  jobs(type, payload, status) │
└──────┬───────────────────────┘
       │ consumida por processos separados
┌──────▼──────┐ ┌───────────┐ ┌───────────┐
│  Downloader │ │  Editor   │ │ Publisher │  ← cada um é um processo isolado,
│   worker    │ │  worker   │ │  worker   │     reiniciável e testável sozinho
└─────────────┘ └───────────┘ └───────────┘
```

- **Nenhum módulo chama outro diretamente.** Toda comunicação passa pela fila `jobs`. Isso permite testar cada módulo isolado (criando jobs falsos direto no banco) e trocar a implementação por trás de qualquer um sem afetar o resto.
- **Workers são processos, não threads** — um download travado ou um render pesado não engasga a UI nem os outros módulos.
- **Conectores de rede social são plugáveis**: mesma interface (`connect`, `fetch_channel`, `publish`) para todas as plataformas, implementação livre por trás (lib dedicada onde existir, Playwright como fallback).
- **Publisher nasce como serviço isolado**, preparado para migrar para a VM GCP no futuro sem mudança de lógica (rodando local por enquanto — decisão tomada).

---

## 2. Stack tecnológica

| Camada | Escolha | Por quê |
|---|---|---|
| UI desktop | Tauri (ou Flet, se preferir manter familiaridade) | Leve, local, sem exigir runtime pesado |
| Workers (download/render/publish) | Python | Ecossistema mais maduro pra scraping/mídia — yt-dlp, ffmpeg-python, playwright, instagrapi |
| Fila de jobs | SQLite (tabela `jobs`) | Suficiente para app local de um usuário; sem necessidade de Redis/RabbitMQ |
| Automação sem API oficial | Playwright | Necessário pro Canva (edição de template) e pra automatizar o agendador nativo do TikTok/Instagram |
| Processamento de vídeo | ffmpeg (CLI, via subprocess) | Padrão da indústria, independente de linguagem |

---

## 3. Módulos — visão geral

### 3.1 Conectores (accounts)
- **Possui:** autenticação/sessão por plataforma, refresh de token/sessão.
- **Não possui:** lógica de o que baixar ou postar.
- **Falha:** sessão expira → `account.status = disconnected`, worker segue rodando pras outras contas.
- **Implementação por plataforma:**
  - YouTube: OAuth oficial (Data API v3) — **planejado em detalhe, seção 8**
  - Instagram: automação (Playwright) do agendador nativo (app público desde março/2026, ou Meta Business Suite)
  - TikTok: automação (Playwright) do TikTok Studio (agendador nativo, web)

### 3.2 Downloader
- **Possui:** dado um canal/perfil, baixa vídeos (shorts e longos) e metadados.
- **Não possui:** conhecimento de template ou destino de publicação.
- **Falha:** vídeo indisponível/geo-bloqueado → job vai pra `failed` com motivo salvo, sem travar a fila.
- **Decisão (atualizada):** consolidar via **yt-dlp como biblioteca Python** (não CLI/subprocess) — dá controle programático total (progress hooks, metadados estruturados, seleção de formato) sem parsear saída de linha de comando.
- **Ferramenta por plataforma:**
  - **YouTube:** yt-dlp — extractor mais maduro e ativo, funciona pra canal próprio e de terceiros sem gastar quota de API. Detalhado na seção 8.
  - **TikTok:** yt-dlp — extractor dedicado e ativo (updates recentes), bom candidato pra reaproveitar sem lógica própria.
  - **Instagram:** yt-dlp — extractor dedicado existe, mas é **frágil** (quebra periodicamente com mudanças do Instagram, houve issue de extração de perfil público quebrada recentemente). Usar, mas com tratamento de erro robusto — não depender 100% dele no fluxo crítico.
- **Independência do módulo Conectores:** baixar vídeo (YouTube/TikTok/Instagram via yt-dlp) não depende de conta conectada nem de autenticação — funciona pra qualquer canal público. Conectores só entra em jogo na hora de **publicar**.
- **Deduplicação:** `--download-archive` do próprio yt-dlp (por ID de vídeo) como camada adicional de segurança, complementando a checagem por `source_url` já feita no banco.

### 3.3 Templates (Canva)
- **Possui:** catálogo local de templates + mapa de placeholders (elemento de vídeo a ser substituído, identificado via seleção manual única).
- **Não possui:** nenhum vídeo — só a "planta" do template.
- **Decisão:** usa o **Canva Apps SDK** (app privado em TypeScript, rodando dentro do editor, permanentemente em modo desenvolvimento/preview) para substituir o vídeo e exportar — mecanismo oficial e estável, em vez de automação cega de UI. **Planejado em detalhe, seção 9.**
- **Mitigação:** checagem de saúde do dev server antes de cada render; remapeamento sinalizado se o elemento salvo não existir mais no template.

### 3.4 Editor
- **Possui:** combina vídeo + template → dispara `job(type=render)` → produz vídeo final.
- **Não possui:** decisão de quando/onde postar.
- **Falha:** render trava/timeout → retry limitado (ex: 2 tentativas), depois `failed` visível na UI.

### 3.5 Agendador
- **Possui:** calendário de posts (render + conta + data/hora + legenda), incluindo postagem em massa.
- **Não possui:** lógica de publicação em si — só decide *quando* o job de publicação nasce.
- **Postagem em massa:** seleciona N renders → legenda única (editável por post depois) → intervalo (ex: 1h) → sistema calcula `scheduled_at` de cada post automaticamente, agrupados em um `batch`. Pode ser feito por plataforma separadamente.
- **Agendamento nativo vs. disparado (atualizado):**
  - **YouTube**: agendamento nativo via API oficial (`publishAt`).
  - **TikTok**: agendamento nativo real via **TikTok Studio** (web) — automatizado via Playwright preenchendo o formulário de agendamento; a partir daí o próprio TikTok publica sozinho. Limite: até 10-30 dias à frente (varia por fonte, confirmar na implementação), exige conta Creator/Business.
  - **Instagram**: agendamento nativo real via **app público** (desde março/2026) ou **Meta Business Suite** (mais viável de automatizar, via navegador) — a plataforma publica sozinho. Até 75 dias à frente, 25 posts/dia, Business Suite exige conta vinculada a uma Página do Facebook.
  - **Implicação:** o Publisher/DueScanner serve de fallback pro TikTok e Instagram até a automação do agendador nativo de cada um ser implementada — o sistema só precisa estar de pé na hora de *agendar* pras plataformas já automatizadas (YouTube).

### 3.6 Publisher
- **Possui:** pra plataformas sem agendamento nativo automatizado ainda (hoje: TikTok, Instagram), consome `job(type=publish)` e publica de fato no horário certo. Pra YouTube, o "publish" já foi resolvido no momento do agendamento nativo (seção 3.5) — o Publisher só confirma/sanity-check opcional.
- **Não possui:** nenhuma lógica de conteúdo — só publica o que já está pronto.
- **Falha:** rate limit / rejeição de formato → retry com backoff; falha persistente numa plataforma não bloqueia as outras.
- **Deploy:** roda local por enquanto (decisão tomada); desenhado como serviço isolado (só acessa a fila `jobs`/tabela `posts`, nunca UI ou outros módulos diretamente) para poder migrar para a VM GCP sem reescrever lógica.

---

## 4. Esquema de dados (sistema geral)

```sql
accounts(id, platform, status, session_data, connected_at)
channels(id, account_id, external_id, name)
videos(id, channel_id, source_url, local_path, type[short|long|live_vod], downloaded_at)
templates(id, canva_template_id, name, placeholder_map)
renders(id, video_id, template_id, output_path, status, created_at)
post_batches(id, caption, interval_seconds, start_at, status, created_at)
posts(id, batch_id, render_id, account_id, caption, scheduled_at, status, published_at)
jobs(id, type[download|render|publish], payload, status, attempts, error, created_at)
```

---

## 5. Fluxo ponta a ponta

1. Usuário conecta contas (Conectores) → `accounts`
2. Usuário informa canal → Downloader cria `job(download)` → baixa vídeos → `videos`
3. Usuário escolhe template → Templates mapeia placeholders → `templates`
4. Usuário associa vídeo(s) + template → Editor cria `job(render)` → gera vídeo final → `renders`
5. Usuário seleciona renders prontos, define legenda + intervalo → Agendador cria `post_batch` + N `posts`
6. No horário certo: para YouTube (API), a publicação já foi agendada nativamente no momento do upload — a própria plataforma publica sozinha; para TikTok e Instagram (até a automação do agendador nativo de cada um existir), Agendador cria `job(publish)` que o Publisher consome e executa no horário exato

---

## 6. Riscos identificados (sistema geral)

| Risco | Módulo | Impacto | Mitigação |
|---|---|---|---|
| App do Canva preso em modo dev/preview (sem publicação possível) | Templates | Médio — depende de dev server sempre disponível | Checagem de saúde antes de cada render, igual ao token do YouTube |
| Elemento de vídeo do template alterado/removido no Canva original | Templates | Médio — quebra o mapeamento salvo | Detectar e sinalizar "remapear", não falhar silenciosamente |
| Automação de UI do TikTok Studio / Meta Business Suite pode quebrar com mudanças de interface | Conectores | Médio — ainda é scraping de UI, mesmo que de uma tela nativa de agendamento | Testes de sanidade periódicos, mensagem clara quando o formulário não bate com o esperado |
| Extractor do yt-dlp pra Instagram é frágil (quebra periodicamente) | Downloader | Médio — pode falhar em ondas até a comunidade corrigir | Tratamento de erro robusto, não depender 100% dele no fluxo crítico |
| Rate limits (Instagram: 25 posts/dia; TikTok: 10-30 dias de antecedência) | Agendador | Médio — lotes grandes podem esbarrar no limite da plataforma | Validar limite por plataforma antes de criar batch grande |

---

## 7. Decisões-chave tomadas até agora

| Decisão | Escolha |
|---|---|
| Origem do download (YouTube) | Canal próprio **e** canais de terceiros |
| Deploy do Publisher | Local por enquanto, arquitetura pronta pra migrar pra VM GCP depois |
| Verificação do app Google Cloud | Adiada — reconexão manual da conta a cada 7 dias (modo Testing) |
| Vídeos com restrição de idade (YouTube) | Contornados via cookies de sessão da conta dedicada do projeto, sem limitar a canal próprio |
| Conta do YouTube usada | Conta dedicada ao projeto (não pessoal) — reduz risco de penalização de conta pessoal |
| Templates (Canva) | Canva Apps SDK (app privado em modo dev/preview permanente) em vez de automação cega de UI |
| Mapeamento de template | Aceita 1 clique manual por template (só na etapa inicial de mapeamento) |
| Agendamento — TikTok/Instagram | Automação do agendador nativo de cada plataforma (TikTok Studio / Meta Business Suite), publicação feita pela própria plataforma |
| Agendamento — TikTok/Instagram (interino) | Até a automação do agendador nativo existir, Publisher/DueScanner dispara a publicação no horário exato |
| Downloader — biblioteca | yt-dlp usado como biblioteca Python (não CLI/subprocess), pra controle programático |
| Downloader — TikTok/Instagram | Consolidado via yt-dlp (extractors dedicados); Instagram tratado como frágil, com fallback de erro robusto |
| Postagem em massa | Selecionar N vídeos, legenda única, intervalo definido (ex: 1h), separado por plataforma |
| Primeiro módulo a implementar | YouTube (Downloader + Conector/Publisher) |

---

## 8. Módulo YouTube — plano detalhado

> Primeiro módulo a ser implementado. Cobre dois papéis distintos: **Downloader** (baixar vídeos de qualquer canal) e **Conector/Publisher** (autenticar e publicar com agendamento nativo no canal próprio).

### 8.1 Escopo — dois fluxos independentes

| Fluxo | Origem | Autenticação | Ferramenta |
|---|---|---|---|
| **Download** | Canal próprio OU de terceiros | Nenhuma (conteúdo público) | yt-dlp, **usado como biblioteca Python** (não CLI/subprocess) — controle programático de progresso, metadados e formato |
| **Upload/Agendamento** | Só canal próprio | OAuth 2.0 (Data API v3) | google-api-python-client |

A API oficial do YouTube **não baixa vídeos** (só metadados) — por isso o download é sempre via yt-dlp, independente da origem ser seu canal ou de terceiros. A autenticação OAuth só entra quando o objetivo é publicar no seu próprio canal. yt-dlp não gasta quota de API nem exige que o canal seja seu — funciona pra qualquer canal público.

### 8.2 Setup do Google Cloud (pré-requisito)

1. Criar projeto no Google Cloud Console.
2. Ativar "YouTube Data API v3".
3. Configurar tela de consentimento OAuth como **External** (conta pessoal, não Workspace) + adicionar a conta do projeto como **test user**.
4. Gerar credenciais OAuth (Client ID/Secret) para app desktop.
5. **Decisão tomada:** manter em modo **Testing** por enquanto — token expira a cada 7 dias, exige reconexão manual nesse intervalo. Migrar pra verificação (produção) fica como item futuro se o app crescer além do uso do projeto.

**Implicação de design:** o sistema precisa detectar token expirado/inválido e sinalizar claramente na UI ("reconecte a conta do YouTube") em vez de falhar silenciosamente — isso vai acontecer toda semana por design, não é um bug.

### 8.3 Fluxo de Download

1. Usuário informa URL do canal (próprio ou de terceiros).
2. Sistema lista vídeos do canal via yt-dlp (sem baixar ainda) — metadados: título, duração, data, URL.
3. **Escopo por aba do canal (decidido):** o YouTube separa o conteúdo em abas distintas — `/videos` (vídeos normais), `/shorts` e `/streams` (lives encerradas/VODs). Apontar só pra `/@canal` sem sufixo resolve, por padrão, pra aba de Vídeos — **shorts e lives não entram automaticamente**. O sistema lista as três abas explicitamente (`/videos`, `/shorts`, `/streams`), já que **lives encerradas foram incluídas no escopo**. Classificação adicional: `kind = live_vod` pra conteúdo vindo da aba `/streams`.
4. Classificação **short vs. longo vs. live**: heurística por duração (≤ 60s = short) + checagem de proporção vertical (9:16) pra shorts/longos; lives identificadas diretamente pela aba de origem (`/streams`), sem precisar de heurística.
5. Usuário escolhe quais baixar (todos, só shorts, só longos, ou seleção manual).
6. Cada vídeo selecionado vira um `job(type=download)` na fila.
7. Download via yt-dlp → salva em `videos.local_path`, marca `downloaded_at`.
8. **Deduplicação em duas camadas:** checagem por `source_url` no banco (já existente) + `--download-archive` do próprio yt-dlp (por ID de vídeo) como camada extra de segurança, evitando reprocessar mesmo se a consulta ao banco falhar.

**Edge cases:**
- Vídeo privado/membros-only de terceiros → não é possível baixar, job falha com motivo claro (não é bug, é limitação esperada).
- Vídeo com restrição de idade → **decidido:** o sistema usa os cookies de sessão da conta do YouTube conectada (a mesma conta dedicada ao projeto, usada também pra postar) para contornar a restrição via yt-dlp, sem limitar isso a vídeos do canal próprio. Ver seção 8.3.1.
- Vídeo geo-bloqueado → falha esperada, registrar motivo.
- Canal com centenas/milhares de vídeos → listar não deve ser bloqueante; considerar paginação/streaming da listagem em vez de carregar tudo de uma vez.
- Live em andamento → yt-dlp por padrão entra a partir do momento atual, não do início; `--live-from-start` existe mas é experimental. Live agendada (ainda não começou) → `--wait-for-video` faz o yt-dlp esperar o horário programado. Relevante já que lives entraram no escopo.

#### 8.3.1 Cookies de sessão para restrição de idade

O token OAuth do Data API (usado pra publicar) **não serve** pro yt-dlp — restrição de idade é contornada com cookies de sessão de navegador, um mecanismo de autenticação diferente.

- Ao conectar a conta do YouTube, o sistema também abre uma sessão de navegador (Playwright) logada com essa mesma conta e extrai os cookies válidos para youtube.com.
- Cookies armazenados junto com a conta (`accounts.session_cookies`, separado do token OAuth).
- Se o yt-dlp encontrar um vídeo com restrição de idade, o sistema tenta novamente passando esses cookies (`--cookies`, formato Netscape — compatível nativamente com o parâmetro `cookiefile` do yt-dlp). Sem sucesso, o vídeo fica marcado como falho.
- Cookies expiram por conta própria (tempo variável, não necessariamente os mesmos 7 dias do token OAuth) — tratar falha de cookie como "reconecte", igual token expirado.
- **Decisão:** aplica-se tanto a vídeos do canal próprio quanto de terceiros — a conta usada é dedicada ao projeto (não pessoal), o que reduz o risco de penalização de uma conta pessoal.
- **Risco aceito:** uso de cookies pra automação de download em volume pode ser sinalizado pelo YouTube como padrão de bot (limite/verificação extra na conta dedicada). Risco de direitos autorais tratado à parte — o usuário confirmou não haver violação de direitos autorais no uso planejado.

### 8.4 Fluxo de Upload/Agendamento nativo

1. Usuário associa um `render` pronto a um agendamento (data/hora futura).
2. Sistema faz upload via `videos.insert` (upload resumível, chunks de 256KB) com `status.privacyStatus = private` e `status.publishAt = <data/hora>`.
3. O YouTube guarda o vídeo como privado e publica sozinho na hora marcada — **nenhum job de publish precisa disparar nesse horário**, diferente de Instagram/TikTok (até a automação nativa existir).
4. Sistema só precisa registrar que o upload foi feito (`posts.status = scheduled`) e, opcionalmente, checar depois se o vídeo realmente ficou público (sanity check, não é crítico).

**Quotas (atualizado 2026):** desde junho/2026, upload tem bucket próprio de **até 100 uploads/dia**, sem competir com outras chamadas — folga considerável pra postagem em massa. Thumbnail customizada custa +50 unidades do pool geral (10.000/dia), separado do bucket de upload.

**Edge cases:**
- Token expirado no momento do upload (ciclo de 7 dias) → job não deve nem tentar, deve sinalizar "reconecte antes de agendar" preventivamente.
- Vídeo classificado incorretamente como "made for kids" → precisa expor esse campo (`selfDeclaredMadeForKids`) na hora de agendar, senão o upload pode ser rejeitado ou ficar com restrições.
- Falha no meio do upload resumível → yt-dlp/API cliente já lida com retomada; só precisa expor status de progresso.

### 8.5 Extensão no esquema de dados (específico do YouTube)

```sql
-- vídeos ganham classificação
videos(..., kind[short|long|live_vod], duration_seconds)

-- controle de reconexão do token e cookies de sessão
accounts(..., token_expires_at, session_cookies)  -- token: alerta antes de expirar; cookies: usados pelo yt-dlp em vídeos com restrição de idade
```

### 8.6 Riscos e decisões em aberto (módulo YouTube)

| Item | Status |
|---|---|
| Verificação do app Google (token permanente) | **Adiado** — reconexão manual a cada 7 dias por ora |
| Suporte a login/cookies pra vídeos com restrição de idade | **Decidido** — usa cookies da conta dedicada do projeto, sem limitar a canal próprio |
| Origem do download (próprio canal + terceiros) | **Decidido** — ambos suportados |
| Paginação de canais muito grandes | Em aberto — não crítico pra v1, mas vale prever |
| Incluir lives/VODs (aba `/streams`) no escopo do Downloader | **Decidido** — inclui lives encerradas (VODs), além de shorts e vídeos normais |

### 8.7 Ordem sugerida de implementação (dentro do módulo YouTube)

1. Setup do projeto Google Cloud + fluxo OAuth básico (reconexão manual incluída)
2. Download via yt-dlp (listar + baixar + deduplicação) — funciona sem depender do OAuth
3. Classificação short/longo/live
4. Upload com `publishAt` (agendamento nativo)
5. Alerta de token expirando na UI

---

## 9. Módulo Templates (Canva) — plano detalhado

> Maior risco técnico do sistema. Planejado antes do Editor porque o Editor depende inteiramente de como o vídeo entra no template.

### 9.1 Escopo

Dado um template do Canva e um vídeo baixado, produzir um vídeo final renderizado. **Mudança de estratégia em relação ao desenho original:** em vez de automação cega de UI (clicar em coordenadas/seletores, frágil), usar o **Canva Apps SDK** — mecanismo oficial que roda dentro do editor e expõe API estável pra selecionar e substituir elementos de vídeo.

### 9.2 Componentes

| Componente | Papel | Tecnologia |
|---|---|---|
| **Canva App (mini-app)** | Roda dentro do editor, detecta o vídeo selecionado e o substitui pelo clip; dispara exportação | TypeScript/React (Canva Apps SDK) |
| **Automação de sessão** | Login persistente no Canva, navega até o design certo com o app carregado | Playwright |
| **Connect API (Export)** | Consulta status do job de exportação, obtém a URL do arquivo final | REST, disponível sem Enterprise |
| **Backend Python** | Orquestra tudo, baixa o arquivo exportado, atualiza `renders` | Python |

**Fora do escopo (bloqueado por Enterprise):** Autofill API / Brand Templates API e distribuição do app como "Team App". Não são necessários — o Apps SDK cobre a substituição de vídeo sem exigir nenhum dos dois.

### 9.3 Setup do Canva Developer Portal

1. Criar conta de desenvolvedor + app usando o `canva-apps-sdk-starter-kit`.
2. Implementar: detecção de seleção de vídeo (`selection.registerOnChange`), substituição de conteúdo, exportação (`requestExport`).
3. Escopos: `canva:design:content:read` + `canva:design:content:write`.
4. **Decisão tomada:** manter o app permanentemente em modo desenvolvimento/preview — publicação pública de app "só exportação" não é aceita pelo Canva, e distribuição como Team App exige Enterprise.

**Implicação de design:** dependência "viva", igual ao token do YouTube — checagem de saúde do dev server antes de cada render.

### 9.4 Fluxo de mapeamento inicial (uma vez por template)

1. Usuário informa a URL do template.
2. Playwright abre o Canva (sessão persistente) e navega até o design, com o painel do app carregado.
3. **Decisão tomada:** usuário clica manualmente no elemento de vídeo do template (1x, só nessa etapa) — o app captura o evento de seleção.
4. App salva a referência do elemento; sistema grava em `templates.placeholder_map`. Templates com mais de um "slot" de vídeo: repete o clique pra cada slot, nessa mesma etapa única.

### 9.5 Fluxo de render (uso repetido, sem intervenção manual)

1. Editor dispara `job(type=render)` com `video_id` + `template_id`.
2. Playwright abre uma cópia do design a partir do template.
3. App usa o `placeholder_map` salvo pra substituir o(s) elemento(s) de vídeo, sem seleção manual.
4. App dispara `requestExport`; backend consulta a Connect API até terminar e baixa o arquivo.
5. Arquivo salvo em `renders.output_path`.

**Edge cases:** elemento mapeado não existe mais (template editado depois) → falha clara "remapeie"; exportação lenta/falha → retry limitado; dev server fora do ar → checagem de saúde antes de iniciar.

### 9.6 Esquema de dados (ajuste)

```sql
templates(id, canva_design_id, name, placeholder_map, mapped_at)
```

### 9.7 Ordem sugerida de implementação

1. Canva App básico (starter kit) rodando localmente, confirmar estabilidade do dev/preview
2. Seleção + substituição de vídeo (exemplos oficiais de "video replacement")
3. Exportação (`requestExport`) + consulta de status via Connect API
4. Automação Playwright: login persistente + navegação + carregar o app
5. Fluxo de mapeamento manual (1x por template)
6. Fluxo de render automático (reusando o mapeamento)
7. Checagem de saúde do dev server antes de cada render

---

## 10. Próximos passos gerais

Módulos YouTube e Templates planejados por completo. Ainda faltam detalhar (mesmo nível de profundidade):
- **Downloader — TikTok/Instagram** — consolidar via yt-dlp (extractors dedicados), com tratamento de erro robusto pro Instagram (extractor frágil)
- **Conectores/Agendamento** (Instagram, TikTok) — automatizar o agendador nativo de cada plataforma (TikTok Studio, Meta Business Suite) pra publicação, não a API de desenvolvedor
- **Editor** — combinação vídeo + template, usando o mapeamento do módulo Templates
- **Agendador** — calendário, postagem em massa, geração de `post_batches`, respeitando os limites nativos por plataforma (75 dias/25 por dia no Instagram, 10-30 dias no TikTok)
- **Publisher** — hoje serve de fallback pro TikTok/Instagram (até a automação nativa existir) + lógica de retry
- **Fila de jobs (`jobs` + workers base)** — esqueleto que sustenta todos os módulos (já implementado de forma genérica durante o módulo YouTube)
