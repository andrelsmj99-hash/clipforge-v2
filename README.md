# Clip Forge V2

Sistema modular e local para download de canais de vídeo, edição automatizada via templates, agendamento em massa e publicação multi-plataforma.

---

## 🚀 Arquitetura Geral

- **Fila de Jobs Transacional (SQLite):** Ponto único de acoplamento entre módulos. Garante isolamento, retentativas e concorrência segura entre múltiplos processos.
- **Workers Independentes:** Processos Python dedicados para Download (`yt-dlp`), Renderização (`Canva/FFmpeg`) e Publicação (`YouTube Data API v3`).
- **Downloader Multi-Plataforma:** YouTube, TikTok e Instagram consolidados em cima de uma base compartilhada de yt-dlp (`YtDlpDownloaderBase`), com deduplicação em duas camadas (banco + `download_archive` nativo).
- **Agendador (postagem em massa):** cria lotes de N vídeos com legenda e intervalo definidos (`batch create`). Pra YouTube, enfileira o publish imediatamente (agendamento nativo via `publishAt`); pras demais plataformas, o post fica `SCHEDULED` e o `DueScanner` (`batch scan-due`) libera o job de publish exatamente na hora marcada, até a automação do agendador nativo de cada uma (TikTok Studio / Meta Business Suite) ser implementada.
- **Conectores Plugáveis:** Interface unificada (`BaseConnector`) com implementações modulares por plataforma.
- **Agendamento Nativo do YouTube:** Publicação automática no futuro através de `publishAt` (a própria plataforma cuida da publicação sem necessidade de manter o app rodando no horário).

---

## 🛠️ Estrutura do Projeto

```
clipforge.v2/
├── canva_app/                 # Mini-app Canva Apps SDK (TypeScript/React + @canva/app-hooks)
├── clipforge/
│   ├── core/                  # Banco de dados, fila de jobs, modelos e configurações
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── models.py
│   │   └── queue.py
│   ├── connectors/            # Conectores de rede social
│   │   ├── base.py
│   │   └── youtube/
│   │       ├── auth.py        # OAuth 2.0 + Alerta de expiração de 7 dias (Testing mode)
│   │       ├── session.py     # Captura de cookies para vídeos restritos via Playwright
│   │       └── publisher.py   # Upload resumível com publishAt
│   ├── modules/
│   │   ├── common/
│   │   │   ├── ytdlp_base.py      # Base compartilhada de download via yt-dlp (YouTube/TikTok/Instagram)
│   │   │   └── profile_scraper.py # Listagem de perfil genérica (TikTok/Instagram)
│   │   ├── youtube/
│   │   │   ├── scraper.py     # Multi-aba (/videos, /shorts, /streams) + classificação Short/Long/Live VOD
│   │   │   └── downloader.py  # Especialização da base yt-dlp pro YouTube
│   │   ├── tiktok/
│   │   │   ├── scraper.py
│   │   │   └── downloader.py  # Extractor dedicado e ativo no yt-dlp
│   │   ├── instagram/
│   │   │   ├── scraper.py
│   │   │   └── downloader.py  # Extractor dedicado, porém frágil — erros desconhecidos viram ExtractorFragileError
│   │   ├── templates/         # Gerenciamento de templates Canva e mapeamento de placeholders
│   │   │   ├── manager.py
│   │   │   ├── session.py     # Sessão Playwright autenticada do Canva
│   │   │   ├── dev_server.py  # Checagem de saúde e inicialização do dev server Canva App
│   │   │   └── mapper.py      # Mapeamento interativo de 1-clique via SelectionEvent
│   │   ├── editor/            # Motor de renderização e substituição de vídeo via Canva
│   │   │   └── renderer.py    # Playwright bridge + media server local + exportação
│   │   └── agendador/
│   │       ├── scheduler.py   # Cria lotes (post_batches + posts) com legenda e intervalo
│   │       └── due_scanner.py # Libera publish na hora exata p/ plataformas sem agendamento nativo
│   ├── workers/               # Workers que consomem a fila de jobs
│   │   ├── base.py
│   │   ├── downloader.py      # Despacha por plataforma (payload["platform"])
│   │   ├── publisher.py
│   │   ├── render.py          # Processa jobs de renderização (video + template)
│   │   └── runner.py
│   └── cli.py                 # CLI interativo com Rich (grupos: youtube, tiktok, instagram, accounts, templates, render, batch, queue, videos, workers)
├── tests/                     # Suite completa de testes automatizados com Pytest
├── data/                      # Diretório local para banco SQLite, mídias, credenciais e download_archives (gitignored)
├── .gitignore
├── pyproject.toml
└── requirements.txt
```

## ⚙️ Instalação e Requisitos

1. **Clone o repositório e prepare o ambiente virtual:**
```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

2. **Instale os navegadores do Playwright (essencial para automação de TikTok, Instagram e Canva):**
```powershell
playwright install chromium
```

---

## 📦 Como Usar a CLI

Ative o ambiente virtual antes de executar os comandos:
```powershell
.\.venv\Scripts\activate
```

### 1. Conectar Conta do YouTube (OAuth 2.0)
Coloque o arquivo `client_secret_*.json` baixado do Google Cloud Console em `data/credentials/youtube_client_secret.json` e execute:
```bash
python -m clipforge.cli accounts connect-youtube
```
Para ver o status das contas conectadas e o tempo restante do token (ciclo de 7 dias):
```bash
python -m clipforge.cli accounts list
```

### 2. Capturar Cookies de Sessão (Para vídeos com restrição de idade)
```bash
python -m clipforge.cli accounts capture-youtube-session
```

### 3. Listar Vídeos de um Canal (sem baixar)
```bash
# Cobre as 3 abas do YouTube por padrão: /videos + /shorts + /streams (lives encerradas)
python -m clipforge.cli youtube list-channel "@NomeDoCanal" --max-results 20
# Apenas shorts:
python -m clipforge.cli youtube list-channel "@NomeDoCanal" --only-shorts
# Apenas lives encerradas (VODs):
python -m clipforge.cli youtube list-channel "@NomeDoCanal" --only-lives
# Excluir lives da listagem:
python -m clipforge.cli youtube list-channel "@NomeDoCanal" --no-lives
```

### 4. Enfileirar Downloads de um Canal (com Deduplicação)
```bash
python -m clipforge.cli youtube enqueue-download "@NomeDoCanal" --only-shorts --max-results 10
```

### 4.1 TikTok e Instagram (via yt-dlp)
```bash
python -m clipforge.cli tiktok list-profile "@usuario" --max-results 20
python -m clipforge.cli tiktok enqueue-download "@usuario" --max-results 10

python -m clipforge.cli instagram list-profile "usuario" --max-results 20
python -m clipforge.cli instagram enqueue-download "usuario" --max-results 10
```
> O extractor do Instagram é conhecido por ser frágil (quebra periodicamente com mudanças da plataforma) — erros inesperados aparecem com uma mensagem apontando essa causa provável.

### 5. Postagem em Massa (Agendador)
```bash
# Cria um lote: 1 post por vídeo, a cada 1h, começando agora
python -m clipforge.cli batch create vid_1 vid_2 vid_3 \
  --account-id "acc1" --platform youtube \
  --caption "Parte {n}/{total} 🔥" --interval-minutes 60

# Ver lotes criados / posts de um lote específico
python -m clipforge.cli batch list
python -m clipforge.cli batch list --batch-id batch_xxxxx

# Libera publicações vencidas (necessário só pra plataformas sem
# agendamento nativo ainda — hoje: TikTok, Instagram). Rode isso periodicamente via
# cron/Agendador de Tarefas, ex: a cada minuto.
python -m clipforge.cli batch scan-due
```
> YouTube: o upload já acontece no momento do `batch create`, com `publishAt` no futuro — a própria plataforma publica sozinha depois. TikTok/Instagram: os posts ficam `SCHEDULED` e só viram job de fato na hora exata, via `scan-due`, até a automação do agendador nativo de cada um ser implementada.

### 6. Agendar Upload Individual
#### 6.1 YouTube (`publishAt`)
```bash
python -m clipforge.cli youtube schedule-upload "caminho/do/video.mp4" `
  --account-id "yt_UCxxxx" `
  --title "Título Incrível #shorts" `
  --caption "Descrição do vídeo #shorts #viral" `
  --tags "shorts,viral,curiosidades" `
  --publish-at "2026-09-05T18:00:00Z"
```

#### 6.2 TikTok Studio (Playwright Automation)
```bash
# 1. Realizar login interativo e salvar sessão persistente (data/sessions/tiktok)
python -m clipforge.cli accounts login-tiktok

# 2. Publicar ou agendar vídeo (até 30 dias no futuro)
python -m clipforge.cli tiktok schedule-upload "caminho/do/video.mp4" `
  --account-id "tt_xxxx" `
  --caption "Vídeo viral #fyp #viral" `
  --tags "fyp,viral" `
  --publish-at "2026-09-20T18:00:00Z"
```

#### 6.3 Meta Business Suite / Instagram Reels (Playwright Automation)
```bash
# 1. Realizar login interativo no Meta Business Suite (data/sessions/instagram)
python -m clipforge.cli accounts login-instagram

# 2. Publicar ou agendar Reel (até 75 dias no futuro)
python -m clipforge.cli instagram schedule-upload "caminho/do/video.mp4" `
  --account-id "ig_xxxx" `
  --caption "Reel incrível #reels #viral" `
  --tags "reels,viral" `
  --publish-at "2026-09-20T18:00:00Z"
```

### 7. Postagem em Lote com Agendamento Nativo Imediato (`--native-schedule`)
```bash
# Criar lote para TikTok ou Instagram enviando imediatamente ao agendador nativo da plataforma
clipforge batch create vid_1 vid_2 vid_3 `
  --account-id "tt_123" --platform tiktok `
  --caption "Parte {n}/{total} 🔥" --interval-minutes 120 `
  --native-schedule
```

### 8. Templates Canva & Mapeamento de Placeholders
```bash
# Registrar um template do Canva
clipforge templates add "https://www.canva.com/design/DAGXXXXX/edit" --name "Shorts Dark 01"

# Listar templates cadastrados e status de mapeamento
clipforge templates list

# Mapear o slot de vídeo (abre o Canva interativamente para 1 clique no elemento)
clipforge templates map "tpl_xxxxxx"
```

### 9. Renderização de Vídeo (Editor Canva)
```bash
# Enfileirar render combinando vídeo baixado + template mapeado
clipforge render enqueue "vid_xxxxxx" --template-id "tpl_xxxxxx"

# Ver histórico de renderizações e caminhos dos arquivos MP4 finais
clipforge render list
```

### 10. Iniciar os Workers de Processamento
```bash
# Iniciar todos os workers (Downloader + Publisher + Render)
clipforge workers start

# Ou rodar apenas o Downloader
clipforge workers start --downloader-only
```

### 11. Inspecionar a Fila de Jobs e Vídeos Locais
```bash
clipforge queue list
clipforge videos list
```

### 12. Interface Gráfica Desktop (Flet)
```bash
# Abrir como aplicativo desktop nativo:
clipforge ui

# Ou abrir no navegador padrão:
clipforge ui --browser
```
A interface gráfica desktop unifica todos os módulos do sistema:
- **Dashboard:** métricas em tempo real, monitor da fila de jobs e controle de workers (Iniciar / Parar).
- **Contas:** gerenciamento e login interativo para YouTube (OAuth + Cookies Playwright), TikTok Studio e Meta Business Suite.
- **Downloads:** formulário para scraping e download de perfis do YouTube, TikTok e Instagram, com galeria de vídeos locais.
- **Canva / Render:** catálogo de templates Canva, mapeamento 1-clique e estúdio de renderização.
- **Agendador:** seleção de vídeos, editor dinâmico de legenda (`{n}`, `{total}`), intervalo e agendamento nativo.

### 13. Gerar Executável Standalone (.exe)
Você pode empacotar todo o sistema em um executável nativo do Windows (`.exe`) sem precisar do terminal para iniciar a aplicação:

```powershell
# 1. Certifique-se de estar com o ambiente virtual ativo
.\.venv\Scripts\activate

# 2. Gerar pasta com o executável (inicialização rápida e recomendada):
flet pack main.py --name "ClipForge" --onedir

# 3. Ou gerar como arquivo único (.exe único):
flet pack main.py --name "ClipForge"

# (Opcional) Adicionar ícone customizado:
flet pack main.py --name "ClipForge" --icon "caminho/do/icone.ico"
```
O executável final gerado estará disponível na pasta:
```
dist/ClipForge/ClipForge.exe
```

---

## 📍 Status do Projeto

**100% Implementado e Testado:**
- **Downloader multi-plataforma** (YouTube com 3 abas incluindo lives, TikTok, Instagram) via yt-dlp
- **Conector YouTube completo** (OAuth, cookies de sessão para restrição de idade, upload com `publishAt` nativo)
- **Publicação nativa TikTok & Instagram** — automação completa do TikTok Studio e do Meta Business Suite via Playwright, com sessões persistentes (`data/sessions`), validação de janela de agendamento (30 dias TikTok, 75 dias Meta), injeção de arquivo e capturas de tela para diagnóstico de erros (`data/logs/failures/`).
- **Fila de jobs transacional + workers** (Downloader, Publisher multi-plataforma, Render)
- **Agendador** (postagem em massa com `batch create` / `scan-due` / `--native-schedule`)
- **Templates (Canva) + Editor** (Mini-app Canva Apps SDK em TypeScript/React com `useSelection` e `requestExport`, automação Playwright, servidor local de mídia com CORS e worker de renderização)
- **Interface Gráfica Desktop (Flet)** — aplicativo desktop nativo moderno (Material 3 Dark) com 5 abas integradas, controle de workers em background e modo navegador opcional (`--browser`).
- **Empacotamento Executável (.exe)** — suporte integrado com `flet pack` / PyInstaller e ponto de entrada [`main.py`](file:///c:/Users/Pichau/Documents/Projetos/clipforge.v2/main.py).

---

## 🧪 Rodando os Testes

```powershell
.\.venv\Scripts\pytest.exe -v
```



