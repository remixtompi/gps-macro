"""
Tests del cableado de ADAPT en produccion: regimen de tendencia y ranking adaptativo.
"""
import numpy as np
import pandas as pd

from src import sector_rotation as sr
from src import config


def test_compute_regime_bull_and_bear():
    idx = pd.bdate_range("2020-01-01", periods=300)
    # Precio subiendo fuerte -> ultimo > media -> bull
    up = pd.Series(np.linspace(100, 200, 300), index=idx)
    assert sr.compute_regime(up) is True
    # Precio bajando -> ultimo < media -> bear
    down = pd.Series(np.linspace(200, 100, 300), index=idx)
    assert sr.compute_regime(down) is False


def test_compute_regime_insufficient_data_defaults_bull():
    idx = pd.bdate_range("2020-01-01", periods=50)
    short = pd.Series(np.linspace(100, 90, 50), index=idx)
    assert sr.compute_regime(short) is True  # sin datos suficientes, neutro=bull


def _reading(ticker, **components):
    base = {"mansfield_rs": 0.0, "momentum": 0.0, "cross_rank": 0.0,
            "breadth": 0.0, "volume_flow": 0.0, "phase_alignment": 0.0}
    base.update(components)
    return sr.SectorReading(
        ticker=ticker, name=ticker, short=ticker, score=50.0, components=base,
        rank=0, momentum={}, rs_mansfield=0.0, breadth_proxy=0.0, volume_flow=0.0,
        phase_alignment=base["phase_alignment"], last_price=100.0, pct_change_1d=0.0, spark=[],
    )


def test_adaptive_ranking_bull_favors_momentum():
    readings = [
        _reading("XLK", momentum=0.9, phase_alignment=-1.0),  # ciclico fuerte
        _reading("XLP", momentum=-0.2, phase_alignment=1.0),  # defensivo favorecido
    ]
    bull = sr.adaptive_ranking(readings, bull=True, top_n=1)
    bear = sr.adaptive_ranking(readings, bull=False, top_n=1)
    assert bull[0]["ticker"] == "XLK"
    assert bear[0]["ticker"] == "XLP"


def test_adaptive_ranking_payload_shape():
    readings = [_reading("XLK", momentum=0.5)]
    out = sr.adaptive_ranking(readings, bull=True, top_n=3)
    assert out and set(["ticker", "name", "short", "score", "pct_1d"]).issubset(out[0])
    assert 0 <= out[0]["score"] <= 100


def test_adapt_weights_sum_to_one():
    assert abs(sum(config.ADAPT_BULL_WEIGHTS.values()) - 1.0) < 1e-9
    assert abs(sum(config.ADAPT_BEAR_WEIGHTS.values()) - 1.0) < 1e-9
