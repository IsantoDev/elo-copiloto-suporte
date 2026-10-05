"""Base de conhecimento: lê documentos e páginas públicas, divide em trechos e gera vetores.

    uv run python -m copilot_suporte.documentos

Fontes, todas dentro da pasta base_conhecimento/ (ou PASTA_DOCS no .env):
- arquivos .md, .txt, .pdf e .html (procedimentos internos, artigos exportados do Movidesk);
- urls.txt: uma URL por linha (documentação pública da TOTVS, artigos públicos da base do Movidesk);
- espacos.txt: espaços inteiros do Confluence publicados pelo Refined (ver refined.py).

Só os trechos novos ou alterados ganham vetor; fontes que sumiram são removidas.

A tela Base de conhecimento também grava arquivos nessa pasta (guardar) e cadastra cada um em
app_documentos. Só entram no RAG os arquivos com rag = true; os enviados pela tela começam fora
(ficam só para consulta) e entram quando alguém liga a chave (indexar_documento). Arquivos
colocados na pasta à mão, sem cadastro, são cadastrados já no RAG, como era antes da tela.
"""

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

import httpx

EXTENSOES = {".md", ".txt", ".pdf", ".html", ".htm"}
CONFIGURACAO = {"urls.txt", "espacos.txt"}  # listas de fontes da pasta, não documentos
LIMITE_BYTES = 25 * 1024 * 1024
TAMANHO = 1200  # caracteres por trecho
SOBREPOSICAO = 200


class _Texto(HTMLParser):
    """HTML → texto, ignorando script, style e navegação."""

    IGNORAR = {"script", "style", "nav", "header", "footer", "noscript", "svg"}

    def __init__(self):
        super().__init__()
        self.partes: list[str] = []
        self.titulo = ""
        self._pilha: list[str] = []

    def handle_starttag(self, tag, attrs):
        self._pilha.append(tag)
        if tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "tr", "div"}:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        while self._pilha and self._pilha.pop() != tag:
            pass

    def handle_data(self, data):
        if any(t in self.IGNORAR for t in self._pilha):
            return
        if self._pilha and self._pilha[-1] == "title":
            self.titulo += data.strip()
            return
        self.partes.append(data)


def html_para_texto(html: str) -> tuple[str, str]:
    leitor = _Texto()
    leitor.feed(html)
    texto = re.sub(r"[ \t\xa0]+", " ", "".join(leitor.partes))
    texto = re.sub(r"\n\s*\n+", "\n\n", texto).strip()
    return leitor.titulo, texto


def dividir(texto: str, tamanho: int = TAMANHO, sobreposicao: int = SOBREPOSICAO) -> list[str]:
    """Trechos de até `tamanho` caracteres, quebrando em parágrafos, com sobreposição."""
    paragrafos = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]
    trechos: list[str] = []
    atual = ""
    for p in paragrafos:
        while len(p) > tamanho:  # parágrafo gigante: corta no tamanho
            if atual:
                trechos.append(atual)
                atual = ""
            trechos.append(p[:tamanho])
            p = p[tamanho - sobreposicao:]
        if len(atual) + len(p) + 2 > tamanho and atual:
            trechos.append(atual)
            atual = atual[-sobreposicao:] + "\n\n" + p
        else:
            atual = f"{atual}\n\n{p}" if atual else p
    if atual:
        trechos.append(atual)
    return trechos


_TITULO_MD = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
_LINHA_TABELA = re.compile(r"^\|.*\|$")
_SEPARADOR = re.compile(r"^:?-{2,}:?$")
_VAZIO = {"", "—", "-", "−"}


def _celulas(linha: str) -> list[str]:
    return [c.strip().replace("**", "").replace("`", "") for c in linha.strip().strip("|").split("|")]


def dividir_markdown(titulo: str, texto: str, tamanho: int = TAMANHO) -> list[str]:
    """Trechos que se entendem sozinhos, pela estrutura do Markdown.

    Cada trecho começa pelo caminho "Página › Seção › Subseção". O texto corrido é dividido por seção.
    Cada linha de tabela vira um trecho próprio, com o cabeçalho junto ("Parâmetro: TP_ACTNP; O que faz:
    Libera nota a prazo no PDV"). Numa tabela de oito parâmetros, o vetor do trecho inteiro fala do
    assunto geral da tabela e a linha procurada se perde (medido em 04/10: 23º a 57º lugar); sozinha,
    a linha tem o vetor dela."""
    trechos: list[str] = []
    secoes: list[tuple[int, str]] = []  # (nível do #, nome)
    paragrafos: list[str] = []
    cabecalho: list[str] | None = None

    def caminho() -> str:
        return " › ".join([titulo, *(nome for _, nome in secoes)])

    def fechar_texto():
        if paragrafos:
            for parte in dividir("\n\n".join(paragrafos), tamanho - len(caminho()) - 2):
                trechos.append(f"{caminho()}\n\n{parte}")
            paragrafos.clear()

    bloco: list[str] = []
    for linha in texto.splitlines() + [""]:
        t = linha.strip()
        if _LINHA_TABELA.match(t):
            celulas = _celulas(t)
            if all(_SEPARADOR.match(c) for c in celulas if c):
                continue
            if cabecalho is None:  # a primeira linha da tabela é o cabeçalho
                if bloco:
                    paragrafos.append("\n".join(bloco))
                    bloco = []
                cabecalho = celulas
                continue
            pares = "; ".join(f"{nome}: {valor}" if nome else valor
                              for nome, valor in zip(cabecalho, celulas) if valor not in _VAZIO)
            if pares:
                trechos.append(f"{caminho()}\n{pares}")
            continue
        cabecalho = None
        titulo_md = _TITULO_MD.match(t)
        if titulo_md:
            if bloco:
                paragrafos.append("\n".join(bloco))
                bloco = []
            fechar_texto()
            nivel = len(titulo_md.group(1))
            while secoes and secoes[-1][0] >= nivel:
                secoes.pop()
            secoes.append((nivel, titulo_md.group(2).replace("**", "")))
            continue
        if t:
            bloco.append(linha.rstrip())
        elif bloco:
            paragrafos.append("\n".join(bloco))
            bloco = []
    fechar_texto()
    return trechos


def trechos_do_documento(titulo: str, texto: str, extensao: str) -> list[str]:
    """Os trechos que vão para o RAG, já com o título (ou o caminho da seção) na frente."""
    if extensao.lower().lstrip(".") == "md":
        return dividir_markdown(titulo, texto)
    return [f"{titulo}\n\n{t}" for t in dividir(texto)]


def ler_arquivo(caminho: Path) -> tuple[str, str]:
    """(título, texto) de um arquivo da pasta."""
    if caminho.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        texto = "\n\n".join(p.extract_text() or "" for p in PdfReader(caminho).pages)
        return caminho.stem, texto
    bruto = caminho.read_text(encoding="utf-8", errors="replace")
    if caminho.suffix.lower() in {".html", ".htm"}:
        titulo, texto = html_para_texto(bruto)
        return titulo or caminho.stem, texto
    return caminho.stem, bruto


def paginas(caminho: Path) -> int | None:
    if caminho.suffix.lower() != ".pdf":
        return None
    from pypdf import PdfReader

    return len(PdfReader(caminho).pages)


def nome_seguro(nome: str) -> str:
    """Só o nome do arquivo (sem pastas), sem caracteres que o Windows recusa."""
    nome = re.split(r"[\\/]", nome or "")[-1]
    nome = re.sub(r'[<>:"|?*\x00-\x1f]', "", nome).strip(" .")
    return nome[:150] or "documento"


def _cadastrar(conn, pasta: Path, fonte: str, titulo: str, texto: str, *, rag: bool,
               descricao: str | None = None, categoria: str | None = None, usuario_id: int | None = None) -> dict:
    caminho = pasta / fonte
    return conn.execute(
        """
        insert into app_documentos (fonte, titulo, descricao, categoria, extensao, tamanho, paginas, texto, rag, enviado_por)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (fonte) do update set titulo = excluded.titulo, tamanho = excluded.tamanho,
            paginas = excluded.paginas, texto = excluded.texto
        returning id
        """,
        (fonte, titulo, descricao, categoria, caminho.suffix.lower().lstrip("."), caminho.stat().st_size,
         paginas(caminho), texto.replace("\x00", ""), rag, usuario_id),
    ).fetchone()


def guardar(banco, pasta: Path, nome: str, conteudo: bytes, *, titulo: str | None = None,
            descricao: str | None = None, categoria: str | None = None, usuario_id: int | None = None) -> int:
    """Grava um arquivo enviado pela tela e o cadastra (fora do RAG). Devolve o id."""
    nome = nome_seguro(nome)
    destino = pasta / nome
    if destino.suffix.lower() not in EXTENSOES:
        raise ValueError("Formato não aceito. Use PDF, Markdown, TXT ou HTML.")
    if not conteudo:
        raise ValueError("O arquivo está vazio.")
    if len(conteudo) > LIMITE_BYTES:
        raise ValueError("O arquivo passa de 25 MB.")
    pasta.mkdir(parents=True, exist_ok=True)
    n = 2
    while destino.exists():  # nunca sobrescreve um documento que já está na base
        destino = pasta / f"{Path(nome).stem} ({n}){Path(nome).suffix}"
        n += 1
    destino.write_bytes(conteudo)
    try:
        titulo_lido, texto = ler_arquivo(destino)
    except Exception:
        destino.unlink(missing_ok=True)
        raise ValueError("Não consegui ler o arquivo. Ele está corrompido ou protegido por senha?")
    with banco.conexao() as conn:
        linha = _cadastrar(conn, pasta, destino.name, (titulo or "").strip() or titulo_lido, texto, rag=False,
                           descricao=descricao, categoria=categoria, usuario_id=usuario_id)
    return linha["id"] if isinstance(linha, dict) else linha[0]


def indexar_documento(banco, vetorizador, documento_id: int) -> int:
    """Põe (ou tira) um documento do RAG conforme a chave rag. Devolve quantos trechos ficaram."""
    with banco.conexao() as conn:
        doc = conn.execute("select fonte, titulo, texto, rag, extensao from app_documentos where id = %s",
                           (documento_id,)).fetchone()
    if doc is None:
        return 0
    fonte, titulo, texto, rag, extensao = (
        (doc["fonte"], doc["titulo"], doc["texto"], doc["rag"], doc["extensao"]) if isinstance(doc, dict) else doc)
    trechos = trechos_do_documento(titulo, texto, extensao) if rag else []
    vetores = vetorizador.documentos(trechos) if trechos else []
    with banco.conexao() as conn:
        conn.execute("delete from docs_trechos where fonte = %s", (fonte,))
        if trechos:
            conn.cursor().executemany(
                """
                insert into docs_trechos (fonte, titulo, ordem, texto, texto_hash, modelo, embedding)
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                [(fonte, titulo, ordem, t, hashlib.md5(t.encode()).hexdigest(), vetorizador.nome, v)
                 for ordem, (t, v) in enumerate(zip(trechos, vetores))],
            )
        conn.execute("update app_documentos set trechos = %s, indexado_em = case when %s then now() end where id = %s",
                     (len(trechos), rag, documento_id))
    return len(trechos)


def remover(banco, pasta: Path, documento_id: int) -> bool:
    """Apaga o arquivo, o cadastro e os trechos do RAG."""
    with banco.conexao() as conn:
        linha = conn.execute("delete from app_documentos where id = %s returning fonte", (documento_id,)).fetchone()
        if linha is None:
            return False
        fonte = linha["fonte"] if isinstance(linha, dict) else linha[0]
        conn.execute("delete from docs_trechos where fonte = %s", (fonte,))
    if not fonte.startswith(("http://", "https://")):  # página de site não tem arquivo na pasta
        (pasta / fonte).unlink(missing_ok=True)
    return True


def ler_url(url: str, http: httpx.Client) -> tuple[str, str]:
    resposta = http.get(url, follow_redirects=True)
    resposta.raise_for_status()
    titulo, texto = html_para_texto(resposta.text)
    return titulo or url, texto


def coletar(pasta: Path) -> list[tuple[str, str, str]]:
    """[(fonte, título, texto)] de todos os arquivos e URLs da pasta."""
    documentos = []
    for caminho in sorted(pasta.rglob("*")):
        if caminho.is_file() and caminho.suffix.lower() in EXTENSOES and caminho.name not in CONFIGURACAO:
            try:
                titulo, texto = ler_arquivo(caminho)
            except Exception as erro:
                print(f"  ignorado {caminho.name}: {type(erro).__name__}")
                continue
            documentos.append((str(caminho.relative_to(pasta)), titulo, texto))
    urls = pasta / "urls.txt"
    if urls.exists():
        with httpx.Client(timeout=30, headers={"User-Agent": "Elo-copiloto/0.1"}) as http:
            for linha in urls.read_text(encoding="utf-8").splitlines():
                url = linha.strip()
                if not url or url.startswith("#"):
                    continue
                try:
                    titulo, texto = ler_url(url, http)
                except Exception as erro:
                    print(f"  ignorada {url}: {type(erro).__name__}")
                    continue
                documentos.append((url, titulo, texto))
    return documentos


def indexar(banco, vetorizador, pasta: Path) -> dict:
    pasta.mkdir(parents=True, exist_ok=True)
    documentos = coletar(pasta)
    with banco.conexao() as conn:
        cadastro = {fonte: rag for fonte, rag in conn.execute("select fonte, rag from app_documentos").fetchall()}
        for fonte, titulo, texto in documentos:  # posto na pasta à mão: entra no RAG, como antes da tela
            if fonte not in cadastro and not fonte.startswith(("http://", "https://")):
                _cadastrar(conn, pasta, fonte, titulo, texto, rag=True)
                cadastro[fonte] = True
    documentos = [d for d in documentos if cadastro.get(d[0], True)]  # URLs do urls.txt sempre entram
    with banco.conexao() as conn:
        # páginas de site cadastradas (refined.py) têm a própria sincronização: ficam fora desta limpeza
        existentes = {
            (fonte, ordem): texto_hash
            for fonte, ordem, texto_hash in conn.execute(
                """
                select fonte, ordem, texto_hash from docs_trechos where modelo = %s
                  and fonte not in (select fonte from app_documentos where fonte ~ '^https?://')
                """,
                (vetorizador.nome,),
            ).fetchall()
        }

    novos = []
    vistos = set()
    for fonte, titulo, texto in documentos:
        extensao = "html" if fonte.startswith(("http://", "https://")) else Path(fonte).suffix
        for ordem, trecho in enumerate(trechos_do_documento(titulo, texto, extensao)):
            vistos.add((fonte, ordem))
            texto_hash = hashlib.md5(trecho.encode()).hexdigest()
            if existentes.get((fonte, ordem)) != texto_hash:
                novos.append((fonte, titulo, ordem, trecho, texto_hash))

    if novos:
        print(f"gerando {len(novos)} vetores de trechos")
        vetores = vetorizador.documentos([n[3] for n in novos])
        with banco.conexao() as conn:
            conn.cursor().executemany(
                """
                insert into docs_trechos (fonte, titulo, ordem, texto, texto_hash, modelo, embedding)
                values (%s, %s, %s, %s, %s, %s, %s)
                on conflict (fonte, ordem) do update set titulo = excluded.titulo, texto = excluded.texto,
                    texto_hash = excluded.texto_hash, modelo = excluded.modelo, embedding = excluded.embedding,
                    gerado_em = now()
                """,
                [(f, t, o, x, h, vetorizador.nome, v) for (f, t, o, x, h), v in zip(novos, vetores)],
            )

    sobrando = [chave for chave in existentes if chave not in vistos]
    if sobrando:
        with banco.conexao() as conn:
            conn.cursor().executemany("delete from docs_trechos where fonte = %s and ordem = %s", sobrando)

    with banco.conexao() as conn:
        conn.execute(
            """
            update app_documentos d set trechos = (select count(*) from docs_trechos t where t.fonte = d.fonte),
                indexado_em = case when d.rag then coalesce(d.indexado_em, now()) end
            """
        )

    resumo = {"documentos": len(documentos), "trechos_novos": len(novos), "removidos": len(sobrando)}
    print(resumo)
    return resumo


def main() -> None:
    from copilot_suporte import refined
    from copilot_suporte.banco import Banco
    from copilot_suporte.config import Config
    from copilot_suporte.vetorizador import Vetorizador

    config = Config()
    vetorizador = Vetorizador(config.modelo)
    with Banco() as banco:
        indexar(banco, vetorizador, config.pasta_docs)
        refined.sincronizar(banco, vetorizador, config.pasta_docs)


if __name__ == "__main__":
    main()
