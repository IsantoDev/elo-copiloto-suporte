"""O copiloto: junta vizinhos, triagem, rascunhos (LLM) e o Pergunte (RAG com fontes).

Velocidade: o banco fica a ~300 ms de distância, então a fila aberta (tickets, textos,
vizinhos e rascunhos) vive num retrato em memória, atualizado em segundo plano. Os dados
só mudam com a carga de hora em hora; a fila e o ticket respondem sem ir ao banco.
"""

import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict
from datetime import datetime, timezone

from copilot_suporte.buscar import limpar, solucao_do_caso
from copilot_suporte.lexico import IndiceLexico, sem_acento
from copilot_suporte.llm import Cancelado
from copilot_suporte.privacidade import mascarar
from copilot_suporte.triagem import Vizinho, eh_urgente, nome_urgencia, risco_sla, situacao, tipo_de_rascunho, votar

K_VIZINHOS = 10
# Os casos parecidos que a tela mostra (e a base do roteiro de análise) vêm da busca híbrida: significado
# + palavras do problema e da solução. A triagem (votos, risco, solução × análise) fica só com o
# significado: medido em 570 tickets, as palavras acham o caso certo (#3166 → #3081 subiu de fora dos 10
# para 3º) mas tiram 1 a 4 pontos do acerto de módulo.
PESO_PALAVRAS_PARECIDOS = 0.5
LIMIAR_PADRAO = 0.80
MIN_SIM_PERGUNTA = 0.45  # abaixo disso, nenhuma fonte trata do assunto: "não encontrei"
NAO_ENCONTREI = "NAO_ENCONTREI"
TAM_CASO = 900  # caracteres de cada solução no prompt: prompts curtos = respostas mais rápidas
TAM_DOC = 1100  # o trecho de documento quase inteiro: scripts e passo a passo não podem sair cortados
MIN_DOC_SOLUCAO = 0.55  # documento tão parecido com o ticket que vira base de um rascunho de solução
MAX_TURNOS = 3  # quantas trocas anteriores do Pergunte entram como memória
TAM_TURNO = 500  # caracteres de cada resposta anterior no pedido
# Documentos no Pergunte: busca híbrida (significado + palavras, ver lexico.py), fundida por RRF.
# Os trechos são curtos (uma linha de tabela tem ~200 caracteres), então entram vários, limitados
# pelo total de caracteres: o contexto do 7B é de 4096 tokens.
CANDIDATOS_DOC = 40  # quantos de cada busca entram na fusão
MAX_DOCS = 12  # 8 deixava de fora parâmetros de uma lista (as vagas iam para glossário e siglas)
ORCAMENTO_DOCS = 4400  # caracteres de documento no pedido
MIN_SIM_DOC = 0.40  # só pelo significado, abaixo disso o trecho não trata do assunto...
TOP_PALAVRAS = 8  # ...a não ser que esteja entre os primeiros da busca por palavras
MAX_CASOS = 3
RRF_K = 60
LEXICO_CONFERIR = 60  # segundos entre conferências de que a base de documentos mudou
_CITACAO = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]")

SISTEMA = (
    "Você é o Elo, copiloto do suporte de um ERP (TOTVS Protheus: Produto A e Produto B). "
    "Escreva em português do Brasil, direto e técnico, para um analista de suporte. "
    "Use só as informações fornecidas. Nunca invente telas, rotinas, parâmetros ou versões que não apareçam nelas. "
    "Não inclua nomes, e-mails ou telefones de clientes."
)

SISTEMA_REESCRITA = (
    "Você reescreve perguntas de um analista de suporte de ERP para que fiquem completas e independentes. "
    "Escreva em português do Brasil. Nunca responda a pergunta."
)

# O rascunho é escrito em texto com seções (e não em JSON) para aparecer na tela enquanto é gerado.
SECOES = {
    "solucao": (("resumo", "Resumo", False), ("passos", "Passos", True), ("resposta_cliente", "Resposta ao cliente", False)),
    "analise": (("resumo", "Resumo", False), ("hipoteses", "O que pode ser", True),
                ("perguntar", "O que perguntar ao cliente", True), ("onde_olhar", "Onde olhar", True)),
}


def formato(tipo: str) -> str:
    """As instruções de formato do rascunho (títulos fixos, itens com hífen)."""
    linhas = []
    for _, titulo, lista in SECOES[tipo]:
        linhas.append(f"## {titulo}")
        linhas.append("- item" if lista else "texto")
    return "\n".join(linhas)


_NUMERACAO = re.compile(r"^\s*(\d+[.)-]|[-•*])\s*")


def ler_secoes(texto: str, tipo: str) -> dict:
    """Texto com '## Título' → {chave: texto ou lista}. Tolera seções faltando ou fora de ordem."""
    por_titulo = {titulo.lower(): (chave, lista) for chave, titulo, lista in SECOES[tipo]}
    conteudo: dict = {chave: [] if lista else "" for chave, _, lista in SECOES[tipo]}
    atual = None
    for linha in texto.splitlines():
        cabecalho = re.match(r"^\s*#+\s*(.+?)\s*:?\s*$", linha)
        if cabecalho:
            atual = por_titulo.get(cabecalho[1].strip().lower())
            continue
        if atual is None or not linha.strip():
            continue
        chave, lista = atual
        if lista:
            conteudo[chave].append(_NUMERACAO.sub("", linha).strip())
        else:
            conteudo[chave] = (conteudo[chave] + "\n" + linha.strip()).strip()
    return conteudo


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _vizinho(linha: dict) -> Vizinho:
    tipo, texto = solucao_do_caso(linha.get("mensagens_publicas"), linha.get("solucao_publica"))
    return Vizinho(
        ticket_id=linha["ticket_id"],
        similaridade=round(float(linha["similaridade"]), 3),
        modulo=linha.get("modulo"),
        item=linha.get("item"),
        urgencia=linha.get("urgencia"),
        categoria=linha.get("categoria"),
        horas_resolucao=float(linha["horas_resolucao"]) if linha.get("horas_resolucao") is not None else None,
        estourou_sla=linha.get("estourou_sla"),
        assunto=linha.get("assunto"),
        # só conta como solução o que descreve a solução; o modelo de encerramento não conta
        solucao_publica=texto if tipo == "solucao" else None,
        orientacao=texto if tipo == "orientacao" else None,
        solucao_interna=limpar(linha.get("solucao_interna")) or None,
        protocolo=linha.get("protocolo"),
    )


def triagem(ticket: dict, vizinhos: list[Vizinho]) -> dict:
    """Sugestões de serviço, tipo e urgência, risco de SLA e a marca de urgente."""
    servico = votar([v for v in vizinhos if v.servico], "servico")
    urgencia = votar(vizinhos, "urgencia")
    tipo = votar(vizinhos, "categoria")
    risco = risco_sla(vizinhos, ticket["criado_em"], ticket.get("sla_solucao_em"), _agora(), ticket.get("sla_pausado"))
    urgencia_ref = urgencia.valor if urgencia else ticket.get("urgencia")
    return {
        "servico": asdict(servico) if servico else None,
        "urgencia": asdict(urgencia) if urgencia else None,
        "tipo": asdict(tipo) if tipo else None,
        "risco": asdict(risco),
        "urgente": eh_urgente(nome_urgencia(ticket.get("urgencia")) or urgencia_ref, risco),
    }


def _caso(i: int, v: Vizinho) -> str:
    partes = [f"[Caso {i}] ticket #{v.ticket_id} · semelhança {v.similaridade:.2f} · {v.servico or 'sem módulo'}"]
    partes.append(f"Assunto: {mascarar(v.assunto)}")
    if v.solucao_publica:
        partes.append(f"Solução enviada ao cliente: {mascarar(v.solucao_publica)[:TAM_CASO]}")
    elif v.orientacao:
        partes.append(f"Última orientação ao cliente (não descreve a solução): {mascarar(v.orientacao)[:TAM_CASO]}")
    if v.solucao_interna:
        partes.append(f"Nota técnica interna: {mascarar(v.solucao_interna)[:TAM_CASO]}")
    return "\n".join(partes)


class Retrato:
    """A fila aberta em memória: tickets (com texto), vizinhos (com soluções) e rascunhos."""

    def __init__(self):
        self.pronto = False
        self.tickets: dict[int, dict] = {}
        self.vizinhos: dict[int, tuple[str, list[Vizinho]]] = {}  # ticket → (hash do texto, vizinhos)
        self.parecidos: dict[int, list[Vizinho]] = {}  # ticket → casos da busca híbrida (tela e roteiro)
        self.rascunhos: dict[int, dict] = {}
        self.atualizado_em: float | None = None


class Copiloto:
    def __init__(self, repo, vetorizador, llm):
        self.repo = repo
        self.vetorizador = vetorizador
        self.llm = llm
        self.retrato = Retrato()
        self._trava_retrato = threading.Lock()
        self._lexico: IndiceLexico | None = None
        self._lexico_trechos: dict[int, dict] = {}
        self._lexico_assinatura: tuple | None = None
        self._lexico_conferido = 0.0
        self._trava_lexico = threading.Lock()

    def limiar(self) -> float:
        return float(self.repo.config().get("limiar", LIMIAR_PADRAO))

    def vizinhos(self, ids: list[int]) -> dict[int, list[Vizinho]]:
        """Para a triagem: só a busca por significado (a régua do limiar e da calibração)."""
        linhas = self.repo.vizinhos(ids, self.vetorizador.nome, K_VIZINHOS, com_textos=True, peso_palavras=0)
        return {tid: [_vizinho(linha) for linha in lista] for tid, lista in linhas.items()}

    def parecidos(self, ids: list[int]) -> dict[int, list[Vizinho]]:
        """Para a tela e o roteiro de análise: a busca híbrida (significado + palavras)."""
        linhas = self.repo.vizinhos(ids, self.vetorizador.nome, 5, com_textos=True, peso_palavras=PESO_PALAVRAS_PARECIDOS)
        return {tid: [_vizinho(linha) for linha in lista] for tid, lista in linhas.items()}

    def _parecidos(self, ticket_id: int) -> list[Vizinho]:
        if ticket_id in self.retrato.parecidos:
            return self.retrato.parecidos[ticket_id]
        return self.parecidos([ticket_id]).get(ticket_id, [])

    # ---------- retrato em memória ----------

    def atualizar_retrato(self) -> None:
        """Relê a fila aberta (1 consulta), os rascunhos (1) e os vizinhos só do que mudou."""
        with self._trava_retrato:
            tickets = {t["ticket_id"]: t for t in self.repo.fila(None, com_texto=True)}
            antigos = self.retrato.vizinhos
            faltando = [tid for tid, t in tickets.items() if antigos.get(tid, ("",))[0] != t["texto_hash"]]
            novos = dict((tid, antigos[tid]) for tid in tickets if tid in antigos and tid not in faltando)
            parecidos = {tid: self.retrato.parecidos[tid] for tid in novos if tid in self.retrato.parecidos}
            for i in range(0, len(faltando), 40):  # lotes: uma consulta por lote
                lote = faltando[i:i + 40]
                calculados, hibridos = self.vizinhos(lote), self.parecidos(lote)
                for tid in lote:
                    novos[tid] = (tickets[tid]["texto_hash"], calculados.get(tid, []))
                    parecidos[tid] = hibridos.get(tid, [])
            rascunhos = {r["ticket_id"]: r for r in self.repo.rascunhos(list(tickets))}
            r = self.retrato
            r.tickets, r.vizinhos, r.parecidos, r.rascunhos = tickets, novos, parecidos, rascunhos
            r.atualizado_em, r.pronto = time.time(), True

    def _ticket(self, ticket_id: int) -> tuple[dict | None, list[Vizinho] | None]:
        """Do retrato, se o ticket está nele; senão do banco (tickets fechados, retrato ainda vazio)."""
        t = self.retrato.tickets.get(ticket_id)
        if t is not None and ticket_id in self.retrato.vizinhos:
            return t, self.retrato.vizinhos[ticket_id][1]
        t = self.repo.ticket(ticket_id)
        if t is None:
            return None, None
        return t, self.vizinhos([ticket_id]).get(ticket_id, [])

    def analistas(self) -> list[dict]:
        """Agentes com tickets abertos (filtro "Ver tickets de"), do retrato quando pronto."""
        if not self.retrato.pronto:
            return self.repo.analistas()
        contagem: dict[tuple, int] = {}
        for t in self.retrato.tickets.values():
            if t.get("responsavel_id"):
                chave = (t["responsavel_id"], t.get("responsavel_nome"), t.get("time_responsavel"))
                contagem[chave] = contagem.get(chave, 0) + 1
        return [{"id": i, "nome": n, "time": tm, "abertos": c} for (i, n, tm), c in sorted(contagem.items(), key=lambda x: x[0][1] or "")]

    # ---------- fila ----------

    def fila(self, responsavel_id: str | None) -> list[dict]:
        if self.retrato.pronto:
            tickets = [t for t in self.retrato.tickets.values() if responsavel_id is None or t["responsavel_id"] == responsavel_id]
            vizinhos = {tid: v for tid, (_, v) in self.retrato.vizinhos.items()}
            rascunhos = self.retrato.rascunhos
        else:  # logo depois de subir, enquanto o retrato carrega
            tickets = self.repo.fila(responsavel_id)
            vizinhos = self.vizinhos([t["ticket_id"] for t in tickets]) if tickets else {}
            rascunhos = {}
        itens = []
        for t in tickets:
            r = rascunhos.get(t["ticket_id"])
            itens.append({**_publico(t), "triagem": triagem(t, vizinhos.get(t["ticket_id"], [])),
                          "rascunho_tipo": r["tipo"] if r else t.get("rascunho_tipo")})
        # urgentes e com risco primeiro; depois os mais recentes
        ordem = {"estourado": 0, "alto": 1, "medio": 2}
        itens.sort(key=lambda i: i["criado_em"], reverse=True)
        itens.sort(key=lambda i: (not i["triagem"]["urgente"], ordem.get(i["triagem"]["risco"]["nivel"], 3)))
        return itens

    # ---------- ticket ----------

    def detalhe(self, ticket_id: int) -> dict | None:
        t, vizinhos = self._ticket(ticket_id)
        if t is None:
            return None
        config = self.repo.config()
        limiar = float(config.get("limiar", LIMIAR_PADRAO))
        tipo, base = tipo_de_rascunho(vizinhos, limiar)
        return {
            **_publico(t),
            "texto": mascarar(t.get("texto")),
            "triagem": triagem(t, vizinhos),
            "calibracao_sla": config.get("calibracao_sla"),
            "parecidos": [_parecido(v) for v in self._parecidos(ticket_id)[:5]],
            "rascunho_previsto": {"tipo": tipo, "base": [v.ticket_id for v in base], "limiar": limiar,
                                  "mais_proximo": vizinhos[0].similaridade if vizinhos else None},
        }

    def caso(self, ticket_id: int) -> dict | None:
        t = self.repo.ticket(ticket_id)
        if t is None:
            return None
        horas = None
        if t.get("resolvido_em"):
            horas = round((t["resolvido_em"] - t["criado_em"]).total_seconds() / 3600, 1)
        tipo, texto = solucao_do_caso(t.get("mensagens_publicas"), t.get("solucao_publica"))
        return {
            **_publico(t),
            "texto": mascarar(t.get("texto")),
            "solucao_tipo": tipo,  # solucao, orientacao ou nenhuma
            "solucao_publica": mascarar(texto),
            "solucao_interna": mascarar(limpar(t.get("solucao_interna"))),
            "horas_resolucao": horas,
            "vezes_base": t["vezes_base"],
        }

    def rascunho_valido(self, ticket_id: int) -> dict | None:
        """O rascunho guardado, se ainda vale (mesmo texto, mesmo modelo, mesmo limiar)."""
        if ticket_id in self.retrato.tickets:
            r, texto_hash = self.retrato.rascunhos.get(ticket_id), self.retrato.tickets[ticket_id]["texto_hash"]
        else:
            r, t = self.repo.rascunho(ticket_id), self.repo.ticket(ticket_id)
            texto_hash = t["texto_hash"] if t else None
        if r and r["texto_hash"] == texto_hash and r["modelo_llm"] == self.llm.modelo \
                and abs(r["limiar"] - self.limiar()) < 1e-6:
            return r
        return None

    def gerar_rascunho(self, ticket_id: int, cancelado: Callable[[], bool] = lambda: False,
                       escrever: Callable[[str], None] = lambda _: None) -> None:
        """Chamado pela fila de geração (uma GPU, um rascunho por vez). escrever recebe o texto
        parcial a cada pedaço gerado, para a tela mostrar o rascunho sendo escrito."""
        inicio = time.perf_counter()
        t, vizinhos = self._ticket(ticket_id)
        if t is None or not t.get("texto"):
            return
        limiar = self.limiar()
        tipo, base = tipo_de_rascunho(vizinhos, limiar)
        if tipo == "analise":
            base = [v for v in self._parecidos(ticket_id) if v.solucao_publica or v.solucao_interna][:3] or base
        problema = f"Ticket #{ticket_id} · {t.get('modulo') or ''} › {t.get('item') or ''}\n{mascarar(t['texto'])[:2000]}"
        casos = "\n\n".join(_caso(i, v) for i, v in enumerate(base, 1)) or "(nenhum caso parecido com solução)"
        # Os documentos entram nos dois tipos. Um guia do mesmo erro é o procedimento oficial: vale mais que
        # um caso parecido, e sozinho já sustenta um rascunho de solução.
        trechos = [d for d in self._trechos(t["texto"][:1000], k=3) if float(d["similaridade"]) >= MIN_SIM_PERGUNTA]
        docs = "\n\n".join(f"[Documento] {d['titulo']}\n{d['texto'][:TAM_DOC]}" for d in trechos) or "(nenhum)"
        if trechos and float(trechos[0]["similaridade"]) >= MIN_DOC_SOLUCAO:
            tipo = "solucao"

        if tipo == "solucao":
            pedido = (
                f"TICKET NOVO:\n{problema}\n\nDOCUMENTAÇÃO DO SUPORTE (procedimento oficial):\n{docs}\n\n"
                f"CASOS RESOLVIDOS PARECIDOS:\n{casos}\n\n"
                "Monte um rascunho de solução para o ticket novo. Se a documentação trata do mesmo erro, siga o "
                "procedimento dela (é a fonte principal); use os casos só para completar. Senão, use o que resolveu "
                "os casos. Os passos devem ser ações concretas que aparecem nas fontes (2 a 6). O resumo descreve o "
                "problema em até 2 frases, sem saudação. A resposta ao cliente é curta e cordial.\n\n"
                f"Responda exatamente neste formato:\n{formato('solucao')}"
            )
        else:
            pedido = (
                f"TICKET NOVO:\n{problema}\n\nCASOS PARECIDOS (semelhança moderada, podem não ser o mesmo problema):\n"
                f"{casos}\n\nDOCUMENTAÇÃO RELACIONADA:\n{docs}\n\n"
                "Nenhum caso é parecido o bastante para propor uma solução. Monte um roteiro de investigação "
                "curto: 1 a 3 hipóteses, até 4 perguntas ao cliente e até 4 lugares para olhar no sistema. "
                "O resumo descreve o problema em até 2 frases, sem saudação.\n\n"
                f"Responda exatamente neste formato:\n{formato('analise')}"
            )

        texto = ""
        for pedaco in self.llm.escrever(SISTEMA, pedido, cancelado=cancelado, limite=700, uso="rascunho"):
            texto += pedaco
            escrever(texto)
        conteudo = ler_secoes(texto, tipo)
        conteudo["documentos"] = sorted({d["titulo"] for d in trechos})  # a tela mostra em que se baseou
        segundos = round(time.perf_counter() - inicio, 1)
        base_ids = [v.ticket_id for v in base]
        self.repo.salvar_rascunho(ticket_id, tipo, conteudo, base_ids, t["texto_hash"], self.llm.modelo, limiar, segundos)
        self.retrato.rascunhos[ticket_id] = {
            "ticket_id": ticket_id, "tipo": tipo, "conteudo": conteudo, "base": base_ids, "texto_hash": t["texto_hash"],
            "modelo_llm": self.llm.modelo, "limiar": limiar, "gerado_em": _agora(), "segundos": segundos,
        }

    # ---------- busca e Pergunte ----------

    def buscar(self, texto: str) -> dict:
        linhas = self.repo.por_vetor(self.vetorizador.consulta(texto), self.vetorizador.nome, k=20)
        abertos, resolvidos = [], []
        for linha in linhas:
            item = {
                "ticket_id": linha["ticket_id"], "protocolo": linha["protocolo"],
                "assunto": mascarar(linha["assunto"]), "similaridade": round(float(linha["similaridade"]), 3),
                "servico": " › ".join(x for x in (linha["modulo"], linha["item"]) if x) or None,
            }
            (resolvidos if linha["status_base"] in ("Resolved", "Closed") else abertos).append(item)
        return {"abertos": abertos[:5], "resolvidos": resolvidos[:10]}

    def _trechos(self, texto: str, k: int = 5, consulta=None) -> list[dict]:
        if not self.repo.config().get("fontes", {}).get("documentos", True):
            return []
        consulta = consulta if consulta is not None else self.vetorizador.consulta(texto)
        return self.repo.trechos(consulta, self.vetorizador.nome, k)

    def _indice_lexico(self) -> tuple[IndiceLexico | None, dict[int, dict]]:
        """O índice de palavras dos trechos, refeito quando a base de documentos muda."""
        with self._trava_lexico:
            if time.monotonic() - self._lexico_conferido > LEXICO_CONFERIR or self._lexico is None:
                assinatura = self.repo.assinatura_trechos(self.vetorizador.nome)
                if assinatura != self._lexico_assinatura:
                    trechos = {t["id"]: t for t in self.repo.todos_trechos(self.vetorizador.nome)}
                    self._lexico = IndiceLexico([(i, t["texto"]) for i, t in trechos.items()])
                    self._lexico_trechos, self._lexico_assinatura = trechos, assinatura
                self._lexico_conferido = time.monotonic()
            return self._lexico, self._lexico_trechos

    def documentos_da_pergunta(self, pergunta: str, consultas: list, palavras: str | None = None,
                               citados: list[str] | None = None) -> list[dict]:
        """Os trechos de documento para responder, pela busca híbrida.

        O significado (vetor) acha o assunto dito com outras palavras; as palavras (BM25) acham o termo
        exato que o vetor dilui. As duas listas se fundem por RRF (cada trecho soma 1/(60 + posição) em
        cada lista). Entram só os trechos que tratam do assunto por um dos dois lados: semelhança de
        significado ≥ MIN_SIM_DOC ou estar entre os primeiros por palavras. Numa conversa, vêm duas
        consultas de significado (a pergunta reescrita e ela junto com a anterior; um trecho achado pelas
        duas fica com a maior semelhança), `palavras` traz também a pergunta anterior e os documentos já
        citados disputam a vaga (sem passe livre: antes entravam com régua baixa e um documento errado
        puxava as respostas seguintes para ele). Trechos de texto igual (páginas duplicadas no site)
        entram uma vez só."""
        if not self.repo.config().get("fontes", {}).get("documentos", True):
            return []
        por_vetor = []
        for consulta in consultas:
            por_vetor += self.repo.trechos(consulta, self.vetorizador.nome, CANDIDATOS_DOC)
            if citados:
                por_vetor += self.repo.trechos_de(consulta, self.vetorizador.nome, citados, k=3)
        indice, todos = self._indice_lexico()
        por_palavras = indice.buscar(palavras or pergunta, CANDIDATOS_DOC) if indice else []

        nota: dict[int, float] = {}
        trecho: dict[int, dict] = {}
        aceito: set[int] = set()
        for posicao, d in enumerate(sorted(por_vetor, key=lambda d: -float(d["similaridade"]))):
            if d["id"] in trecho:
                continue
            trecho[d["id"]] = d
            nota[d["id"]] = 1 / (RRF_K + posicao)
            if float(d["similaridade"]) >= MIN_SIM_DOC:
                aceito.add(d["id"])
        for posicao, (i, _) in enumerate(por_palavras):
            if i not in todos:
                continue
            trecho.setdefault(i, {**todos[i], "similaridade": 0.0})
            nota[i] = nota.get(i, 0.0) + 1 / (RRF_K + posicao)
            if posicao < TOP_PALAVRAS:
                aceito.add(i)

        escolhidos, vistos, usado = [], set(), 0
        for i in sorted(aceito, key=lambda i: -nota[i]):
            d = trecho[i]
            corpo = re.sub(r"\s+", " ", d["texto"].split("\n", 1)[-1]).strip().lower()
            if corpo in vistos:
                continue
            texto = d["texto"][:TAM_DOC]
            if escolhidos and usado + len(texto) > ORCAMENTO_DOCS:
                break
            vistos.add(corpo)
            usado += len(texto)
            escolhidos.append({**d, "texto": texto, "relevancia": nota[i]})
            if len(escolhidos) >= MAX_DOCS:
                break
        return escolhidos

    def fontes_da_pergunta(self, pergunta: str, historico: list[dict] | None = None) -> list[dict]:
        """Até 3 casos resolvidos e os trechos de documento (busca híbrida), só os que tratam do assunto.

        Numa conversa, a pergunta já chega reescrita (completa), mas o 7B às vezes tira o assunto na
        reescrita ("quais usadas para ativar?" virou "quais parâmetros são usados para ativar?", sem a
        nota a prazo). Por isso a busca leva também a pergunta anterior: no significado, como uma segunda
        consulta; nas palavras, junto da pergunta."""
        historico = (historico or [])[-MAX_TURNOS:]
        consultas = [self.vetorizador.consulta(pergunta)]
        if historico:
            consultas.append(self.vetorizador.consulta(f"{pergunta} ({historico[-1]['pergunta']})"))
        fontes: list[dict] = []
        if self.repo.config().get("fontes", {}).get("tickets", True):
            linhas: dict[int, dict] = {}
            for consulta in consultas:  # o mesmo caso pode vir das duas buscas: fica a maior semelhança
                for linha in self.repo.por_vetor(consulta, self.vetorizador.nome, k=8):
                    if linha["ticket_id"] not in linhas or linha["similaridade"] > linhas[linha["ticket_id"]]["similaridade"]:
                        linhas[linha["ticket_id"]] = linha
            for linha in sorted(linhas.values(), key=lambda x: -x["similaridade"]):
                tipo, texto = solucao_do_caso(linha.get("mensagens_publicas"), linha.get("solucao_publica"))
                interna = limpar(linha.get("solucao_interna"))
                partes = [mascarar(linha["assunto"])]
                if tipo == "solucao":
                    partes.append(f"Solução: {mascarar(texto)[:TAM_CASO]}")
                if interna:
                    partes.append(f"Nota técnica: {mascarar(interna)[:TAM_CASO]}")
                if len(partes) > 1:  # caso sem solução descrita não ajuda a responder
                    fontes.append({"tipo": "caso", "id": linha["ticket_id"], "protocolo": linha["protocolo"], "titulo": f"Caso #{linha['ticket_id']}",
                                   "similaridade": float(linha["similaridade"]), "texto": "\n".join(partes)})
            fontes = [f for f in fontes if f["similaridade"] >= MIN_SIM_PERGUNTA][:MAX_CASOS]
        citados = sorted({f["fonte"] for t in historico for f in t.get("fontes") or [] if f.get("tipo") == "documento" and f.get("fonte")})
        palavras = " ".join([pergunta, *(t["pergunta"] for t in historico[-1:])])
        for d in self.documentos_da_pergunta(pergunta, consultas, palavras, citados):
            fontes.append({"tipo": "documento", "id": d["id"], "titulo": d["titulo"], "fonte": d["fonte"],
                           "similaridade": float(d["similaridade"]), "texto": d["texto"]})
        return fontes

    def perguntar(self, pergunta: str, ticket_id: int | None = None,
                  cancelado: Callable[[], bool] = lambda: False, historico: list[dict] | None = None) -> Iterator[dict]:
        """Eventos da resposta, para a tela mostrar enquanto é escrita:
        {"t": pedaço do texto}... e, no fim, {"fim": {fontes, encontrou, resposta, segundos, entendida}}.

        Numa conversa, a continuação é reescrita como pergunta completa. Se mesmo assim der "não encontrei"
        (o modelo às vezes devolve a pergunta sem reescrever), tenta de novo com o assunto da pergunta
        anterior junto. "Não encontrei" não mostra nada na tela, então a segunda tentativa não aparece."""
        inicio = time.perf_counter()
        historico = (historico or [])[-MAX_TURNOS:]
        contexto = ""
        if ticket_id:
            t, _ = self._ticket(ticket_id) if ticket_id in self.retrato.tickets else (self.repo.ticket(ticket_id), None)
            if t:
                contexto = f"TICKET EM ATENDIMENTO (#{ticket_id}):\n{mascarar(t.get('texto'))[:1500]}\n\n"

        candidatas = [self.pergunta_completa(pergunta, historico, cancelado)]
        if historico:
            junto = f"{pergunta.rstrip(' ?')} (sobre: {historico[-1]['pergunta'].rstrip(' ?')})?"
            if junto != candidatas[0]:
                candidatas.append(junto)

        for completa in candidatas:
            fontes = self.fontes_da_pergunta(completa, historico)
            if not fontes:
                continue
            resultado = {}
            # a reescrita pode ter perdido o assunto: o modelo vê também a pergunta anterior
            para_o_modelo = completa if not historico else (
                f"{completa}\n(A pergunta anterior desta conversa foi: \"{historico[-1]['pergunta']}\")")
            yield from self._responder(para_o_modelo, fontes, contexto, cancelado, resultado)
            if resultado["encontrou"]:
                yield {"fim": {**resultado, "segundos": round(time.perf_counter() - inicio, 1),
                               "entendida": completa if completa != pergunta else None}}  # a tela mostra como leu
                return
        yield {"fim": {"encontrou": False, "fontes": [], "resposta": "Não encontrei isso na base. Tente descrever de outro jeito.",
                       "segundos": round(time.perf_counter() - inicio, 1), "entendida": None}}

    def _responder(self, pergunta: str, fontes: list[dict], contexto: str,
                   cancelado: Callable[[], bool], resultado: dict) -> Iterator[dict]:
        """Escreve a resposta com as fontes; os pedaços saem como {"t": ...} e o resultado fica em `resultado`."""
        numeradas = "\n\n".join(f"[{i}] {f['titulo']}\n{f['texto']}" for i, f in enumerate(fontes, 1))
        # A primeira linha ("FONTES ÚTEIS: 2, 5") faz o modelo decidir quais fontes servem antes de
        # escrever. Medido em 04/10 com o 7B, mesmas fontes, temperatura 0: sem ela, 4 de 11 perguntas
        # difíceis certas (desistia com "não encontrei" tendo a resposta nas fontes); com ela, 10 de 11,
        # e as 4 perguntas sem resposta na base continuaram em "não encontrei". A linha não vai para a tela.
        pedido = (
            f"{contexto}FONTES:\n{numeradas}\n\nPERGUNTA: {pergunta}\n\n"
            "Na primeira linha, escreva FONTES ÚTEIS: e os números das fontes que ajudam a responder a pergunta "
            "(ou FONTES ÚTEIS: nenhuma). Depois, na linha seguinte, escreva a resposta. "
            "Responda só com o que está nas fontes, citando os números delas no texto, como [1]. "
            "Use as fontes que tratam do assunto da pergunta e deixe de lado as que tratam de outro assunto. "
            "Se pedirem uma lista (parâmetros, passos, motivos), traga os itens das fontes, um por linha, começando "
            "com hífen, explicando com suas palavras o que cada um faz, como em: - TP_ACTNP: libera a nota a prazo "
            "no PDV (padrão .F.) [2]. "
            "Use bloco ``` só para script, consulta SQL ou comando, copiado exatamente como está na fonte; "
            "parâmetros, campos e passos vão em texto, nunca em bloco ```. "
            "Se as fontes respondem só em parte, responda com o que há e diga o que falta. "
            f"Se nenhuma fonte ajuda, a resposta é apenas {NAO_ENCONTREI}."
        )

        bruto, texto, mostrado, uteis = "", "", "", None
        # temperatura 0: a mesma pergunta com as mesmas fontes dá a mesma resposta (com 0,2 a decisão
        # entre responder e "não encontrei" virava sorteio nas perguntas no limite)
        for pedaco in self.llm.escrever(SISTEMA, pedido, cancelado=cancelado, limite=800, temperatura=0, uso="pergunta"):
            bruto += pedaco
            uteis, texto = _separar_fontes_uteis(bruto)
            if uteis is None:  # ainda escrevendo a linha das fontes úteis
                continue
            # Segura o começo até saber se a resposta é só "não encontrei", e segura o fim enquanto ele
            # pode ser o começo do marcador: o marcador nunca aparece na tela.
            if so_marcador(texto, parcial=True):
                continue
            visivel = sem_marcador(texto[:len(texto) - _inicio_de_marcador(texto)])
            if len(visivel) > len(mostrado):
                yield {"t": visivel[len(mostrado):]}
                mostrado = visivel
        if uteis is None:  # acabou ainda na primeira linha (ex.: "FONTES ÚTEIS: nenhuma" sem quebra)
            uteis, texto = _separar_fontes_uteis(bruto, fim=True)
        if not so_marcador(texto) and len(sem_marcador(texto)) > len(mostrado):
            yield {"t": sem_marcador(texto)[len(mostrado):]}

        citadas = sorted({int(n) for grupo in re.findall(r"\[(\d+(?:\s*,\s*\d+)*)\]", texto)
                          for n in re.findall(r"\d+", grupo) if 1 <= int(n) <= len(fontes)})
        if not citadas:  # respondeu sem citar no texto: valem as fontes que ele disse que ajudam
            citadas = [n for n in uteis if 1 <= n <= len(fontes)]
        # "não encontrei" só quando a resposta é o marcador; no meio do texto ("NAO_ENCONTREI um script
        # específico, mas...") é uma ressalva de uma resposta que existe
        # "não encontrei" no começo e sem citar fonte é a resposta vazia escrita por extenso ("não encontrei.
        # As fontes não falam do iFood..."); com citação, é a ressalva de uma resposta parcial
        negou = bool(_COMECA_NEGANDO.match(texto)) and not citadas
        encontrou = bool(texto.strip()) and not so_marcador(texto) and not negou
        if encontrou and not citadas:
            citadas = [1]
        resultado.update(
            encontrou=encontrou,
            resposta=sem_marcador(_MARCADOR_NO_FIM.sub("", _normalizar(texto))).strip(),
            # o número de cada fonte é o mesmo citado no texto
            fontes=[{"n": n, **{k: fontes[n - 1].get(k) for k in ("tipo", "id", "titulo", "fonte", "protocolo")}}
                    for n in citadas] if encontrou else [],
        )

    def pergunta_completa(self, pergunta: str, historico: list[dict],
                          cancelado: Callable[[], bool] = lambda: False) -> str:
        """Numa conversa, reescreve a continuação ("qual o script usado?") como pergunta completa
        ("qual o script usado para corrigir o erro de chave duplicada?"). Testado com o 7B: a pergunta
        solta vira "não encontrei" quase sempre; a completa encontra a resposta."""
        if not historico:
            return pergunta
        conversa = "\n".join(
            f"Analista: {t['pergunta']}\nElo: {_CITACAO.sub('', t.get('resposta') or '(não encontrou)')[:TAM_TURNO]}"
            for t in historico)
        pedido = (
            f"CONVERSA:\n{conversa}\n\nÚLTIMA PERGUNTA DO ANALISTA: {pergunta}\n\n"
            "Reescreva a última pergunta como uma pergunta completa, que se entenda sem a conversa: acrescente só "
            "o assunto principal a que ela se refere (o erro, a rotina ou a funcionalidade de que se fala), com as "
            "mesmas palavras usadas pelo analista. Ex.: depois de \"quais parâmetros da nota a prazo?\", a pergunta "
            "\"e qual ativa?\" vira \"qual parâmetro ativa a nota a prazo?\". "
            "Não copie detalhes técnicos das respostas (campos, tabelas, valores). Não responda a pergunta. "
            "Se ela já é completa ou muda de assunto, repita-a como está. Devolva só a pergunta, em uma linha."
        )
        texto = ""
        try:
            for pedaco in self.llm.escrever(SISTEMA_REESCRITA, pedido, cancelado=cancelado, limite=80,
                                            temperatura=0, uso="reescrita"):
                texto += pedaco
        except Cancelado:
            raise
        except Exception:
            texto = ""
        linha = re.sub(r"^\s*(pergunta( completa| reescrita)?\s*:)\s*", "", texto.strip().splitlines()[0] if texto.strip() else "",
                       flags=re.IGNORECASE).strip(" \"'“”")
        if len(linha) < 3 or len(linha) > 400:  # saída estranha: a continuação com a pergunta anterior ao lado
            return f"{pergunta} ({historico[-1]['pergunta']})"
        return linha


# o modelo às vezes escreve o marcador com acento ou espaço ("NÃO_ENCONTREI", "NAO ENCONTREI")
_VARIANTES = re.compile(r"N[AÃ]O[_ ]ENCONTREI")
_COMECA_NEGANDO = re.compile(r"\s*\**n[aã]o[_ ]encontrei", re.IGNORECASE)
_MARCADOR_NO_FIM = re.compile(r"\s*\b" + NAO_ENCONTREI + r"[\s.]*(\[\d+(?:\s*,\s*\d+)*\][\s.]*)*$")
_INICIOS = (NAO_ENCONTREI, "NÃO_ENCONTREI", "NAO ENCONTREI", "NÃO ENCONTREI")


def _normalizar(texto: str) -> str:
    return _VARIANTES.sub(NAO_ENCONTREI, texto)


_FONTES_UTEIS = re.compile(r"\s*\**\s*FONTES [ÚU]TEIS\s*\**\s*:?\s*\**(.*?)\**\s*(\n|$)", re.IGNORECASE)


def _separar_fontes_uteis(bruto: str, fim: bool = False) -> tuple[list[int] | None, str]:
    """(números das fontes úteis, resposta), da primeira linha "FONTES ÚTEIS: 2, 5" que o modelo escreve
    antes da resposta. None enquanto essa linha ainda está sendo escrita; [] se o modelo pulou a linha
    (aí o texto todo é a resposta). Com fim, a linha vale mesmo sem a quebra depois dela."""
    linha = _FONTES_UTEIS.match(bruto)
    if linha and (linha.group(2) or fim):
        return [int(n) for n in re.findall(r"\d+", linha.group(1))], bruto[linha.end():].lstrip()
    cabeca = re.sub(r"[\s*:]", "", sem_acento(bruto)).upper()
    if not fim and (cabeca == "" or "FONTESUTEIS".startswith(cabeca) or cabeca.startswith("FONTESUTEIS")):
        return None, ""
    return [], bruto


def so_marcador(texto: str, parcial: bool = False) -> bool:
    """A resposta é só o "não encontrei" (com parcial, ainda pode virar só isso enquanto é escrita)."""
    nucleo = _normalizar(texto).strip()
    if parcial and len(nucleo) < len(NAO_ENCONTREI):
        return any(m.startswith(nucleo) for m in _INICIOS)
    return nucleo.startswith(NAO_ENCONTREI) and len(nucleo.replace(NAO_ENCONTREI, "").strip(" .:-\n")) < (40 if parcial else 1)


def sem_marcador(texto: str) -> str:
    return _normalizar(texto).replace(NAO_ENCONTREI, "não encontrei")


def _inicio_de_marcador(texto: str) -> int:
    """Quantos caracteres do fim podem ser o começo do marcador (ainda incompleto)."""
    for n in range(min(len(NAO_ENCONTREI) - 1, len(texto)), 0, -1):
        if any(m.startswith(texto[-n:]) for m in _INICIOS):
            return n
    return 0


def _publico(t: dict) -> dict:
    """Os campos de um ticket que a interface mostra."""
    return {
        "ticket_id": t["ticket_id"], "protocolo": t["protocolo"], "assunto": mascarar(t.get("assunto")),
        "status": t.get("status"), "status_base": t.get("status_base"), "categoria": t.get("categoria"),
        "justificativa": t.get("justificativa"),
        "empresa": t.get("empresa"), "solicitante": t.get("solicitante"),
        "ultima_resposta_em": t["ultima_resposta_em"].isoformat() if t.get("ultima_resposta_em") else None,
        "situacao": situacao(t.get("status_base"), t.get("status"), t.get("justificativa")),
        "urgencia": nome_urgencia(t.get("urgencia")), "produto": t.get("produto"),
        "servico": " › ".join(x for x in (t.get("modulo"), t.get("item")) if x) or None,
        "responsavel_id": t.get("responsavel_id"), "responsavel": t.get("responsavel_nome"),
        "time": t.get("time_responsavel"), "criado_em": t["criado_em"].isoformat(),
        "sla_solucao_em": t["sla_solucao_em"].isoformat() if t.get("sla_solucao_em") else None,
        "sla_pausado": t.get("sla_pausado"),
        "planner": planner(t.get("planner_titulo"), t.get("planner_url"), t),
    }


def _link_do_card(plano_id: str, card_id: str) -> str:
    from copilot_suporte.planner import link_da_tarefa

    return link_da_tarefa(plano_id, card_id)


def planner(titulo: str | None, url: str | None, linha: dict | None = None) -> dict | None:
    """O card do Planner do ticket.

    Com o card ligado (planner.py, pelo protocolo ou pelo título): link direto, coluna, andamento e responsáveis.
    Sem ele, o que a conversa interna cita: o título (sem o protocolo repetido no fim) e a URL, se veio crua;
    um link do Teams/Planner colado inteiro vira o link direto do card.
    """
    titulo = (titulo or "").replace(" ", " ").strip(" -:")
    titulo = re.sub(r"\s*-\s*\[?\d{12}\]?$", "", titulo)       # "... - [202609003155]": o protocolo do próprio ticket
    titulo = re.sub(r"^(\[[^\]]+\])\s*-\s*", r"\1 ", titulo)   # "[Acme] - Título" -> "[Acme] Título"
    linha = linha or {}
    if url:
        plano = re.search(r"plan(?:/|%2F)([\w-]{20,})", url, re.I)
        card = re.search(r"task(?:/|%2F)([\w-]{20,})", url, re.I)
        if plano and card:
            url = _link_do_card(plano.group(1), card.group(1))
    if linha.get("card_id"):
        concluido = linha.get("card_status") == "Completed"
        return {
            "titulo": mascarar(linha["card_titulo"]) or mascarar(titulo) or None,
            "url": _link_do_card(linha["card_plano_id"], linha["card_id"]),
            "coluna": linha.get("card_coluna"), "progresso": linha.get("card_progresso"), "concluido": concluido,
            "responsaveis": [n.strip() for n in (linha.get("card_responsaveis") or "").split(";") if n.strip()],
            "prazo": linha["card_prazo"].isoformat() if linha.get("card_prazo") else None,
            "concluido_em": linha["card_concluido_em"].isoformat() if linha.get("card_concluido_em") else None,
        }
    if not titulo and not url:
        return None
    return {"titulo": mascarar(titulo) or None, "url": url}


def _parecido(v: Vizinho) -> dict:
    if v.solucao_publica:
        rotulo, texto = "solucao", v.solucao_publica
    elif v.solucao_interna:
        rotulo, texto = "nota", v.solucao_interna
    elif v.orientacao:
        rotulo, texto = "orientacao", v.orientacao
    else:
        rotulo, texto = "nenhuma", ""
    return {
        "ticket_id": v.ticket_id, "protocolo": v.protocolo, "assunto": mascarar(v.assunto),
        "similaridade": v.similaridade, "servico": v.servico,
        "horas_resolucao": round(v.horas_resolucao, 1) if v.horas_resolucao is not None else None,
        "solucao_tipo": rotulo, "solucao": mascarar(texto)[:600],
    }
