"""
Calculo del sentimiento de mercado y deteccion de divergencias macro vs mercado.

Sentimiento de mercado (-1 a +1, >0 = risk-on):
  - Tendencia del SPY (precio vs SMA200 y SMA50)        peso 0.30
  - VIX invertido (z-score 12M, invertido)              peso 0.20
  - HY OAS invertido (z-score 12M, invertido)           peso 0.25
  - Breadth proxy: % de sectores con score > 50         peso 0.15
  - Relative strength de XLY/XLP (defensivo vs cyclico) peso 0.10
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import config


@dataclass
class SentimentResult:
    sentiment: float          # [-1, +1]
    label: str                # "Risk-On" | "Neutral" | "Risk-Off"
    components: Dict[str, float]
    divergence: Optional[str] # "macro_better" | "market_better" | None
    divergence_magnitude: float


def _trend_score(spy: pd.Series) -> float:
    s = spy.dropna()
    if len(s) < 210:
        return 0.0
    price = float(s.iloc[-1])
    sma50 = float(s.rolling(50).mean().iloc[-1])
    sma200 = float(s.rolling(200).mean().iloc[-1])
    above50 = 1.0 if price > sma50 else -1.0
    above200 = 1.0 if price > sma200 else -1.0
    slope200 = float((sma200 - s.rolling(200).mean().iloc[-21]) / sma200) if sma200 > 0 else 0.0
    base = above50 * 0.4 + above200 * 0.6
    return float(np.clip(0.7 * base + 0.3 * np.tanh(slope200 * 80.0), -1.0, 1.0))


def _vix_score(vix: pd.Series) -> float:
    """VIX bajo = risk-on. Devuelve score invertido."""
    s = vix.dropna()
    if len(s) < 60:
        return 0.0
    cutoff = s.index.max() - pd.Timedelta(days=365)
    window = s.loc[s.index >= cutoff]
    median = float(np.nanmedian(window))
    mad = float(np.nanmedian(np.abs(window - median)))
    val = float(s.iloc[-1])
    if mad < 1e-9:
        std = float(np.nanstd(window))
        z = (val - median) / std if std > 1e-9 else 0.0
    else:
        z = (val - median) / (1.4826 * mad)
    z = float(np.clip(z, -3.0, 3.0))
    return float(-z / 3.0)  # VIX alto -> z>0 -> sentimiento negativo


def _hy_score(hy: pd.Series) -> float:
    """HY OAS bajo = risk-on. Score invertido."""
    s = hy.dropna()
    if len(s) < 60:
        return 0.0
    cutoff = s.index.max() - pd.Timedelta(days=365)
    window = s.loc[s.index >= cutoff]
    median = float(np.nanmedian(window))
    mad = float(np.nanmedian(np.abs(window - median)))
    val = float(s.iloc[-1])
    if mad < 1e-9:
        std = float(np.nanstd(window))
        z = (val - median) / std if std > 1e-9 else 0.0
    else:
        z = (val - median) / (1.4826 * mad)
    z = float(np.clip(z, -3.0, 3.0))
    return float(-z / 3.0)


def _sector_breadth_score(sector_readings: List) -> float:
    """% de sectores con score > 50, escalado a [-1, +1]."""
    if not sector_readings:
        return 0.0
    above = sum(1 for r in sector_readings if r.score > 50.0)
    pct = above / len(sector_readings)
    return float((pct - 0.5) * 2.0)


def _risk_appetite_score(sector_prices: Dict[str, pd.Series]) -> float:
    """Discrecional (XLY) vs Staples (XLP). Si XLY supera XLP, risk-on."""
    xly = sector_prices.get("XLY")
    xlp = sector_prices.get("XLP")
    if xly is None or xlp is None:
        return 0.0
    df = pd.concat({"y": xly, "p": xlp}, axis=1).dropna()
    if len(df) < 130:
        return 0.0
    ratio = df["y"] / df["p"]
    # Comparar nivel actual contra mediana del ultimo ano
    last = float(ratio.iloc[-1])
    window = ratio.tail(252)
    median = float(np.nanmedian(window))
    if median < 1e-9:
        return 0.0
    diff = (last - median) / median
    return float(np.tanh(diff * 12.0))


def compute_sentiment(
    macro_data: Dict[str, pd.Series],
    sector_prices: Dict[str, pd.Series],
    sector_readings: List,
) -> SentimentResult:
    spy = sector_prices.get(config.BENCHMARK)
    trend = _trend_score(spy) if spy is not None else 0.0
    vix = _vix_score(macro_data["vix"]) if "vix" in macro_data else 0.0
    hy = _hy_score(macro_data["hy_oas"]) if "hy_oas" in macro_data else 0.0
    breadth = _sector_breadth_score(sector_readings)
    appetite = _risk_appetite_score(sector_prices)

    components = {
        "trend_spy": trend,
        "vix_inv": vix,
        "hy_oas_inv": hy,
        "sector_breadth": breadth,
        "risk_appetite": appetite,
    }
    weights = {
        "trend_spy": 0.30,
        "vix_inv": 0.20,
        "hy_oas_inv": 0.25,
        "sector_breadth": 0.15,
        "risk_appetite": 0.10,
    }
    sentiment = float(np.clip(sum(components[k] * w for k, w in weights.items()), -1.0, 1.0))

    if sentiment > 0.25:
        label = "Risk-On"
    elif sentiment < -0.25:
        label = "Risk-Off"
    else:
        label = "Neutral"

    return SentimentResult(
        sentiment=sentiment,
        label=label,
        components=components,
        divergence=None,
        divergence_magnitude=0.0,
    )


def detect_divergence(macro_growth: float, macro_stress: float,
                      sentiment_result: SentimentResult) -> SentimentResult:
    """Compara la salud macro vs el sentimiento de mercado."""
    macro_summary = macro_growth - macro_stress  # [-2, +2] aprox
    macro_summary_norm = float(np.clip(macro_summary / 2.0, -1.0, 1.0))
    market = sentiment_result.sentiment
    gap = market - macro_summary_norm  # market - macro
    sentiment_result.divergence_magnitude = float(abs(gap))
    if abs(gap) < 0.35:
        sentiment_result.divergence = None
    elif gap > 0:
        sentiment_result.divergence = "market_better"   # mercado optimista, macro debil
    else:
        sentiment_result.divergence = "macro_better"    # macro fuerte, mercado pesimista
    return sentiment_result


def sentiment_payload(s: SentimentResult, macro_growth: float, macro_stress: float) -> Dict:
    return {
        "sentiment": round(s.sentiment, 3),
        "label": s.label,
        "components": {k: round(v, 3) for k, v in s.components.items()},
        "divergence": s.divergence,
        "divergence_magnitude": round(s.divergence_magnitude, 3),
        "macro_summary": round(float(macro_growth - macro_stress) / 2.0, 3),
    }
