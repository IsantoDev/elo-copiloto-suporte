import httpx
import pytest

from copilot_suporte.refined import baixar, ler_espacos, ler_indice, ler_pagina, limpar_markdown

SITE = "https://exemplo.refined.site/space/PRODUTOA"

INDICE = f"""# Produto A

Canonical: {SITE}/

- [P&#xE1;gina antiga]({SITE}/36700197.md)
- [Documenta&#xE7;&#xE3;o V2]({SITE}/982319121.md)
- [Atualiza&#xE7;&#xE3;o 16/09/2026]({SITE}/1007222786.md)
- [Documenta&#xE7;&#xE3;o V2]({SITE}/982319121.md)
"""

PAGINA = """---
title: "Configuração \\"Produto A\\" x ERP"
canonical: "https://exemplo.refined.site/space/PRODUTOA/982319121/Configura%C3%A7%C3%A3o"
format: markdown
---
Veja o [Cadastro de Empresas](https://empresa.atlassian.net/wiki/spaces/PRODUTOA/pages/1013186641).

![tela](/images/abc.png)



| Campo | O que faz |
| --- | --- |
"""


def test_ler_indice_filtra_pelo_id_e_nao_repete():
    nome, paginas = ler_indice(INDICE, minimo=900_000_000)
    assert nome == "Produto A"
    assert paginas == [(f"{SITE}/982319121", "Documentação V2"), (f"{SITE}/1007222786", "Atualização 16/09/2026")]


def test_ler_pagina_separa_cabecalho_e_tira_links_e_imagens():
    titulo, texto = ler_pagina(PAGINA)
    assert titulo == 'Configuração "Produto A" x ERP'
    assert texto.startswith("Veja o Cadastro de Empresas.")
    assert "http" not in texto and "images" not in texto
    assert "\n\n\n" not in texto
    assert "| Campo | O que faz |" in texto


def test_limpar_markdown_mantem_colchetes_sem_link():
    assert limpar_markdown("use [F12] e [ajuda](http://x)") == "use [F12] e ajuda"


def test_ler_espacos(tmp_path):
    assert ler_espacos(tmp_path) == []
    (tmp_path / "espacos.txt").write_text(f"# comentário\n\n{SITE}/  a_partir_de=900000000\n{SITE}\n", encoding="utf-8")
    assert ler_espacos(tmp_path) == [(SITE, 900_000_000), (SITE, 0)]
    (tmp_path / "espacos.txt").write_text(f"{SITE} ate=5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="a_partir_de"):
        ler_espacos(tmp_path)


def test_baixar_conta_falha_sem_perder_a_pagina_da_lista():
    def responder(pedido: httpx.Request) -> httpx.Response:
        caminho = pedido.url.path
        if caminho.endswith("PRODUTOA.md"):
            return httpx.Response(200, text=INDICE)
        if caminho.endswith("982319121.md"):
            return httpx.Response(200, text=PAGINA)
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(responder)) as http:
        nome, paginas, falhas, listadas = baixar(http, SITE, 900_000_000)
    assert nome == "Produto A"
    assert [(fonte, titulo) for fonte, titulo, _ in paginas] == [(f"{SITE}/982319121", 'Configuração "Produto A" x ERP')]
    assert falhas == 1
    # a que falhou continua listada: a sincronização não a remove da base
    assert listadas == {f"{SITE}/982319121", f"{SITE}/1007222786"}


def test_baixar_sem_indice_levanta_erro():
    with httpx.Client(transport=httpx.MockTransport(lambda p: httpx.Response(404))) as http:
        with pytest.raises(httpx.HTTPStatusError):
            baixar(http, SITE, 0)
