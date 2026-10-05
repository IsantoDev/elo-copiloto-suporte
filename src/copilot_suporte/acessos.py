"""Acessos e credenciais dos clientes, com a senha cifrada.

A chave fica em ELO_CHAVE_ACESSOS no .env (fora do Git). Na primeira vez ela é criada e gravada lá.
Sem essa chave as senhas guardadas não podem ser lidas: guarde uma cópia dela num lugar seguro.
"""

import csv
import io
import json
import os

from cryptography.fernet import Fernet, InvalidToken
from psycopg.rows import dict_row

from copilot_suporte.config import RAIZ

VARIAVEL = "ELO_CHAVE_ACESSOS"
CAMPOS = ("cliente", "produto", "nome", "endereco", "usuario", "observacao")
# nomes aceitos na importação (planilhas e exportações costumam variar)
SINONIMOS = {
    "cliente": ("cliente", "empresa", "rede", "grupo"),
    "produto": ("produto", "produto a/produto b", "tipo_produto"),
    "nome": ("nome", "sistema", "titulo", "título", "acesso", "descricao", "descrição"),
    "endereco": ("endereco", "endereço", "url", "host", "ip", "link", "servidor"),
    "usuario": ("usuario", "usuário", "login", "user"),
    "senha": ("senha", "password", "pass", "chave"),
    "observacao": ("observacao", "observação", "obs", "notas", "nota", "comentario", "comentário"),
}


def _chave() -> bytes:
    chave = os.getenv(VARIAVEL)
    if chave:
        return chave.encode()
    chave = Fernet.generate_key().decode()
    with open(RAIZ / ".env", "a", encoding="utf-8") as env:  # .env fica fora do Git
        env.write(f"\n# chave das senhas da tela Acessos: sem ela as senhas guardadas não podem ser lidas\n{VARIAVEL}={chave}\n")
    os.environ[VARIAVEL] = chave
    return chave.encode()


class Acessos:
    def __init__(self, banco):
        self.banco = banco
        self._fernet = Fernet(_chave())

    def cifrar(self, senha: str | None) -> str | None:
        return self._fernet.encrypt(senha.encode()).decode() if senha else None

    def decifrar(self, cifrada: str | None) -> str:
        if not cifrada:
            return ""
        try:
            return self._fernet.decrypt(cifrada.encode()).decode()
        except InvalidToken:
            raise ValueError("Não consegui abrir esta senha: a chave do .env não é a que a gravou.")

    def _log(self, conn, acesso_id, usuario_id, acao) -> None:
        conn.execute("insert into app_acessos_log (acesso_id, usuario_id, acao) values (%s, %s, %s)",
                     (acesso_id, usuario_id, acao))

    def listar(self, busca: str | None = None) -> list[dict]:
        termo = f"%{(busca or '').strip().lower()}%"
        with self.banco.conexao() as conn:
            return conn.cursor(row_factory=dict_row).execute(
                """
                select id, cliente, produto, nome, endereco, usuario, observacao, alterado_em,
                       senha_cifrada is not null as tem_senha
                from app_acessos
                where lower(concat_ws(' ', cliente, produto, nome, endereco, usuario, observacao)) like %s
                order by lower(cliente), lower(nome)
                """,
                (termo,),
            ).fetchall()

    def salvar(self, dados: dict, usuario_id: int, acesso_id: int | None = None) -> int:
        """Cria ou altera. Na alteração, senha None mantém a atual; "" apaga."""
        valores = {c: (dados.get(c) or "").strip() or None for c in CAMPOS}
        with self.banco.conexao() as conn:
            if acesso_id is None:
                acesso_id = conn.execute(
                    """
                    insert into app_acessos (cliente, produto, nome, endereco, usuario, observacao, senha_cifrada, criado_por)
                    values (%(cliente)s, %(produto)s, %(nome)s, %(endereco)s, %(usuario)s, %(observacao)s, %(senha)s, %(por)s)
                    returning id
                    """,
                    {**valores, "senha": self.cifrar(dados.get("senha")), "por": usuario_id},
                ).fetchone()[0]
                self._log(conn, acesso_id, usuario_id, "criou")
            else:
                trocar_senha = dados.get("senha") is not None
                conn.execute(
                    f"""
                    update app_acessos set cliente = %(cliente)s, produto = %(produto)s, nome = %(nome)s,
                        endereco = %(endereco)s, usuario = %(usuario)s, observacao = %(observacao)s,
                        {"senha_cifrada = %(senha)s," if trocar_senha else ""} alterado_em = now()
                    where id = %(id)s
                    """,
                    {**valores, "senha": self.cifrar(dados.get("senha")), "id": acesso_id},
                )
                self._log(conn, acesso_id, usuario_id, "alterou")
        return acesso_id

    def revelar(self, acesso_id: int, usuario_id: int, acao: str = "revelou") -> str | None:
        with self.banco.conexao() as conn:
            linha = conn.execute("select senha_cifrada from app_acessos where id = %s", (acesso_id,)).fetchone()
            if linha is None:
                return None
            self._log(conn, acesso_id, usuario_id, acao)
        return self.decifrar(linha[0])

    def excluir(self, acesso_id: int, usuario_id: int) -> bool:
        with self.banco.conexao() as conn:
            apagado = conn.execute("delete from app_acessos where id = %s returning id", (acesso_id,)).fetchone()
            if apagado:
                self._log(conn, acesso_id, usuario_id, "excluiu")
        return apagado is not None

    def importar(self, conteudo: str, usuario_id: int) -> dict:
        """JSON (lista de objetos) ou CSV (com cabeçalho; vírgula ou ponto e vírgula)."""
        linhas = ler_importacao(conteudo)
        criados, ignorados = 0, 0
        for linha in linhas:
            if not linha.get("cliente") or not linha.get("nome"):
                ignorados += 1
                continue
            self.salvar(linha, usuario_id)
            criados += 1
        return {"criados": criados, "ignorados": ignorados}


def _normalizar(registro: dict) -> dict:
    """Leva as colunas do arquivo para os campos do Elo, pelos sinônimos."""
    chaves = {str(k).strip().lower(): v for k, v in registro.items()}
    saida = {}
    for campo, nomes in SINONIMOS.items():
        for nome in nomes:
            if chaves.get(nome) not in (None, ""):
                saida[campo] = str(chaves[nome]).strip()
                break
    return saida


def ler_importacao(conteudo: str) -> list[dict]:
    texto = conteudo.lstrip("﻿").strip()
    if texto.startswith(("[", "{")):
        dados = json.loads(texto)
        if isinstance(dados, dict):  # {"acessos": [...]} ou {"CLIENTE": [...]}
            lista = dados.get("acessos")
            if lista is None:
                lista = [{**item, "cliente": item.get("cliente") or cliente}
                         for cliente, itens in dados.items() if isinstance(itens, list) for item in itens]
            dados = lista
        return [_normalizar(d) for d in dados if isinstance(d, dict)]
    separador = ";" if texto.splitlines()[0].count(";") > texto.splitlines()[0].count(",") else ","
    return [_normalizar(d) for d in csv.DictReader(io.StringIO(texto), delimiter=separador)]
