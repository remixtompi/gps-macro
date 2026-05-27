"""
Score Compuesto de Rotacion Sectorial.

Para cada uno de los 11 sectores GICS:
  1) Mansfield Relative Strength (52-week)         peso 0.25
  2) Momentum multi-periodo (1M/3M/6M)             peso 0.30
  3) Cross-sectional rank                          peso 0.15
  4) Breadth interno (proxy via posicion en banda) peso 0.10
  5) Volume flow (OBV pendiente vs SPY)            peso 0.10
  6) Phase alignment (modelo Stovall)              peso 0.10

Score final escalado 0-100.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import config


@dataclass
class SectorReading:
    ticker: str
    name: str
    short: str
    score: float                # 0-100 final
    components: Dict[str, float]  # sub-componentes (cada uno -1 a +1 normalizado)
    rank: int                    # 1 = mejor
    momentum: Dict[str, float]   # m1, m3, m6 (retorno relativo vs SPY en %)
    rs_mansfield: float
    breadth_proxy: float
    volume_flow: float
    phase_alignment: float
    last_price: float
    pct_change_1d: float
    spark: List[Dict]            # ultimos N puntos ratio sector/SPY


def _align(s1: pd.Series, s2: pd.Series) -> tuple[pd.Series, pd.Series]:
    df = pd.concat({"a": s1, "b": s2}, axis=1).dropna()
    return df["a"], df["b"]


def _mansfield_rs(sector: pd.Series, bench: pd.Series, window: int = 252) -> float:
    """Mansfield RS normalizada. >0 = sector mas fuerte que el indice (en escala normalizada)."""
    a, b = _align(sector, bench)
    if len(a) < window + 5:
        return 0.0
    ratio = a / b
    mavg = ratio.rolling(window=window, min_periods=int(window * 0.7)).mean()
    rs = (ratio / mavg - 1.0) * 100.0
    val = rs.iloc[-1]
    if pd.isna(val):
        return 0.0
    # Normalizar a [-1, +1] mediante tanh
    return float(np.tanh(val / 5.0))


def _momentum_components(sector: pd.Series, bench: pd.Series) -> Dict[str, float]:
    """Retornos relativos vs SPY a 1M / 3M / 6M."""
    a, b = _align(sector, bench)
    out = {}
    for label, days in config.MOMENTUM_PERIODS.items():
        if len(a) < days + 1:
            out[label] = 0.0
            continue
        sec_ret = (a.iloc[-1] / a.iloc[-1 - days]) - 1.0
        bench_ret = (b.iloc[-1] / b.iloc[-1 - days]) - 1.0
        out[label] = float(sec_ret - bench_ret)
    return out


def _composite_momentum(momentum: Dict[str, float]) -> float:
    """Combina los 3 momentums con sus pesos y normaliza con tanh."""
    raw = sum(momentum.get(k, 0.0) * w for k, w in config.MOMENTUM_WEIGHTS.items())
    return float(np.tanh(raw * 10.0))   # 10% rel = score ~1


def _breadth_proxy(sector: pd.Series) -> float:
    """Proxy de breadth: distancia normalizada del precio a sus medias 50 y 200."""
    s = sector.dropna()
    if len(s) < 220:
        return 0.0
    ma50 = s.rolling(50).mean().iloc[-1]
    ma200 = s.rolling(200).mean().iloc[-1]
    price = s.iloc[-1]
    above50 = 1.0 if price > ma50 else -1.0
    above200 = 1.0 if price > ma200 else -1.0
    # Distancia % por encima de la 200 (para magnitud)
    dist = (price - ma200) / ma200 if ma200 > 0 else 0.0
    base = (above50 * 0.4 + above200 * 0.6)
    magnitude = float(np.tanh(dist * 8.0))
    return float(np.clip(0.6 * base + 0.4 * magnitude, -1.0, 1.0))


def _volume_flow(sector_price: pd.Series, sector_vol: Optional[pd.Series],
                 bench_price: pd.Series, bench_vol: Optional[pd.Series]) -> float:
    """Pendiente del OBV del sector relativa al benchmark (60 dias)."""
    if sector_vol is None or bench_vol is None:
        return 0.0
    # OBV: suma de volumen con signo del cambio diario
    def obv(price: pd.Series, vol: pd.Series) -> pd.Series:
        p, v = _align(price, vol)
        sign = np.sign(p.diff().fillna(0.0))
        return (sign * v).cumsum()

    try:
        obv_sec = obv(sector_price, sector_vol).tail(60)
        obv_ben = obv(bench_price, bench_vol).tail(60)
        if len(obv_sec) < 20 or len(obv_ben) < 20:
            return 0.0
        # Normalizar a [0,1] dentro de la ventana, calcular pendiente lineal
        def slope_norm(s: pd.Series) -> float:
            arr = s.values.astype(float)
            if arr.max() == arr.min():
                return 0.0
            arr = (arr - arr.min()) / (arr.max() - arr.min())
            x = np.arange(len(arr))
            slope = np.polyfit(x, arr, 1)[0]
            return float(slope)
        rel = slope_norm(obv_sec) - slope_norm(obv_ben)
        return float(np.tanh(rel * 30.0))
    except Exception:
        return 0.0


def _phase_alignment(ticker: str, phase_id: int) -> float:
    """Bonus/penalty por alineacion del sector con la fase macro detectada."""
    pdef = config.PHASES.get(phase_id, {})
    fav = set(pdef.get("favored_sectors", []))
    avoid = set(pdef.get("avoid_sectors", []))
    if ticker in fav:
        return 1.0
    if ticker in avoid:
        return -1.0
    return 0.0


def _ratio_sparkline(sector: pd.Series, bench: pd.Series, weeks: int = 52) -> List[Dict]:
    a, b = _align(sector, bench)
    if len(a) < 5:
        return []
    ratio = (a / b).tail(int(weeks * 5))
    # muestrear semanal
    weekly = ratio.resample("W").last().dropna()
    out = []
    for ts, val in weekly.items():
        out.append({"d": ts.strftime("%Y-%m-%d"), "v": float(val)})
    return out


def compute_sector_scores(
    sector_prices: Dict[str, pd.Series],
    sector_volumes: Optional[Dict[str, pd.Series]],
    phase_id: int,
) -> List[SectorReading]:
    """Calcula scores para los 11 sectores. Devuelve lista ordenada por score descendente."""
    bench_price = sector_prices.get(config.BENCHMARK)
    if bench_price is None or bench_price.empty:
        raise ValueError(f"Benchmark {config.BENCHMARK} no disponible.")
    bench_vol = (sector_volumes or {}).get(config.BENCHMARK)

    readings: List[SectorReading] = []

    # Primera pasada: componentes brutos
    raw_components = []
    for ticker, name, short in config.SECTORS:
        sp = sector_prices.get(ticker)
        if sp is None or sp.empty:
            continue
        sv = (sector_volumes or {}).get(ticker)

        rs = _mansfield_rs(sp, bench_price)
        mom = _momentum_components(sp, bench_price)
        mom_comp = _composite_momentum(mom)
        breadth = _breadth_proxy(sp)
        vflow = _volume_flow(sp, sv, bench_price, bench_vol)
        align = _phase_alignment(ticker, phase_id)

        last_price = float(sp.iloc[-1])
        pct_1d = float((sp.iloc[-1] / sp.iloc[-2] - 1.0) * 100.0) if len(sp) > 1 else 0.0

        raw_components.append({
            "ticker": ticker, "name": name, "short": short,
            "rs": rs, "mom_comp": mom_comp, "mom_breakdown": mom,
            "breadth": breadth, "vflow": vflow, "align": align,
            "last_price": last_price, "pct_1d": pct_1d, "sp": sp,
        })

    # Cross-sectional rank: ranking 1-N por momentum compuesto (mas alto = mejor)
    sorted_by_mom = sorted(raw_components, key=lambda x: x["mom_comp"], reverse=True)
    rank_map = {item["ticker"]: i for i, item in enumerate(sorted_by_mom)}
    n = max(len(raw_components), 1)

    # Construir scores finales
    final = []
    for item in raw_components:
        # rank normalizado en [-1, +1]: top = +1, bottom = -1
        r = rank_map[item["ticker"]]
        rank_norm = 1.0 - (2.0 * r / max(n - 1, 1))

        components = {
            "mansfield_rs": item["rs"],
            "momentum": item["mom_comp"],
            "cross_rank": rank_norm,
            "breadth": item["breadth"],
            "volume_flow": item["vflow"],
            "phase_alignment": item["align"],
        }

        # Score ponderado -> [-1, +1] -> 0-100
        weighted = sum(components[k] * w for k, w in config.SECTOR_SCORE_WEIGHTS.items())
        weighted = float(np.clip(weighted, -1.0, 1.0))
        score_100 = 50.0 + weighted * 50.0

        final.append(SectorReading(
            ticker=item["ticker"],
            name=item["name"],
            short=item["short"],
            score=score_100,
            components=components,
            rank=0,  # lo asignamos despues por score final
            momentum=item["mom_breakdown"],
            rs_mansfield=item["rs"],
            breadth_proxy=item["breadth"],
            volume_flow=item["vflow"],
            phase_alignment=item["align"],
            last_price=item["last_price"],
            pct_change_1d=item["pct_1d"],
            spark=_ratio_sparkline(item["sp"], bench_price),
        ))

    # Ordenar por score y asignar rank
    final.sort(key=lambda x: x.score, reverse=True)
    for i, r in enumerate(final):
        r.rank = i + 1

    return final


def compute_subsector_scores(
    sub_prices: Dict[str, pd.Series],
    sub_names: Dict[str, str],
    bench: pd.Series,
) -> List[Dict]:
    """Score sencillo para sub-sectores: solo Mansfield RS + momentum compuesto."""
    out = []
    for ticker, price in sub_prices.items():
        if price is None or price.empty:
            continue
        rs = _mansfield_rs(price, bench)
        mom = _momentum_components(price, bench)
        mc = _composite_momentum(mom)
        score = 50.0 + np.clip(0.5 * rs + 0.5 * mc, -1.0, 1.0) * 50.0
        out.append({
            "ticker": ticker,
            "name": sub_names.get(ticker, ticker),
            "score": float(score),
            "rs": float(rs),
            "momentum_1m": float(mom.get("m1", 0.0)),
            "momentum_3m": float(mom.get("m3", 0.0)),
            "last_price": float(price.iloc[-1]),
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def sector_payload(readings: List[SectorReading]) -> List[Dict]:
    """Convierte readings a JSON serializable."""
    return [
        {
            "ticker": r.ticker,
            "name": r.name,
            "short": r.short,
            "score": round(r.score, 2),
            "rank": r.rank,
            "components": {k: round(v, 3) for k, v in r.components.items()},
            "momentum": {k: round(v, 4) for k, v in r.momentum.items()},
            "rs_mansfield": round(r.rs_mansfield, 3),
            "breadth": round(r.breadth_proxy, 3),
            "volume_flow": round(r.volume_flow, 3),
            "phase_alignment": round(r.phase_alignment, 3),
            "last_price": round(r.last_price, 2),
            "pct_1d": round(r.pct_change_1d, 2),
            "spark": r.spark,
        }
        for r in readings
    ]
