-- Base de conhecimento: o cadastro de cada documento enviado pela tela (o arquivo fica na pasta
-- base_conhecimento/, fora do Git). "rag" diz se os trechos dele entram no Pergunte e nos rascunhos.
create table if not exists app_documentos (
    id           bigint generated always as identity primary key,
    fonte        text not null unique,     -- caminho dentro da pasta (= docs_trechos.fonte)
    titulo       text not null,
    descricao    text,
    categoria    text,
    extensao     text not null,
    tamanho      bigint not null,
    paginas      integer,
    texto        text not null default '', -- texto extraído, para a busca da tela
    rag          boolean not null default false,
    trechos      integer not null default 0,
    indexado_em  timestamptz,
    enviado_por  bigint references app_usuarios (id),
    enviado_em   timestamptz not null default now()
);
