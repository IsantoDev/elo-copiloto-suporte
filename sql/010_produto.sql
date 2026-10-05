-- Tabelas do produto (Elo): usuários, sessões, rascunhos, feedback, configurações e documentos.
-- Rodar uma vez (idempotente): uv run python -m copilot_suporte.usuarios preparar

create table if not exists app_usuarios (
    id           bigint generated always as identity primary key,
    email        text not null unique,
    nome         text not null,
    papel        text not null check (papel in ('analista', 'gestor', 'admin')),
    nivel        text,                     -- N1, N2, N3 (só exibição)
    movidesk_id  text,                     -- id do agente no Movidesk: monta "meus tickets"
    senha_hash   text not null,
    ativo        boolean not null default true,
    criado_em    timestamptz not null default now()
);

create table if not exists app_sessoes (
    token_hash   text primary key,         -- só o hash do token fica no banco
    usuario_id   bigint not null references app_usuarios(id) on delete cascade,
    criada_em    timestamptz not null default now(),
    expira_em    timestamptz not null
);

-- Um rascunho por ticket; refeito quando o texto do problema, o modelo ou o limiar mudam.
create table if not exists app_rascunhos (
    ticket_id    integer primary key,
    tipo         text not null check (tipo in ('solucao', 'analise')),
    conteudo     jsonb not null,
    base         integer[] not null default '{}',   -- tickets usados como base
    texto_hash   text not null,
    modelo_llm   text not null,
    limiar       real not null,
    gerado_em    timestamptz not null default now(),
    segundos     real
);

-- Toda avaliação do analista: rascunho usado/útil/não ajudou, resposta do Pergunte, sugestão confirmada.
create table if not exists app_feedback (
    id           bigint generated always as identity primary key,
    usuario_id   bigint references app_usuarios(id) on delete set null,
    ticket_id    integer,
    alvo         text not null,            -- rascunho, pergunta, categoria, urgencia, parecido
    valor        text not null,            -- usei, util, nao_util, confirmado, ...
    detalhe      jsonb,
    criado_em    timestamptz not null default now()
);
create index if not exists app_feedback_alvo on app_feedback (alvo, criado_em);

create table if not exists app_perguntas (
    id           bigint generated always as identity primary key,
    usuario_id   bigint references app_usuarios(id) on delete set null,
    ticket_id    integer,
    pergunta     text not null,
    resposta     text,
    fontes       jsonb,
    encontrou    boolean,
    segundos     real,
    criado_em    timestamptz not null default now()
);

create table if not exists app_config (
    chave        text primary key,
    valor        jsonb not null,
    alterado_em  timestamptz not null default now()
);
insert into app_config (chave, valor) values
    ('limiar', '0.80'),
    ('fontes', '{"tickets": true, "documentos": true}')
on conflict (chave) do nothing;

-- Base de conhecimento: trechos de documentos (pasta local, páginas públicas), com vetor.
create extension if not exists vector;
create table if not exists docs_trechos (
    id           bigint generated always as identity primary key,
    fonte        text not null,            -- arquivo ou URL de origem
    titulo       text not null,
    ordem        integer not null,
    texto        text not null,
    texto_hash   text not null,
    modelo       text not null,
    embedding    vector(768) not null,
    gerado_em    timestamptz not null default now(),
    unique (fonte, ordem)
);
create index if not exists docs_trechos_hnsw on docs_trechos using hnsw (embedding vector_cosine_ops);
