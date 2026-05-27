"""
Generador de datos sinteticos.
Permite probar el pipeline sin FRED API key ni conexion a internet.
Las series tienen forma realista (trends + ruido) pero NO son datos reales.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict

import numpy as np
import pandas as pd

from . import config


SEED = 42


def _date_range(years: int = 12) -> pd.DatetimeIndex:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=int(365 * years))
    return pd.date_range(start=start, end=end, freq="B")  # business days


def _synthetic_series(name: str, base: float, vol: float, trend: float, cycle_amp: float,
                      cycle_period: int = 252 * 4, dates: pd.DatetimeIndex = None) -> pd.Series:
    rng = np.random.default_rng(abs(hash(name)) % (2**32))
    n = len(dates)
    t = np.arange(n)
    cycle = cycle_amp * np.sin(2 * np.pi * t / cycle_period)
    drift = trend * t / 252
    noise = rng.normal(0, vol, n).cumsum() * 0.1
    out = base + cycle + drift + noise
    return pd.Series(out, index=dates, name=name)


def build_synthetic_macro() -> Dict[str, pd.Series]:
    dates = _date_range(12)
    out: Dict[str, pd.Series] = {}
    # 8 principales
    out["manuf_health"] = _synthetic_series("manuf_health", base=50.0, vol=1.0, trend=0.1,  cycle_amp=4.0, dates=dates)
    out["yield_curve"]  = _synthetic_series("yield_curve",  base=0.5,  vol=0.05, trend=0.0, cycle_amp=1.2, dates=dates)
    out["jobless_4w"]   = _synthetic_series("jobless_4w",   base=250000, vol=8000, trend=20, cycle_amp=60000, dates=dates)
    out["hy_oas"]       = _synthetic_series("hy_oas",       base=4.0,  vol=0.15, trend=0.0, cycle_amp=1.8, dates=dates)
    out["nfci"]         = _synthetic_series("nfci",         base=-0.5, vol=0.05, trend=0.0, cycle_amp=0.6, dates=dates)
    out["lei_proxy"]    = _synthetic_series("lei_proxy",    base=0.0,  vol=0.05, trend=0.0, cycle_amp=1.0, dates=dates)
    out["breakeven_5y"] = _synthetic_series("breakeven_5y", base=2.2,  vol=0.05, trend=0.0, cycle_amp=0.5, dates=dates)

    # Secundarias
    out["m2_yoy"]       = _synthetic_series("m2_yoy",       base=5.0,  vol=0.2,  trend=0.0, cycle_amp=4.0, dates=dates)
    out["real_yield"]   = _synthetic_series("real_yield",   base=1.0,  vol=0.05, trend=0.0, cycle_amp=1.0, dates=dates)
    out["retail_sales"] = _synthetic_series("retail_sales", base=500.0, vol=4.0, trend=0.3, cycle_amp=30.0, dates=dates)
    out["permits"]      = _synthetic_series("permits",      base=1400, vol=20,   trend=0.0, cycle_amp=200, dates=dates)
    out["dxy"]          = _synthetic_series("dxy",          base=100.0, vol=0.5, trend=0.0, cycle_amp=8.0, dates=dates)

    # Yahoo proxies
    out["copper_gold"]  = _synthetic_series("copper_gold",  base=0.0018, vol=0.00005, trend=0.0, cycle_amp=0.0003, dates=dates)
    out["vix"]          = _synthetic_series("vix",          base=18.0, vol=0.8, trend=0.0, cycle_amp=8.0, dates=dates).clip(lower=10.0)
    return out


def build_synthetic_sectors() -> Dict[str, pd.Series]:
    dates = _date_range(years=6)
    out: Dict[str, pd.Series] = {}
    rng = np.random.default_rng(SEED)

    # SPY baseline
    n = len(dates)
    base_returns = rng.normal(0.0005, 0.01, n)  # ~12% anual con vol diaria 1%
    spy = 400 * np.exp(np.cumsum(base_returns))
    out[config.BENCHMARK] = pd.Series(spy, index=dates, name=config.BENCHMARK)

    # Cada sector = SPY + beta * extra_return; algunos sectores con beta especifico
    sector_specs = {
        "XLK":  (1.20,  0.0003),
        "XLF":  (1.05, -0.0001),
        "XLE":  (0.95,  0.0001),
        "XLI":  (1.05,  0.0001),
        "XLV":  (0.85,  0.0001),
        "XLY":  (1.15,  0.0002),
        "XLP":  (0.60,  0.0000),
        "XLB":  (1.05,  0.0000),
        "XLU":  (0.55, -0.0001),
        "XLRE": (0.85, -0.0001),
        "XLC":  (1.10,  0.0001),
    }
    for ticker, (beta, alpha) in sector_specs.items():
        # rets sectoriales con beta sobre returns SPY + idiosincratico
        idio = rng.normal(0, 0.008, n)
        sec_rets = beta * base_returns + alpha + idio
        prices = 100 * np.exp(np.cumsum(sec_rets))
        out[ticker] = pd.Series(prices, index=dates, name=ticker)
    return out
