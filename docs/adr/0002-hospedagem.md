# ADR 0002: Onde o produto roda (piloto e produto)

- **Status:** aceita
- **Data:** 01/10/2026

## Contexto

O backend precisa do modelo de embeddings na memória (cerca de 2 GB de RAM) e de uma carga de hora em
hora. O banco já é um PostgreSQL gerenciado, na conta da empresa. O frontend poderia ir para uma
plataforma serverless, mas as funções não comportam o modelo (limite de tamanho e inicialização a
frio). O orçamento atual é zero.

Há ainda uma restrição que não é técnica: os textos dos tickets são dados de clientes, então cada
provedor novo no caminho é um provedor a mais com acesso a eles.

## Opções consideradas

| Opção | Por que não (ou não agora) |
|---|---|
| VM ou PaaS no provedor do banco | Melhor tecnicamente, mas tem custo mensal |
| PaaS de camada gratuita (512 MB de RAM) | O modelo não cabe |
| Plataforma de hospedagem de modelos (grátis) | Dorme sem uso, sem agendador, e leva dados de clientes para um provedor novo |
| Serverless com cota grátis | Recarrega o modelo a cada inicialização a frio; provedor novo para os dados |
| Nuvem "always free" (24 GB de RAM) | Boa para o produto, mas provedor novo e com risco de recuperação de máquinas ociosas |
| **Cloudflare Tunnel + Access** | **Escolhida para o piloto** |

## Decisão

- **Piloto:** a API roda numa máquina (do autor ou da empresa) e fica acessível pela internet por um
  **Cloudflare Tunnel**, protegido pelo **Cloudflare Access** (login por e-mail, plano grátis até 50
  usuários). Nenhum dado passa a ficar **armazenado** num provedor novo: ele só trafega pelo túnel.
- **Produto:** quando o piloto provar valor, o backend e a carga vão para um servidor sempre ligado —
  um servidor da empresa, se houver, ou uma nuvem de camada gratuita com RAM suficiente.
- **O produto é empacotado em container**, para que a mesma imagem rode no piloto e no servidor
  definitivo.

## Consequências

- O piloto depende de a máquina estar ligada. É aceitável para um piloto, mas não para o produto.
- O Cloudflare Access exige um **domínio** gerenciado pelo Cloudflare (próprio ou subdomínio da
  empresa). Isso virou uma dependência externa explícita no plano.
- A API continua escutando só em `127.0.0.1`: quem a expõe é o túnel, e o túnel só deixa passar quem
  passou pelo login. O login do próprio produto continua valendo por baixo, como segunda camada.
- A busca por ticket não usa o modelo. No futuro ela pode rodar separada, consultando o banco direto,
  e só a busca por texto depender do servidor com o modelo.
