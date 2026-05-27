"""
Tests del modelo macro.
Validamos que con datos sinteticos calibrados el modelo identifica la fase correcta.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from src import config, macro_model


def _build_series(value: float, recent_drift: float = 0.0, days: int = 365 * 11) -> pd.Series:
    dates = pd.date_range(end=datetime.utcnow().date(), periods=days, freq="D")
    # Serie casi plana en valor base, con drift hacia el final
    rng = np.random.default_rng(0)
    noise = rng.normal(0, abs(value) * 0.02 + 0.01, days)
    vals = np.full(days, value, dtype=float) + noise
    # Drift en el ultimo trimestre
    drift = np.linspace(0, recent_drift, days // 4)
    vals[-len(drift):] += drift
    return pd.Series(vals, index=dates)


def _macro_data_for_phase(phase_id: int) -> dict:
    """Construye series sinteticas alineadas con la fase deseada."""
    # Para forzar la fase modificamos los valores recientes vs el historico
    # Las series del modelo: manuf_health, yield_curve, jobless_4w, hy_oas, nfci, lei_proxy, breakeven_5y
    # signos:                +1,            +1,          -1,         -1,    -1,    +1,       -1
    # axis:                  growth        growth      growth       stress stress growth    stress

    if phase_id == 1:  # EXPANSION: growth alto, stress bajo
        out = {
            "manuf_health": _build_series(50, recent_drift=8),     # alto -> growth+
            "yield_curve":  _build_series(0.5, recent_drift=0.8),  # alta -> growth+
            "jobless_4w":   _build_series(280000, recent_drift=-60000),  # bajo -> growth+
            "hy_oas":       _build_series(4.0, recent_drift=-1.5),     # bajo -> stress-
            "nfci":         _build_series(-0.5, recent_drift=-0.5),    # bajo -> stress-
            "lei_proxy":    _build_series(0.0, recent_drift=1.2),      # alto -> growth+
            "breakeven_5y": _build_series(2.2, recent_drift=-0.4),     # bajo -> stress-
        }
    elif phase_id == 2:  # RECALENTAMIENTO: growth alto, stress alto
        out = {
            "manuf_health": _build_series(50, recent_drift=6),
            "yield_curve":  _build_series(0.5, recent_drift=0.5),
            "jobless_4w":   _build_series(280000, recent_drift=-40000),
            "hy_oas":       _build_series(4.0, recent_drift=+1.5),    # alto stress+
            "nfci":         _build_series(-0.5, recent_drift=+0.8),   # alto stress+
            "lei_proxy":    _build_series(0.0, recent_drift=0.8),
            "breakeven_5y": _build_series(2.2, recent_drift=+1.0),    # alto stress+
        }
    elif phase_id == 3:  # CONTRACCION: growth bajo, stress alto
        out = {
            "manuf_health": _build_series(50, recent_drift=-8),
            "yield_curve":  _build_series(0.5, recent_drift=-1.2),
            "jobless_4w":   _build_series(280000, recent_drift=+80000),
            "hy_oas":       _build_series(4.0, recent_drift=+3.0),
            "nfci":         _build_series(-0.5, recent_drift=+1.0),
            "lei_proxy":    _build_series(0.0, recent_drift=-1.5),
            "breakeven_5y": _build_series(2.2, recent_drift=+0.8),
        }
    elif phase_id == 4:  # DESACELERACION: growth bajo, stress bajo
        out = {
            "manuf_health": _build_series(50, recent_drift=-6),
            "yield_curve":  _build_series(0.5, recent_drift=-0.8),
            "jobless_4w":   _build_series(280000, recent_drift=+40000),
            "hy_oas":       _build_series(4.0, recent_drift=-1.0),
            "nfci":         _build_series(-0.5, recent_drift=-0.3),
            "lei_proxy":    _build_series(0.0, recent_drift=-0.8),
            "breakeven_5y": _build_series(2.2, recent_drift=-0.5),
        }
    else:
        raise ValueError(phase_id)
    return out


@pytest.mark.parametrize("phase_id", [1, 2, 3, 4])
def test_phase_identification(phase_id):
    macro_data = _macro_data_for_phase(phase_id)
    result = macro_model.run_macro_model(macro_data)
    assert result["phase"]["id"] == phase_id, (
        f"Fase esperada {phase_id}, obtenida {result['phase']['id']}. "
        f"Growth={result['axes']['growth']:.2f}, Stress={result['axes']['stress']:.2f}, "
        f"Probs={result['phase']['probabilities']}"
    )
    # Prob de la fase ganadora debe ser razonable
    top_prob = result["phase"]["probabilities"][phase_id]
    assert top_prob > 0.30, f"Probabilidad demasiado baja: {top_prob}"


def test_score_color_helpers_dont_throw():
    from src import dashboard_builder as db
    assert db._score_color(0.0).startswith("rgba")
    assert db._score_color(1.0).startswith("rgba")
    assert db._score_color(-1.0).startswith("rgba")
    assert db._score_color_100(80.0).startswith("rgba")
    assert db._score_color_100(20.0).startswith("rgba")


def test_phases_config_complete():
    for pid in (1, 2, 3, 4):
        p = config.PHASES[pid]
        assert "code" in p and "label" in p and "color" in p
        assert isinstance(p["favored_sectors"], list)
        assert isinstance(p["avoid_sectors"], list)
