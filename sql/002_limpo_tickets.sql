-- Camada limpa: um ticket por linha, com tipos corretos e datas com fuso.
-- A API manda as datas em UTC sem fuso; o "+00:00" declara isso explicitamente.

drop table if exists limpo_tickets;

create table limpo_tickets as
select
    (payload->>'id')::int                                  as ticket_id,
    payload->>'subject'                                    as assunto,
    payload->>'category'                                   as categoria,
    payload->>'urgency'                                    as urgencia,
    payload->>'baseStatus'                                 as status_base,
    payload->>'status'                                     as status,
    nullif(trim(payload->>'justification'), '')            as justificativa,  -- o motivo da espera (Aguardando)
    nullif(trim(payload->>'_empresa'), '')                 as empresa,        -- movidesk.reduzir_pessoas
    payload->>'_solicitante'                               as solicitante,    -- só o primeiro nome
    ((payload->>'createdDate') || '+00:00')::timestamptz   as criado_em,
    ((payload->>'lastUpdate')  || '+00:00')::timestamptz   as atualizado_em,
    jsonb_array_length(payload->'actions')                 as qtd_acoes,
    -- serviço em três níveis: produto (Produto A, Produto B) › módulo › item.
    -- Tickets antigos não têm o 3º nível; o time responsável diz a frente.
    case
        when payload->>'serviceThirdLevel' ilike '%produto b%' then 'Produto B'
        when payload->>'serviceThirdLevel' ilike '%produto a%' then 'Produto A'
        when payload->>'ownerTeam' ilike '%produto b%' then 'Produto B'
        when payload->>'ownerTeam' ilike '%produto a%' then 'Produto A'
    end                                                    as produto,
    nullif(trim(payload->>'serviceSecondLevel'), '')       as modulo,
    nullif(trim(payload->>'serviceFirstLevel'), '')        as item,
    nullif(trim(payload->>'ownerTeam'), '')                as time_responsavel,
    payload->'owner'->>'id'                                as responsavel_id,
    nullif(trim(payload->'owner'->>'businessName'), '')    as responsavel_nome,
    payload->>'slaAgreement'                               as sla_contrato,
    ((payload->>'slaResponseDate') || '+00:00')::timestamptz     as sla_resposta_em,
    ((payload->>'slaRealResponseDate') || '+00:00')::timestamptz as respondido_em,
    ((payload->>'slaSolutionDate') || '+00:00')::timestamptz     as sla_solucao_em,
    coalesce((payload->>'slaSolutionDateIsPaused')::boolean, false) as sla_pausado,
    ((payload->>'resolvedIn') || '+00:00')::timestamptz    as resolvido_em,
    ((payload->>'closedIn') || '+00:00')::timestamptz      as fechado_em
from bruto_tickets;

alter table limpo_tickets add primary key (ticket_id);
create index on limpo_tickets (responsavel_id) where status_base in ('New', 'InAttendance', 'Stopped');
