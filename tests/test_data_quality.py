"""
Tests de validacion de calidad de datos (sin red).
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from src import data_quality as dq
from src import config


def _fresh_series(n=400, freq="D", end=None):
    end = end or datetime.now(timezone.utc).date()
    idx = pd.date_range(end=end, periods=n, freq=freq)
    return pd.Series(np.arange(len(idx), dtype=float), index=idx)


def _full_macro():
    keys = [k for k, *_ in config.MACRO_SERIES] + [k for k, *_ in config.YAHOO_MACRO]
    return {k: _fresh_series() for k in keys}


def _full_sectors():
    out = {t: _fresh_series() for t, _n, _s in config.SECTORS}
    out[config.BENCHMARK] = _fresh_series()
    return out


def test_all_good_high_reliability():
    rep = dq.assess(_full_macro(), _full_sectors())
    assert rep.reliability == 1.0
    assert rep.label == "alta"
    assert rep.warnings == []


def test_missing_macro_series_lowers_reliability():
    macro = _full_macro()
    del macro["vix"]
    del macro["yield_curve"]
    rep = dq.assess(macro, _full_sectors())
    assert rep.reliability < 1.0
    assert any("vix" in w for w in rep.warnings)
    assert any("yield_curve" in w for w in rep.warnings)


def test_stale_series_flagged():
    macro = _full_macro()
    # serie diaria pero con ultimo dato de hace 120 dias -> vieja
    old_end = (datetime.now(timezone.utc).date() - timedelta(days=120))
    macro["vix"] = _fresh_series(end=old_end)
    rep = dq.assess(macro, _full_sectors())
    assert any("vix" in w and "vieja" in w for w in rep.warnings)


def test_monthly_series_not_flagged_as_stale():
    """Una serie mensual con ~30 dias de antiguedad NO debe marcarse vieja."""
    macro = _full_macro()
    # serie mensual: ultimo punto hace ~31 dias es normal
    end = datetime.now(timezone.utc).date() - timedelta(days=31)
    macro["manuf_health"] = _fresh_series(n=120, freq="ME", end=end)
    rep = dq.assess(macro, _full_sectors())
    assert not any("manuf_health" in w for w in rep.warnings)


def test_missing_benchmark_is_critical():
    sectors = _full_sectors()
    del sectors[config.BENCHMARK]
    rep = dq.assess(_full_macro(), sectors)
    assert any("benchmark" in w.lower() for w in rep.warnings)


def test_label_thresholds():
    keys = [k for k, *_ in config.MACRO_SERIES] + [k for k, *_ in config.YAHOO_MACRO]
    # dejar solo ~60% OK -> reducida
    macro = {k: _fresh_series() for k in keys[:5]}
    rep = dq.assess(macro, _full_sectors())
    assert rep.label in ("media", "reducida")


def test_payload_serializable():
    rep = dq.assess(_full_macro(), _full_sectors())
    p = dq.quality_payload(rep)
    assert set(["reliability", "label", "warnings", "macro", "sectors_ok", "sectors_total"]).issubset(p)
    assert p["sectors_total"] == len(config.SECTORS) + 1
