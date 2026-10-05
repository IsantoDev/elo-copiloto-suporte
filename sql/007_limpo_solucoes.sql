-- Solução de cada ticket resolvido: a última mensagem pública do agente, a última nota
-- interna do agente (onde costuma ficar o detalhe técnico) e todas as mensagens públicas
-- do agente, em ordem. A última mensagem às vezes é só o modelo de encerramento
-- ("Seu chamado ... foi solucionado com sucesso"); o Python (buscar.solucao_do_caso)
-- usa a lista para separar a solução de verdade da última orientação dada ao cliente.

drop table if exists limpo_solucoes;

create table limpo_solucoes as
with ultimas as (
    select distinct on (ticket_id, visibilidade)
        ticket_id, visibilidade, descricao
    from limpo_acoes
    where autor_perfil = 'agente'
      and nullif(trim(descricao), '') is not null
    order by ticket_id, visibilidade, (length(trim(descricao)) >= 30) desc, acao_id desc
),
publicas as (
    select ticket_id, array_agg(descricao order by acao_id) as mensagens_publicas
    from limpo_acoes
    where autor_perfil = 'agente' and visibilidade = 'publica' and nullif(trim(descricao), '') is not null
    group by ticket_id
)
select
    t.ticket_id,
    max(u.descricao) filter (where u.visibilidade = 'publica') as solucao_publica,
    max(u.descricao) filter (where u.visibilidade = 'interna') as solucao_interna,
    max(p.mensagens_publicas)                                   as mensagens_publicas
from limpo_tickets t
join ultimas u on u.ticket_id = t.ticket_id
left join publicas p on p.ticket_id = t.ticket_id
where t.status_base in ('Resolved', 'Closed')
group by t.ticket_id;

alter table limpo_solucoes add primary key (ticket_id);
