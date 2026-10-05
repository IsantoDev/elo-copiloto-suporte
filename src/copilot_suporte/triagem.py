"""Triagem pelos vizinhos: categoria, urgência, risco de SLA e o tipo de rascunho.

Tudo aqui são funções puras sobre a lista de tickets resolvidos mais parecidos (kNN).
A ideia: um ticket novo tende a ter o módulo, a urgência e o tempo de resolução dos
casos parecidos. A confiança é a fração do peso (similaridade) que vota no vencedor.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

URGENTES = {"Crítico", "Alta"}


@dataclass(frozen=True)
class Vizinho:
    ticket_id: int
    similaridade: float
    modulo: str | None = None
    item: str | None = None
    urgencia: str | None = None
    categoria: str | None = None
    horas_resolucao: float | None = None  # da abertura até resolver (horas corridas)
    estourou_sla: bool | None = None
    assunto: str | None = None
    solucao_publica: str | None = None
    solucao_interna: str | None = None
    orientacao: str | None = None  # última orientação ao cliente quando o encerramento não descreve a solução
    protocolo: str | None = None

    @property
    def servico(self) -> str | None:
        """"Módulo › item", o nível que o analista reconhece."""
        if self.modulo and self.item:
            return f"{self.modulo} › {self.item}"
        return self.modulo or self.item


@dataclass(frozen=True)
class Sugestao:
    valor: str
    confianca: float  # 0 a 1
    votos: int


@dataclass(frozen=True)
class Risco:
    nivel: str  # estourado, alto, medio, baixo, sem_prazo, pausado
    probabilidade: float | None  # chance estimada de estourar
    horas_restantes: float | None
    previsao_horas: float | None  # tempo típico de resolução dos parecidos
    base: int  # quantos parecidos sustentam a estimativa


def nome_urgencia(urgencia: str | None) -> str | None:
    """'2 - Alta' → 'Alta'."""
    if not urgencia:
        return None
    return urgencia.split(" - ", 1)[-1].strip()


def votar(vizinhos: list[Vizinho], campo: str, minimo: int = 3) -> Sugestao | None:
    """Voto ponderado pela similaridade. None se poucos vizinhos têm o campo preenchido."""
    pesos: dict[str, float] = defaultdict(float)
    votos: dict[str, int] = defaultdict(int)
    for v in vizinhos:
        valor = getattr(v, campo)
        if campo == "urgencia":
            valor = nome_urgencia(valor)
        if valor:
            pesos[valor] += max(v.similaridade, 0.0)
            votos[valor] += 1
    total = sum(pesos.values())
    if sum(votos.values()) < minimo or total == 0:
        return None
    vencedor = max(pesos, key=pesos.get)
    return Sugestao(vencedor, round(pesos[vencedor] / total, 2), votos[vencedor])


def _ponderada(pares: list[tuple[float, float]]) -> float:
    total = sum(p for _, p in pares)
    return sum(v * p for v, p in pares) / total if total else 0.0


def mediana(valores: list[float]) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    meio = len(ordenados) // 2
    return ordenados[meio] if len(ordenados) % 2 else (ordenados[meio - 1] + ordenados[meio]) / 2


def risco_sla(
    vizinhos: list[Vizinho],
    criado_em: datetime,
    sla_em: datetime | None,
    agora: datetime,
    pausado: bool = False,
) -> Risco:
    """Chance de o ticket estourar o prazo de solução, pelo tempo que os parecidos levaram.

    Condiciona no tempo já decorrido: entre os parecidos que levaram mais do que este
    ticket já leva aberto, qual fração passou do prazo dele? (Um ticket aberto há 3 dias
    já não é comparável aos parecidos resolvidos em 1 hora.)
    """
    duracoes = [(v.horas_resolucao, v.similaridade) for v in vizinhos if v.horas_resolucao is not None]
    previsao = mediana([d for d, _ in duracoes])
    if pausado:
        return Risco("pausado", None, None, previsao, len(duracoes))
    if sla_em is None:
        return Risco("sem_prazo", None, None, previsao, len(duracoes))

    restantes = (sla_em - agora).total_seconds() / 3600
    if restantes <= 0:
        return Risco("estourado", 1.0, round(restantes, 1), previsao, len(duracoes))

    decorridas = (agora - criado_em).total_seconds() / 3600
    prazo_total = (sla_em - criado_em).total_seconds() / 3600
    ainda_abertos = [(d, p) for d, p in duracoes if d > decorridas]
    base = ainda_abertos if len(ainda_abertos) >= 3 else duracoes
    if len(base) < 3:
        return Risco("baixo" if restantes > 24 else "medio", None, round(restantes, 1), previsao, len(base))

    probabilidade = round(_ponderada([(1.0 if d > prazo_total else 0.0, p) for d, p in base]), 2)
    if probabilidade >= 0.5:
        nivel = "alto"
    elif probabilidade >= 0.25 or restantes < 24:
        nivel = "medio"
    else:
        nivel = "baixo"
    return Risco(nivel, probabilidade, round(restantes, 1), previsao, len(base))


def tipo_de_rascunho(vizinhos: list[Vizinho], limiar: float) -> tuple[str, list[Vizinho]]:
    """'solucao' só com confiança alta: o mais parecido passa do limiar, tem solução
    registrada e os vizinhos de cima concordam no módulo. Senão, 'analise'.

    Devolve o tipo e os casos que servem de base para o rascunho.
    """
    com_solucao = [v for v in vizinhos if v.solucao_publica or v.solucao_interna]
    if not com_solucao:
        return "analise", []
    por_problema = sorted(com_solucao, key=lambda v: -v.similaridade)
    topo = por_problema[0]
    if topo.similaridade >= limiar:
        tres = por_problema[:3]
        concordam = topo.modulo is None or sum(v.modulo == topo.modulo for v in tres) >= min(2, len(tres))
        if concordam:
            base = [v for v in por_problema if v.similaridade >= limiar - 0.05][:3]
            return "solucao", base
    return "analise", com_solucao[:3]


def eh_urgente(urgencia: str | None, risco: Risco) -> bool:
    return nome_urgencia(urgencia) in URGENTES or risco.nivel in ("alto", "estourado")


def situacao(status_base: str | None, status: str | None, justificativa: str | None) -> str:
    """Em que pé está o ticket: novo, atendimento, produto, cliente ou outros (outro time)."""
    if status_base == "New":
        return "novo"
    if status_base == "InAttendance":
        return "atendimento"
    motivo = f"{status or ''} {justificativa or ''}".lower()
    if "produto" in motivo or "desenvolvimento" in motivo:
        return "produto"
    if "cliente" in motivo:
        return "cliente"
    return "outros"
