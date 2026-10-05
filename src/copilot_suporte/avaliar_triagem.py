"""Validação temporal da triagem (módulo, urgência e risco de SLA).

    uv run python -m copilot_suporte.avaliar_triagem --dias 60

Para cada ticket resolvido nos últimos N dias, a triagem só enxerga os tickets
resolvidos ANTES de ele ser aberto (como seria na vida real). Compara com a linha
de base "chutar a classe mais comum" e mostra se a confiança está calibrada.
O resultado (só números agregados) vai para docs/avaliacao_triagem.md.
"""

import argparse
from collections import Counter
from datetime import timedelta

from copilot_suporte.config import RAIZ
from copilot_suporte.copiloto import _vizinho
from copilot_suporte.triagem import nome_urgencia, risco_sla, votar

FAIXAS = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01))


def _acuracia(pares) -> str:
    return f"{sum(a == b for a, b in pares) / len(pares):.0%}" if pares else "—"


def avaliar(repo, modelo: str, dias: int, peso_palavras: float | None = None, gravar: bool = True) -> str:
    teste = repo._todos(
        """
        select ticket_id, criado_em, sla_solucao_em, modulo, item, urgencia,
               coalesce(resolvido_em, fechado_em) > sla_solucao_em as estourou
        from limpo_tickets
        where status_base in ('Resolved', 'Closed') and criado_em >= now() - make_interval(days => %s)
        order by criado_em
        """,
        (dias,),
    )
    treino = repo._todos(
        "select modulo, item, urgencia from limpo_tickets where status_base in ('Resolved','Closed') "
        "and criado_em < now() - make_interval(days => %s)",
        (dias,),
    )
    vizinhos = {}
    ids = [t["ticket_id"] for t in teste]
    for i in range(0, len(ids), 50):
        for tid, linhas in repo.vizinhos(ids[i:i + 50], modelo, 10, so_passado=True, peso_palavras=peso_palavras).items():
            vizinhos[tid] = [_vizinho(linha) for linha in linhas]

    def servico(m, it):
        return f"{m} › {it}" if m and it else m or it

    base_servico = Counter(servico(t["modulo"], t["item"]) for t in treino).most_common(1)[0][0]
    base_modulo = Counter(t["modulo"] for t in treino).most_common(1)[0][0]
    base_urg = Counter(nome_urgencia(t["urgencia"]) for t in treino).most_common(1)[0][0]

    linhas = {"servico": [], "modulo": [], "urgencia": []}
    base = {"servico": [], "modulo": [], "urgencia": []}
    faixas = {nome: {f: [] for f in FAIXAS} for nome in linhas}
    sla = []
    for t in teste:
        vs = vizinhos.get(t["ticket_id"], [])
        reais = {"servico": servico(t["modulo"], t["item"]), "modulo": t["modulo"], "urgencia": nome_urgencia(t["urgencia"])}
        palpites = {
            "servico": votar([v for v in vs if v.servico], "servico"),
            "modulo": votar(vs, "modulo"),
            "urgencia": votar(vs, "urgencia"),
        }
        chutes = {"servico": base_servico, "modulo": base_modulo, "urgencia": base_urg}
        for nome, sug in palpites.items():
            if reais[nome] is None or sug is None:
                continue
            linhas[nome].append((sug.valor, reais[nome]))
            base[nome].append((chutes[nome], reais[nome]))
            for f in FAIXAS:
                if f[0] <= sug.confianca < f[1]:
                    faixas[nome][f].append((sug.valor, reais[nome]))
        if t["sla_solucao_em"] is not None and t["estourou"] is not None:
            # o risco como seria visto 1 hora depois da abertura
            r = risco_sla(vs, t["criado_em"], t["sla_solucao_em"], t["criado_em"] + timedelta(hours=1))
            if r.probabilidade is not None:
                sla.append((r.probabilidade, bool(t["estourou"]), r.nivel))

    saida = [
        "# Avaliação temporal da triagem",
        "",
        f"Tickets resolvidos abertos nos últimos {dias} dias: **{len(teste)}**. Cada um só enxerga os tickets "
        "resolvidos antes da sua abertura (10 vizinhos mais parecidos, do mesmo produto).",
        "",
        "## Sugestões",
        "",
        "| Campo | Acerto do Elo | Linha de base (classe mais comum) | Tickets avaliados |",
        "|---|---|---|---|",
    ]
    nomes = {"servico": "Módulo › item", "modulo": "Módulo", "urgencia": "Urgência"}
    for nome in linhas:
        saida.append(f"| {nomes[nome]} | {_acuracia(linhas[nome])} | {_acuracia(base[nome])} | {len(linhas[nome])} |")
    saida += ["", "## Calibração: acerto por faixa de confiança", "",
              "| Campo | " + " | ".join(f"{a:.0%}–{min(b, 1):.0%}" for a, b in FAIXAS) + " |",
              "|---|" + "---|" * len(FAIXAS)]
    for nome in linhas:
        celulas = [f"{_acuracia(faixas[nome][f])} (n={len(faixas[nome][f])})" for f in FAIXAS]
        saida.append(f"| {nomes[nome]} | " + " | ".join(celulas) + " |")

    if sla:
        taxa = sum(e for _, e, _ in sla) / len(sla)
        brier = sum((p - e) ** 2 for p, e, _ in sla) / len(sla)
        brier_base = sum((taxa - e) ** 2 for _, e, _ in sla) / len(sla)
        por_nivel = Counter(n for _, _, n in sla)
        estouro_nivel = {n: sum(e for _, e, m in sla if m == n) / c for n, c in por_nivel.items()}
        positivos = [p for p, e, _ in sla if e]
        negativos = [p for p, e, _ in sla if not e]
        auc = (
            sum((p > q) + 0.5 * (p == q) for p in positivos for q in negativos) / (len(positivos) * len(negativos))
            if positivos and negativos else None
        )
        saida += [
            "", "## Risco de estourar o SLA", "",
            f"Tickets com prazo: {len(sla)} · estouraram: {taxa:.1%}.",
            "",
            f"- Brier do Elo: {brier:.3f} · Brier chutando a taxa média: {brier_base:.3f} (menor é melhor)",
            f"- AUC (separa quem estoura de quem não estoura): {auc:.2f}" if auc is not None else "- AUC: —",
            "",
            "| Nível previsto | Tickets | Estouraram de fato |",
            "|---|---|---|",
        ]
        for nivel in ("alto", "medio", "baixo"):
            if nivel in por_nivel:
                saida.append(f"| {nivel} | {por_nivel[nivel]} | {estouro_nivel[nivel]:.0%} |")
        # a tela do ticket mostra estas taxas medidas, e não a fração bruta dos vizinhos
        gravar and repo.salvar_config("calibracao_sla", {
            "geral": round(taxa, 3), "auc": round(auc, 2) if auc is not None else None, "tickets": len(sla),
            "dias": dias, **{n: round(estouro_nivel[n], 3) for n in por_nivel},
        })
    return "\n".join(saida) + "\n"


def main() -> None:
    from copilot_suporte.banco import Banco
    from copilot_suporte.config import Config
    from copilot_suporte.repositorio import Repositorio

    parser = argparse.ArgumentParser(description="Validação temporal da triagem.")
    parser.add_argument("--dias", type=int, default=60)
    parser.add_argument("--peso-palavras", type=float, help="peso da busca por palavras junto da por significado")
    parser.add_argument("--so-medir", action="store_true", help="só imprime; não grava o relatório nem a calibração")
    args = parser.parse_args()
    with Banco() as banco:
        relatorio = avaliar(Repositorio(banco), Config().modelo, args.dias, args.peso_palavras, gravar=not args.so_medir)
    if args.so_medir:
        print(relatorio)
        return
    destino = RAIZ / "docs" / "avaliacao_triagem.md"
    destino.write_text(relatorio, encoding="utf-8")
    print(relatorio)
    print("gravado em", destino)


if __name__ == "__main__":
    main()
