# ADR 0001: Usuários do produto e etapas de entrada

- **Status:** aceita
- **Data:** 01/10/2026

## Contexto

O copiloto nasceu como ferramenta pessoal de um analista. O suporte atende duas frentes de produto,
com sistemas e problemas diferentes; aqui elas aparecem como **Produto A** e **Produto B**. A
qualidade da busca só foi avaliada por quem conhece o Produto A.

## Opções consideradas

1. **Só o autor:** o mais rápido, mas o produto não gera valor para a equipe.
2. **O suporte de uma frente:** exige sair da máquina do autor, ter login e filtrar por produto.
3. **Todo o suporte (as duas frentes):** o maior valor, mas exige validar a busca também na frente
   que ninguém da equipe do projeto conhece.

## Decisão

O destino é a **opção 3**, alcançada em etapas. Cada etapa só começa quando a anterior provar valor:

1. **Autor:** uso diário na própria máquina (já acontece).
2. **Piloto:** alguns analistas do Produto A, com login e com a busca restrita a esse produto.
3. **O suporte do Produto A inteiro.**
4. **Produto B:** entra depois que alguém dessa frente validar a qualidade da busca.

## Consequências

- A separação por produto passa a ser obrigatória antes da etapa 2, na coleta e na busca.
- Cada etapa precisa de um critério de "provou valor", medido (uso e avaliação dos resultados), e não
  de impressão.
- Expor a ferramenta para a equipe exige aprovação da empresa, porque os resultados mostram dados de
  clientes. Isso é uma dependência externa do plano, não um detalhe de implementação.
- A avaliação da busca feita por uma pessoa só é um viés conhecido: a etapa 4 existe para corrigi-lo.
