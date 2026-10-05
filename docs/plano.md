# Plano do produto

Do uso pessoal ao suporte inteiro, em etapas (ver [ADR 0001](adr/0001-usuarios-e-etapas.md) e
[ADR 0002](adr/0002-hospedagem.md)). Cada fase termina com algo funcionando e testado.

## Fase 0: formalizar

- [x] ADR 0001: usuários e etapas
- [x] ADR 0002: hospedagem do piloto e do produto
- [x] `docs/arquitetura.md`: componentes, fluxo do dado e onde cada coisa roda
- [ ] ADRs das decisões já tomadas: bruto em jsonb (ELT), prefixos no lugar de schemas, modelo local
      escolhido por benchmark, registro de execuções, POO só nas bordas

## Fase 1: produto empacotado e testável

- [x] Docker: uma imagem com a API e a carga, configurada só por variáveis de ambiente
- [x] CI: os testes rodam a cada push, sem dados reais
- [ ] Ambientes separados: o desenvolvimento deixa de ser a mesma pasta da produção

## Fase 2: piloto seguro

- [ ] Domínio no Cloudflare (próprio ou subdomínio da empresa)
- [ ] Cloudflare Tunnel apontando para a API local
- [ ] Cloudflare Access: login por e-mail, só para os e-mails autorizados
- [ ] Limite de tentativas de login por e-mail e por IP, antes de qualquer exposição
- [ ] A API e o túnel sobem sozinhos com a máquina
- [ ] Teste com um colega da mesma frente de produto

## Fase 3: uma frente de produto inteira

- [x] Separação por produto na coleta e filtro na busca
- [x] Botão "útil / não útil" em cada resultado (avaliação feita pelo próprio uso)
- [x] Registro de uso: quem buscou o quê e quando
- [ ] Monitoramento: o sentinela lê `execucoes_pipeline` e `/saude` e alerta no chat do time
- [x] `docs/operacao.md`: como saber se está saudável e o que fazer em cada incidente
- [x] Avaliação refeita, com o resultado no README

## Fase 4: suporte inteiro

- [ ] Validação da busca na segunda frente de produto, por quem a conhece
- [ ] Backend e carga num servidor sempre ligado
- [ ] Frontend servido separadamente

## Dependências externas

- Aprovação da empresa para expor a ferramenta à equipe (os resultados mostram dados de clientes)
- Como os tickets de cada frente de produto se separam no Movidesk
- Domínio para o Cloudflare
- Existência de um servidor da empresa para o produto
