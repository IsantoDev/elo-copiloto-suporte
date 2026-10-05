"""Cliente do LLM local (Ollama), com a resposta chegando aos pedaços (streaming)."""

import json
import os
import time
from collections.abc import Callable, Iterator

import httpx

from copilot_suporte import medicao

CONTEXTO = 4096  # tokens; com 8192 o modelo 7B não cabe inteiro nos 6 GB da GPU e fica mais lento


class LLMIndisponivel(RuntimeError):
    pass


class Cancelado(Exception):
    """A geração foi interrompida (ex.: a pré-geração cedeu a GPU para alguém esperando)."""


class LLM:
    def __init__(self, url: str, modelo: str, *, tempo_limite: float = 600, cliente: httpx.Client | None = None):
        self.url = url
        self.modelo = modelo
        self._http = cliente or httpx.Client(timeout=httpx.Timeout(tempo_limite, connect=5))

    def disponivel(self) -> bool:
        """O Ollama responde e o modelo já foi baixado."""
        try:
            nomes = [m["name"] for m in self._http.get(f"{self.url}/api/tags", timeout=3).json()["models"]]
        except Exception:
            return False
        return any(n == self.modelo or n.split(":")[0] == self.modelo for n in nomes)

    def escrever(self, sistema: str, usuario: str, *, cancelado: Callable[[], bool] = lambda: False,
                 limite: int = 700, temperatura: float = 0.2, uso: str = "outro") -> Iterator[str]:
        """Os pedaços do texto, à medida que o modelo escreve.

        Se cancelado() virar True no meio, fecha a conexão (o Ollama para de gerar na hora)
        e levanta Cancelado.
        """
        corpo = {
            "model": self.modelo,
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
            "stream": True,
            # quanto tempo o modelo fica carregado depois do último pedido; no notebook, curto devolve a RAM
            "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "60m"),
            "options": {"temperature": temperatura, "num_ctx": CONTEXTO, "num_predict": limite},
        }
        # medição: espera até a primeira palavra, tempo total e velocidade (tokens por segundo)
        inicio, primeira, pedacos, final = time.perf_counter(), None, 0, {}
        try:
            with self._http.stream("POST", f"{self.url}/api/chat", json=corpo) as resposta:
                if resposta.status_code != 200:
                    raise LLMIndisponivel(f"Ollama respondeu {resposta.status_code}")
                for linha in resposta.iter_lines():
                    if cancelado():
                        final["cancelado"] = True
                        raise Cancelado()
                    if not linha:
                        continue
                    evento = json.loads(linha)
                    pedaco = evento.get("message", {}).get("content", "")
                    if pedaco:
                        primeira = primeira or time.perf_counter()
                        pedacos += 1
                        yield pedaco
                    if evento.get("done"):
                        final = evento
                        return
        except httpx.HTTPError as erro:
            raise LLMIndisponivel(f"Ollama não respondeu ({type(erro).__name__})") from erro
        finally:
            total = time.perf_counter() - inicio
            tokens = final.get("eval_count") or pedacos
            duracao = (final.get("eval_duration") or 0) / 1e9 or max(total - ((primeira or inicio) - inicio), 1e-6)
            medicao.registrar({
                "tipo": "llm", "uso": uso, "modelo": self.modelo, "cancelado": bool(final.get("cancelado")),
                "primeira_ms": round(((primeira or time.perf_counter()) - inicio) * 1000),
                "total_ms": round(total * 1000), "tokens": tokens, "tokens_s": round(tokens / duracao, 1),
                "prompt_tokens": final.get("prompt_eval_count"),
                "prompt_ms": round((final.get("prompt_eval_duration") or 0) / 1e6),
            })
