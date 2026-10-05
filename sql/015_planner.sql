-- Cards do Planner (time de produto) e a ligação de cada um ao seu ticket.
-- Vêm de um arquivo: o Excel exportado do Planner ou o planner.json que o Power Automate grava no OneDrive.
-- Só o necessário para acompanhar: título, coluna, andamento, responsáveis e datas. As notas do card
-- não são guardadas; delas só saem os protocolos de ticket citados (protocolos).
create table if not exists app_planner_cards (
    id            text primary key,          -- Task ID do Planner
    plano_id      text not null,
    plano         text,
    titulo        text not null,
    coluna        text,                      -- bucket: Entrada de Demandas, Em Desenvolvimento, Concluído...
    status        text,                      -- Not started | In progress | Completed
    progresso     smallint,                  -- 0, 50, 100
    prioridade    text,
    responsaveis  text,
    criado_em     date,
    prazo         date,
    concluido_em  date,
    protocolos    text[] not null default '{}',
    visto_em      timestamptz not null default now()
);

-- Um ticket, um card. "como": protocolo (citado no card) ou titulo (o título que o ticket cita bate com o card).
create table if not exists app_planner_vinculos (
    ticket_id  integer primary key,
    card_id    text not null references app_planner_cards (id) on delete cascade,
    como       text not null
);

-- Nomes de quem aparece nos cards (aba Users do Excel exportado): o JSON do Power Automate só traz os ids.
create table if not exists app_planner_pessoas (
    id    text primary key,
    nome  text not null
);
