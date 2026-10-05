"""Fila de geração: a GPU atende um pedido por vez, por ordem de quem está esperando.

Prioridades: alguém no Pergunte > rascunho pedido por um analista > pré-geração.
A pré-geração cede a GPU na hora: se alguém pede algo, o rascunho em segundo plano é
interrompido e volta para a fila. O texto parcial de cada rascunho fica disponível para
a tela mostrar o rascunho sendo escrito.
"""

import itertools
import queue
import threading
from collections.abc import Callable
from contextlib import contextmanager

from copilot_suporte.llm import Cancelado

AGORA, DEPOIS = 0, 1  # prioridade: pedido do analista × pré-geração


class FilaDeGeracao:
    def __init__(self, gerar: Callable[..., None]):
        """gerar(ticket_id, cancelado=..., escrever=...) faz o trabalho de um rascunho."""
        self._gerar = gerar
        self._fila: queue.PriorityQueue = queue.PriorityQueue()
        self._contador = itertools.count()
        self._pendentes: dict[int, tuple[int, int]] = {}  # ticket → (prioridade, ordem de chegada)
        self._atual: tuple[int, int] | None = None  # (ticket, prioridade) em geração
        self._parcial: dict[int, str] = {}
        self._erros: dict[int, str] = {}
        self._interativos = 0  # quantos Pergunte estão usando a GPU agora
        self._pausada = False  # pré-geração pausada (bandeja ou Configurações): só os pedidos do analista
        self._interromper = threading.Event()
        self._livre = threading.Condition()
        self._trava = threading.Lock()
        threading.Thread(target=self._trabalhar, name="geracao", daemon=True).start()

    def pedir(self, ticket_id: int, prioridade: int = AGORA) -> None:
        with self._trava:
            if prioridade == DEPOIS and self._pausada:
                return
            if self._atual and self._atual[0] == ticket_id:
                return
            anterior = self._pendentes.get(ticket_id)
            if anterior is not None and anterior[0] <= prioridade:
                return
            chave = (prioridade, next(self._contador))
            self._pendentes[ticket_id] = chave
            self._erros.pop(ticket_id, None)
            # um analista esperando tira a pré-geração da GPU
            if prioridade == AGORA and self._atual and self._atual[1] == DEPOIS:
                self._interromper.set()
        self._fila.put((*chave, ticket_id))

    def pausar(self, pausada: bool) -> None:
        """Pausada: interrompe a pré-geração em curso e esquece a que estava na fila."""
        with self._livre:
            with self._trava:
                if pausada == self._pausada:
                    return
                self._pausada = pausada
                if pausada:
                    for ticket_id in [t for t, chave in self._pendentes.items() if chave[0] == DEPOIS]:
                        del self._pendentes[ticket_id]
                    if self._atual and self._atual[1] == DEPOIS:
                        self._interromper.set()
            self._livre.notify_all()

    @property
    def pausada(self) -> bool:
        return self._pausada

    @contextmanager
    def interativo(self):
        """Enquanto alguém usa o Pergunte, a pré-geração para (e não começa outra)."""
        with self._trava:
            self._interativos += 1
            if self._atual and self._atual[1] == DEPOIS:
                self._interromper.set()
        try:
            yield
        finally:
            with self._livre:
                with self._trava:
                    self._interativos -= 1
                self._livre.notify_all()

    def posicao(self, ticket_id: int) -> int | None:
        """0 = gerando agora; n = posição na fila; None = não está na fila."""
        with self._trava:
            if self._atual and self._atual[0] == ticket_id:
                return 0
            if ticket_id not in self._pendentes:
                return None
            minha = self._pendentes[ticket_id]
            frente = sum(1 for chave in self._pendentes.values() if chave < minha)
            return frente + 1 + (1 if self._atual is not None else 0)

    def parcial(self, ticket_id: int) -> str:
        return self._parcial.get(ticket_id, "")

    def erro(self, ticket_id: int) -> str | None:
        return self._erros.get(ticket_id)

    def tamanho(self) -> int:
        return len(self._pendentes)

    def _trabalhar(self) -> None:
        while True:
            prioridade, ordem, ticket_id = self._fila.get()
            with self._trava:
                # entradas repetidas (o mesmo ticket subiu de prioridade): ignora as antigas
                if self._pendentes.get(ticket_id) != (prioridade, ordem):
                    continue
            if prioridade == DEPOIS:
                with self._livre:  # pré-geração só com a GPU sem ninguém no Pergunte
                    self._livre.wait_for(lambda: (self._interativos == 0 and not self._pausada)
                                         or self._pendentes.get(ticket_id) != (prioridade, ordem))
            with self._trava:
                if self._pendentes.get(ticket_id) != (prioridade, ordem):
                    continue
                del self._pendentes[ticket_id]
                self._atual = (ticket_id, prioridade)
                self._interromper.clear()
            try:
                self._gerar(
                    ticket_id,
                    cancelado=self._interromper.is_set,
                    escrever=lambda texto, tid=ticket_id: self._parcial.__setitem__(tid, texto),
                )
            except Cancelado:
                with self._trava:
                    self._atual = None
                self.pedir(ticket_id, DEPOIS)  # volta para a fila; recomeça quando a GPU liberar
            except Exception as erro:  # um ticket com problema não derruba a fila
                self._erros[ticket_id] = type(erro).__name__
                print(f"rascunho do #{ticket_id} falhou: {type(erro).__name__}: {erro}")
            finally:
                self._parcial.pop(ticket_id, None)
                with self._trava:
                    self._atual = None
