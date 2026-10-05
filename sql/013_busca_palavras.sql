-- Busca por palavras nos casos resolvidos: problema + solução + mensagens públicas do analista.
-- Complementa a busca por significado (vetores_problemas): o mesmo erro muitas vezes é descrito com
-- palavras diferentes no problema, mas o termo certo ("administradora financeira") aparece na solução.
-- Refeita a cada carga (roda depois do 007).

create table if not exists busca_palavras (
    ticket_id  integer primary key,
    tsv        tsvector not null
);
create index if not exists busca_palavras_gin on busca_palavras using gin (tsv);

insert into busca_palavras (ticket_id, tsv)
select t.ticket_id,
       to_tsvector('portuguese', concat_ws(' ', t.assunto, p.texto, s.solucao_publica, s.solucao_interna,
                                          array_to_string(s.mensagens_publicas, ' ')))
from limpo_tickets t
left join limpo_problemas p on p.ticket_id = t.ticket_id
left join limpo_solucoes s on s.ticket_id = t.ticket_id
where t.status_base in ('Resolved', 'Closed')
on conflict (ticket_id) do update set tsv = excluded.tsv;

delete from busca_palavras b
where not exists (select 1 from limpo_tickets t where t.ticket_id = b.ticket_id and t.status_base in ('Resolved', 'Closed'));
