"""
Validacion de calidad de datos.

Antes de fiarse del resultado, comprueba que cada serie esperada:
  1) Esta presente (se descargo).
  2) Es fresca (su ultimo dato no es demasiado viejo, ajustado a su frecuencia).
  3) Tiene longitud suficiente para los calculos (z-score, medias moviles).

Con eso calcula una FIABILIDAD global. Si una serie macro que vota en la fase falla,
el modelo la promedia con menos indicadores: este modulo lo detecta y lo avisa, en vez
de devolver un resultado silenciosamente menos fiable.

La frescura se mide de forma adaptativa: estima la frecuencia real de la serie (diaria,
semanal, mensual) por el espaciado entre observaciones y marca "vieja" si el ultimo dato
supera ~4x ese espaciado. Asi una serie mensual no se marca como vieja por tener 30 dias.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import config


# Longitud minima (en observaciones) para que los calculos sean fiables.
_MIN_POINTS_MACRO = 30      # z-score necesita >=30
_MIN_POINTS_SECTOR = 220    # breadth usa media de 200

# Series macro que VOTAN en la fase (las criticas).
_VOTING_KEYS = [k for k, *_ in config.MACRO_SERIES] + [k for k, *_ in config.YAHOO_MACRO]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _typical_spacing_days(idx: pd.DatetimeIndex, tail: int = 30) -> float:
    """Espaciado mediano (en dias) entre las ultimas observaciones."""
    if len(idx) < 3:
        return 1.0
    diffs = pd.Series(idx[-tail:]).diff().dropna().dt.days
    diffs = diffs[diffs > 0]
    if diffs.empty:
        return 1.0
    return float(np.median(diffs))


@dataclass
class SeriesCheck:
    key: str
    present: bool
    n_points: int
    last_date: Optional[str]
    age_days: Optional[int]
    fresh: bool
    long_enough: bool
    ok: bool
    reason: str = ""


def _check_series(key: str, s: Optional[pd.Series], min_points: int) -> SeriesCheck:
    if s is None or len(s.dropna()) == 0:
        return SeriesCheck(key, False, 0, None, None, False, False, False, "ausente")
    s = s.dropna().sort_index()
    n = len(s)
    last = s.index[-1]
    age = (_utcnow() - last.to_pydatetime().replace(tzinfo=None)).days
    spacing = _typical_spacing_days(s.index)
    max_age = min(max(spacing * 4.0, 10.0), 90.0)  # tolerancia adaptativa, tope 90d
    fresh = age <= max_age
    long_enough = n >= min_points
    reasons = []
    if not fresh:
        reasons.append(f"vieja ({age}d, esperado <= {int(max_age)}d)")
    if not long_enough:
        reasons.append(f"corta ({n} pts, min {min_points})")
    ok = fresh and long_enough
    return SeriesCheck(
        key=key, present=True, n_points=n,
        last_date=last.strftime("%Y-%m-%d"), age_days=age,
        fresh=fresh, long_enough=long_enough, ok=ok,
        reason="; ".join(reasons),
    )


@dataclass
class QualityReport:
    reliability: float                 # 0-1 (fraccion de series macro criticas OK)
    label: str                         # "alta" | "media" | "reducida"
    macro_checks: List[SeriesCheck] = field(default_factory=list)
    sector_checks: List[SeriesCheck] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def assess(macro_data: Dict[str, pd.Series],
           sector_prices: Dict[str, pd.Series]) -> QualityReport:
    macro_checks = [
        _check_series(k, macro_data.get(k), _MIN_POINTS_MACRO) for k in _VOTING_KEYS
    ]
    sector_keys = [t for t, _n, _s in config.SECTORS] + [config.BENCHMARK]
    sector_checks = [
        _check_series(k, sector_prices.get(k), _MIN_POINTS_SECTOR) for k in sector_keys
    ]

    good_macro = sum(1 for c in macro_checks if c.ok)
    reliability = good_macro / len(macro_checks) if macro_checks else 0.0

    if reliability >= 0.9:
        label = "alta"
    elif reliability >= 0.7:
        label = "media"
    else:
        label = "reducida"

    warnings: List[str] = []
    for c in macro_checks:
        if not c.ok:
            warnings.append(f"[macro] {c.key}: {c.reason or ('ausente' if not c.present else 'problema')}")
    # Benchmark ausente es critico aparte
    bench_ok = any(c.key == config.BENCHMARK and c.ok for c in sector_checks)
    if not bench_ok:
        warnings.append(f"[sector] benchmark {config.BENCHMARK} ausente o invalido (critico)")
    missing_sectors = [c.key for c in sector_checks if not c.present and c.key != config.BENCHMARK]
    if missing_sectors:
        warnings.append(f"[sector] sin datos: {', '.join(missing_sectors)}")

    return QualityReport(
        reliability=round(reliability, 3),
        label=label,
        macro_checks=macro_checks,
        sector_checks=sector_checks,
        warnings=warnings,
    )


def quality_payload(report: QualityReport) -> Dict:
    """Version serializable para el snapshot/dashboard."""
    return {
        "reliability": report.reliability,
        "label": report.label,
        "warnings": report.warnings,
        "macro": [
            {"key": c.key, "ok": c.ok, "present": c.present, "n": c.n_points,
             "last_date": c.last_date, "age_days": c.age_days, "reason": c.reason}
            for c in report.macro_checks
        ],
        "sectors_ok": sum(1 for c in report.sector_checks if c.ok),
        "sectors_total": len(report.sector_checks),
    }
