"""Planner (Microsoft Graph): os cards que o time de produto usa para acompanhar os tickets.

    uv run python -m copilot_suporte.planner entrar        # uma vez: login pelo código em microsoft.com/devicelogin
    uv run python -m copilot_suporte.planner explorar      # mostra colunas e alguns cards de cada plano
    uv run python -m copilot_suporte.planner sincronizar   # grava os cards no banco e liga cada um ao seu ticket

Login delegado (app "Elo" no Entra da empresa, permissão Tasks.Read): o Elo lê só os planos que a
pessoa que entrou já enxerga. A senha nunca passa pelo Elo; fica só o token, cifrado com a chave
do .env (a mesma da tela Acessos) em data/, fora do Git, e renovado sozinho.
"""

import os
import re
import sys
from datetime import datetime

import requests
from cryptography.fernet import Fernet, InvalidToken

from copilot_suporte.acessos import _chave
from copilot_suporte.config import RAIZ

GRAPH = "https://graph.microsoft.com/v1.0"
ESCOPOS = ["Tasks.Read"]  # offline_access (renovar sem novo login) o MSAL pede sozinho
CACHE = RAIZ / "data" / "microsoft_token.bin"


class SemLogin(RuntimeError):
    """Ninguém entrou ainda (ou o login expirou): rode `planner entrar`."""


def _configuracao() -> tuple[str, str, list[str]]:
    tenant, cliente = os.getenv("MICROSOFT_TENANT_ID", ""), os.getenv("MICROSOFT_CLIENT_ID", "")
    planos = [p.strip() for p in os.getenv("PLANNER_PLANOS", "").split(",") if p.strip()]
    if not tenant or not cliente:
        raise RuntimeError("MICROSOFT_TENANT_ID e MICROSOFT_CLIENT_ID não estão no .env")
    return tenant, cliente, planos


def _app():
    import msal

    tenant, cliente, _ = _configuracao()
    cache = msal.SerializableTokenCache()
    if CACHE.exists():
        try:
            cache.deserialize(Fernet(_chave()).decrypt(CACHE.read_bytes()).decode())
        except InvalidToken:
            pass  # chave trocada: vale como sem login
    app = msal.PublicClientApplication(cliente, authority=f"https://login.microsoftonline.com/{tenant}", token_cache=cache)
    return app, cache


def _guardar(cache) -> None:
    if cache.has_state_changed:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_bytes(Fernet(_chave()).encrypt(cache.serialize().encode()))


def entrar() -> str:
    """Login pelo código: mostra o código e espera a pessoa entrar no navegador. Devolve a conta."""
    app, cache = _app()
    fluxo = app.initiate_device_flow(scopes=ESCOPOS)
    if "user_code" not in fluxo:
        raise RuntimeError(fluxo.get("error_description") or "não consegui iniciar o login")
    print(fluxo["message"], flush=True)
    resultado = app.acquire_token_by_device_flow(fluxo)
    if "access_token" not in resultado:
        raise RuntimeError(resultado.get("error_description") or "login não concluído")
    _guardar(cache)
    return resultado.get("id_token_claims", {}).get("preferred_username", "")


def token() -> str:
    app, cache = _app()
    contas = app.get_accounts()
    resultado = app.acquire_token_silent(ESCOPOS, account=contas[0]) if contas else None
    _guardar(cache)
    if not resultado or "access_token" not in resultado:
        raise SemLogin("sem login no Microsoft 365: rode `uv run python -m copilot_suporte.planner entrar`")
    return resultado["access_token"]


def conectado() -> bool:
    try:
        token()
        return True
    except (SemLogin, RuntimeError):
        return False


class Graph:
    def __init__(self, sessao: requests.Session | None = None):
        self.sessao = sessao or requests.Session()
        self.sessao.headers["Authorization"] = f"Bearer {token()}"

    def todos(self, caminho: str) -> list[dict]:
        """GET com as páginas seguintes (@odata.nextLink)."""
        url, itens = f"{GRAPH}{caminho}", []
        while url:
            resposta = self.sessao.get(url, timeout=60)
            if not resposta.ok:
                raise RuntimeError(f"Graph respondeu {resposta.status_code} em {caminho}: {resposta.text[:200]}")
            dados = resposta.json()
            itens.extend(dados.get("value", [dados]))
            url = dados.get("@odata.nextLink")
        return itens

    def um(self, caminho: str) -> dict:
        return self.todos(caminho)[0]


def link_da_tarefa(plano_id: str, tarefa_id: str) -> str:
    tenant = os.getenv("MICROSOFT_TENANT_ID", "")
    return f"https://planner.cloud.microsoft/webui/plan/{plano_id}/view/board/task/{tarefa_id}?tid={tenant}"


def ler_plano(graph: Graph, plano_id: str) -> dict:
    plano = graph.um(f"/planner/plans/{plano_id}")
    colunas = {b["id"]: b["name"] for b in graph.todos(f"/planner/plans/{plano_id}/buckets")}
    tarefas = graph.todos(f"/planner/plans/{plano_id}/tasks")
    return {"id": plano_id, "titulo": plano.get("title"), "colunas": colunas, "tarefas": tarefas}


def explorar() -> None:
    _, _, planos = _configuracao()
    graph = Graph()
    for plano_id in planos:
        p = ler_plano(graph, plano_id)
        print(f"\nPLANO {p['titulo']!r} · {len(p['tarefas'])} cards")
        por_coluna: dict[str, int] = {}
        for t in p["tarefas"]:
            nome = p["colunas"].get(t.get("bucketId"), "?")
            por_coluna[nome] = por_coluna.get(nome, 0) + 1
        for nome, n in por_coluna.items():
            print(f"  coluna {nome!r}: {n}")
        recentes = sorted(p["tarefas"], key=lambda t: t.get("createdDateTime") or "", reverse=True)[:15]
        for t in recentes:
            print(f"  - [{p['colunas'].get(t.get('bucketId'), '?')}] {t.get('percentComplete')}% · {t.get('title')!r}")


# ---------- cards vindos de arquivo (sem precisar de aprovação de app no Microsoft 365) ----------
# O Excel de "Exportar como Excel" do Planner, ou o planner.json que um fluxo do Power Automate grava
# no OneDrive de hora em hora (o OneDrive sincroniza a pasta no notebook). O mesmo leitor serve aos dois.

PROTOCOLO = re.compile(r"(?<!\d)(20\d{10})(?!\d)")  # protocolo do Movidesk: AAAAMM + id com 6 dígitos
STATUS_POR_PROGRESSO = {0: "Not started", 50: "In progress", 100: "Completed"}
PRIORIDADE_GRAPH = {1: "Urgent", 3: "Important", 5: "Medium", 9: "Low"}
_vistos: dict[str, float] = {}  # arquivo -> data de modificação já vista
_conteudo: dict[str, str] = {}  # arquivo -> hash do conteúdo já importado


def _hash(arquivo) -> str:
    import hashlib

    return hashlib.sha256(arquivo.read_bytes()).hexdigest()


def pastas() -> list:
    """base_conhecimento/planner (Excel exportado) e OneDrive\\Elo (fluxo do Power Automate), mais PLANNER_PASTAS."""
    from pathlib import Path

    from copilot_suporte.config import Config

    lista = [Config().pasta_docs / "planner"]
    for variavel in ("OneDriveCommercial", "OneDrive"):
        if os.getenv(variavel):
            lista.append(Path(os.environ[variavel]) / "Elo")
    # a pasta do OneDrive da empresa ("OneDrive - <Empresa> ..."): vale mesmo se o servidor subiu antes de ela existir
    lista += sorted(Path.home().glob("OneDrive - */Elo"))
    lista += [Path(p.strip()) for p in os.getenv("PLANNER_PASTAS", "").split(";") if p.strip()]
    return list(dict.fromkeys(lista))


def _data(valor) -> str | None:
    if valor in (None, ""):
        return None
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    texto = str(valor)
    return texto[:10] if re.match(r"\d{4}-\d{2}-\d{2}", texto) else None


def _protocolos(*textos) -> list[str]:
    return sorted({p for t in textos for p in PROTOCOLO.findall(t or "")})


def ler_excel(caminho) -> dict:
    import openpyxl

    livro = openpyxl.load_workbook(caminho, read_only=True)
    def tabela(nome):
        linhas = list(livro[nome].iter_rows(values_only=True))
        return [dict(zip(linhas[0], linha)) for linha in linhas[1:] if any(linha)]
    plano = tabela("Plan")[0]
    pessoas = {u["User ID"]: u["User Name"] for u in tabela("Users") if u.get("User ID") and u.get("User Name")} if "Users" in livro.sheetnames else {}
    cards = [{
        "id": c["Task ID"], "titulo": (c["Task Name"] or "").strip(), "coluna": c.get("Bucket"), "status": c.get("Status"),
        "progresso": {"Not started": 0, "In progress": 50, "Completed": 100}.get(c.get("Status")),
        "prioridade": c.get("Priority"), "responsaveis": c.get("Assigned To"),
        "criado_em": _data(c.get("Created Date")), "prazo": _data(c.get("Due date")), "concluido_em": _data(c.get("Completed Date")),
        # das notas só sai o protocolo do ticket: o texto delas não é guardado
        "protocolos": _protocolos(c["Task Name"], c.get("Notes")),
    } for c in tabela("Consolidated Data") if c.get("Task ID")]
    return {"plano_id": plano["Plan ID"], "plano": plano.get("Plan name"), "cards": cards, "pessoas": pessoas}


def ler_json(caminho) -> dict:
    """planner.json do Power Automate: {"plano", "nome", "colunas": [buckets], "tarefas": [tarefas do Graph]}."""
    import json

    dados = json.loads(open(caminho, encoding="utf-8-sig").read())
    colunas = {b["id"]: b.get("name") for b in dados.get("colunas") or []}
    cards = []
    for t in dados.get("tarefas") or []:
        progresso = t.get("percentComplete")
        cards.append({
            "id": t["id"], "titulo": (t.get("title") or "").strip(), "coluna": colunas.get(t.get("bucketId")),
            "status": STATUS_POR_PROGRESSO.get(progresso), "progresso": progresso,
            "prioridade": PRIORIDADE_GRAPH.get(t.get("priority")),
            "responsaveis": None,  # a tarefa só traz ids: gravar() troca pelos nomes guardados do Excel exportado
            "ids_responsaveis": list((t.get("assignments") or {}).keys()),
            "criado_em": _data(t.get("createdDateTime")), "prazo": _data(t.get("dueDateTime")),
            "concluido_em": _data(t.get("completedDateTime")), "protocolos": _protocolos(t.get("title")),
        })
    plano_id = dados.get("plano") or (dados.get("tarefas") or [{}])[0].get("planId")
    return {"plano_id": plano_id, "plano": dados.get("nome"), "cards": cards}


def gravar(banco, lido: dict) -> int:
    """Atualiza os cards de um plano. Protocolos e responsáveis já conhecidos não se perdem quando o
    arquivo novo não os traz (o JSON do Power Automate não tem as notas nem os nomes)."""
    with banco.conexao() as conn:
        if lido.get("pessoas"):
            conn.cursor().executemany(
                "insert into app_planner_pessoas (id, nome) values (%s, %s) on conflict (id) do update set nome = excluded.nome",
                list(lido["pessoas"].items()))
        if any(c.get("ids_responsaveis") for c in lido["cards"]):
            nomes = dict(conn.execute("select id, nome from app_planner_pessoas").fetchall())
            for c in lido["cards"]:
                conhecidos = [nomes[i] for i in c.get("ids_responsaveis") or [] if i in nomes]
                c["responsaveis"] = ";".join(conhecidos) or None
        # um envio só (executemany): card a card seriam ~500 idas e voltas até o banco remoto
        conn.cursor().executemany(
                """
                insert into app_planner_cards (id, plano_id, plano, titulo, coluna, status, progresso, prioridade,
                                               responsaveis, criado_em, prazo, concluido_em, protocolos, visto_em)
                values (%(id)s, %(plano_id)s, %(plano)s, %(titulo)s, %(coluna)s, %(status)s, %(progresso)s, %(prioridade)s,
                        %(responsaveis)s, %(criado_em)s, %(prazo)s, %(concluido_em)s, %(protocolos)s, now())
                on conflict (id) do update set
                    plano_id = excluded.plano_id, plano = coalesce(excluded.plano, app_planner_cards.plano),
                    titulo = excluded.titulo, coluna = coalesce(excluded.coluna, app_planner_cards.coluna),
                    status = excluded.status, progresso = excluded.progresso, prioridade = excluded.prioridade,
                    responsaveis = coalesce(excluded.responsaveis, app_planner_cards.responsaveis),
                    criado_em = excluded.criado_em, prazo = excluded.prazo, concluido_em = excluded.concluido_em,
                    protocolos = array(select distinct unnest(app_planner_cards.protocolos || excluded.protocolos)),
                    visto_em = now()
                """,
                [{**c, "plano_id": lido["plano_id"], "plano": lido["plano"]} for c in lido["cards"]],
            )
        # card apagado no Planner: some daqui também
        conn.execute("delete from app_planner_cards where plano_id = %s and not (id = any(%s))",
                     (lido["plano_id"], [c["id"] for c in lido["cards"]]))
    return len(lido["cards"])


def _normalizar(texto: str | None) -> str:
    import unicodedata

    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    texto = PROTOCOLO.sub(" ", texto)
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def casar(tickets: list[dict], cards: list[dict]) -> dict[int, tuple[str, str]]:
    """ticket_id -> (card_id, como). Primeiro o protocolo citado no card; senão, o título do Planner que o
    ticket cita na conversa interna igual ao título do card (ou um contido no outro, se longo)."""
    por_protocolo: dict[str, list[dict]] = {}
    for c in cards:
        for p in c["protocolos"]:
            por_protocolo.setdefault(p, []).append(c)
    recente = lambda lista: max(lista, key=lambda c: (c.get("criado_em") or "", c["id"]))
    titulos = [(_normalizar(c["titulo"]), c) for c in cards]
    vinculos = {}
    for t in tickets:
        if t["protocolo"] in por_protocolo:
            vinculos[t["ticket_id"]] = (recente(por_protocolo[t["protocolo"]])["id"], "protocolo")
            continue
        alvo = _normalizar(t.get("planner_titulo"))
        if len(alvo) < 12:
            continue
        iguais = [c for n, c in titulos if n == alvo] or [c for n, c in titulos if len(n) >= 15 and (n in alvo or alvo in n)]
        if iguais:
            vinculos[t["ticket_id"]] = (recente(iguais)["id"], "titulo")
    return vinculos


def vincular(banco) -> int:
    from psycopg.rows import dict_row

    from copilot_suporte.repositorio import JOIN_PLANNER

    with banco.conexao() as conn:
        cur = conn.cursor(row_factory=dict_row)
        cards = cur.execute("select id, titulo, criado_em::text, protocolos from app_planner_cards").fetchall()
        if not cards:
            return 0
        tickets = cur.execute(f"""
            select t.ticket_id,
                   to_char(t.criado_em at time zone 'America/Sao_Paulo', 'YYYYMM') || lpad(t.ticket_id::text, 6, '0') as protocolo,
                   pl.planner_titulo
            from limpo_tickets t {JOIN_PLANNER}
            where t.status_base in ('New', 'InAttendance', 'Stopped')
        """).fetchall()
        vinculos = casar(tickets, cards)
        conn.execute("delete from app_planner_vinculos")
        if vinculos:
            cur.executemany("insert into app_planner_vinculos (ticket_id, card_id, como) values (%s, %s, %s)",
                            [(t, c, como) for t, (c, como) in vinculos.items()])
    return len(vinculos)


def sincronizar_arquivos(banco, *, forcar: bool = False) -> dict | None:
    """Importa os arquivos novos ou alterados (o mais recente de cada plano) e refaz os vínculos.
    None = nada mudou desde a última vez."""
    arquivos = [a for p in pastas() if p.is_dir() for a in p.iterdir()
                if a.suffix.lower() in (".xlsx", ".json") and not a.name.startswith("~$")]
    mudou = [a for a in arquivos if _vistos.get(str(a)) != a.stat().st_mtime]
    # o Power Automate regrava o arquivo a cada execução mesmo sem mudança no Planner: só relê se o conteúdo mudou
    mudou = [a for a in mudou if _conteudo.get(str(a)) != _hash(a) or forcar]
    for a in arquivos:
        _vistos[str(a)] = a.stat().st_mtime
    if not mudou and not forcar:
        return None
    por_plano: dict[str, tuple[float, dict]] = {}
    for arquivo in sorted(arquivos, key=lambda a: a.stat().st_mtime):
        try:
            lido = ler_excel(arquivo) if arquivo.suffix.lower() == ".xlsx" else ler_json(arquivo)
        except Exception as erro:  # arquivo pela metade (OneDrive ainda baixando): tenta no próximo ciclo
            print(f"planner: não li {arquivo.name} ({type(erro).__name__})")
            continue
        _conteudo[str(arquivo)] = _hash(arquivo)
        if lido.get("plano_id"):
            # o Excel traz responsáveis e notas (protocolos); o JSON, só o estado: grava os dois, o mais novo por último
            gravar(banco, lido)
            por_plano[lido["plano_id"]] = (arquivo.stat().st_mtime, lido)
    return {"planos": len(por_plano), "cards": sum(len(l["cards"]) for _, l in por_plano.values()), "vinculos": vincular(banco)}


if __name__ == "__main__":
    comando = sys.argv[1] if len(sys.argv) > 1 else ""
    if comando == "entrar":
        print("Conectado como", entrar())
    elif comando == "explorar":
        explorar()
    elif comando == "sincronizar":
        from copilot_suporte.banco import Banco

        with Banco() as banco:
            print(sincronizar_arquivos(banco, forcar=True))
    else:
        print(__doc__)
