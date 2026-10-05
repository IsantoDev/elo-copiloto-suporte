"""Medição de desempenho: quanto cada requisição gasta em banco, vetor e LLM.

Cada requisição ganha um "cronômetro" (contextvar). O banco, o vetorizador e o LLM somam o
tempo deles nele. No fim, a API devolve o cabeçalho Server-Timing (visível no DevTools do
navegador, aba Network → Timing) e guarda a medição em memória e em logs/desempenho.jsonl.
"""

import json
import threading
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_cronometro: ContextVar[dict | None] = ContextVar("cronometro", default=None)
_recentes: deque = deque(maxlen=5000)
_trava = threading.Lock()
_arquivo: Path | None = None


def configurar(arquivo: Path) -> None:
    global _arquivo
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    _arquivo = arquivo


def iniciar() -> dict:
    cronometro = {"etapas": {}, "contagem": {}}
    _cronometro.set(cronometro)
    return cronometro


@contextmanager
def etapa(nome: str):
    """Soma o tempo do bloco na etapa `nome` da requisição atual (se houver uma)."""
    inicio = time.perf_counter()
    try:
        yield
    finally:
        cronometro = _cronometro.get()
        if cronometro is not None:
            cronometro["etapas"][nome] = cronometro["etapas"].get(nome, 0.0) + time.perf_counter() - inicio
            cronometro["contagem"][nome] = cronometro["contagem"].get(nome, 0) + 1


def registrar(evento: dict) -> None:
    """Guarda uma medição (requisição ou geração do LLM)."""
    evento = {"em": round(time.time(), 3), **evento}
    with _trava:
        _recentes.append(evento)
        if _arquivo is not None:
            with _arquivo.open("a", encoding="utf-8") as f:
                f.write(json.dumps(evento, ensure_ascii=False) + "\n")


def server_timing(cronometro: dict, total: float) -> str:
    partes = [f'{nome};dur={segundos * 1000:.1f};desc="{nome} ×{cronometro["contagem"][nome]}"'
              for nome, segundos in cronometro["etapas"].items()]
    partes.append(f"total;dur={total * 1000:.1f}")
    return ", ".join(partes)


def _percentil(valores: list[float], p: float) -> float:
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(round(p * (len(ordenados) - 1))))]


def resumo(desde_segundos: float = 24 * 3600) -> dict:
    """p50 e p95 por rota (e por etapa), e as gerações do LLM, nas últimas horas."""
    limite = time.time() - desde_segundos
    with _trava:
        eventos = [e for e in _recentes if e["em"] >= limite]
    rotas: dict[str, list[dict]] = {}
    for e in eventos:
        if e.get("tipo") == "requisicao":
            rotas.setdefault(e["rota"], []).append(e)
    por_rota = []
    for rota, lista in sorted(rotas.items()):
        totais = [e["ms"] for e in lista]
        etapas = {}
        for nome in {n for e in lista for n in e["etapas"]}:
            valores = [e["etapas"].get(nome, 0) for e in lista]
            etapas[nome] = {"p50": round(_percentil(valores, 0.5), 1), "p95": round(_percentil(valores, 0.95), 1)}
        por_rota.append({"rota": rota, "n": len(lista), "p50": round(_percentil(totais, 0.5), 1),
                         "p95": round(_percentil(totais, 0.95), 1), "etapas": etapas})
    geracoes = {}
    for e in eventos:
        if e.get("tipo") == "llm":
            geracoes.setdefault(e["uso"], []).append(e)
    fluxos = {}
    for e in eventos:
        if e.get("tipo") == "fluxo":
            fluxos.setdefault(e["uso"], []).append(e)
    espera = [{"uso": uso, "n": len(lista),
               "primeira_palavra_p50": round(_percentil([e["primeira_ms"] for e in lista], 0.5)),
               "primeira_palavra_p95": round(_percentil([e["primeira_ms"] for e in lista], 0.95)),
               "total_p50": round(_percentil([e["total_ms"] for e in lista], 0.5))}
              for uso, lista in fluxos.items()]
    llm = []
    for uso, lista in geracoes.items():
        llm.append({
            "uso": uso, "n": len(lista),
            "primeira_palavra_p50": round(_percentil([e["primeira_ms"] for e in lista], 0.5)),
            "total_p50": round(_percentil([e["total_ms"] for e in lista], 0.5)),
            "tokens_por_segundo_p50": round(_percentil([e["tokens_s"] for e in lista], 0.5), 1),
            "interrompidas": sum(1 for e in lista if e.get("cancelado")),
        })
    return {"rotas": sorted(por_rota, key=lambda r: -r["p95"]), "llm": llm, "espera": espera, "eventos": len(eventos)}
