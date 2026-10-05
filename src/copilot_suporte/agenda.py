"""Tarefas diárias do container (no Windows é o Agendador de Tarefas que faz isso).

    python -m copilot_suporte.agenda

Todo dia às AGENDA_DOCUMENTOS (padrão 07:30, no fuso do TZ): base de conhecimento + documentação do Refined.
"""

import os
import time
from datetime import datetime, timedelta


def proxima(hora: str, agora: datetime) -> datetime:
    h, m = (int(x) for x in hora.split(":"))
    alvo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
    return alvo if alvo > agora else alvo + timedelta(days=1)


def diaria(vetorizador=None) -> None:
    """Laço da atualização diária. Com o vetorizador do servidor, não carrega uma segunda cópia do modelo."""
    from copilot_suporte import documentos, refined
    from copilot_suporte.banco import Banco
    from copilot_suporte.config import Config

    def rodar():
        if vetorizador is None:
            return documentos.main()
        config = Config()
        with Banco(tamanho_max=2) as banco:
            documentos.indexar(banco, vetorizador, config.pasta_docs)
            refined.sincronizar(banco, vetorizador, config.pasta_docs)

    hora = os.getenv("AGENDA_DOCUMENTOS", "07:30")
    while True:
        alvo = proxima(hora, datetime.now())
        print(f"base de conhecimento: próxima atualização {alvo:%d/%m %H:%M}", flush=True)
        time.sleep((alvo - datetime.now()).total_seconds())
        print(f"===== {datetime.now():%d/%m/%Y %H:%M}", flush=True)
        try:
            rodar()
        except Exception as erro:  # um dia que falha não derruba a agenda
            print("base de conhecimento:", type(erro).__name__, erro, flush=True)


if __name__ == "__main__":
    diaria()
