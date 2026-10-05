"""Busca por palavras nos trechos da base (BM25), somada à busca por significado no Pergunte.

O vetor acha o assunto pelo sentido ("comprar fiado" ≈ "venda a prazo"), mas dilui o termo exato:
numa tabela de oito parâmetros, "nota a prazo" é uma linha só e o vetor do trecho fala de "liberações
do PDV" em geral. Medido em 04/10: para "quais parâmetros são usados para nota a prazo?", o trecho certo
vinha em 23º a 57º no vetor; a frase exata o punha em 2º.

O BM25 pesa cada palavra pela raridade dela na base: "parâmetro" está em centenas de trechos e quase
não conta; "prazo" está em poucos e decide. Pares de palavras seguidas ("nota prazo") contam como termo
próprio, então a expressão inteira vale mais que as palavras soltas.

Tudo em memória (alguns milhares de trechos): nenhuma ida ao banco por pergunta.
"""

import math
import re
import unicodedata
from collections import Counter

import snowballstemmer

_RAIZ = snowballstemmer.stemmer("portuguese")
_PALAVRA = re.compile(r"[a-z0-9_]+")
PARADAS = set("""
a à ao aos as às com como da das de do dos e é em entre essa esse esta este eu foi há isso ja já lhe mais mas me
mesmo meu minha muito na nas não nem no nos nós o os ou para pela pelas pelo pelos por qual quais quando que quem
se sem ser seu seus só sua suas também te tem têm tu um uma umas uns vai você vocês está estão era são ter fazer
faz faço pra pro onde porque por que sobre cada eles elas ele ela isto aquilo aquele aquela
""".split())


def sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


_PARADAS = {sem_acento(p) for p in PARADAS}


def termos(texto: str) -> list[str]:
    """Raízes das palavras (sem acento, sem palavras vazias) e os pares de raízes vizinhas."""
    palavras = [p for p in _PALAVRA.findall(sem_acento(texto.lower())) if p not in _PARADAS]
    raizes = _RAIZ.stemWords(palavras)
    return raizes + [f"{a} {b}" for a, b in zip(raizes, raizes[1:])]


class IndiceLexico:
    """BM25 sobre [(id, texto)]. k1 e b nos valores de costume."""

    def __init__(self, itens: list[tuple[int, str]], k1: float = 1.2, b: float = 0.75):
        self.ids = [i for i, _ in itens]
        self.frequencias = [Counter(termos(texto)) for _, texto in itens]
        self.tamanhos = [sum(f.values()) for f in self.frequencias]
        self.media = sum(self.tamanhos) / max(1, len(itens))
        n = len(itens)
        presenca = Counter(t for f in self.frequencias for t in f)
        self.idf = {t: math.log(1 + (n - q + 0.5) / (q + 0.5)) for t, q in presenca.items()}
        self.k1, self.b = k1, b

    def buscar(self, consulta: str, k: int = 40) -> list[tuple[int, float]]:
        """[(id, nota)] dos k trechos de maior nota; só os que têm alguma palavra da consulta."""
        busca = [t for t in set(termos(consulta)) if t in self.idf]
        if not busca:
            return []
        notas = []
        for posicao, freq in enumerate(self.frequencias):
            nota = 0.0
            for termo in busca:
                f = freq.get(termo)
                if f:
                    norma = 1 - self.b + self.b * self.tamanhos[posicao] / self.media
                    nota += self.idf[termo] * f * (self.k1 + 1) / (f + self.k1 * norma)
            if nota:
                notas.append((nota, posicao))
        notas.sort(reverse=True)
        return [(self.ids[p], nota) for nota, p in notas[:k]]
