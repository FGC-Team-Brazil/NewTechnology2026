# Base de espécies por bioma

Cada arquivo representa o conjunto de espécies disponível para um bioma. A
base separa duas situações que não devem ser confundidas:

- `eligible`: espécie com índice de inflamabilidade abaixo do limite do motor
  e evidência térmica publicada ou ensaio de campo validado;
- `trial_candidate`: espécie nativa candidata para experimento. Ela não é
  apresentada como espécie de baixa inflamabilidade até que o projeto registre
  evidência comparável.

Antes de alterar o status de uma espécie, registre a fonte, a procedência das
sementes, a fitofisionomia e o resultado de ensaios locais. Valores ausentes
não devem ser substituídos por estimativas no código: o motor reduz a confiança
ou direciona a espécie para ensaio.

As doses de biochar e hidrogel produzidas pelo motor são matrizes de ensaio.
Elas exigem delineamento com controle, repetição e acompanhamento de
sobrevivência e crescimento antes de qualquer recomendação operacional.
