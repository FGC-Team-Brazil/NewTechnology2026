# ml/ — Machine Learning (Espaço Reservado)

Este diretório está reservado para a futura camada de Machine Learning do ReviveTech.

## Quando ativar

Quando houver dados de campo suficientes (sobrevivência das biocápsulas, crescimento, etc.),
implemente aqui um modelo de regressão/classificação que substitua ou complemente
o motor determinístico (`src/engine/decision.py`).

## Estrutura planejada

```
ml/
├── datasets/          # Dados de treinamento (.jsonl / .csv) — ignorados pelo git
├── train.py           # Script de treino (scikit-learn ou PyTorch)
├── evaluate.py        # Métricas e validação cruzada
└── models/            # Modelos serializado (.pkl / .joblib) — ignorados pelo git
```

## Como conectar ao pipeline

1. Treine o modelo com `python ml/train.py`
2. O modelo serializado ficará em `ml/models/`
3. Em `src/engine/decision.py`, a função `recommend_biocapsule()` já possui
   um hook `# ML_HOOK` onde o modelo poderá ser carregado e consultado

## Dependências

```bash
pip install -r requirements/ml.txt
```

## Estado atual

O pipeline funciona **sem** ML utilizando o motor determinístico baseado em
curvas dose-resposta da literatura científica. O ML será puramente aditivo.
