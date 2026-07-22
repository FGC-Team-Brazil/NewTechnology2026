#!/usr/bin/env python3
"""
Builds the prompt with environmental data + GBIF species, and calls a model
via Ollama (runs locally on your machine or via cloud) to suggest 
reforestation species and strategies.

Requires (one-time setup):
    1. Install Ollama: https://ollama.com/download and log in (required
       for ":cloud" models — non-cloud models do not require login).
    2. In the Ollama app (or via terminal), download/select the model:
         - For lightweight/casual use without a heavy GPU: use an efficient
           cloud model like "nemotron-3-super:cloud" (default below).
           Runs on Ollama's servers without taxing your PC.
         - For BATCH processing (hundreds/thousands of requests): use a 
           LOCAL model instead of cloud to avoid usage limits.
    3. pip install ollama

Ollama needs to be running in the background (the app keeps it active by default;
otherwise, run 'ollama serve' in a separate terminal).
"""
DEFAULT_MODEL = "nemotron-3-super:cloud"


def _fmt(value, unit="") -> str:
    if value is None or value == "":
        return "not available"
    return f"{value}{unit}"


def _format_environmental_data(data: dict, reliable_biome: str = "") -> str:
    """
    Extracts and organizes the same fields displayed on the rtdash.py dashboard
    cards into human-readable text — avoiding raw JSON dumps.

    Explicitly lists which sources failed during retrieval so the AI doesn't 
    hallucinate generic values for missing parameters.
    """
    parts = []

    # Reliable biome (from INPE/spreadsheet) takes priority over biome collected dynamically
    collected_biome = (data.get("biome_and_vegetation") or data.get("bioma_e_vegetacao") or {}).get("biome") or (data.get("bioma_e_vegetacao") or {}).get("bioma")
    final_biome = reliable_biome or collected_biome
    if final_biome:
        vegetation = (data.get("biome_and_vegetation") or data.get("bioma_e_vegetacao") or {}).get("original_vegetation") or (data.get("bioma_e_vegetacao") or {}).get("vegetacao_original")
        parts.append(f"BIOME (confirmed by INPE): {final_biome}" + (f", original vegetation: {vegetation}" if vegetation else ""))

    history = data.get("query_datetime") or data.get("data_hora_consultada")
    climate = data.get("historical_climate") or data.get("clima_historico") if history else ((data.get("current_climate_and_forecast") or data.get("clima_atual_e_previsao") or {}).get("current_conditions") or (data.get("clima_atual_e_previsao") or {}).get("condicoes_atuais", {}))
    climate = climate or {}
    if climate:
        parts.append(
            f"CLIMATE ({'historical' if history else 'current'}): "
            f"temperature {_fmt(climate.get('temperature_c') or climate.get('temperatura_c'), ' °C')}, "
            f"relative humidity {_fmt(climate.get('relative_humidity_pct') or climate.get('umidade_relativa_pct'), ' %')}, "
            f"precipitation {_fmt(climate.get('precipitation_mm') or climate.get('precipitacao_mm'), ' mm')}, "
            f"wind {_fmt(climate.get('wind_kmh') or climate.get('vento_kmh'), ' km/h')}"
        )

    soil = data.get("soil") or data.get("solo") or {}
    if soil:
        soil_lines = []
        for prop, info in soil.items():
            val = (info.get("values") or info.get("valores") or {}).get("0-5cm")
            if val is not None:
                unit = info.get("unit") or info.get("unidade", "")
                soil_lines.append(f"{prop}={val}{unit}")
        if soil_lines:
            parts.append("SOIL (0-5cm): " + ", ".join(soil_lines))

    relief = data.get("relief_slope") or data.get("declividade_relevo") or {}
    if relief:
        parts.append(
            f"RELIEF: estimated slope {_fmt(relief.get('estimated_slope_pct') or relief.get('declividade_pct_estimada'), '%')} "
            f"({_fmt(relief.get('relief_classification') or relief.get('classificacao_relevo'))})"
        )

    erosion = data.get("erosion_risk") or data.get("risco_erosao") or {}
    risk_class = erosion.get("erosion_risk_class") or erosion.get("classe_risco_erosao")
    if risk_class:
        parts.append(f"EROSION RISK: {risk_class}")

    water = (data.get("water_distance") or data.get("distancia_agua") or {}).get("nearest_water_body") or (data.get("distancia_agua") or {}).get("corpo_dagua_mais_proximo")
    if water:
        parts.append(f"NEAREST WATER BODY: {water.get('name') or water.get('nome')} at {water.get('distance_km') or water.get('distancia_km')} km")

    veg = (data.get("native_vegetation_distance") or data.get("distancia_vegetacao_nativa") or {}).get("nearest_vegetation_fragment") or (data.get("distancia_vegetacao_nativa") or {}).get("fragmento_vegetacao_mais_proximo")
    if veg:
        parts.append(f"NEAREST NATIVE VEGETATION: {veg.get('name') or veg.get('nome')} at {veg.get('distance_km') or veg.get('distancia_km')} km")

    normals = (data.get("climatological_normals") or data.get("normais_climatologicas") or {}).get("annual_average") or (data.get("normais_climatologicas") or {}).get("media_anual") or {}
    if normals:
        parts.append(
            f"CLIMATOLOGICAL NORMALS (annual average, 1991-2020): "
            f"avg temp {_fmt(normals.get('avg_temp_c') or normals.get('temp_media_c'), ' °C')}, "
            f"precipitation {_fmt(normals.get('precipitation_mm_day') or normals.get('precipitacao_mm_dia'), ' mm/day')}"
        )

    wildfires = data.get("local_wildfire_history") or data.get("historico_queimadas_local") or {}
    if (wildfires.get("total_hotspots_in_radius") or wildfires.get("total_focos_no_raio")) is not None:
        parts.append(
            f"WILDFIRE HISTORY (radius {wildfires.get('queried_radius_km') or wildfires.get('raio_consultado_km')} km): "
            f"{wildfires.get('total_hotspots_in_radius') or wildfires.get('total_focos_no_raio')} hotspots, years recorded: {wildfires.get('years_recorded') or wildfires.get('anos_com_registro') or 'none'}"
        )

    protected_areas = data.get("nearby_protected_areas") or data.get("areas_protegidas_proximas") or []
    if protected_areas:
        names = ", ".join(pa.get("name") or pa.get("nome", "?") for pa in protected_areas[:5])
        parts.append(f"NEARBY PROTECTED AREAS: {names}")

    errors = data.get("errors") or data.get("erros") or {}
    if errors:
        parts.append(
            "\nFAILED DATA SOURCES FOR THIS QUERY (data unavailable — DO NOT invent values "
            "for these categories, simply indicate that no real data is available): "
            + ", ".join(errors.keys())
        )

    return "\n".join(parts) if parts else "No environmental data available."


PROJECT_CONTEXT = """ReviveTech is an automated post-wildfire ecological recovery pipeline.
When a wildfire hotspot is detected (via satellite/INPE), the system collects soil, climate,
and biodiversity data from the burned area. It uses this information to determine HOW to formulate
encapsulated seeds ("biocapsules") — small capsules containing seed + substrate/additives — 
which are then dispersed by DRONES over the burned area. This is not conventional manual planting: 
it is a seed capsule designed to survive on the ground until optimal conditions (rain, temperature) 
trigger germination."""


def build_prompt(environmental_data: dict, neighboring_species: str, municipality: str = "", reliable_biome: str = "") -> str:
    """
    Builds the LLM prompt combining environmental data with GBIF species entries 
    within the context of drone-based seed capsule dispersion.
    """
    return f"""You are an ecological restoration expert working on the ReviveTech project.

{PROJECT_CONTEXT}

Analyze the data below from an area that suffered a wildfire{f" in {municipality}" if municipality else ""}
and recommend the biocapsule formulation and drone dispersion plan for this specific location.

ENVIRONMENTAL DATA:
{_format_environmental_data(environmental_data, reliable_biome)}

NATIVE SPECIES ALREADY REGISTERED IN THIS REGION (GBIF):
{neighboring_species or "No registered species found nearby"}

Based on these data, answer:
1. Which 3-5 species (preferably from those already registered in the region or 
   highly compatible with this biome) are most suitable to include in the biocapsules 
   for this area, and why? (Consider soil pH, texture, rainfall patterns, and wildfire risk).
2. What additional components should the biocapsule contain besides the seed, given these 
   specific soil and climate conditions (e.g., substrate/hydrogel for water retention if rainfall 
   is low, pH modifiers, mycorrhizal inoculants, heat/burn protection)?
3. Suggested dispersion density (capsules per hectare) and the ideal time window for 
   drone deployment, considering climatological normals (germination relies on rainfall to 
   activate the capsule, not manual irrigation).
4. Are there any site constraints that make aerial dispersion unsuitable right now 
   (e.g., extreme slope, proximity to water bodies requiring buffer zones, high erosion 
   risk requiring soil stabilization first)?

Be direct and practical — this will guide real field operations and capsule technical 
specifications. If any required information is listed as unavailable, explicitly note that 
it was missing from this query rather than assuming typical default values without warning."""


def ask_suggestion(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """Sends the prompt to the model (via Ollama) and returns the generated text response."""
    try:
        import ollama
    except ImportError:
        return "[Error] The 'ollama' package is not installed. Run: pip install ollama"

    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        msg = str(e).lower()
        if "connection" in msg or "refused" in msg:
            return (
                "[Error] Could not connect to Ollama. Is it running?\n"
                "Launch the Ollama application, or run 'ollama serve' in a separate terminal."
            )
        if "not found" in msg or "no such model" in msg:
            return (
                f"[Error] Model '{model}' not found locally.\n"
                f"Download it with: ollama pull {model}"
            )
        if "unauthorized" in msg or "401" in msg or "sign in" in msg or "log in" in msg:
            return (
                f"[Error] Model '{model}' is a cloud model and requires an Ollama sign-in.\n"
                "Open the Ollama app and log into your account."
            )
        if "weekly usage limit" in msg or "429" in msg:
            return (
                "[Error] Weekly usage limit for the free tier reached.\n"
                "Wait for the reset (7 days) or switch to a local model (without ':cloud')."
            )
        return f"[Error calling Ollama]: {e}"

    # Handles dictionary or object responses depending on the installed ollama package version
    try:
        return response["message"]["content"]
    except (TypeError, KeyError):
        return response.message.content