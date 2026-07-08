import pandas as pd
from pathlib import Path
import geopandas as gpd
import geobr
from shapely.geometry import Point
import folium

# Usamos o Pathlib aqui para gerenciar os caminhos das pastas.
pathDiario = Path("focos_diarios")      # Pasta onde salvamos o histórico diário do INPE
pathMinuto = Path("focos_por_minuto")    # Pasta com as atualizações de tempo real (10 min)

# Carregamos a base diária.
df_diario = pd.read_csv(pathDiario / "focos_diario_br_20260701.csv")

# Faxina nos dados: jogamos fora colunas redundantes para deixar o DataFrame leve.
# Como a nossa base já é exclusiva do Brasil, as colunas 'pais' e 'pais_id' só
# ocupam memória à toa. Também dropamos o 'satelite' daqui.
df_diario = df_diario.drop("satelite", axis=1)
df_diario = df_diario.drop("pais_id", axis=1)
df_diario = df_diario.drop("pais", axis=1)

print(df_diario)