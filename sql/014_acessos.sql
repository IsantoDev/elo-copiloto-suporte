-- Acessos e credenciais dos clientes (tela Acessos). A senha fica cifrada (Fernet) com a chave
-- ELO_CHAVE_ACESSOS do .env: quem lê o banco sem a chave não vê senha nenhuma.
create table if not exists app_acessos (
    id             bigint generated always as identity primary key,
    cliente        text not null,
    produto        text,                 -- Produto A, Produto B ou vazio
    nome           text not null,        -- o sistema: VPN, AnyDesk, banco do Produto A, Protheus...
    endereco       text,
    usuario        text,
    senha_cifrada  text,
    observacao     text,
    criado_por     bigint references app_usuarios (id) on delete set null,
    criado_em      timestamptz not null default now(),
    alterado_em    timestamptz not null default now()
);
create index if not exists app_acessos_cliente on app_acessos (lower(cliente));

-- Quem revelou, copiou, criou, alterou ou excluiu cada acesso.
create table if not exists app_acessos_log (
    id          bigint generated always as identity primary key,
    acesso_id   bigint,
    usuario_id  bigint references app_usuarios (id) on delete set null,
    acao        text not null,
    em          timestamptz not null default now()
);
