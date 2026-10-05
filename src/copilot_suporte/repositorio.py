"""Consultas do produto (Elo): fila, ticket, vizinhos, rascunhos, feedback, painel e configurações.

O Banco (banco.py) cuida do pool e do pipeline; aqui ficam as consultas que a API usa.
"""

import time

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ABERTOS = ("New", "InAttendance", "Stopped")

CAMPOS_TICKET = """
    t.ticket_id, t.assunto, t.categoria, t.urgencia, t.status, t.status_base, t.justificativa, t.criado_em,
    t.empresa, t.solicitante,
    t.atualizado_em, t.produto, t.modulo, t.item, t.time_responsavel, t.responsavel_id,
    t.responsavel_nome, t.sla_solucao_em, t.sla_pausado, t.resolvido_em,
    to_char(t.criado_em at time zone 'America/Sao_Paulo', 'YYYYMM') || lpad(t.ticket_id::text, 6, '0') as protocolo,
    pl.planner_titulo, pl.planner_url,
    pc.id as card_id, pc.plano_id as card_plano_id, pc.titulo as card_titulo, pc.coluna as card_coluna,
    pc.status as card_status, pc.progresso as card_progresso, pc.responsaveis as card_responsaveis,
    pc.prazo as card_prazo, pc.concluido_em as card_concluido_em
"""

# O card do Planner (Teams) que o ticket de "Time de produto" deve citar numa conversa interna.
# A API entrega a ação em texto puro: o link vira só o título ("Planner - [ACME] ..."), e o
# endereço só aparece quando alguém colou a URL crua.  : o Movidesk põe espaço não separável.
JOIN_PLANNER = r"""
    left join lateral (
        select trim(substring(a.descricao from '(?i)planner[\s ]*[-:][\s ]*(?:planner[\s ]*-[\s ]*)?([^\n]+)')) as planner_titulo,
               substring(a.descricao from '(?i)(https?://[^\s "<>]*(?:planner|tasks\.office)[^\s "<>]*)') as planner_url
        from limpo_acoes a
        where a.ticket_id = t.ticket_id and a.descricao ~* 'planner[\s ]*[-:]|https?://[^\s]*(planner|tasks\.office)'
        order by (a.visibilidade = 'interna') desc, a.criado_em desc
        limit 1
    ) pl on true
    -- o card do Planner ligado a este ticket (planner.py: pelo protocolo citado no card ou pelo título)
    left join app_planner_vinculos pv on pv.ticket_id = t.ticket_id
    left join app_planner_cards pc on pc.id = pv.card_id
"""

# Os k resolvidos mais parecidos com cada ticket da lista, do mesmo produto (Produto A × Produto B).
# so_passado: na avaliação temporal, só vale o que já existia quando o ticket foi aberto.
# Busca híbrida: os 40 mais parecidos por significado (vetores), reordenados juntando a posição de cada
# um na busca por palavras do ticket (busca_palavras: problema + solução). RRF: 1 / (60 + posição).
# "similaridade" continua sendo a do vetor do problema: é a régua do limiar e da calibração.
_FILTRO_CANDIDATO = """
          t2.ticket_id <> t.ticket_id
          and t2.status_base in ('Resolved', 'Closed')
          and (t.produto is null or t2.produto is not distinct from t.produto)
          and (not %(so_passado)s or t2.resolvido_em < t.criado_em)
"""
SQL_VIZINHOS = """
    select t.ticket_id as origem, n.*
    from limpo_tickets t
    join vetores_problemas v on v.ticket_id = t.ticket_id and v.modelo = %(modelo)s
    left join limpo_problemas p on p.ticket_id = t.ticket_id
    cross join lateral (
        with por_vetor as (
            select v2.ticket_id, row_number() over (order by v2.embedding <=> v.embedding) as pos
            from vetores_problemas v2
            join limpo_tickets t2 on t2.ticket_id = v2.ticket_id
            where v2.modelo = %(modelo)s and """ + _FILTRO_CANDIDATO + """
            order by v2.embedding <=> v.embedding
            limit %(candidatos)s
        ), consulta as (  -- as palavras do ticket, qualquer uma (OU), não todas
            select to_tsquery('portuguese', nullif(replace(
                plainto_tsquery('portuguese', left(coalesce(p.texto, t.assunto, ''), 800))::text, ' & ', ' | '), '')) as q
        ), por_palavras as (  -- as palavras só reordenam os candidatos do significado (sem varrer o índice)
            select pv.ticket_id, row_number() over (order by ts_rank_cd(b.tsv, c.q, 32) desc) as pos
            from por_vetor pv
            join busca_palavras b on b.ticket_id = pv.ticket_id
            cross join consulta c
            where %(peso_palavras)s > 0 and b.tsv @@ c.q
            order by ts_rank_cd(b.tsv, c.q, 32) desc
            limit 5  -- só os que mais citam as palavras ganham reforço (senão ele se dilui entre os 40)
        ), juntos as (
            select ticket_id, sum(nota) as afinidade
            from (select ticket_id, 1.0 / (60 + pos) as nota from por_vetor
                  union all
                  select ticket_id, %(peso_palavras)s / (60 + pos) from por_palavras) x
            group by ticket_id
        )
        select t2.ticket_id, 1 - (v2.embedding <=> v.embedding) as similaridade, j.afinidade,
               t2.modulo, t2.item, t2.urgencia, t2.categoria, t2.assunto,
               extract(epoch from coalesce(t2.resolvido_em, t2.fechado_em) - t2.criado_em) / 3600 as horas_resolucao,
               case when t2.sla_solucao_em is null then null
                    else coalesce(t2.resolvido_em, t2.fechado_em) > t2.sla_solucao_em end as estourou_sla,
               {textos}
               to_char(t2.criado_em at time zone 'America/Sao_Paulo', 'YYYYMM') || lpad(t2.ticket_id::text, 6, '0') as protocolo
        from juntos j
        join limpo_tickets t2 on t2.ticket_id = j.ticket_id
        join vetores_problemas v2 on v2.ticket_id = t2.ticket_id and v2.modelo = %(modelo)s
        {join_solucoes}
        order by j.afinidade desc
        limit %(k)s
    ) n
    where t.ticket_id = any(%(ids)s)
    order by t.ticket_id, n.afinidade desc
"""
PESO_PALAVRAS = 0.0  # padrão: só significado (a triagem); os casos parecidos pedem 0,5 (copiloto.py)
CANDIDATOS = 40

SQL_TEXTO_LIVRE = """
    select t.ticket_id, 1 - (v.embedding <=> %(consulta)s) as similaridade, t.assunto, t.status_base,
           t.modulo, t.item, s.solucao_publica, s.solucao_interna, s.mensagens_publicas,
           to_char(t.criado_em at time zone 'America/Sao_Paulo', 'YYYYMM') || lpad(t.ticket_id::text, 6, '0') as protocolo
    from vetores_problemas v
    join limpo_tickets t on t.ticket_id = v.ticket_id
    left join limpo_solucoes s on s.ticket_id = v.ticket_id
    where v.modelo = %(modelo)s
    order by v.embedding <=> %(consulta)s
    limit %(k)s
"""


CONFIG_SEGUNDOS = 30  # a configuração quase nunca muda; não vale uma ida ao banco por clique


class Repositorio:
    def __init__(self, banco):
        self.banco = banco
        self._config: tuple[float, dict] | None = None

    def _todos(self, sql: str, params=None) -> list[dict]:
        with self.banco.conexao() as conn:
            return conn.cursor(row_factory=dict_row).execute(sql, params).fetchall()

    def _um(self, sql: str, params=None) -> dict | None:
        linhas = self._todos(sql, params)
        return linhas[0] if linhas else None

    def _executar(self, sql: str, params=None):
        with self.banco.conexao() as conn:
            cur = conn.execute(sql, params)
            return cur.fetchone() if cur.description else None

    # ---------- fila e ticket ----------

    def fila(self, responsavel_id: str | None = None, *, com_texto: bool = False) -> list[dict]:
        filtro = "and t.responsavel_id = %(resp)s" if responsavel_id else ""
        texto = ", p.texto" if com_texto else ""
        return self._todos(
            f"""
            select {CAMPOS_TICKET}, r.tipo as rascunho_tipo, md5(p.texto) as texto_hash{texto},
                   -- a última mensagem pública do analista: "há quanto tempo o cliente espera um retorno"
                   (select max(a.criado_em) from limpo_acoes a
                     where a.ticket_id = t.ticket_id and a.autor_perfil = 'agente' and a.visibilidade = 'publica'
                   ) as ultima_resposta_em
            from limpo_tickets t{JOIN_PLANNER}
            left join limpo_problemas p on p.ticket_id = t.ticket_id
            left join app_rascunhos r on r.ticket_id = t.ticket_id
            where t.status_base = any(%(abertos)s) {filtro}
            order by t.criado_em desc
            """,
            {"abertos": list(ABERTOS), "resp": responsavel_id},
        )

    def analistas(self) -> list[dict]:
        """Agentes com tickets abertos, para o filtro "Ver tickets de"."""
        return self._todos(
            """
            select responsavel_id as id, responsavel_nome as nome, time_responsavel as time, count(*) as abertos
            from limpo_tickets
            where status_base = any(%s) and responsavel_id is not null
            group by 1, 2, 3 order by 2
            """,
            (list(ABERTOS),),
        )

    def ticket(self, ticket_id: int) -> dict | None:
        return self._um(
            f"""
            select {CAMPOS_TICKET}, p.texto, md5(p.texto) as texto_hash,
                   s.solucao_publica, s.solucao_interna, s.mensagens_publicas,
                   (select count(*) from app_rascunhos r where t.ticket_id = any(r.base)) as vezes_base
            from limpo_tickets t{JOIN_PLANNER}
            left join limpo_problemas p on p.ticket_id = t.ticket_id
            left join limpo_solucoes s on s.ticket_id = t.ticket_id
            where t.ticket_id = %s
            """,
            (ticket_id,),
        )

    def vizinhos(self, ids: list[int], modelo: str, k: int = 10, *, com_textos: bool = False, so_passado: bool = False,
                 peso_palavras: float | None = None):
        """{ticket_id: [linhas dos vizinhos]} com uma consulta só para a lista inteira."""
        sql = SQL_VIZINHOS.format(
            textos="s.solucao_publica, s.solucao_interna, s.mensagens_publicas," if com_textos else "",
            join_solucoes="left join limpo_solucoes s on s.ticket_id = t2.ticket_id" if com_textos else "",
        )
        params = {"ids": ids, "modelo": modelo, "k": k, "so_passado": so_passado,
                  "peso_palavras": PESO_PALAVRAS if peso_palavras is None else peso_palavras,
                  "candidatos": max(k, CANDIDATOS)}
        with self.banco.conexao() as conn:  # hnsw.ef_search já vem ajustado na conexão (banco.py)
            linhas = conn.cursor(row_factory=dict_row).execute(sql, params).fetchall()
        por_ticket: dict[int, list[dict]] = {}
        for linha in linhas:
            por_ticket.setdefault(linha.pop("origem"), []).append(linha)
        return por_ticket

    def por_vetor(self, consulta, modelo: str, k: int = 10) -> list[dict]:
        return self._todos(SQL_TEXTO_LIVRE, {"consulta": consulta, "modelo": modelo, "k": k})

    def trechos(self, consulta, modelo: str, k: int = 5) -> list[dict]:
        return self._todos(
            """
            select id, fonte, titulo, texto, 1 - (embedding <=> %(c)s) as similaridade
            from docs_trechos where modelo = %(m)s
            order by embedding <=> %(c)s limit %(k)s
            """,
            {"c": consulta, "m": modelo, "k": k},
        )

    def assinatura_trechos(self, modelo: str) -> tuple:
        """Muda sempre que um trecho entra ou sai: diz quando refazer o índice de palavras."""
        return tuple(self._um("select count(*) as n, max(id) as ultimo from docs_trechos where modelo = %s",
                              (modelo,)).values())

    def todos_trechos(self, modelo: str) -> list[dict]:
        return self._todos("select id, fonte, titulo, texto from docs_trechos where modelo = %s", (modelo,))

    # ---------- base de conhecimento ----------

    def documentos(self, busca: str | None = None) -> list[dict]:
        """Os documentos da base; com busca, só os que citam o termo, com o trecho onde aparece."""
        termo = (busca or "").strip().lower()
        return self._todos(
            """
            select d.id, d.fonte, d.titulo, d.descricao, d.categoria, d.extensao, d.tamanho, d.paginas,
                   d.rag, d.trechos, d.indexado_em, d.enviado_em, length(d.texto) as caracteres,
                   split_part(u.nome, ' ', 1) as enviado_por,
                   case when %(t)s <> '' and strpos(lower(d.texto), %(t)s) > 0 then
                        substr(d.texto, greatest(1, strpos(lower(d.texto), %(t)s) - 120), 320) end as trecho
            from app_documentos d left join app_usuarios u on u.id = d.enviado_por
            where %(t)s = '' or strpos(lower(d.titulo || ' ' || coalesce(d.descricao, '') || ' '
                                             || coalesce(d.categoria, '') || ' ' || d.texto), %(t)s) > 0
            order by d.enviado_em desc
            """,
            {"t": termo},
        )

    def documento(self, documento_id: int | None = None, fonte: str | None = None) -> dict | None:
        if documento_id is not None:
            return self._um("select id, fonte, titulo, extensao, rag from app_documentos where id = %s", (documento_id,))
        return self._um("select id, fonte, titulo, extensao, rag from app_documentos where fonte = %s", (fonte,))

    def documento_completo(self, documento_id: int) -> dict | None:
        return self._um(
            """
            select d.id, d.fonte, d.titulo, d.descricao, d.categoria, d.extensao, d.tamanho, d.paginas,
                   d.rag, d.trechos, d.enviado_em, d.texto, split_part(u.nome, ' ', 1) as enviado_por
            from app_documentos d left join app_usuarios u on u.id = d.enviado_por where d.id = %s
            """,
            (documento_id,),
        )

    def alterar_documento(self, documento_id: int, campos: dict) -> None:
        permitidos = {k: v for k, v in campos.items() if k in ("titulo", "descricao", "categoria", "rag")}
        if not permitidos:
            return
        atribuicoes = ", ".join(f"{k} = %({k})s" for k in permitidos)
        with self.banco.conexao() as conn:
            conn.execute(f"update app_documentos set {atribuicoes} where id = %(id)s", {**permitidos, "id": documento_id})

    def trechos_de(self, consulta, modelo: str, fontes: list[str], k: int = 3) -> list[dict]:
        """Os trechos mais próximos dentro de documentos específicos (os já citados na conversa)."""
        return self._todos(
            """
            select id, fonte, titulo, texto, 1 - (embedding <=> %(c)s) as similaridade
            from docs_trechos where modelo = %(m)s and fonte = any(%(f)s)
            order by embedding <=> %(c)s limit %(k)s
            """,
            {"c": consulta, "m": modelo, "f": fontes, "k": k},
        )

    # ---------- rascunhos ----------

    def rascunho(self, ticket_id: int) -> dict | None:
        return self._um("select * from app_rascunhos where ticket_id = %s", (ticket_id,))

    def rascunhos(self, ids: list[int]) -> list[dict]:
        return self._todos("select * from app_rascunhos where ticket_id = any(%s)", (ids,))

    def salvar_rascunho(self, ticket_id, tipo, conteudo, base, texto_hash, modelo_llm, limiar, segundos) -> None:
        self._executar(
            """
            insert into app_rascunhos (ticket_id, tipo, conteudo, base, texto_hash, modelo_llm, limiar, segundos)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (ticket_id) do update set tipo = excluded.tipo, conteudo = excluded.conteudo,
                base = excluded.base, texto_hash = excluded.texto_hash, modelo_llm = excluded.modelo_llm,
                limiar = excluded.limiar, segundos = excluded.segundos, gerado_em = now()
            """,
            (ticket_id, tipo, Jsonb(conteudo), base, texto_hash, modelo_llm, limiar, segundos),
        )

    def abertos_sem_rascunho(self, modelo_llm: str, limiar: float, limite: int = 50) -> list[int]:
        linhas = self._todos(
            """
            select t.ticket_id
            from limpo_tickets t
            join limpo_problemas p on p.ticket_id = t.ticket_id
            left join app_rascunhos r on r.ticket_id = t.ticket_id
            where t.status_base = any(%s)
              and (r.ticket_id is null or r.texto_hash <> md5(p.texto) or r.modelo_llm <> %s or r.limiar <> %s)
            order by t.criado_em desc limit %s
            """,
            (list(ABERTOS), modelo_llm, limiar, limite),
        )
        return [linha["ticket_id"] for linha in linhas]

    # ---------- feedback, perguntas, configuração ----------

    def registrar_feedback(self, usuario_id, ticket_id, alvo, valor, detalhe=None) -> None:
        self._executar(
            "insert into app_feedback (usuario_id, ticket_id, alvo, valor, detalhe) values (%s, %s, %s, %s, %s)",
            (usuario_id, ticket_id, alvo, valor, Jsonb(detalhe) if detalhe is not None else None),
        )

    def registrar_pergunta(self, usuario_id, ticket_id, pergunta, resposta, fontes, encontrou, segundos) -> int:
        return self._executar(
            """
            insert into app_perguntas (usuario_id, ticket_id, pergunta, resposta, fontes, encontrou, segundos)
            values (%s, %s, %s, %s, %s, %s, %s) returning id
            """,
            (usuario_id, ticket_id, pergunta, resposta, Jsonb(fontes), encontrou, segundos),
        )[0]

    def config(self) -> dict:
        if self._config and time.monotonic() - self._config[0] < CONFIG_SEGUNDOS:
            return self._config[1]
        valores = {linha["chave"]: linha["valor"] for linha in self._todos("select chave, valor from app_config")}
        self._config = (time.monotonic(), valores)
        return valores

    def salvar_config(self, chave: str, valor) -> None:
        self._executar(
            """
            insert into app_config (chave, valor) values (%s, %s)
            on conflict (chave) do update set valor = excluded.valor, alterado_em = now()
            """,
            (chave, Jsonb(valor)),
        )
        self._config = None  # a próxima leitura já vê o valor novo

    def contagem_fontes(self) -> dict:
        return self._um(
            """
            select (select count(*) from vetores_problemas) as tickets,
                   (select count(*) from limpo_solucoes) as solucoes,
                   (select count(distinct fonte) from docs_trechos) as documentos,
                   (select count(*) from app_documentos) as cadastrados,
                   (select count(*) from docs_trechos) as trechos,
                   (select max(terminado_em) from execucoes_pipeline where status = 'sucesso') as ultima_carga
            """
        )

    # ---------- painel do gestor ----------

    def painel(self, dias: int) -> dict:
        p = {"dias": dias}
        time = self._um(
            """
            select count(*) as resolvidos,
                   percentile_cont(0.5) within group (
                       order by extract(epoch from resolvido_em - criado_em) / 3600) as horas_mediana,
                   avg((resolvido_em <= sla_solucao_em)::int) filter (where sla_solucao_em is not null) as sla_cumprido
            from limpo_tickets
            where resolvido_em >= now() - make_interval(days => %(dias)s)
            """,
            p,
        )
        anterior = self._um(
            """
            select percentile_cont(0.5) within group (
                       order by extract(epoch from resolvido_em - criado_em) / 3600) as horas_mediana,
                   avg((resolvido_em <= sla_solucao_em)::int) filter (where sla_solucao_em is not null) as sla_cumprido
            from limpo_tickets
            where resolvido_em >= now() - make_interval(days => 2 * %(dias)s)
              and resolvido_em < now() - make_interval(days => %(dias)s)
            """,
            p,
        )
        elo = self._um(
            """
            select count(*) filter (where alvo = 'rascunho' and valor = 'usei') as usados,
                   count(*) filter (where valor = 'util') as uteis,
                   count(*) filter (where valor = 'nao_util') as nao_uteis,
                   (select count(*) from app_rascunhos where gerado_em >= now() - make_interval(days => %(dias)s)) as gerados,
                   (select count(*) from app_perguntas where criado_em >= now() - make_interval(days => %(dias)s)) as perguntas
            from app_feedback where criado_em >= now() - make_interval(days => %(dias)s)
            """,
            p,
        )
        analistas = self._todos(
            """
            select responsavel_id as id, responsavel_nome as nome, count(*) as resolvidos,
                   percentile_cont(0.5) within group (
                       order by extract(epoch from resolvido_em - criado_em) / 3600) as horas_mediana,
                   avg((resolvido_em <= sla_solucao_em)::int) filter (where sla_solucao_em is not null) as sla_cumprido
            from limpo_tickets
            where resolvido_em >= now() - make_interval(days => %(dias)s) and responsavel_id is not null
            group by 1, 2 order by resolvidos desc
            """,
            p,
        )
        erros = self._todos(
            """
            select coalesce(t.modulo, 'sem módulo') as modulo,
                   count(*) filter (where f.valor = 'nao_util') as nao_uteis,
                   count(*) as avaliacoes
            from app_feedback f join limpo_tickets t on t.ticket_id = f.ticket_id
            where f.valor in ('util', 'nao_util') and f.criado_em >= now() - make_interval(days => %(dias)s)
            group by 1 having count(*) filter (where f.valor = 'nao_util') > 0
            order by nao_uteis desc limit 5
            """,
            p,
        )
        abertos = self._um(
            """
            select count(*) as abertos,
                   count(*) filter (where sla_solucao_em < now() and not sla_pausado) as estourados
            from limpo_tickets where status_base = any(%s)
            """,
            (list(ABERTOS),),
        )
        return {"time": time, "anterior": anterior, "elo": elo, "analistas": analistas, "erros": erros, **abertos}
