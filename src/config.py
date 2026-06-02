"""
Configuracion central del sistema GPS_MACRO.
Todo lo calibrable vive aqui: tickers, umbrales, pesos.
"""
from __future__ import annotations

from pathlib import Path

# ---------- Rutas ----------
ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
DATA_DIR = DOCS_DIR / "data"
TEMPLATES_DIR = DOCS_DIR / "templates"
SNAPSHOT_PATH = DATA_DIR / "snapshot.json"
HISTORY_PATH = DATA_DIR / "history.json"
INDEX_HTML = DOCS_DIR / "index.html"

# ---------- Series macro FRED (8 principales) ----------
# (id_interno, fred_series, descripcion_humana, eje_crecimiento_o_stress, signo)
# signo = +1  -> mas valor = mejor para crecimiento (o menor stress)
# signo = -1  -> mas valor = peor (mas stress)
# eje "growth" votan en eje crecimiento, "stress" votan en eje stress/inflacion
MACRO_SERIES = [
    # id              fred         descripcion                              eje       signo
    ("manuf_health", "MANEMP",     "Empleo Manufactura (proxy PMI)",        "growth", +1),
    ("yield_curve",  "T10Y2Y",     "Curva de Rendimientos 10Y-2Y",          "growth", +1),
    ("jobless_4w",   "IC4WSA",     "Jobless Claims Initial (4W MA)",        "growth", -1),
    ("hy_oas",       "BAMLH0A0HYM2","High Yield OAS Spread",                "stress", -1),
    ("nfci",         "NFCI",       "Chicago Fed Financial Conditions Index","stress", -1),
    ("lei_proxy",    "USSLIND",    "State Coincident Index (LEI proxy)",    "growth", +1),
    ("breakeven_5y", "T5YIE",      "Breakeven Inflation 5Y",                "stress", -1),
]

# Series secundarias (informativas, no votan):
MACRO_SECONDARY = [
    ("m2_yoy",       "M2SL",       "M2 Money Supply (YoY %)"),
    ("real_yield",   "DFII10",     "Real Yield 10Y"),
    ("retail_sales", "RSAFS",      "Retail Sales (YoY %)"),
    ("permits",      "PERMIT",     "Building Permits"),
    ("dxy",          "DTWEXBGS",   "Trade-Weighted Dollar Index"),
]

# Series desde Yahoo Finance (no estan en FRED o las queremos diarias):
YAHOO_MACRO = [
    ("copper_gold",  ("HG=F", "GC=F"), "Copper/Gold Ratio (apetito riesgo)", "growth", +1),
    ("vix",          "^VIX",          "VIX (miedo)",                          "stress", -1),
]

# ---------- ETFs sectoriales GICS (los 11) ----------
SECTORS = [
    # (ticker, nombre, codigo_corto)
    ("XLK",  "Tecnologia",              "Tech"),
    ("XLF",  "Financiero",              "Fin"),
    ("XLE",  "Energia",                 "Energy"),
    ("XLI",  "Industrial",              "Ind"),
    ("XLV",  "Salud",                   "Health"),
    ("XLY",  "Consumo Discrecional",    "Disc"),
    ("XLP",  "Consumo Defensivo",       "Staples"),
    ("XLB",  "Materiales",              "Mat"),
    ("XLU",  "Utilities",               "Util"),
    ("XLRE", "Inmobiliario",            "RealEst"),
    ("XLC",  "Comunicaciones",          "Comm"),
]

BENCHMARK = "SPY"

# ---------- Sub-sectores (granularidad cuando un sector lidera) ----------
SUBSECTORS = {
    "XLK": [("SMH", "Semis"), ("IGV", "Software"), ("CIBR", "Ciberseguridad")],
    "XLF": [("KRE", "Bancos Regionales"), ("KIE", "Seguros")],
    "XLE": [("XOP", "E&P"), ("OIH", "Servicios Petroleros"), ("TAN", "Solar")],
    "XLV": [("IBB", "Biotecnologia"), ("IHF", "Proveedores Salud")],
    "XLY": [("XRT", "Retail"), ("ITB", "Homebuilders")],
    "XLI": [("JETS", "Aerolineas"), ("XAR", "Aeroespacial")],
    "XLB": [("LIT", "Litio"), ("GDX", "Mineros Oro")],
    "XLU": [],
    "XLRE": [("REZ", "Residencial REIT")],
    "XLC": [("SOCL", "Social Media")],
    "XLP": [],
}

# ---------- Modelo de fases ----------
# Fases:
#   1 EXPANSION:          growth > 0,  stress < 0
#   2 RECALENTAMIENTO:    growth > 0,  stress > 0
#   3 CONTRACCION:        growth < 0,  stress > 0
#   4 DESACELERACION:     growth < 0,  stress < 0
PHASES = {
    1: {
        "code": "EXPANSION",
        "label": "Expansion",
        "description": "Crecimiento solido, inflacion y stress controlados",
        "favored_sectors": ["XLK", "XLY", "XLC", "XLI"],
        "avoid_sectors":   ["XLP", "XLU"],
        "color": "#22c55e",
    },
    2: {
        "code": "RECALENTAMIENTO",
        "label": "Recalentamiento",
        "description": "Crecimiento pero con inflacion/stress al alza - tarde en el ciclo",
        "favored_sectors": ["XLE", "XLB", "XLI", "XLF"],
        "avoid_sectors":   ["XLK", "XLU"],
        "color": "#f59e0b",
    },
    3: {
        "code": "CONTRACCION",
        "label": "Contraccion",
        "description": "Crecimiento debil y stress alto - modo defensivo",
        "favored_sectors": ["XLP", "XLU", "XLV"],
        "avoid_sectors":   ["XLF", "XLY", "XLB"],
        "color": "#ef4444",
    },
    4: {
        "code": "DESACELERACION",
        "label": "Desaceleracion",
        "description": "Crecimiento perdiendo fuerza pero sin stress agudo",
        "favored_sectors": ["XLV", "XLP", "XLU", "XLRE"],
        "avoid_sectors":   ["XLE", "XLB", "XLI"],
        "color": "#3b82f6",
    },
}

# Umbral para declarar cambio de fase (probabilidad sostenida).
PHASE_CHANGE_THRESHOLD = 0.55
PHASE_CHANGE_PERSISTENCE_DAYS = 5

# ---------- Pesos del Score Compuesto Sectorial ----------
SECTOR_SCORE_WEIGHTS = {
    "mansfield_rs":     0.25,  # fuerza relativa normalizada vs SPY
    "momentum":         0.30,  # combinado 1M/3M/6M
    "cross_rank":       0.15,  # ranking entre los 11
    "breadth":          0.10,  # holdings sobre 50/200 DMA (proxy via precio del ETF si no hay holdings)
    "volume_flow":      0.10,  # OBV slope relativo
    "phase_alignment":  0.10,  # bonus/penalty por alineacion con fase macro
}

MOMENTUM_PERIODS = {  # dias de trading
    "m1": 21,
    "m3": 63,
    "m6": 126,
}
MOMENTUM_WEIGHTS = {"m1": 0.5, "m3": 0.3, "m6": 0.2}

# ---------- Estrategia ADAPT ("menos defensivo en bull") ----------
# Filtro de tendencia: si SPY > su media movil de ADAPT_TREND_MA dias -> regimen "bull".
# Validado por backtest (2007-2026): robusto a la media (120-250) y mejora el caso
# general; en bull prioriza momentum y anula el sesgo de fase, en bear lo refuerza.
ADAPT_TREND_MA = 200
ADAPT_BULL_WEIGHTS = {
    "mansfield_rs": 0.25, "momentum": 0.40, "cross_rank": 0.20,
    "breadth": 0.10, "volume_flow": 0.05, "phase_alignment": 0.0,
}
ADAPT_BEAR_WEIGHTS = {
    "mansfield_rs": 0.20, "momentum": 0.20, "cross_rank": 0.10,
    "breadth": 0.10, "volume_flow": 0.10, "phase_alignment": 0.30,
}

# ---------- Historico ----------
HISTORY_MAX_DAYS = 365 * 5   # guardamos hasta 5 anos en history.json

# ---------- Dashboard ----------
SUBSECTOR_TRIGGER_SCORE = 70  # solo mostramos sub-sectores si el sector lider tiene score >=

# ---------- Cache ----------
CACHE_DIR = ROOT / ".cache"
CACHE_TTL_HOURS = 6
