"""Mede o Pergunte de ponta a ponta com perguntas cuja resposta certa foi conferida na documentação.

    uv run python -m copilot_suporte.avaliar_pergunte [--so 1,3,5]

As perguntas ficam em base_conhecimento/avaliacao_pergunte.json (fora do Git, como a base):

    [{"pergunta": "...", "esperado": "<regex do que a resposta certa contém>",
      "historico": ["pergunta anterior da conversa", ...]},          # opcional
     {"pergunta": "...", "nao_encontrado": true}]                     # a base não tem a resposta

Para cada pergunta:
- busca: alguma fonte entregue ao modelo contém o esperado (o modelo teve a chance de acertar);
- resposta: a resposta contém o esperado (ou, sem resposta na base, o Elo disse que não encontrou);
- inventados: códigos (MV_X, TP_X, KP_X...) que a resposta cita mas nenhuma fonte entregue contém.

O resultado vai para logs/avaliacao_pergunte.jsonl, uma linha por rodada, para comparar versões.
"""

import argparse
import json
import re
import time
from datetime import datetime

from copilot_suporte.config import RAIZ, Config

CODIGO = re.compile(r"\b[A-Z][A-Z0-9]{1,5}_[A-Z0-9_]{2,}\b")
PEDE_CODIGO = re.compile(r"script|sql|consulta|comando|query|docker", re.I)


def bloco_indevido(pergunta: str, resposta: str) -> bool:
    """Bloco ``` numa resposta que não pediu script, SQL ou comando (ex.: parâmetros copiados crus)."""
    return "```" in resposta and not PEDE_CODIGO.search(pergunta)


def inventados(resposta: str, fontes: list[dict]) -> list[str]:
    texto = "\n".join(f.get("texto") or "" for f in fontes)
    return sorted({c for c in CODIGO.findall(resposta) if c not in texto})


def avaliar_caso(copiloto, caso: dict) -> dict:
    entregues: list[dict] = []
    original = copiloto.fontes_da_pergunta

    def espiar(*args, **kwargs):  # guarda o que o modelo recebeu na tentativa que valeu
        fontes = original(*args, **kwargs)
        entregues[:] = fontes
        return fontes

    historico = []
    for anterior in caso.get("historico", []):
        fim = next(e["fim"] for e in copiloto.perguntar(anterior) if "fim" in e)
        historico.append({"pergunta": anterior, "resposta": fim["resposta"], "fontes": fim["fontes"]})
    copiloto.fontes_da_pergunta = espiar
    try:
        inicio = time.perf_counter()
        fim = next(e["fim"] for e in copiloto.perguntar(caso["pergunta"], historico=historico) if "fim" in e)
    finally:
        copiloto.fontes_da_pergunta = original
    resposta = fim["resposta"] if fim["encontrou"] else ""
    if caso.get("nao_encontrado"):
        busca, acertou = None, not fim["encontrou"]
    else:
        esperado = re.compile(caso["esperado"], re.S | re.I)
        busca = any(esperado.search(f.get("texto") or "") for f in entregues)
        acertou = bool(esperado.search(resposta))
    return {
        "pergunta": caso["pergunta"], "entendida": fim.get("entendida"), "busca": busca, "acertou": acertou,
        "inventados": inventados(resposta, entregues), "bloco_indevido": bloco_indevido(caso["pergunta"], resposta),
        "segundos": round(time.perf_counter() - inicio, 1),
        "fontes": [f"{f['tipo']}: {f['titulo']}" for f in entregues], "resposta": resposta,
    }


def main() -> None:
    from copilot_suporte.banco import Banco
    from copilot_suporte.copiloto import Copiloto
    from copilot_suporte.llm import LLM
    from copilot_suporte.repositorio import Repositorio
    from copilot_suporte.vetorizador import Vetorizador

    argumentos = argparse.ArgumentParser()
    argumentos.add_argument("--so", help="números das perguntas (1 = a primeira), separados por vírgula")
    argumentos.add_argument("--rotulo", default="", help="nome desta rodada no log (ex.: 'busca híbrida')")
    argumentos.add_argument("--arquivo", default="avaliacao_pergunte.json", help="arquivo de perguntas na pasta da base")
    args = argumentos.parse_args()

    config = Config()
    casos = json.loads((config.pasta_docs / args.arquivo).read_text(encoding="utf-8"))
    escolhidos = [int(n) for n in args.so.split(",")] if args.so else range(1, len(casos) + 1)
    with Banco(autocommit=True) as banco:
        copiloto = Copiloto(Repositorio(banco), Vetorizador(config.modelo), LLM(config.ollama_url, config.llm))
        resultados = []
        for n in escolhidos:
            r = avaliar_caso(copiloto, casos[n - 1])
            resultados.append(r)
            marca = "ok " if r["acertou"] else "ERRO"
            busca = "-" if r["busca"] is None else ("sim" if r["busca"] else "não")
            bloco = " BLOCO```" if r["bloco_indevido"] else ""
            print(f"{n:2} {marca} busca={busca:3} inventados={r['inventados'] or '-'}{bloco} {r['segundos']:5.1f}s  {r['pergunta'][:70]}")
            if r["entendida"]:
                print(f"      entendida como: {r['entendida']}")

    positivos = [r for r in resultados if r["busca"] is not None]
    resumo = {
        "quando": datetime.now().isoformat(timespec="seconds"), "rotulo": args.rotulo, "arquivo": args.arquivo, "perguntas": len(resultados),
        "acertos": sum(r["acertou"] for r in resultados),
        "busca_achou": sum(bool(r["busca"]) for r in positivos), "com_resposta_na_base": len(positivos),
        "respostas_com_codigo_inventado": sum(bool(r["inventados"]) for r in resultados),
        "respostas_com_bloco_indevido": sum(r["bloco_indevido"] for r in resultados),
        "segundos_medio": round(sum(r["segundos"] for r in resultados) / max(1, len(resultados)), 1),
    }
    print(resumo)
    log = RAIZ / "logs" / "avaliacao_pergunte.jsonl"
    log.parent.mkdir(exist_ok=True)
    with log.open("a", encoding="utf-8") as arquivo:
        arquivo.write(json.dumps({**resumo, "casos": resultados}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
