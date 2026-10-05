"""Espaços do Confluence publicados pelo Refined (a documentação do produto no site público).

    uv run python -m copilot_suporte.documentos    # roda junto com o resto da base

O Refined publica cada página também em Markdown (/space/<ESPAÇO>/<id>.md) e o espaço inteiro como
um índice (/space/<ESPAÇO>.md). Cada página vira um documento da base, já no RAG, e a fonte é o link
da página: a citação no Pergunte abre a página no site.

Os espaços ficam em base_conhecimento/espacos.txt (fora do Git), um por linha:

    https://empresa.refined.site/space/PRODUTOA  a_partir_de=900000000

a_partir_de (opcional) deixa de fora as páginas de id menor: no PRODUTOA, os ids 367xxxxx são a
documentação antiga e os da V2 começam em 9xxxxxxxx.

A cada sincronização: página nova entra no RAG; página alterada é reindexada; página que saiu do
espaço sai da base. Se alguém desliga "Usar no RAG" numa página, ela continua desligada.
"""

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from copilot_suporte.documentos import indexar_documento

ARQUIVO = "espacos.txt"
CABECALHOS = {"User-Agent": "Elo-copiloto/0.1"}
_ITEM = re.compile(r"^- \[(.*)\]\((https?://\S+?/space/[^/\s]+/(\d+))\.md\)\s*$", re.M)
_LINK = re.compile(r"(!?)\[([^\]]*)\]\([^)\s]*\)")


def ler_espacos(pasta: Path) -> list[tuple[str, int]]:
    """[(url do espaço, id mínimo)] do espacos.txt."""
    arquivo = pasta / ARQUIVO
    if not arquivo.exists():
        return []
    espacos = []
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        partes = linha.split()
        if not partes or partes[0].startswith("#"):
            continue
        minimo = 0
        for opcao in partes[1:]:
            if not opcao.startswith("a_partir_de="):
                raise ValueError(f"{ARQUIVO}: opção desconhecida {opcao!r} (só existe a_partir_de=<id>)")
            minimo = int(opcao.split("=", 1)[1])
        espacos.append((partes[0].rstrip("/"), minimo))
    return espacos


def limpar_markdown(texto: str) -> str:
    """Tira os endereços dos links (fica o nome) e as imagens: no RAG, URL só ocupa espaço do trecho."""
    texto = _LINK.sub(lambda m: "" if m.group(1) else m.group(2), html.unescape(texto))
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def ler_indice(texto: str, minimo: int = 0) -> tuple[str, list[tuple[str, str]]]:
    """(nome do espaço, [(url da página, título)]) do índice em Markdown, sem repetir página."""
    nome = re.search(r"^# (.+)$", texto, re.M)
    paginas: dict[str, tuple[str, str]] = {}
    for titulo, url, pagina_id in _ITEM.findall(texto):
        if int(pagina_id) >= minimo:
            paginas.setdefault(pagina_id, (url, html.unescape(titulo)))
    return (html.unescape(nome.group(1)).strip() if nome else ""), list(paginas.values())


def ler_pagina(texto: str) -> tuple[str, str]:
    """(título, texto) de uma página em Markdown, com o cabeçalho (front matter) separado."""
    titulo = ""
    cabecalho = re.match(r"---\n(.*?)\n---\n?", texto, re.S)
    if cabecalho:
        linha = re.search(r'^title: (".*")\s*$', cabecalho.group(1), re.M)
        if linha:
            try:
                titulo = json.loads(linha.group(1))
            except ValueError:
                titulo = linha.group(1).strip('"')
        texto = texto[cabecalho.end():]
    return titulo, limpar_markdown(texto)


def baixar(http: httpx.Client, url_espaco: str, minimo: int) -> tuple[str, list[tuple[str, str, str]], int, set[str]]:
    """(nome do espaço, [(fonte, título, texto)], falhas, fontes listadas no índice). Se o índice não
    vier, levanta o erro: sem o índice não dá para saber o que saiu do espaço."""
    resposta = http.get(url_espaco + ".md")
    resposta.raise_for_status()
    nome, paginas = ler_indice(resposta.text, minimo)

    def uma(pagina):
        url, titulo_indice = pagina
        try:
            r = http.get(url + ".md")
            r.raise_for_status()
        except httpx.HTTPError as erro:
            print(f"  ignorada {url}: {type(erro).__name__}")
            return url, None
        titulo, texto = ler_pagina(r.text)
        return url, (titulo or titulo_indice, texto)

    with ThreadPoolExecutor(4) as pool:
        lidas = list(pool.map(uma, paginas))
    # página que falhou não entra aqui, mas também não é removida da base (ver sincronizar)
    falhas = sum(1 for _, lida in lidas if lida is None)
    return nome, [(url, *lida) for url, lida in lidas if lida], falhas, {url for url, _ in lidas}


def sincronizar(banco, vetorizador, pasta: Path) -> dict:
    espacos = ler_espacos(pasta)
    resumo = {"paginas": 0, "novas": 0, "alteradas": 0, "removidas": 0, "falhas": 0}
    if not espacos:
        return resumo
    reindexar: list[int] = []
    with httpx.Client(timeout=30, headers=CABECALHOS, follow_redirects=True) as http:
        for url_espaco, minimo in espacos:
            try:
                nome, paginas, falhas, listadas = baixar(http, url_espaco, minimo)
            except httpx.HTTPError as erro:
                print(f"  espaço ignorado {url_espaco}: {type(erro).__name__}")
                resumo["falhas"] += 1
                continue
            resumo["paginas"] += len(paginas)
            resumo["falhas"] += falhas
            with banco.conexao() as conn:
                atuais = {
                    fonte: (doc_id, titulo, texto, rag, trechos)
                    for doc_id, fonte, titulo, texto, rag, trechos in conn.execute(
                        "select id, fonte, titulo, texto, rag, trechos from app_documentos where fonte like %s",
                        (url_espaco + "/%",),
                    ).fetchall()
                }
                for fonte, titulo, texto in paginas:
                    tamanho = len(texto.encode())
                    if fonte not in atuais:
                        doc_id = conn.execute(
                            """
                            insert into app_documentos (fonte, titulo, categoria, extensao, tamanho, texto, rag)
                            values (%s, %s, %s, 'md', %s, %s, true) returning id
                            """,
                            (fonte, titulo, nome or None, tamanho, texto),
                        ).fetchone()[0]
                        resumo["novas"] += 1
                        reindexar.append(doc_id)
                        continue
                    doc_id, titulo_antes, texto_antes, rag, trechos = atuais[fonte]
                    if (titulo, texto) != (titulo_antes, texto_antes):
                        conn.execute("update app_documentos set titulo = %s, tamanho = %s, texto = %s where id = %s",
                                     (titulo, tamanho, texto, doc_id))
                        resumo["alteradas"] += 1
                        if rag:
                            reindexar.append(doc_id)
                    elif rag and not trechos and texto:  # a indexação anterior não terminou
                        reindexar.append(doc_id)
                saiu = [(doc_id, fonte) for fonte, (doc_id, *_) in atuais.items() if fonte not in listadas]
                for doc_id, fonte in saiu:
                    conn.execute("delete from docs_trechos where fonte = %s", (fonte,))
                    conn.execute("delete from app_documentos where id = %s", (doc_id,))
                resumo["removidas"] += len(saiu)
    for n, doc_id in enumerate(reindexar, 1):
        print(f"  indexando página {n}/{len(reindexar)}", end="\r")
        indexar_documento(banco, vetorizador, doc_id)
    if reindexar:
        print()
    print({"refined": resumo})
    return resumo
