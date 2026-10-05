"""Configuração lida do .env, num lugar só."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

RAIZ = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Config:
    # repr=False: o token nunca aparece se a configuração for impressa ou cair num log.
    movidesk_token: str = field(default_factory=lambda: os.getenv("MOVIDESK_TOKEN", ""), repr=False)
    # Endereço do Movidesk da empresa; fica no .env para não aparecer no repositório público.
    movidesk_url: str = field(default_factory=lambda: os.getenv("MOVIDESK_URL", "").rstrip("/"))
    modelo: str = "google/embeddinggemma-300m"
    # LLM local (Ollama): nada sai da máquina.
    ollama_url: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"))
    llm: str = field(default_factory=lambda: os.getenv("OLLAMA_MODELO", "qwen2.5:7b"))
    # Pasta com a base de conhecimento (md, txt, pdf, html) e o urls.txt das páginas públicas.
    pasta_docs: Path = field(default_factory=lambda: Path(os.getenv("PASTA_DOCS", RAIZ / "base_conhecimento")))
    # Gera os rascunhos da fila aberta em segundo plano, para o analista já encontrá-los prontos.
    pregerar: bool = field(default_factory=lambda: os.getenv("PREGERAR", "1") == "1")
