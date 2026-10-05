# Avaliação temporal da triagem

Tickets resolvidos abertos nos últimos 60 dias: **570**. Cada um só enxerga os tickets resolvidos antes da sua abertura (10 vizinhos mais parecidos, do mesmo produto).

## Sugestões

| Campo | Acerto do Elo | Linha de base (classe mais comum) | Tickets avaliados |
|---|---|---|---|
| Módulo › item | 42% | 5% | 570 |
| Módulo | 65% | 36% | 570 |
| Urgência | 66% | 66% | 570 |

## Calibração: acerto por faixa de confiança

| Campo | 0%–50% | 50%–70% | 70%–90% | 90%–100% |
|---|---|---|---|---|
| Módulo › item | 27% (n=366) | 48% (n=89) | 75% (n=51) | 92% (n=64) |
| Módulo | 44% (n=123) | 57% (n=200) | 73% (n=117) | 92% (n=130) |
| Urgência | 38% (n=32) | 53% (n=182) | 68% (n=216) | 86% (n=140) |

## Risco de estourar o SLA

Tickets com prazo: 570 · estouraram: 4.6%.

- Brier do Elo: 0.085 · Brier chutando a taxa média: 0.044 (menor é melhor)
- AUC (separa quem estoura de quem não estoura): 0.84

| Nível previsto | Tickets | Estouraram de fato |
|---|---|---|
| alto | 81 | 14% |
| medio | 65 | 14% |
| baixo | 424 | 1% |
