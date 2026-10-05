# Operação

Tudo roda na máquina do time: API (FastAPI), embeddings (EmbeddingGemma, CPU) e LLM (Ollama, GPU).
O banco fica num PostgreSQL gerenciado. Nenhum texto de ticket vai para um serviço de IA externo.

## Primeira vez

```bash
uv sync
uv run python -m copilot_suporte.usuarios preparar          # tabelas do produto (sql/010_produto.sql)
uv run python -m copilot_suporte.carga --completa           # coleta o histórico com responsável, serviço e SLA
uv run python -m copilot_suporte.documentos                 # indexa a base de conhecimento
uv run python -m copilot_suporte.avaliar_triagem --dias 60  # mede a triagem e grava a calibração do risco de SLA
uv run python -m copilot_suporte.avaliar_pergunte --rotulo "linha de base"
uv run python -m copilot_suporte.usuarios agentes           # lista os ids dos agentes no Movidesk
uv run python -m copilot_suporte.usuarios criar email@empresa.com.br "Nome" analista --nivel N1 --movidesk-id 123
```

Ollama: `winget install Ollama.Ollama` e `ollama pull qwen2.5:7b` (4,7 GB; cabe numa GPU de 6 GB
quando nenhum outro programa pesado está usando a placa).

## Dia a dia

- **Subir:** o atalho da área de trabalho sobe o Ollama e o servidor (sem janela) e abre a aba no
  navegador. Recriar o atalho: `powershell -ExecutionPolicy Bypass -File scripts\criar_atalho.ps1`.
  Com o servidor no ar, o ícone fica na bandeja: Abrir, Ver log, Pausar pré-geração e Desligar — as
  mesmas ações estão em *Configurações › Servidor* (admin). A pausa é o arquivo
  `logs\pregeracao_pausada` e vale até ser retomada, mesmo reiniciando.
  Parar pelo terminal: `powershell -File scripts\parar_elo.ps1`. Log em `logs\elo_servidor.log`.
  Sem atalho: `scripts\elo.bat` e <http://127.0.0.1:8000>.
- **Em container:** `docker compose up -d --build`. O compose publica a porta só no loopback e lê
  tudo do `.env`; `agenda.py` faz dentro do container o que o Agendador de Tarefas faz no Windows.
- **Carga:** `scripts\carga.bat` de hora em hora, pelo Agendador de Tarefas. Saída em `logs\carga.log`.
- **Base de conhecimento:** pela tela *Base de conhecimento* (PDF, Markdown, TXT ou HTML, até 25 MB).
  O arquivo vai para a pasta local (fora do Git) e entra **só para consulta**; gestor e admin ligam
  "Usar no RAG" em cada documento para os trechos passarem a valer no Pergunte e nos rascunhos.
  Arquivos colocados na pasta à mão e as URLs de `urls.txt` entram no RAG com
  `uv run python -m copilot_suporte.documentos`.
- **Documentação pública do produto:** o mesmo comando sincroniza os espaços listados em
  `espacos.txt`. Cada página vira um documento já no RAG, com o link da página como fonte; página
  alterada é reindexada e página que saiu do espaço sai da base. Na tela elas aparecem como
  "sincronizada" e não têm Excluir (voltariam na próxima rodada) — para tirar uma do ar, desligue o
  RAG dela. A primeira carga (~150 páginas) é lenta com os vetores na CPU; as seguintes só reindexam
  o que mudou. Roda sozinha todo dia às 07:30 (`scripts\documentos.bat`, log em `logs\documentos.log`);
  com a máquina desligada nesse horário, roda assim que ela ligar.
- **Pré-geração:** com `PREGERAR=1` (padrão), o copiloto gera rascunhos dos tickets abertos em
  segundo plano, 50 por ciclo de 10 minutos, um de cada vez. Quem abre um ticket passa na frente.
  `PREGERAR=0` desliga.

## Antes de mexer em busca, trechos ou prompt: medir

`avaliar_pergunte` roda as perguntas de `avaliacao_pergunte.json` (resposta certa conferida na
documentação) e grava cada rodada em `logs/avaliacao_pergunte.jsonl`.
`--arquivo avaliacao_pergunte_validacao.json` roda o conjunto separado, que **não** deve ser usado
para ajustar nada — só para conferir se o ajuste vale fora das perguntas usadas nele.

Reindexar a base inteira (mudança no divisor de trechos) leva ~15 min e pode cair por falta de
memória com Ollama, Docker e navegador abertos; retome do documento onde parou.

## Como saber se está saudável

`GET /saude` → `{"banco": true, "modelo": true, "llm": true}`.
`llm: false` = Ollama fechado ou modelo não baixado: a fila, a triagem e os casos parecidos continuam
funcionando; só os rascunhos e o Pergunte param.

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| Rascunho "na fila" por muito tempo | pré-geração ocupando a GPU | normal; o pedido do analista passa na frente |
| Rascunho demora > 2 min | GPU dividida com outro programa (`ollama ps` mostra % de CPU) | fechar o programa ou usar `OLLAMA_MODELO=qwen2.5:3b` |
| "Ollama não respondeu" | Ollama fechado | abrir o Ollama |
| Fila do time demora na primeira vez | vizinhos sendo calculados (≈ 20 s para 382 tickets) | só na primeira vez; depois fica em cache por 1 h |
| Carga parou com erro de checagem | `sql/004` achou inconsistência | ver `execucoes_pipeline` e `logs/carga.log`; corrigir antes de reprocessar |

## Acesso do time

A API escuta só em `127.0.0.1`. Para o time acessar, um túnel do Cloudflare expõe a porta e o login
do próprio produto protege tudo (cookie `HttpOnly`, senha com scrypt, sessão de 12 h):

```bash
winget install Cloudflare.cloudflared
cloudflared tunnel --url http://127.0.0.1:8000     # gera um https://<aleatório>.trycloudflare.com
```

O endereço muda a cada execução e só funciona com a máquina ligada. Com um domínio no Cloudflare, o
próximo passo é o túnel nomeado + Cloudflare Access ([ADR 0002](adr/0002-hospedagem.md)).

> Expor a ferramenta para a equipe depende de aprovação, porque os resultados mostram dados de
> clientes ([ADR 0001](adr/0001-usuarios-e-etapas.md)).

## Desempenho

Cada rota da API é cronometrada: o total e quanto foi **banco**, **vetor** (embedding da pergunta) e
**LLM**. Onde ver:

- **Configurações › Desempenho:** p50/p95 por tela, a espera até a 1ª palavra do Pergunte e do
  rascunho, e a velocidade do LLM (tokens/s), nas últimas 24 h.
- **Navegador:** DevTools › Network › uma requisição › aba *Timing* (cabeçalho `Server-Timing`).
- **Arquivo:** `logs/desempenho.jsonl`, uma linha por requisição ou geração.

Os números da última medição estão no [README](../README.md#desempenho). Ainda lentos (p95 ≥ 1 s):
Painel (6 consultas em sequência), Busca por texto (vetor + banco), Entrar (hash da senha + banco: é
proposital) e o modelo 7B rodando ~16% na CPU, por não caber inteiro em 6 GB de vídeo.
