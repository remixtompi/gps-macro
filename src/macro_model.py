"""
Modelo de fase del ciclo economico.

Para cada serie macro:
  1) Calcula z-score recortado contra los ultimos 10 anos (robusto a outliers).
  2) Calcula direccion (cambio reciente vs 90 dias atras).
  3) Combina en score [-1, +1] segun el signo configurado.

Luego agrega a dos ejes:
  - growth_score  = media ponderada de las series eje "growth"
  - stress_score  = media ponderada de las series eje "stress"  (positivo = mas stress)

Mapea a 4 fases con probabilidades suaves (logistic blending):
  Fase 1 EXPANSION:        growth > 0, stress < 0
  Fase 2 RECALENTAMIENTO:  growth > 0, stress > 0
  Fase 3 CONTRACCION:      growth < 0, stress > 0
  Fase 4 DESACELERACION:   growth < 0, stress < 0
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import config


# ============== INDICADOR INDIVIDUAL ==============

@dataclass
class IndicatorReading:
    key: str
    fred_id: str
    description: str
    axis: str           # "growth" | "stress"
    sign: int           # +1 | -1
    value: float        # ultimo valor crudo
    prev_value: float   # valor de hace ~90 dias (para tendencia)
    z_score: float      # z-score recortado [-3, 3]
    momentum: float     # cambio normalizado vs 90d
    score: float        # score final [-1, +1] (>0 = bullish para crecimiento o bajo stress)
    trend: str          # "up" | "down" | "flat"
    history: List[Dict] = field(default_factory=list)  # ultimos N puntos para sparkline


def _trimmed_z(series: pd.Series, value: float, lookback_years: int = 10) -> float:
    """Z-score recortado: usa mediana y MAD para ser robusto a outliers."""
    cutoff = series.index.max() - pd.Timedelta(days=365 * lookback_years)
    sub = series.loc[series.index >= cutoff].dropna()
    if len(sub) < 30:
        return 0.0
    median = float(np.nanmedian(sub.values))
    mad = float(np.nanmedian(np.abs(sub.values - median)))
    if mad < 1e-9:
        std = float(np.nanstd(sub.values))
        if std < 1e-9:
            return 0.0
        z = (value - median) / std
    else:
        z = (value - median) / (1.4826 * mad)  # MAD a std equivalente
    return float(np.clip(z, -3.0, 3.0))


def _direction(series: pd.Series, window_days: int = 90) -> tuple[float, float]:
    """Devuelve (valor_actual, valor_hace_window_days)."""
    s = series.dropna()
    if len(s) < 2:
        return float(s.iloc[-1]) if len(s) else 0.0, 0.0
    current = float(s.iloc[-1])
    cutoff = s.index.max() - pd.Timedelta(days=window_days)
    prev_slice = s.loc[s.index <= cutoff]
    prev = float(prev_slice.iloc[-1]) if len(prev_slice) else float(s.iloc[0])
    return current, prev


def _compute_indicator(
    key: str, fred_id: str, desc: str, axis: str, sign: int, series: pd.Series
) -> IndicatorReading:
    series = series.dropna().sort_index()
    # Resample a frecuencia diaria con forward fill para series mensuales/semanales
    daily = series.resample("D").ffill()

    value, prev = _direction(daily, window_days=90)
    z = _trimmed_z(daily, value)
    # momentum normalizado: cambio % robusto, recortado
    if abs(prev) > 1e-9:
        mom = (value - prev) / (abs(prev) + 1e-9)
    else:
        mom = 0.0
    mom = float(np.clip(mom, -1.0, 1.0))

    # Score [-1, +1]: combinacion 70% nivel (z) + 30% momentum, con el signo
    raw = 0.7 * (z / 3.0) + 0.3 * mom
    score = float(np.clip(raw * sign, -1.0, 1.0))

    # Tendencia textual
    if mom * sign > 0.05:
        trend = "up"
    elif mom * sign < -0.05:
        trend = "down"
    else:
        trend = "flat"

    # Sparkline: ultimos 365 dias, sample mensual
    sparkline_series = daily.tail(365)
    sparkline = []
    for ts, val in sparkline_series.resample("W").last().dropna().items():
        sparkline.append({"d": ts.strftime("%Y-%m-%d"), "v": float(val)})

    return IndicatorReading(
        key=key,
        fred_id=fred_id,
        description=desc,
        axis=axis,
        sign=sign,
        value=value,
        prev_value=prev,
        z_score=z,
        momentum=mom,
        score=score,
        trend=trend,
        history=sparkline,
    )


# ============== AGREGACION POR EJES ==============

@dataclass
class AxisScore:
    growth: float    # [-1, +1]; >0 = crecimiento
    stress: float    # [-1, +1]; >0 = stress alto
    growth_contribs: Dict[str, float]
    stress_contribs: Dict[str, float]


def compute_indicators(macro_data: Dict[str, pd.Series]) -> List[IndicatorReading]:
    readings: List[IndicatorReading] = []
    for key, fred_id, desc, axis, sign in config.MACRO_SERIES:
        if key not in macro_data:
            continue
        try:
            readings.append(_compute_indicator(key, fred_id, desc, axis, sign, macro_data[key]))
        except Exception as e:
            print(f"[macro_model] error en {key}: {e}")

    # Yahoo-based:
    for key, _yh, desc, axis, sign in config.YAHOO_MACRO:
        if key in macro_data:
            try:
                readings.append(_compute_indicator(key, "", desc, axis, sign, macro_data[key]))
            except Exception as e:
                print(f"[macro_model] error en {key}: {e}")
    return readings


def aggregate_axes(readings: List[IndicatorReading]) -> AxisScore:
    growth_vals = []
    stress_vals = []
    growth_contribs = {}
    stress_contribs = {}

    for r in readings:
        if r.axis == "growth":
            growth_vals.append(r.score)
            growth_contribs[r.key] = r.score
        elif r.axis == "stress":
            # Para stress: invertimos signo porque el score ya tiene signo=-1 para "mas stress = peor"
            # Queremos en este eje que >0 signifique "mas stress" (la convencion del modelo).
            stress_score = -r.score
            stress_vals.append(stress_score)
            stress_contribs[r.key] = stress_score

    growth = float(np.mean(growth_vals)) if growth_vals else 0.0
    stress = float(np.mean(stress_vals)) if stress_vals else 0.0
    return AxisScore(growth=growth, stress=stress,
                     growth_contribs=growth_contribs, stress_contribs=stress_contribs)


# ============== CLASIFICACION DE FASE ==============

@dataclass
class PhaseResult:
    phase_id: int
    phase_code: str
    phase_label: str
    phase_description: str
    favored_sectors: List[str]
    avoid_sectors: List[str]
    probabilities: Dict[int, float]  # {fase: prob}
    color: str
    growth_score: float
    stress_score: float


def _softmax(x: np.ndarray, tau: float = 0.5) -> np.ndarray:
    x = x / tau
    x = x - x.max()
    e = np.exp(x)
    return e / e.sum()


def classify_phase(axis: AxisScore) -> PhaseResult:
    """Asigna probabilidad suave a cada fase basada en posicion en plano growth/stress."""
    # Centros de cada fase en el plano (growth, stress)
    centers = {
        1: ( 0.6, -0.6),   # EXPANSION
        2: ( 0.6,  0.6),   # RECALENTAMIENTO
        3: (-0.6,  0.6),   # CONTRACCION
        4: (-0.6, -0.6),   # DESACELERACION
    }
    pt = np.array([axis.growth, axis.stress])
    # Distancia euclidiana al centro de cada fase
    dists = np.array([
        np.linalg.norm(pt - np.array(centers[k]))
        for k in (1, 2, 3, 4)
    ])
    # Convertir distancia en logit: cuanto menor, mayor probabilidad
    logits = -dists
    probs = _softmax(logits, tau=0.45)
    probs_dict = {k: float(probs[i]) for i, k in enumerate((1, 2, 3, 4))}
    top_phase = int(max(probs_dict, key=probs_dict.get))
    pdef = config.PHASES[top_phase]
    return PhaseResult(
        phase_id=top_phase,
        phase_code=pdef["code"],
        phase_label=pdef["label"],
        phase_description=pdef["description"],
        favored_sectors=pdef["favored_sectors"],
        avoid_sectors=pdef["avoid_sectors"],
        probabilities=probs_dict,
        color=pdef["color"],
        growth_score=axis.growth,
        stress_score=axis.stress,
    )


# ============== API DE ALTO NIVEL ==============

def run_macro_model(macro_data: Dict[str, pd.Series]) -> Dict:
    """Ejecuta todo el pipeline macro y devuelve un dict serializable."""
    readings = compute_indicators(macro_data)
    axes = aggregate_axes(readings)
    phase = classify_phase(axes)

    indicators_payload = []
    for r in readings:
        indicators_payload.append({
            "key": r.key,
            "fred_id": r.fred_id,
            "description": r.description,
            "axis": r.axis,
            "value": r.value,
            "prev_value": r.prev_value,
            "z_score": r.z_score,
            "momentum": r.momentum,
            "score": r.score,
            "trend": r.trend,
            "sparkline": r.history,
        })

    return {
        "phase": {
            "id": phase.phase_id,
            "code": phase.phase_code,
            "label": phase.phase_label,
            "description": phase.phase_description,
            "favored_sectors": phase.favored_sectors,
            "avoid_sectors": phase.avoid_sectors,
            "probabilities": phase.probabilities,
            "color": phase.color,
        },
        "axes": {
            "growth": axes.growth,
            "stress": axes.stress,
            "growth_contribs": axes.growth_contribs,
            "stress_contribs": axes.stress_contribs,
        },
        "indicators": indicators_payload,
    }
