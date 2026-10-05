from copilot_suporte.documentos import dividir_markdown, trechos_do_documento
from copilot_suporte.lexico import IndiceLexico, termos

PAGINA = """Grupo que define o que cada PDV pode fazer.

## Funcionalidades liberadas no PDV

Cada parâmetro abaixo liga ou desliga uma operação.

| Parâmetro | Tipo | O que faz | Chave |
| --- | --- | --- | --- |
| `TP_ACTCF` | L | Libera **carta frete** no PDV. | — |
| `TP_ACTNP` | L | Libera **nota a prazo** no PDV. | — |

### Alçadas

Texto da subseção.

## Outra seção

Fim.
"""


def test_termos_sem_acento_sem_palavras_vazias_e_com_pares():
    t = termos("Quais parâmetros da Nota a Prazo?")
    assert "quais" not in t and "da" not in t and "a" not in t
    assert any(" " in x for x in t)  # o par "nota prazo" (em raízes)
    assert termos("parâmetro") == termos("parametros")[:1]


def test_bm25_pesa_a_palavra_rara():
    indice = IndiceLexico([
        (1, "Parâmetro de impressão do cupom. Parâmetro de e-mail."),
        (2, "Parâmetro: TP_ACTNP; O que faz: Libera nota a prazo no PDV."),
        (3, "Parâmetro de preço. Parâmetro de estoque. Parâmetro fiscal."),
    ])
    assert indice.buscar("quais parâmetros são usados na nota a prazo?")[0][0] == 2
    assert indice.buscar("iFood") == []


def test_dividir_markdown_linha_de_tabela_com_caminho_e_cabecalho():
    trechos = dividir_markdown("005 — PDV", PAGINA)
    linha = next(t for t in trechos if "TP_ACTNP" in t)
    assert linha == ("005 — PDV › Funcionalidades liberadas no PDV\n"
                     "Parâmetro: TP_ACTNP; Tipo: L; O que faz: Libera nota a prazo no PDV.")
    assert not any("| ---" in t or "TP_ACTCF" in t and "TP_ACTNP" in t for t in trechos)


def test_dividir_markdown_caminho_das_secoes():
    trechos = dividir_markdown("005 — PDV", PAGINA)
    assert trechos[0].startswith("005 — PDV\n\nGrupo que define")
    assert any(t.startswith("005 — PDV › Funcionalidades liberadas no PDV › Alçadas\n\nTexto") for t in trechos)
    # o ## seguinte fecha a subseção e a seção anterior
    assert any(t.startswith("005 — PDV › Outra seção\n\nFim.") for t in trechos)


def test_trechos_do_documento_pdf_mantem_titulo_na_frente():
    assert trechos_do_documento("Guia", "texto do guia", "pdf") == ["Guia\n\ntexto do guia"]


def test_separar_fontes_uteis():
    from copilot_suporte.copiloto import _separar_fontes_uteis as separar

    assert separar("FONTES Ú") == (None, "")                      # ainda escrevendo a linha
    assert separar("FONTES ÚTEIS: 2, 5\nO parâmetro [2]...") == ([2, 5], "O parâmetro [2]...")
    assert separar("**Fontes uteis:** 3\n\nResposta") == ([3], "Resposta")
    assert separar("FONTES ÚTEIS: nenhuma\nNAO_ENCONTREI") == ([], "NAO_ENCONTREI")
    assert separar("FONTES ÚTEIS: nenhuma", fim=True) == ([], "")
    assert separar("A venda ficou com ER [1].") == ([], "A venda ficou com ER [1].")  # pulou a linha
