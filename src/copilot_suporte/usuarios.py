"""Usuários, senhas e sessões do Elo. Também é a linha de comando de administração.

    uv run python -m copilot_suporte.usuarios preparar        cria as tabelas do produto
    uv run python -m copilot_suporte.usuarios agentes         lista os agentes do Movidesk (id e nome)
    uv run python -m copilot_suporte.usuarios criar EMAIL "Nome" admin --movidesk-id 123 --nivel N1
    uv run python -m copilot_suporte.usuarios listar
"""

import argparse
import getpass
import hashlib
import hmac
import secrets
import time
from datetime import timedelta

from psycopg.rows import dict_row

PAPEIS = ("analista", "gestor", "admin")
DURACAO_SESSAO = timedelta(hours=12)
CACHE_SESSAO = 60  # segundos: cada clique não precisa ir ao banco conferir o login


def hash_senha(senha: str, sal: bytes | None = None) -> str:
    sal = sal or secrets.token_bytes(16)
    chave = hashlib.scrypt(senha.encode(), salt=sal, n=2**14, r=8, p=1)
    return f"scrypt${sal.hex()}${chave.hex()}"


def conferir_senha(senha: str, guardado: str) -> bool:
    try:
        _, sal, chave = guardado.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_senha(senha, bytes.fromhex(sal)), guardado)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Usuarios:
    def __init__(self, banco):
        self.banco = banco
        self._sessoes: dict[str, tuple[float, dict]] = {}  # hash do token → (quando leu, usuário)

    def _esquecer(self, usuario_id: int) -> None:
        """Tira do cache as sessões de um usuário (senha trocada, papel mudado, desativado)."""
        for chave, (_, u) in list(self._sessoes.items()):
            if u["id"] == usuario_id:
                self._sessoes.pop(chave, None)

    def _cur(self, conn):
        return conn.cursor(row_factory=dict_row)

    def criar(self, email: str, nome: str, papel: str, senha: str, *, nivel=None, movidesk_id=None) -> dict:
        if papel not in PAPEIS:
            raise ValueError(f"papel deve ser um de {PAPEIS}")
        if len(senha) < 8:
            raise ValueError("a senha precisa de pelo menos 8 caracteres")
        with self.banco.conexao() as conn:
            return self._cur(conn).execute(
                """
                insert into app_usuarios (email, nome, papel, nivel, movidesk_id, senha_hash)
                values (lower(%s), %s, %s, %s, %s, %s)
                on conflict (email) do update set nome = excluded.nome, papel = excluded.papel,
                    nivel = excluded.nivel, movidesk_id = excluded.movidesk_id, senha_hash = excluded.senha_hash,
                    ativo = true
                returning id, email, nome, papel, nivel, movidesk_id
                """,
                (email.strip(), nome.strip(), papel, nivel, movidesk_id, hash_senha(senha)),
            ).fetchone()

    def listar(self) -> list[dict]:
        with self.banco.conexao() as conn:
            return self._cur(conn).execute(
                "select id, email, nome, papel, nivel, movidesk_id, ativo from app_usuarios order by nome"
            ).fetchall()

    def excluir(self, usuario_id: int) -> bool:
        """Apaga o cadastro. Sessões vão junto; feedbacks, perguntas e documentos enviados ficam, sem o autor."""
        with self.banco.conexao() as conn:
            conn.execute("update app_documentos set enviado_por = null where enviado_por = %s", (usuario_id,))
            apagado = conn.execute("delete from app_usuarios where id = %s returning id", (usuario_id,)).fetchone()
        self._esquecer(usuario_id)
        return apagado is not None

    def alterar(self, usuario_id: int, *, papel=None, ativo=None, movidesk_id=None, nivel=None) -> None:
        with self.banco.conexao() as conn:
            conn.execute(
                """
                update app_usuarios set papel = coalesce(%s, papel), ativo = coalesce(%s, ativo),
                       movidesk_id = coalesce(%s, movidesk_id), nivel = coalesce(%s, nivel) where id = %s
                """,
                (papel, ativo, movidesk_id, nivel, usuario_id),
            )
            if ativo is False:
                conn.execute("delete from app_sessoes where usuario_id = %s", (usuario_id,))
        self._esquecer(usuario_id)

    def trocar_senha(self, usuario_id: int, nova: str, *, senha_atual: str | None = None,
                     manter_token: str | None = None) -> bool:
        """Troca a senha e encerra as outras sessões do usuário.

        Com senha_atual (o próprio usuário trocando), só troca se ela conferir.
        Sem ela, é o admin definindo a senha de alguém. Devolve False se a atual não confere.
        """
        if len(nova) < 8:
            raise ValueError("a senha precisa de pelo menos 8 caracteres")
        with self.banco.conexao() as conn:
            linha = conn.execute("select senha_hash from app_usuarios where id = %s", (usuario_id,)).fetchone()
            if linha is None or (senha_atual is not None and not conferir_senha(senha_atual, linha[0])):
                return False
            conn.execute("update app_usuarios set senha_hash = %s where id = %s", (hash_senha(nova), usuario_id))
            # quem tinha a senha antiga deixa de entrar; só a sessão de quem trocou continua
            conn.execute(
                "delete from app_sessoes where usuario_id = %s and token_hash <> %s",
                (usuario_id, _hash_token(manter_token) if manter_token else ""),
            )
        self._esquecer(usuario_id)
        return True

    def entrar(self, email: str, senha: str) -> str | None:
        """Devolve o token da sessão nova, ou None se e-mail ou senha não conferem."""
        with self.banco.conexao() as conn:
            u = self._cur(conn).execute(
                "select id, senha_hash from app_usuarios where email = lower(%s) and ativo", (email.strip(),)
            ).fetchone()
            if not u or not conferir_senha(senha, u["senha_hash"]):
                return None
            token = secrets.token_urlsafe(32)
            conn.execute(
                "insert into app_sessoes (token_hash, usuario_id, expira_em) values (%s, %s, now() + %s)",
                (_hash_token(token), u["id"], DURACAO_SESSAO),
            )
            conn.execute("delete from app_sessoes where expira_em < now()")
            return token

    def da_sessao(self, token: str | None) -> dict | None:
        if not token:
            return None
        chave = _hash_token(token)
        guardado = self._sessoes.get(chave)
        if guardado and time.monotonic() - guardado[0] < CACHE_SESSAO:
            return guardado[1]
        with self.banco.conexao() as conn:
            usuario = self._cur(conn).execute(
                """
                select u.id, u.email, u.nome, u.papel, u.nivel, u.movidesk_id
                from app_sessoes s join app_usuarios u on u.id = s.usuario_id
                where s.token_hash = %s and s.expira_em > now() and u.ativo
                """,
                (chave,),
            ).fetchone()
        if usuario:
            self._sessoes[chave] = (time.monotonic(), usuario)
        else:
            self._sessoes.pop(chave, None)
        return usuario

    def sair(self, token: str) -> None:
        self._sessoes.pop(_hash_token(token), None)
        with self.banco.conexao() as conn:
            conn.execute("delete from app_sessoes where token_hash = %s", (_hash_token(token),))


def main() -> None:
    from copilot_suporte.banco import Banco

    parser = argparse.ArgumentParser(description="Administração de usuários do Elo.")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("preparar", help="cria as tabelas do produto (sql/010 e 011)")
    sub.add_parser("listar")
    sub.add_parser("agentes", help="agentes do Movidesk com tickets, para preencher --movidesk-id")
    criar = sub.add_parser("criar")
    criar.add_argument("email")
    criar.add_argument("nome")
    criar.add_argument("papel", choices=PAPEIS)
    criar.add_argument("--nivel", help="N1, N2 ou N3")
    criar.add_argument("--movidesk-id", help="id do agente no Movidesk (ver o comando agentes)")
    args = parser.parse_args()

    with Banco() as banco:
        usuarios = Usuarios(banco)
        if args.comando == "preparar":
            banco.executar_sql("010_produto.sql")
            banco.executar_sql("011_documentos.sql")
            banco.executar_sql("014_acessos.sql")
            banco.executar_sql("015_planner.sql")
            print("tabelas do produto prontas")
        elif args.comando == "listar":
            for u in usuarios.listar():
                print(f"{u['id']:>3}  {u['email']:<35} {u['papel']:<9} {u['nivel'] or '':<3} {u['movidesk_id'] or '-'}  {u['nome']}")
        elif args.comando == "agentes":
            with banco.conexao() as conn:
                linhas = conn.execute(
                    """
                    select responsavel_id, responsavel_nome, time_responsavel, count(*)
                    from limpo_tickets where responsavel_id is not null group by 1, 2, 3 order by 2
                    """
                ).fetchall()
            for agente_id, nome, time, n in linhas:
                print(f"{agente_id:>12}  {nome:<40} {time or '':<18} {n} tickets")
        else:
            senha = getpass.getpass("senha (mín. 8 caracteres): ")
            u = usuarios.criar(args.email, args.nome, args.papel, senha, nivel=args.nivel, movidesk_id=args.movidesk_id)
            print("usuário pronto:", u["email"], u["papel"])


if __name__ == "__main__":
    main()
