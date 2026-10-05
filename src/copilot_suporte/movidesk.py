"""Cliente da API do Movidesk."""

import time
from collections.abc import Callable

import requests

BASE_URL = "https://api.movidesk.com/public/v1"
CAMPOS = (
    "id,subject,createdDate,category,urgency,baseStatus,status,justification,lastUpdate,ownerTeam,"
    "serviceFirstLevel,serviceSecondLevel,serviceThirdLevel,"
    "slaAgreement,slaResponseDate,slaRealResponseDate,slaSolutionDate,slaSolutionDateIsPaused,resolvedIn,closedIn"
)
# Só o id e o perfil do autor de cada ação: nome, e-mail e telefone nem chegam ao código.
ACOES = "actions($select=id,type,origin,description,createdDate;$expand=createdBy($select=id,profileType))"
# Do responsável (um agente da empresa), só id e nome: é o que monta a fila de cada analista.
RESPONSAVEL = "owner($select=id,businessName)"
# Quem abriu e a empresa: chegam da API, mas só a empresa e o primeiro nome são gravados
# (reduzir_pessoas corta o resto antes de o ticket ir para o banco).
PESSOAS = (
    "clients($select=businessName,personType,profileType;$expand=organization($select=businessName)),"
    "createdBy($select=businessName,personType,profileType)"
)
PERFIL_AGENTE = 1
PESSOA_EMPRESA = 2
STATUS_PARA_REPETIR = {429, 500, 502, 503, 504}


class ClienteMovidesk:
    """Busca tickets na API, respeitando o limite de 10 requisições por minuto.

    A sessão e a função de espera podem ser trocadas nos testes, para simular
    respostas da API sem internet e sem esperar de verdade.
    """

    def __init__(
        self,
        token: str,
        *,
        sessao: requests.Session | None = None,
        tentativas: int = 3,
        espera_entre_paginas: float = 6,
        dormir: Callable[[float], None] = time.sleep,
    ):
        if not token:
            raise RuntimeError("MOVIDESK_TOKEN não encontrado no .env")
        self._token = token
        # A Session reaproveita a conexão HTTPS entre as páginas, em vez de abrir uma nova a cada uma.
        self._sessao = sessao or requests.Session()
        self._tentativas = tentativas
        self._espera_entre_paginas = espera_entre_paginas
        self._dormir = dormir

    def buscar_pagina(self, rota: str, top: int, skip: int = 0, filtro: str | None = None) -> list[dict]:
        """Uma página de tickets, com novas tentativas em falhas temporárias."""
        params = {
            "token": self._token,
            "$select": CAMPOS,
            "$expand": f"{ACOES},{RESPONSAVEL},{PESSOAS}",
            "$orderby": "id asc",
            "$top": top,
            "$skip": skip,
        }
        if filtro:
            params["$filter"] = filtro

        for tentativa in range(1, self._tentativas + 1):
            try:
                resposta = self._sessao.get(f"{BASE_URL}/{rota}", params=params, timeout=60)
            except requests.RequestException as erro:
                # Só o nome do erro: a mensagem completa traz a URL, e o token vai nela.
                motivo = type(erro).__name__
                espera = 30 * tentativa
            else:
                if resposta.ok:
                    return [reduzir_pessoas(t) for t in resposta.json()]
                if resposta.status_code not in STATUS_PARA_REPETIR:
                    raise RuntimeError(f"Movidesk respondeu {resposta.status_code}")
                motivo = f"status {resposta.status_code}"
                espera = int(resposta.headers.get("retry-after", 30 * tentativa))

            print(f"  falha ({motivo}) · tentativa {tentativa}/{self._tentativas} · esperando {espera}s")
            self._dormir(espera)

        raise RuntimeError(f"Movidesk falhou {self._tentativas} vezes seguidas ({motivo})")

    def buscar_todos(
        self,
        rota: str = "tickets",
        por_pagina: int = 100,
        max_paginas: int | None = None,
        filtro: str | None = None,
    ) -> list[dict]:
        """Todas as páginas de uma rota."""
        todos: list[dict] = []
        skip = 0
        pagina = 1
        while True:
            lote = self.buscar_pagina(rota, top=por_pagina, skip=skip, filtro=filtro)
            todos.extend(lote)
            print(f"{rota} · página {pagina}: {len(lote)} tickets, total {len(todos)}")

            if len(lote) < por_pagina:
                break
            if max_paginas and pagina >= max_paginas:
                break

            skip += por_pagina
            pagina += 1
            self._dormir(self._espera_entre_paginas)
        return todos

    def buscar_historico(self, por_pagina: int = 100) -> list[dict]:
        """Carga completa: as duas rotas, sem duplicados."""
        recentes = self.buscar_todos(rota="tickets", por_pagina=por_pagina)
        antigos = self.buscar_todos(rota="tickets/past", por_pagina=por_pagina)
        tickets = juntar_sem_duplicar(recentes, antigos)

        duplicados = len(recentes) + len(antigos) - len(tickets)
        print("recentes:", len(recentes), "| antigos:", len(antigos), "| duplicados removidos:", duplicados)
        return tickets


def juntar_sem_duplicar(*listas: list[dict]) -> list[dict]:
    """Junta listas de tickets; se um id se repete, fica o de lastUpdate mais recente."""
    por_id: dict[int, dict] = {}
    for lista in listas:
        for ticket in lista:
            guardado = por_id.get(ticket["id"])
            if guardado is None or ticket["lastUpdate"] > guardado["lastUpdate"]:
                por_id[ticket["id"]] = ticket
    return list(por_id.values())


def _primeiro_nome(nome: str | None) -> str | None:
    partes = (nome or "").split()
    return partes[0].capitalize() if partes else None


def reduzir_pessoas(ticket: dict) -> dict:
    """Troca clients e createdBy por só a empresa (_empresa) e o primeiro nome de quem abriu (_solicitante).

    Quem abriu: o criador do ticket, se for cliente; se um agente abriu em nome do cliente,
    o primeiro cliente do ticket. A empresa é a organização dessa pessoa (ou ela mesma, se
    o cliente cadastrado for uma empresa).
    """
    ticket = dict(ticket)
    clientes = ticket.pop("clients", None) or []
    criador = ticket.pop("createdBy", None) or {}
    pessoa = criador if criador and criador.get("profileType") != PERFIL_AGENTE else (clientes[0] if clientes else {})
    empresa = None
    for cliente in [pessoa, *clientes]:
        if not cliente:
            continue
        if cliente.get("personType") == PESSOA_EMPRESA:
            empresa = cliente.get("businessName")
        else:
            empresa = (cliente.get("organization") or {}).get("businessName")
        if empresa:
            break
    ticket["_empresa"] = empresa.strip() if empresa else None
    ticket["_solicitante"] = None if pessoa.get("personType") == PESSOA_EMPRESA else _primeiro_nome(pessoa.get("businessName"))
    return ticket
