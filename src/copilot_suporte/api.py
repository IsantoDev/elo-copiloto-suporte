"""API do Elo, o copiloto de suporte.

Rodar com:  uv run python -m copilot_suporte.api        (ou scripts/elo.bat)
e abrir     http://127.0.0.1:8000

Escuta só em 127.0.0.1: a API devolve dados de clientes. Para o time acessar, quem
expõe é o túnel (ver docs/operacao.md), e toda rota de dados exige login.
"""

import json
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from copilot_suporte import documentos, medicao
from copilot_suporte.acessos import Acessos
from copilot_suporte.banco import Banco
from copilot_suporte.buscar import Buscador, Resultado, numero_para_id
from copilot_suporte.config import RAIZ, Config
from copilot_suporte.copiloto import Copiloto
from copilot_suporte.geracao import AGORA, DEPOIS, FilaDeGeracao
from copilot_suporte.llm import LLM, LLMIndisponivel
from copilot_suporte.repositorio import Repositorio
from copilot_suporte.usuarios import PAPEIS, Usuarios
from copilot_suporte.vetorizador import Vetorizador

sys.stdout.reconfigure(line_buffering=True)  # o log (logs/elo_servidor.log) sai na hora, não em blocos
STATIC = Path(__file__).parent / "static"
COOKIE = "elo_sessao"
INTERVALO_PREGERACAO = 10 * 60  # segundos
PAUSA = RAIZ / "logs" / "pregeracao_pausada"  # o arquivo existe = pré-geração pausada (bandeja e Configurações)
INTERVALO_RETRATO = 2 * 60  # a carga é de hora em hora; 2 min mantém a fila em dia sem pesar
recursos: dict = {}


_agora = threading.Event()  # o botão "Atualizar agora" adianta o ciclo
_ciclos = threading.Condition()
_ciclo = {"n": 0}


def _sincronizar() -> None:
    """Traz do Movidesk o que mudou desde a última carga (incremental) e relê a fila em memória."""
    from copilot_suporte.carga import Pipeline
    from copilot_suporte.movidesk import ClienteMovidesk

    resumo = None
    if recursos["config"].movidesk_token:
        try:
            inicio = time.perf_counter()
            resumo = Pipeline(ClienteMovidesk(recursos["config"].movidesk_token), recursos["banco_carga"],
                              recursos["vetorizador"]).rodar()
            print(f"sincronizado com o Movidesk: {resumo.tickets} tickets em {time.perf_counter() - inicio:.1f} s")
        except Exception as erro:  # sem Movidesk, a fila continua com o que já tem no banco
            print("sincronização:", type(erro).__name__)
    try:  # cards do Planner: só relê quando o Excel exportado ou o planner.json do OneDrive mudou
        from copilot_suporte import planner

        resumo_planner = planner.sincronizar_arquivos(recursos["banco"])
        if resumo_planner:
            print(f"planner: {resumo_planner['cards']} cards, {resumo_planner['vinculos']} tickets ligados")
        elif resumo is not None and resumo.tickets:
            planner.vincular(recursos["banco"])  # tickets que chegaram agora podem citar cards que já temos
    except Exception as erro:
        print("planner:", type(erro).__name__)
    inicio = time.perf_counter()
    recursos["copiloto"].atualizar_retrato()
    print(f"retrato da fila atualizado em {time.perf_counter() - inicio:.1f} s")


def _manter_retrato(parar: threading.Event) -> None:
    """Carrega a fila ao subir e a mantém em dia: a cada 2 min (ou no botão) sincroniza com o Movidesk."""
    primeira = True
    while True:
        try:
            if primeira:  # a fila aparece logo; a sincronização vem no ciclo seguinte
                recursos["copiloto"].atualizar_retrato()
                primeira = False
                _agora.set()
            else:
                _sincronizar()
        except Exception as erro:
            print("retrato:", type(erro).__name__, erro)
        with _ciclos:
            _ciclo["n"] += 1
            _ciclos.notify_all()
        if parar.is_set():
            return
        _agora.wait(INTERVALO_RETRATO)
        _agora.clear()
        if parar.is_set():
            return


def _vigiar_pausa(parar: threading.Event) -> None:
    """O ícone da bandeja pausa criando o arquivo; aqui a fila acompanha em até 3 s."""
    while not parar.wait(3):
        recursos["fila"].pausar(PAUSA.exists())


def _pregerar(parar: threading.Event) -> None:
    """De tempos em tempos, põe na fila de geração (sem prioridade) os abertos sem rascunho válido."""
    while not parar.wait(30):  # deixa o retrato carregar primeiro
        try:
            copiloto: Copiloto = recursos["copiloto"]
            if copiloto.llm.disponivel() and not PAUSA.exists():
                for ticket_id in recursos["repo"].abertos_sem_rascunho(copiloto.llm.modelo, copiloto.limiar()):
                    recursos["fila"].pedir(ticket_id, DEPOIS)
        except Exception as erro:
            print("pré-geração:", type(erro).__name__, erro)
        if parar.wait(INTERVALO_PREGERACAO):
            return


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    # Tudo nasce uma vez, quando a API sobe: o pool de conexões, o modelo e a fila de geração.
    config = Config()
    medicao.configurar(RAIZ / "logs" / "desempenho.jsonl")
    banco = Banco(tamanho_max=6, autocommit=True)
    banco_carga = Banco(tamanho_max=2)  # a carga troca tabelas inteiras: precisa de transação
    vetorizador = Vetorizador(config.modelo)
    vetorizador.carregar()  # carrega já, para a primeira busca por texto não esperar
    repo = Repositorio(banco)
    llm = LLM(config.ollama_url, config.llm)
    copiloto = Copiloto(repo, vetorizador, llm)
    parar = threading.Event()
    recursos.update(
        config=config, banco=banco, banco_carga=banco_carga, vetorizador=vetorizador, repo=repo, llm=llm, copiloto=copiloto,
        buscador=Buscador(banco, vetorizador), usuarios=Usuarios(banco), acessos=Acessos(banco),
        fila=FilaDeGeracao(copiloto.gerar_rascunho),
    )
    threading.Thread(target=_manter_retrato, args=(parar,), name="retrato", daemon=True).start()
    recursos["fila"].pausar(PAUSA.exists())
    threading.Thread(target=_vigiar_pausa, args=(parar,), name="pausa", daemon=True).start()
    if config.pregerar:
        threading.Thread(target=_pregerar, args=(parar,), name="pregeracao", daemon=True).start()
    if os.getenv("AGENDA_DOCUMENTOS"):  # no container: a atualização diária da base usa o modelo já carregado
        from copilot_suporte import agenda

        threading.Thread(target=agenda.diaria, args=(vetorizador,), name="agenda", daemon=True).start()
    yield
    parar.set()
    _agora.set()
    banco.fechar()
    banco_carga.fechar()
    recursos.clear()


app = FastAPI(title="Elo · Copiloto de Suporte", lifespan=ciclo_de_vida)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def medir(request: Request, chamar):
    """Cronometra cada rota da API: o total e quanto foi banco, vetor e LLM (Server-Timing)."""
    if not request.url.path.startswith("/api/"):
        return await chamar(request)
    cronometro = medicao.iniciar()
    inicio = time.perf_counter()
    resposta = await chamar(request)
    total = time.perf_counter() - inicio
    resposta.headers["Server-Timing"] = medicao.server_timing(cronometro, total)
    rota = getattr(request.scope.get("route"), "path", request.url.path)
    medicao.registrar({"tipo": "requisicao", "rota": f"{request.method} {rota}", "status": resposta.status_code,
                       "ms": round(total * 1000, 1),
                       "etapas": {k: round(v * 1000, 1) for k, v in cronometro["etapas"].items()}})
    return resposta


# ---------- sessão e papéis ----------

def usuario_atual(request: Request) -> dict:
    usuario = recursos["usuarios"].da_sessao(request.cookies.get(COOKIE))
    if usuario is None:
        raise HTTPException(401, "entre para continuar")
    return usuario


def exige(*papeis: str):
    def verificar(usuario: dict = Depends(usuario_atual)) -> dict:
        if usuario["papel"] not in papeis:
            raise HTTPException(403, "sem permissão para esta tela")
        return usuario

    return verificar


class Entrada(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    senha: str = Field(min_length=1, max_length=200)


@app.post("/api/entrar")
def entrar(dados: Entrada, request: Request, response: Response):
    time.sleep(0.3)  # encarece tentativas em sequência
    token = recursos["usuarios"].entrar(dados.email, dados.senha)
    if token is None:
        raise HTTPException(401, "e-mail ou senha não conferem")
    seguro = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=seguro, max_age=12 * 3600)
    return {"ok": True}


@app.post("/api/sair")
def sair(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        recursos["usuarios"].sair(token)
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/eu")
def eu(usuario: dict = Depends(usuario_atual)):
    return {**usuario, "llm": recursos["llm"].modelo, "movidesk_url": recursos["config"].movidesk_url}


# ---------- fila e ticket ----------

@app.get("/api/fila")
def fila(de: str = Query("meus"), usuario: dict = Depends(usuario_atual)):
    """de = meus | time | <id do agente no Movidesk>."""
    if de == "meus":
        if not usuario["movidesk_id"]:
            return {"tickets": [], "aviso": "Seu usuário não está ligado a um agente do Movidesk. Veja o time todo."}
        responsavel = usuario["movidesk_id"]
    elif de == "time":
        responsavel = None
    else:
        responsavel = de
    return {"tickets": recursos["copiloto"].fila(responsavel)}


@app.post("/api/atualizar")
def atualizar_agora(usuario: dict = Depends(usuario_atual)):
    """Botão "Atualizar agora": adianta a sincronização com o Movidesk e espera ela terminar (até 2 min)."""
    with _ciclos:
        alvo = _ciclo["n"] + 1
        _agora.set()
        terminou = _ciclos.wait_for(lambda: _ciclo["n"] >= alvo, timeout=120)
    return {"ok": terminou}


@app.get("/api/analistas")
def analistas(usuario: dict = Depends(usuario_atual)):
    return recursos["copiloto"].analistas()


@app.get("/api/ticket/{numero}")
def ticket(numero: int, usuario: dict = Depends(usuario_atual)):
    detalhe = recursos["copiloto"].detalhe(numero_para_id(numero))
    if detalhe is None:
        raise HTTPException(404, "ticket não encontrado")
    return detalhe


@app.get("/api/caso/{numero}")
def caso(numero: int, usuario: dict = Depends(usuario_atual)):
    detalhe = recursos["copiloto"].caso(numero_para_id(numero))
    if detalhe is None:
        raise HTTPException(404, "caso não encontrado")
    return detalhe


@app.get("/api/ticket/{numero}/rascunho")
def rascunho(numero: int, gerar: bool = False, usuario: dict = Depends(usuario_atual)):
    """Pronto, na fila (com posição) ou parado. gerar=true põe na frente da fila."""
    ticket_id = numero_para_id(numero)
    copiloto: Copiloto = recursos["copiloto"]
    fila: FilaDeGeracao = recursos["fila"]
    pronto = copiloto.rascunho_valido(ticket_id)
    if pronto:
        return {"estado": "pronto", "rascunho": {
            "tipo": pronto["tipo"], "conteudo": pronto["conteudo"], "base": pronto["base"],
            "gerado_em": pronto["gerado_em"].isoformat(), "segundos": pronto["segundos"], "modelo": pronto["modelo_llm"],
        }}
    if not copiloto.llm.disponivel():
        return {"estado": "indisponivel", "mensagem": "O LLM local (Ollama) não está respondendo."}
    if gerar:
        fila.pedir(ticket_id, AGORA)
    posicao = fila.posicao(ticket_id)
    if posicao is None:
        erro = fila.erro(ticket_id)
        return {"estado": "erro" if erro else "parado", "mensagem": erro}
    return {"estado": "gerando" if posicao == 0 else "na_fila", "posicao": posicao}


def _linha(evento: dict) -> str:
    return json.dumps(evento, ensure_ascii=False, default=str) + "\n"


@app.get("/api/ticket/{numero}/rascunho/ao-vivo")
def rascunho_ao_vivo(numero: int, refazer: bool = False, usuario: dict = Depends(usuario_atual)):
    """O rascunho sendo escrito, em linhas JSON: {"fila": posição}, {"texto": parcial}
    e, no fim, {"pronto": rascunho} ou {"erro": mensagem}. Pede a geração com prioridade."""
    ticket_id = numero_para_id(numero)
    copiloto: Copiloto = recursos["copiloto"]
    fila: FilaDeGeracao = recursos["fila"]

    def eventos():
        inicio, primeira = time.perf_counter(), None
        # refazer: ignora o guardado e escreve de novo (com os casos e documentos de agora)
        pronto = None if refazer else copiloto.rascunho_valido(ticket_id)
        ja_pronto = pronto is not None
        try:
            if pronto is None:
                if not copiloto.llm.disponivel():
                    yield _linha({"erro": "O LLM local (Ollama) não está respondendo."})
                    return
                fila.pedir(ticket_id, AGORA)
            ultimo, limite = None, time.monotonic() + 600
            while pronto is None and time.monotonic() < limite:
                posicao = fila.posicao(ticket_id)
                if posicao is None:
                    pronto = copiloto.rascunho_valido(ticket_id)
                    if pronto is None:
                        yield _linha({"erro": fila.erro(ticket_id) or "O rascunho não foi gerado."})
                        return
                    break
                evento = {"fila": posicao} if posicao > 0 else {"texto": fila.parcial(ticket_id)}
                if evento != ultimo:
                    if primeira is None and evento.get("texto"):
                        primeira = time.perf_counter()
                    yield _linha(evento)
                    ultimo = evento
                time.sleep(0.12)
            if pronto is not None:
                primeira = primeira or time.perf_counter()
                yield _linha({"pronto": {
                    "tipo": pronto["tipo"], "conteudo": pronto["conteudo"], "base": pronto["base"],
                    "segundos": pronto["segundos"], "modelo": pronto["modelo_llm"],
                }})
        finally:  # o que a pessoa sente ao abrir o ticket: até a 1ª palavra do rascunho e até ele terminar
            fim = time.perf_counter()
            medicao.registrar({"tipo": "fluxo", "uso": "rascunho já pronto" if ja_pronto else "rascunho gerado na hora",
                               "primeira_ms": round(((primeira or fim) - inicio) * 1000),
                               "total_ms": round((fim - inicio) * 1000)})

    return StreamingResponse(eventos(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})


# ---------- busca e Pergunte ----------

@app.get("/api/busca")
def busca(q: str = Query(min_length=1, max_length=500), usuario: dict = Depends(usuario_atual)):
    texto = q.strip().lstrip("#")
    if texto.isdigit():
        encontrado = recursos["repo"].ticket(numero_para_id(int(texto)))
        numero = {"ticket_id": encontrado["ticket_id"], "status_base": encontrado["status_base"]} if encontrado else None
        return {"numero": numero, "abertos": [], "resolvidos": []}
    if len(texto) < 3:
        raise HTTPException(400, "Descreva com pelo menos 3 letras.")
    return {"numero": None, **recursos["copiloto"].buscar(texto)}


class FonteCitada(BaseModel):
    tipo: str = Field(max_length=20)
    id: int | None = None
    fonte: str | None = Field(None, max_length=300)


class Turno(BaseModel):
    """Uma troca anterior da conversa, que a tela devolve como memória."""
    pergunta: str = Field(max_length=1000)
    resposta: str = Field("", max_length=6000)
    fontes: list[FonteCitada] = Field(default_factory=list, max_length=10)


class Pergunta(BaseModel):
    pergunta: str = Field(min_length=3, max_length=1000)
    ticket_id: int | None = None
    historico: list[Turno] = Field(default_factory=list, max_length=10)


@app.post("/api/perguntar")
def perguntar(dados: Pergunta, usuario: dict = Depends(usuario_atual)):
    """A resposta sendo escrita, em linhas JSON: {"t": pedaço}... e {"fim": {...}}."""
    copiloto: Copiloto = recursos["copiloto"]
    fila: FilaDeGeracao = recursos["fila"]

    def eventos():
        inicio, primeira = time.perf_counter(), None
        with fila.interativo():  # a pré-geração sai da GPU enquanto a pergunta é respondida
            try:
                historico = [t.model_dump() for t in dados.historico]
                for evento in copiloto.perguntar(dados.pergunta, dados.ticket_id, historico=historico):
                    if primeira is None and ("t" in evento or "fim" in evento):
                        primeira = time.perf_counter()
                    if "fim" in evento:
                        fim = evento["fim"]
                        fim["id"] = recursos["repo"].registrar_pergunta(
                            usuario["id"], dados.ticket_id, dados.pergunta, fim["resposta"], fim["fontes"],
                            fim["encontrou"], fim["segundos"],
                        )
                    yield _linha(evento)
            except LLMIndisponivel as erro:
                yield _linha({"erro": str(erro)})
            finally:  # o que a pessoa sente: até a primeira palavra aparecer e até terminar
                fim = time.perf_counter()
                medicao.registrar({"tipo": "fluxo", "uso": "pergunta",
                                   "primeira_ms": round(((primeira or fim) - inicio) * 1000),
                                   "total_ms": round((fim - inicio) * 1000)})

    return StreamingResponse(eventos(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})


@app.get("/api/desempenho")
def desempenho(horas: int = Query(24, ge=1, le=24 * 30), usuario: dict = Depends(exige("admin"))):
    """p50/p95 de cada rota e etapa, e da geração do LLM (etapa 0: medir antes de otimizar)."""
    return medicao.resumo(horas * 3600)


class Avaliacao(BaseModel):
    alvo: str = Field(pattern="^(rascunho|pergunta|servico|urgencia|tipo|parecido)$")
    valor: str = Field(pattern="^(usei|util|nao_util|confirmado|copiado)$")
    ticket_id: int | None = None
    detalhe: dict | None = None


@app.post("/api/feedback")
def feedback(dados: Avaliacao, usuario: dict = Depends(usuario_atual)):
    recursos["repo"].registrar_feedback(usuario["id"], dados.ticket_id, dados.alvo, dados.valor, dados.detalhe)
    return {"ok": True}


# ---------- base de conhecimento ----------

TIPOS = {"pdf": "application/pdf", "md": "text/plain; charset=utf-8", "txt": "text/plain; charset=utf-8"}
_indexando: set[int] = set()
_trava_indice = threading.Lock()  # um documento por vez no vetorizador


def _indexar_em_segundo_plano(documento_id: int) -> None:
    def rodar():
        try:
            with _trava_indice:
                documentos.indexar_documento(recursos["banco"], recursos["vetorizador"], documento_id)
        except Exception as erro:
            print("indexar documento:", type(erro).__name__, erro)
        finally:
            _indexando.discard(documento_id)

    _indexando.add(documento_id)
    threading.Thread(target=rodar, name=f"indexar-{documento_id}", daemon=True).start()


@app.get("/api/documentos")
def listar_documentos(q: str | None = Query(None, max_length=200), usuario: dict = Depends(usuario_atual)):
    lista = recursos["repo"].documentos(q)
    for d in lista:
        d["indexando"] = d["id"] in _indexando
    return {"documentos": lista, "pode_editar": usuario["papel"] in ("gestor", "admin"),
            "limite_mb": documentos.LIMITE_BYTES // (1024 * 1024), "extensoes": sorted(documentos.EXTENSOES)}


@app.post("/api/documentos")
async def enviar_documento(
    request: Request,
    nome: str = Query(..., min_length=1, max_length=200),
    titulo: str | None = Query(None, max_length=200),
    descricao: str | None = Query(None, max_length=1000),
    categoria: str | None = Query(None, max_length=80),
    usuario: dict = Depends(usuario_atual),
):
    """O corpo é o próprio arquivo (sem multipart). Entra só para consulta; o RAG é ligado depois."""
    if int(request.headers.get("content-length") or 0) > documentos.LIMITE_BYTES:
        raise HTTPException(413, "O arquivo passa de 25 MB.")
    conteudo = await request.body()
    try:
        documento_id = await run_in_threadpool(
            documentos.guardar, recursos["banco"], recursos["config"].pasta_docs, nome, conteudo,
            titulo=titulo, descricao=(descricao or "").strip() or None, categoria=(categoria or "").strip() or None,
            usuario_id=usuario["id"],
        )
    except ValueError as erro:
        raise HTTPException(400, str(erro))
    return {"id": documento_id}


def _arquivo(doc: dict | None, baixar: bool = False):
    if doc is None:
        raise HTTPException(404, "documento não encontrado")
    caminho = (recursos["config"].pasta_docs / doc["fonte"]).resolve()
    if not caminho.is_file() or recursos["config"].pasta_docs.resolve() not in caminho.parents:
        raise HTTPException(404, "o arquivo não está mais na pasta da base")
    # HTML baixa em vez de abrir: um HTML aberto aqui rodaria com o login do Elo
    abrir = doc["extensao"] in TIPOS and not baixar
    return FileResponse(caminho, media_type=TIPOS.get(doc["extensao"], "application/octet-stream"),
                        filename=caminho.name, content_disposition_type="inline" if abrir else "attachment",
                        headers={"X-Content-Type-Options": "nosniff"})


@app.get("/api/documentos/arquivo")
def abrir_documento_da_fonte(fonte: str = Query(..., max_length=300), usuario: dict = Depends(usuario_atual)):
    """Para as fontes citadas no Pergunte, que vêm pelo caminho do arquivo."""
    return _arquivo(recursos["repo"].documento(fonte=fonte))


@app.get("/api/documentos/{documento_id}")
def ver_documento(documento_id: int, usuario: dict = Depends(usuario_atual)):
    """Os dados e o texto extraído, para ler o documento dentro do Elo."""
    doc = recursos["repo"].documento_completo(documento_id)
    if doc is None:
        raise HTTPException(404, "documento não encontrado")
    return doc


@app.get("/api/documentos/{documento_id}/arquivo")
def abrir_documento(documento_id: int, baixar: bool = False, usuario: dict = Depends(usuario_atual)):
    return _arquivo(recursos["repo"].documento(documento_id), baixar)


class AlteraDocumento(BaseModel):
    titulo: str | None = Field(None, min_length=1, max_length=200)
    descricao: str | None = Field(None, max_length=1000)
    categoria: str | None = Field(None, max_length=80)
    rag: bool | None = None


@app.patch("/api/documentos/{documento_id}")
def alterar_documento(documento_id: int, dados: AlteraDocumento, usuario: dict = Depends(exige("gestor", "admin"))):
    repo: Repositorio = recursos["repo"]
    doc = repo.documento(documento_id)
    if doc is None:
        raise HTTPException(404, "documento não encontrado")
    if documento_id in _indexando:
        raise HTTPException(409, "Ainda estou processando este documento. Tente em alguns segundos.")
    campos = dados.model_dump(exclude_none=True)
    repo.alterar_documento(documento_id, campos)
    # entrou ou saiu do RAG, ou mudou o título (que vai junto de cada trecho): refaz os trechos
    if ("rag" in campos and campos["rag"] != doc["rag"]) or ("titulo" in campos and doc["rag"]):
        _indexar_em_segundo_plano(documento_id)
    return {"ok": True, "indexando": documento_id in _indexando}


@app.delete("/api/documentos/{documento_id}")
def excluir_documento(documento_id: int, usuario: dict = Depends(exige("gestor", "admin"))):
    if documento_id in _indexando:
        raise HTTPException(409, "Ainda estou processando este documento. Tente em alguns segundos.")
    if not documentos.remover(recursos["banco"], recursos["config"].pasta_docs, documento_id):
        raise HTTPException(404, "documento não encontrado")
    return {"ok": True}


# ---------- acessos dos clientes (senha cifrada; revelar fica registrado) ----------

class Acesso(BaseModel):
    cliente: str = Field(min_length=1, max_length=120)
    produto: str | None = Field(None, max_length=40)
    nome: str = Field(min_length=1, max_length=200)
    endereco: str | None = Field(None, max_length=500)
    usuario: str | None = Field(None, max_length=200)
    senha: str | None = Field(None, max_length=500)  # na alteração: None mantém a atual
    observacao: str | None = Field(None, max_length=2000)


@app.get("/api/acessos")
def listar_acessos(q: str | None = Query(None, max_length=200), usuario: dict = Depends(usuario_atual)):
    return {"acessos": recursos["acessos"].listar(q), "pode_excluir": usuario["papel"] in ("gestor", "admin")}


@app.post("/api/acessos")
def criar_acesso(dados: Acesso, usuario: dict = Depends(usuario_atual)):
    return {"id": recursos["acessos"].salvar(dados.model_dump(), usuario["id"])}


@app.put("/api/acessos/{acesso_id}")
def alterar_acesso(acesso_id: int, dados: Acesso, usuario: dict = Depends(usuario_atual)):
    recursos["acessos"].salvar(dados.model_dump(), usuario["id"], acesso_id)
    return {"ok": True}


@app.post("/api/acessos/{acesso_id}/senha")
def revelar_senha(acesso_id: int, acao: str = Query("revelou", pattern="^(revelou|copiou)$"),
                  usuario: dict = Depends(usuario_atual)):
    try:
        senha = recursos["acessos"].revelar(acesso_id, usuario["id"], acao)
    except ValueError as erro:
        raise HTTPException(409, str(erro))
    if senha is None:
        raise HTTPException(404, "acesso não encontrado")
    return JSONResponse({"senha": senha}, headers={"Cache-Control": "no-store"})


@app.delete("/api/acessos/{acesso_id}")
def excluir_acesso(acesso_id: int, usuario: dict = Depends(exige("gestor", "admin"))):
    if not recursos["acessos"].excluir(acesso_id, usuario["id"]):
        raise HTTPException(404, "acesso não encontrado")
    return {"ok": True}


@app.post("/api/acessos/importar")
async def importar_acessos(request: Request, usuario: dict = Depends(exige("gestor", "admin"))):
    """O corpo é o arquivo (JSON ou CSV). Lido aqui e gravado cifrado; o arquivo não fica salvo."""
    if int(request.headers.get("content-length") or 0) > 5 * 1024 * 1024:
        raise HTTPException(413, "O arquivo passa de 5 MB.")
    try:
        texto = (await request.body()).decode("utf-8-sig")
        return await run_in_threadpool(recursos["acessos"].importar, texto, usuario["id"])
    except (UnicodeDecodeError, ValueError) as erro:
        raise HTTPException(400, f"Não consegui ler o arquivo: {erro}")


# ---------- painel (gestor) e configurações (admin) ----------

@app.get("/api/painel")
def painel(dias: int = Query(30, ge=1, le=365), usuario: dict = Depends(exige("gestor", "admin"))):
    return recursos["repo"].painel(dias)


@app.get("/api/config")
def ver_config(usuario: dict = Depends(exige("admin"))):
    repo: Repositorio = recursos["repo"]
    return {**repo.config(), "contagem": repo.contagem_fontes(), "usuarios": recursos["usuarios"].listar(),
            "llm": {"modelo": recursos["llm"].modelo, "disponivel": recursos["llm"].disponivel()},
            "fila_geracao": recursos["fila"].tamanho()}


class NovaConfig(BaseModel):
    limiar: float | None = Field(None, ge=0.70, le=0.95)
    fontes: dict[str, bool] | None = None


@app.put("/api/config")
def salvar_config(dados: NovaConfig, usuario: dict = Depends(exige("admin"))):
    if dados.limiar is not None:
        recursos["repo"].salvar_config("limiar", round(dados.limiar, 2))
    if dados.fontes is not None:
        recursos["repo"].salvar_config("fontes", dados.fontes)
    return {"ok": True}


class NovoUsuario(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    nome: str = Field(min_length=2, max_length=200)
    papel: str
    senha: str = Field(min_length=8, max_length=200)
    nivel: str | None = None
    movidesk_id: str | None = None


@app.post("/api/usuarios")
def criar_usuario(dados: NovoUsuario, usuario: dict = Depends(exige("admin"))):
    if dados.papel not in PAPEIS:
        raise HTTPException(400, "papel inválido")
    return recursos["usuarios"].criar(
        dados.email, dados.nome, dados.papel, dados.senha, nivel=dados.nivel, movidesk_id=dados.movidesk_id or None
    )


class AlteraUsuario(BaseModel):
    papel: str | None = None
    ativo: bool | None = None
    movidesk_id: str | None = None
    nivel: str | None = Field(None, pattern="^N[123]$")


@app.patch("/api/usuarios/{usuario_id}")
def alterar_usuario(usuario_id: int, dados: AlteraUsuario, usuario: dict = Depends(exige("admin"))):
    if dados.papel is not None and dados.papel not in PAPEIS:
        raise HTTPException(400, "papel inválido")
    # o admin não se tranca para fora: não tira o próprio papel de admin nem se desativa
    if usuario_id == usuario["id"] and (dados.ativo is False or dados.papel not in (None, "admin")):
        raise HTTPException(400, "Você não pode tirar o seu próprio acesso de admin.")
    recursos["usuarios"].alterar(
        usuario_id, papel=dados.papel, ativo=dados.ativo, movidesk_id=dados.movidesk_id, nivel=dados.nivel
    )
    return {"ok": True}


@app.delete("/api/usuarios/{usuario_id}")
def excluir_usuario(usuario_id: int, usuario: dict = Depends(exige("admin"))):
    if usuario_id == usuario["id"]:
        raise HTTPException(400, "Você não pode excluir o seu próprio cadastro.")
    if not recursos["usuarios"].excluir(usuario_id):
        raise HTTPException(404, "usuário não encontrado")
    return {"ok": True}


class NovaSenha(BaseModel):
    nova: str = Field(min_length=8, max_length=200)
    atual: str | None = Field(None, max_length=200)


@app.put("/api/eu/senha")
def trocar_minha_senha(dados: NovaSenha, request: Request, usuario: dict = Depends(usuario_atual)):
    """Qualquer usuário troca a própria senha, confirmando a atual."""
    if not dados.atual:
        raise HTTPException(400, "Informe a senha atual.")
    ok = recursos["usuarios"].trocar_senha(
        usuario["id"], dados.nova, senha_atual=dados.atual, manter_token=request.cookies.get(COOKIE)
    )
    if not ok:
        raise HTTPException(400, "A senha atual não confere.")
    return {"ok": True}


@app.put("/api/usuarios/{usuario_id}/senha")
def definir_senha(usuario_id: int, dados: NovaSenha, request: Request, usuario: dict = Depends(exige("admin"))):
    """O admin define a senha de qualquer usuário (as sessões abertas dele são encerradas)."""
    manter = request.cookies.get(COOKIE) if usuario_id == usuario["id"] else None
    if not recursos["usuarios"].trocar_senha(usuario_id, dados.nova, manter_token=manter):
        raise HTTPException(404, "usuário não encontrado")
    return {"ok": True}


class Pregeracao(BaseModel):
    pausada: bool


@app.get("/api/pregeracao")
def ver_pregeracao(usuario: dict = Depends(exige("admin"))):
    return {"pausada": PAUSA.exists(), "ligada_no_env": recursos["config"].pregerar}


@app.put("/api/pregeracao")
def pausar_pregeracao(dados: Pregeracao, usuario: dict = Depends(exige("admin"))):
    PAUSA.parent.mkdir(exist_ok=True)
    PAUSA.touch() if dados.pausada else PAUSA.unlink(missing_ok=True)
    recursos["fila"].pausar(dados.pausada)
    return {"pausada": dados.pausada}


@app.post("/api/desligar")
def desligar(usuario: dict = Depends(exige("admin"))):
    """Encerra o servidor (o ícone da bandeja some sozinho). Volta pelo atalho Elo."""
    print(f"desligado pela tela por {usuario['email']}")
    threading.Timer(0.8, lambda: os._exit(0)).start()  # depois de responder à tela
    return {"ok": True}


# ---------- página, compatibilidade e saúde ----------

@app.get("/", include_in_schema=False)
def pagina():
    return FileResponse(STATIC / "index.html")


def _para_json(resultado: Resultado) -> dict:
    return {**asdict(resultado), "link": resultado.link(recursos["config"].movidesk_url)}


@app.get("/parecidos")
def parecidos(
    ticket: int | None = Query(None, description="id ou protocolo do ticket"),
    texto: str | None = Query(None, min_length=3, description="descrição do problema"),
    k: int = Query(5, ge=1, le=20, description="quantos resultados"),
    usuario: dict = Depends(usuario_atual),
):
    """Tickets resolvidos mais parecidos com um ticket ou com um texto (rota da primeira versão)."""
    buscador: Buscador = recursos["buscador"]
    if ticket:
        resultados = buscador.por_ticket(ticket, k)
        if resultados is None:
            raise HTTPException(404, "ticket sem vetor (ainda não foi carregado?)")
    elif texto:
        resultados = buscador.por_texto(texto, k)
    else:
        raise HTTPException(400, "informe ticket ou texto")
    return [_para_json(r) for r in resultados]


@app.get("/saude")
def saude():
    """Para o monitoramento: 200 só se o banco responde e o modelo está carregado."""
    try:
        banco_ok = recursos["banco"].saude()
    except Exception:
        banco_ok = False
    modelo_ok = recursos["vetorizador"].carregado
    corpo = {"banco": banco_ok, "modelo": modelo_ok, "llm": recursos["llm"].disponivel()}
    return JSONResponse(corpo, status_code=200 if banco_ok and modelo_ok else 503)


def main() -> None:
    import uvicorn

    # 127.0.0.1 no Windows; no container, ELO_HOST=0.0.0.0 (a porta só sai para fora pelo compose)
    uvicorn.run("copilot_suporte.api:app", host=os.getenv("ELO_HOST", "127.0.0.1"), port=int(os.getenv("ELO_PORTA", "8000")))


if __name__ == "__main__":
    main()
