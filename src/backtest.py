"""
Backtest / validacion historica de GPS_MACRO.

Reconstruye, mes a mes hacia atras y SIN mirar el futuro (point-in-time), la fase
del ciclo economico y el ranking sectorial, y simula estrategias de rotacion
comparandolas con comprar-y-mantener el SPY. Responde a la pregunta clave:

    "Las senales de GPS_MACRO habrian generado valor frente a comprar el indice?"

Estrategias evaluadas
---------------------
  - TOPN  : equiponderar los N sectores con mayor SCORE COMPUESTO (el motor completo).
  - PHASE : comprar los sectores 'favored' de la fase macro vigente (teoria pura Stovall,
            sin la capa tecnica). Sirve para aislar cuanto aporta el score sobre la teoria.
  - SPY   : benchmark comprar-y-mantener.

Anti-lookahead
--------------
En cada fecha de rebalanceo `d` los scores se calculan usando UNICAMENTE datos hasta
`d` (truncando cada serie con `.loc[:d]`). La cartera resultante se mantiene a partir
del dia SIGUIENTE (`d+1`). Asi ningun retorno usa informacion futura.

Salidas
-------
  - docs/data/backtest.json : metricas + curvas de capital + timeline de fase.
  - docs/backtest.html      : informe visual autocontenido (grafico SVG, cero dependencias).

Uso
---
  python -m src.backtest                      # datos reales (requiere FRED_API_KEY)
  python -m src.backtest --synthetic          # 20 anos sinteticos (valida la logica offline)
  python -m src.backtest --years 18 --top 3 --rebalance M
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import config
from . import macro_model
from . import sector_rotation


TRADING_DAYS = 252


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ============================================================
# 1. CARGA DE DATOS (historico largo, point-in-time)
# ============================================================

def load_real_data(years: int, yahoo_only: bool = False) -> tuple[Dict[str, pd.Series], Dict[str, pd.Series], Dict[str, pd.Series]]:
    """Descarga historico largo desde FRED + Yahoo (sin tocar el cache de 6 anos del
    pipeline diario, gracias a que la clave de cache ahora incluye los anos).

    yahoo_only=True omite FRED por completo: la fase macro se calcula solo con
    Copper/Gold (crecimiento) y VIX (stress). Util para validar la rotacion sectorial
    con datos reales cuando no hay FRED_API_KEY disponible. La fase queda simplificada."""
    from . import data_fetcher as df

    macro: Dict[str, pd.Series] = {}
    if not yahoo_only:
        for key, fred_id, _desc, _axis, _sign in config.MACRO_SERIES:
            try:
                macro[key] = df.fetch_fred_series(fred_id, years=years)
            except Exception as e:
                _log(f"  [macro] fallo {key}({fred_id}): {e}")
    # Copper/Gold y VIX desde Yahoo
    try:
        hg = df.fetch_yahoo_close("HG=F", years=years)
        gc = df.fetch_yahoo_close("GC=F", years=years)
        joined = pd.concat({"HG": hg, "GC": gc}, axis=1).dropna()
        macro["copper_gold"] = (joined["HG"] / joined["GC"]).rename("copper_gold")
    except Exception as e:
        _log(f"  [macro] fallo copper_gold: {e}")
    try:
        macro["vix"] = df.fetch_yahoo_close("^VIX", years=years)
    except Exception as e:
        _log(f"  [macro] fallo vix: {e}")

    sectors: Dict[str, pd.Series] = {}
    for ticker, _name, _short in config.SECTORS:
        try:
            sectors[ticker] = df.fetch_yahoo_close(ticker, years=years)
        except Exception as e:
            _log(f"  [sector] fallo {ticker}: {e}")
    try:
        sectors[config.BENCHMARK] = df.fetch_yahoo_close(config.BENCHMARK, years=years)
    except Exception as e:
        _log(f"  [sector] fallo benchmark {config.BENCHMARK}: {e}")
    # RSP = S&P 500 equiponderado (benchmark JUSTO para una rotacion equiponderada)
    try:
        sectors["RSP"] = df.fetch_yahoo_close("RSP", years=years)
    except Exception as e:
        _log(f"  [sector] fallo benchmark RSP: {e}")

    volumes: Dict[str, pd.Series] = {}
    for ticker, _name, _short in config.SECTORS:
        try:
            volumes[ticker] = df.fetch_yahoo_volume(ticker, years=years)
        except Exception as e:
            _log(f"  [vol] fallo {ticker}: {e}")
    try:
        volumes[config.BENCHMARK] = df.fetch_yahoo_volume(config.BENCHMARK, years=years)
    except Exception:
        pass

    return macro, sectors, volumes


def load_synthetic_data(years: int) -> tuple[Dict[str, pd.Series], Dict[str, pd.Series], Dict[str, pd.Series]]:
    """Genera historico largo sintetico con varios ciclos macro y dispersion sectorial.
    NO son datos reales: sirve para validar que el motor del backtest funciona offline."""
    from .synthetic_data import _synthetic_series

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=int(365 * years))
    dates = pd.date_range(start=start, end=end, freq="B")
    rng = np.random.default_rng(7)

    macro: Dict[str, pd.Series] = {}
    macro["manuf_health"] = _synthetic_series("bt_manuf", 50.0, 1.0, 0.0, 5.0, 252 * 4, dates)
    macro["yield_curve"]  = _synthetic_series("bt_yc",    0.8,  0.05, 0.0, 1.4, 252 * 4, dates)
    macro["jobless_4w"]   = _synthetic_series("bt_jobl",  260000, 7000, 0.0, 80000, 252 * 4, dates)
    macro["hy_oas"]       = _synthetic_series("bt_hy",    4.2,  0.15, 0.0, 2.2, 252 * 4, dates)
    macro["nfci"]         = _synthetic_series("bt_nfci", -0.3,  0.05, 0.0, 0.8, 252 * 4, dates)
    macro["lei_proxy"]    = _synthetic_series("bt_lei",   0.0,  0.05, 0.0, 1.2, 252 * 4, dates)
    macro["breakeven_5y"] = _synthetic_series("bt_be",    2.2,  0.05, 0.0, 0.6, 252 * 4, dates)
    macro["copper_gold"]  = _synthetic_series("bt_cg", 0.0018, 0.00005, 0.0, 0.0004, 252 * 4, dates)
    macro["vix"]          = _synthetic_series("bt_vix",  18.0, 0.8,  0.0, 9.0, 252 * 4, dates).clip(lower=10.0)

    # Sectores: SPY + beta + alfa que rota con el ciclo (para que la rotacion tenga algo que capturar)
    n = len(dates)
    base_ret = rng.normal(0.0004, 0.01, n)
    spy = 200 * np.exp(np.cumsum(base_ret))
    sectors: Dict[str, pd.Series] = {config.BENCHMARK: pd.Series(spy, index=dates, name=config.BENCHMARK)}
    volumes: Dict[str, pd.Series] = {config.BENCHMARK: pd.Series(rng.uniform(7e7, 1.2e8, n), index=dates)}

    t = np.arange(n)
    cycle = np.sin(2 * np.pi * t / (252 * 4))  # mismo periodo que el macro
    sector_specs = {
        "XLK": (1.20, +1), "XLF": (1.05, +1), "XLE": (0.95, +1), "XLI": (1.05, +1),
        "XLV": (0.85, -1), "XLY": (1.15, +1), "XLP": (0.60, -1), "XLB": (1.05, +1),
        "XLU": (0.55, -1), "XLRE": (0.85, -1), "XLC": (1.10, +1),
    }
    for ticker, (beta, cyc_sign) in sector_specs.items():
        idio = rng.normal(0, 0.007, n)
        # alfa ciclico: ciclicos (+1) suben cuando cycle>0; defensivos (-1) al reves
        cyc_alpha = 0.0006 * cyc_sign * cycle
        sec_ret = beta * base_ret + cyc_alpha + idio
        prices = 100 * np.exp(np.cumsum(sec_ret))
        sectors[ticker] = pd.Series(prices, index=dates, name=ticker)
        volumes[ticker] = pd.Series(rng.uniform(1e7, 4e7, n), index=dates)

    # RSP sintetico = indice equiponderado de los 11 sectores (rebasado a 100)
    sec_norm = [sectors[t] / sectors[t].iloc[0] for t, _n, _s in config.SECTORS if t in sectors]
    if sec_norm:
        sectors["RSP"] = (sum(sec_norm) / len(sec_norm) * 100).rename("RSP")

    return macro, sectors, volumes


# ============================================================
# 2. RECONSTRUCCION POINT-IN-TIME
# ============================================================

@dataclass
class RebalancePoint:
    date: pd.Timestamp
    phase_id: int
    phase_code: str
    growth: float
    stress: float
    leader: Optional[str]
    leader_score: Optional[float]
    topn: List[str]          # seleccion estrategia TOPN
    favored: List[str]       # seleccion estrategia PHASE (favored con datos disponibles)


def _truncate(data: Dict[str, pd.Series], upto: pd.Timestamp) -> Dict[str, pd.Series]:
    out = {}
    for k, s in data.items():
        sub = s.loc[s.index <= upto]
        if len(sub) > 0:
            out[k] = sub
    return out


def _rebalance_dates(trading: pd.DatetimeIndex, freq: str, warmup_days: int) -> List[pd.Timestamp]:
    """Ultimo dia habil de cada periodo, dejando un warm-up inicial para que las medias
    moviles (252d Mansfield, 200d breadth) tengan datos."""
    if len(trading) <= warmup_days:
        return []
    eligible = trading[warmup_days:]
    s = pd.Series(eligible, index=eligible)
    rule = {"M": "ME", "W": "W", "Q": "QE"}.get(freq, "ME")
    last_of_period = s.resample(rule).last().dropna()
    return [pd.Timestamp(d) for d in last_of_period.values]


def reconstruct(
    macro: Dict[str, pd.Series],
    sectors: Dict[str, pd.Series],
    volumes: Optional[Dict[str, pd.Series]],
    top_n: int,
    freq: str,
) -> List[RebalancePoint]:
    bench = sectors.get(config.BENCHMARK)
    if bench is None or bench.empty:
        raise ValueError("Falta el benchmark SPY en los datos.")
    trading = bench.dropna().index
    # warm-up: 1 ano de calendario (~252 dias habiles) + margen
    warmup = 280
    rebal_dates = _rebalance_dates(trading, freq, warmup)
    _log(f"  Fechas de rebalanceo: {len(rebal_dates)} ({freq})  "
         f"[{rebal_dates[0].date()} -> {rebal_dates[-1].date()}]")

    points: List[RebalancePoint] = []
    for i, d in enumerate(rebal_dates):
        macro_T = _truncate(macro, d)
        if len(macro_T) < 2:  # minimo: 1 serie de crecimiento + 1 de stress
            continue
        try:
            mp = macro_model.run_macro_model(macro_T)
        except Exception as e:
            _log(f"  [reconstruct] macro fallo en {d.date()}: {e}")
            continue
        phase = mp["phase"]
        phase_id = phase["id"]

        sectors_T = _truncate(sectors, d)
        vols_T = _truncate(volumes, d) if volumes else None
        try:
            readings = sector_rotation.compute_sector_scores(sectors_T, vols_T, phase_id)
        except Exception as e:
            _log(f"  [reconstruct] sectores fallo en {d.date()}: {e}")
            continue
        if not readings:
            continue

        topn = [r.ticker for r in readings[:top_n]]
        # PHASE: favored de la fase, pero solo los que ya tienen suficiente historia
        avail = {r.ticker for r in readings}
        favored = [t for t in phase["favored_sectors"] if t in avail]

        leader = readings[0]
        points.append(RebalancePoint(
            date=d,
            phase_id=phase_id,
            phase_code=phase["code"],
            growth=mp["axes"]["growth"],
            stress=mp["axes"]["stress"],
            leader=leader.ticker,
            leader_score=round(leader.score, 1),
            topn=topn,
            favored=favored,
        ))
        if (i + 1) % 24 == 0:
            _log(f"  ...{i + 1}/{len(rebal_dates)} rebalanceos reconstruidos")

    return points


# ============================================================
# 3. SIMULACION DE ESTRATEGIAS (curvas de capital diarias)
# ============================================================

def _daily_returns_frame(sectors: Dict[str, pd.Series], trading: pd.DatetimeIndex) -> pd.DataFrame:
    cols = {}
    for ticker, _n, _s in config.SECTORS:
        if ticker in sectors:
            cols[ticker] = sectors[ticker].reindex(trading).pct_change()
    return pd.DataFrame(cols, index=trading)


def _simulate(selections: Dict[pd.Timestamp, List[str]],
              rets: pd.DataFrame,
              trading: pd.DatetimeIndex) -> pd.Series:
    """Aplica cada seleccion desde el dia SIGUIENTE al rebalanceo hasta el proximo.
    Devuelve la serie de retornos diarios de la cartera (equiponderada)."""
    rebal = sorted(selections.keys())
    port = pd.Series(0.0, index=trading, dtype=float)
    for i, rd in enumerate(rebal):
        end = rebal[i + 1] if i + 1 < len(rebal) else trading[-1]
        mask = (trading > rd) & (trading <= end)
        days = trading[mask]
        if len(days) == 0:
            continue
        sel = [t for t in selections[rd] if t in rets.columns]
        if not sel:
            port.loc[days] = 0.0
            continue
        sub = rets.loc[days, sel]
        port.loc[days] = sub.mean(axis=1, skipna=True).fillna(0.0).values
    # recortar al primer dia operado en adelante
    first = rebal[0]
    return port.loc[port.index > first]


# ============================================================
# 4. METRICAS
# ============================================================

def equity_curve(daily_ret: pd.Series, start: float = 1.0) -> pd.Series:
    return start * (1.0 + daily_ret.fillna(0.0)).cumprod()


def perf_metrics(daily_ret: pd.Series,
                 benchmarks: Optional[Dict[str, pd.Series]] = None) -> Dict:
    """Metricas core + (opcional) hit-rate y exceso de CAGR frente a uno o varios
    benchmarks. `benchmarks` = {NOMBRE: serie_de_retornos_diarios}."""
    r = daily_ret.dropna()
    if len(r) < 5:
        return {}
    eq = (1.0 + r).cumprod()
    n = len(r)
    yrs = n / TRADING_DAYS
    final = float(eq.iloc[-1])
    total = final - 1.0
    cagr = final ** (1.0 / yrs) - 1.0 if yrs > 0 and final > 0 else float("nan")
    vol = float(r.std() * np.sqrt(TRADING_DAYS))
    sharpe = float((r.mean() * TRADING_DAYS) / vol) if vol > 1e-12 else 0.0
    dd = float((eq / eq.cummax() - 1.0).min())
    downside = r[r < 0]
    sortino = float((r.mean() * TRADING_DAYS) / (downside.std() * np.sqrt(TRADING_DAYS))) \
        if len(downside) > 1 and downside.std() > 1e-12 else 0.0

    out = {
        "total_return": round(total, 4),
        "cagr": round(cagr, 4),
        "ann_vol": round(vol, 4),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "max_drawdown": round(dd, 4),
        "n_days": n,
        "years": round(yrs, 2),
    }

    for name, bench_ret in (benchmarks or {}).items():
        b = bench_ret.reindex(r.index).fillna(0.0)
        sm = (1 + r).resample("ME").prod() - 1
        bm = (1 + b).resample("ME").prod() - 1
        aligned = pd.concat({"s": sm, "b": bm}, axis=1).dropna()
        if len(aligned) > 0:
            out[f"monthly_hit_rate_{name}"] = round(float((aligned["s"] > aligned["b"]).mean()), 3)
            out["n_months"] = int(len(aligned))
        beq = (1 + b).cumprod()
        bfinal = float(beq.iloc[-1])
        bcagr = bfinal ** (1.0 / yrs) - 1.0 if yrs > 0 and bfinal > 0 else float("nan")
        out[f"excess_cagr_vs_{name}"] = round(cagr - bcagr, 4)
    return out


def per_phase_stats(points: List[RebalancePoint], rets: pd.DataFrame,
                    spy_ret: pd.Series, trading: pd.DatetimeIndex) -> Dict:
    """Retorno medio del periodo siguiente (hasta el proximo rebalanceo) por fase,
    para la estrategia TOPN y para el SPY. Mide si las fases tienen poder predictivo."""
    stats: Dict[int, Dict] = {}
    for i, p in enumerate(points):
        end = points[i + 1].date if i + 1 < len(points) else trading[-1]
        mask = (trading > p.date) & (trading <= end)
        days = trading[mask]
        if len(days) == 0:
            continue
        sel = [t for t in p.topn if t in rets.columns]
        topn_r = rets.loc[days, sel].mean(axis=1, skipna=True).fillna(0.0) if sel else pd.Series(0.0, index=days)
        topn_period = float((1 + topn_r).prod() - 1)
        spy_period = float((1 + spy_ret.reindex(days).fillna(0.0)).prod() - 1)
        d = stats.setdefault(p.phase_id, {"topn": [], "spy": [], "count": 0})
        d["topn"].append(topn_period)
        d["spy"].append(spy_period)
        d["count"] += 1

    out = {}
    for pid, d in sorted(stats.items()):
        out[config.PHASES[pid]["code"]] = {
            "periods": d["count"],
            "avg_topn_return": round(float(np.mean(d["topn"])), 4) if d["topn"] else None,
            "avg_spy_return": round(float(np.mean(d["spy"])), 4) if d["spy"] else None,
            "avg_excess": round(float(np.mean(d["topn"]) - np.mean(d["spy"])), 4) if d["topn"] else None,
        }
    return out


# ============================================================
# 5. INFORME (JSON + HTML con grafico SVG autocontenido)
# ============================================================

def _downsample(eq: pd.Series, max_points: int = 600) -> List[Dict]:
    if len(eq) > max_points:
        step = len(eq) // max_points
        eq = eq.iloc[::step]
    return [{"d": ts.strftime("%Y-%m-%d"), "v": round(float(v), 4)} for ts, v in eq.items()]


def _svg_chart(curves: Dict[str, pd.Series], width: int = 900, height: int = 380) -> str:
    """Grafico de lineas SVG (escala log) de las curvas de capital. Sin dependencias."""
    pad_l, pad_r, pad_t, pad_b = 60, 130, 20, 40
    colors = {"TOPN": "#2563eb", "PHASE": "#f59e0b", "SPY": "#6b7280", "RSP": "#15803d"}
    # dominio comun de fechas / valores
    all_idx = sorted(set().union(*[set(c.index) for c in curves.values()]))
    if not all_idx:
        return "<p>Sin datos</p>"
    t0, t1 = all_idx[0], all_idx[-1]
    span = max((t1 - t0).days, 1)
    vmin = min(float(np.log(c[c > 0]).min()) for c in curves.values())
    vmax = max(float(np.log(c[c > 0]).max()) for c in curves.values())
    vspan = max(vmax - vmin, 1e-6)

    def x(ts):
        return pad_l + (ts - t0).days / span * (width - pad_l - pad_r)

    def y(v):
        return pad_t + (1 - (np.log(v) - vmin) / vspan) * (height - pad_t - pad_b)

    parts = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
             f'style="width:100%;height:auto;font-family:system-ui,sans-serif;">']
    # marco
    parts.append(f'<rect x="{pad_l}" y="{pad_t}" width="{width-pad_l-pad_r}" '
                 f'height="{height-pad_t-pad_b}" fill="#fafafa" stroke="#e5e7eb"/>')
    # gridlines horizontales (valores de capital, base 100)
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        logv = vmin + frac * vspan
        val = np.exp(logv) * 100
        yy = pad_t + (1 - frac) * (height - pad_t - pad_b)
        parts.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width-pad_r}" y2="{yy:.1f}" stroke="#eee"/>')
        parts.append(f'<text x="{pad_l-8}" y="{yy+4:.1f}" font-size="11" fill="#888" '
                     f'text-anchor="end">{val:,.0f}</text>')
    # eje X (anos)
    years = pd.date_range(t0, t1, freq="YS")
    for ys in years:
        xx = x(pd.Timestamp(ys))
        parts.append(f'<line x1="{xx:.1f}" y1="{pad_t}" x2="{xx:.1f}" y2="{height-pad_b}" stroke="#f1f1f1"/>')
        parts.append(f'<text x="{xx:.1f}" y="{height-pad_b+16}" font-size="11" fill="#888" '
                     f'text-anchor="middle">{ys.year}</text>')
    # lineas
    for name, c in curves.items():
        c = c[c > 0]
        pts = " ".join(f"{x(ts):.1f},{y(v):.1f}" for ts, v in c.items())
        col = colors.get(name, "#999")
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2"/>')
    # leyenda
    ly = pad_t + 6
    for name, c in curves.items():
        col = colors.get(name, "#999")
        final_mult = float(c.iloc[-1] / c.iloc[0])
        parts.append(f'<rect x="{width-pad_r+8}" y="{ly}" width="12" height="12" fill="{col}"/>')
        parts.append(f'<text x="{width-pad_r+24}" y="{ly+11}" font-size="12" fill="#333">'
                     f'{name}  x{final_mult:.2f}</text>')
        ly += 22
    parts.append('</svg>')
    return "".join(parts)


def _fmt_pct(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return f"{x*100:+.1f}%"


def build_html_report(results: Dict, curves: Dict[str, pd.Series]) -> str:
    m = results["metrics"]
    cfg = results["config"]
    svg = _svg_chart(curves)
    strategies = [s for s in ("TOPN", "PHASE", "SPY", "RSP") if s in m]
    has_rsp = "RSP" in m

    def metric_row(label, key, pct=True):
        cells = ""
        for s in strategies:
            v = m[s].get(key)
            cells += f"<td>{_fmt_pct(v) if pct else (f'{v:.2f}' if v is not None else '-')}</td>"
        return f"<tr><th>{label}</th>{cells}</tr>"

    phase_rows = "".join(
        f"<tr><td>{code}</td><td>{d['periods']}</td><td>{_fmt_pct(d['avg_topn_return'])}</td>"
        f"<td>{_fmt_pct(d['avg_spy_return'])}</td>"
        f"<td style='color:{'#15803d' if (d['avg_excess'] or 0)>=0 else '#b01b1b'}'>"
        f"{_fmt_pct(d['avg_excess'])}</td></tr>"
        for code, d in results["per_phase"].items()
    )

    warn = ""
    if cfg["synthetic"]:
        warn = ('<div class="warn">DATOS SINTETICOS: este informe valida que el motor de '
                'backtest funciona, pero los numeros NO son reales. Ejecuta con FRED_API_KEY '
                'para obtener resultados con datos reales.</div>')
    elif cfg.get("yahoo_only"):
        warn = ('<div class="warn">MODO YAHOO-ONLY: datos de mercado REALES, pero la fase '
                'macro esta simplificada (solo Copper/Gold + VIX, sin las 7 series de FRED). '
                'Fia-te de TOPN vs SPY (rotacion tecnica real); la columna PHASE y el desglose '
                'por fase son aproximados. Anade la FRED_API_KEY para el modelo macro completo.</div>')

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GPS_MACRO - Backtest</title>
<style>
  body{{font-family:system-ui,Segoe UI,sans-serif;margin:0;background:#f4f6f9;color:#222;}}
  .wrap{{max-width:980px;margin:0 auto;padding:24px;}}
  h1{{color:#1f3a5f;margin-bottom:2px;}}
  .sub{{color:#666;margin-top:0;}}
  .card{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:20px;margin:18px 0;
        box-shadow:0 1px 3px rgba(0,0,0,.05);}}
  table{{border-collapse:collapse;width:100%;font-size:14px;}}
  th,td{{padding:8px 10px;text-align:right;border-bottom:1px solid #eee;}}
  th:first-child,td:first-child{{text-align:left;}}
  thead th{{background:#1f3a5f;color:#fff;}}
  .warn{{background:#fff4e5;border:1px solid #f59e0b;color:#92400e;padding:12px 14px;
        border-radius:8px;margin:12px 0;font-size:14px;}}
  .legend{{font-size:13px;color:#555;}}
  code{{background:#eef;padding:1px 5px;border-radius:4px;}}
</style></head><body><div class="wrap">
  <h1>GPS_MACRO - Validacion historica (Backtest)</h1>
  <p class="sub">Generado {results['generated_at']} &middot;
     {cfg['years']} anos &middot; Top-{cfg['top_n']} &middot; rebalanceo {cfg['rebalance']} &middot;
     {results['n_rebalances']} rebalanceos</p>
  {warn}

  <div class="card">
    <h3>Curva de capital (base 100, escala logaritmica)</h3>
    {svg}
    <p class="legend">TOPN = top-{cfg['top_n']} sectores por score compuesto &middot;
       PHASE = sectores favorecidos por la fase (teoria pura) &middot;
       SPY = comprar y mantener el indice.</p>
  </div>

  <div class="card">
    <h3>Metricas de rendimiento</h3>
    <table>
      <thead><tr><th>Metrica</th>{"".join(f"<th>{s}</th>" for s in strategies)}</tr></thead>
      <tbody>
        {metric_row("Retorno total", "total_return")}
        {metric_row("CAGR (anual)", "cagr")}
        {metric_row("Volatilidad anual", "ann_vol")}
        {metric_row("Sharpe", "sharpe", pct=False)}
        {metric_row("Sortino", "sortino", pct=False)}
        {metric_row("Max Drawdown", "max_drawdown")}
        {metric_row("Hit-rate mensual vs SPY", "monthly_hit_rate_SPY")}
        {metric_row("Exceso CAGR vs SPY", "excess_cagr_vs_SPY")}
        {(metric_row("Hit-rate mensual vs RSP", "monthly_hit_rate_RSP") + metric_row("Exceso CAGR vs RSP", "excess_cagr_vs_RSP")) if has_rsp else ""}
      </tbody>
    </table>
    {'<p class="legend"><b>RSP = S&amp;P 500 equiponderado</b>: el benchmark JUSTO para una rotacion equiponderada como TOPN. Comparar contra el SPY (ponderado por capitalizacion) penaliza la rotacion porque el SPY se beneficio de la concentracion en megacaps tech.</p>' if has_rsp else ''}
  </div>

  <div class="card">
    <h3>Rendimiento por fase del ciclo (estrategia TOPN)</h3>
    <table>
      <thead><tr><th>Fase</th><th>Periodos</th><th>Ret. medio TOPN</th>
        <th>Ret. medio SPY</th><th>Exceso</th></tr></thead>
      <tbody>{phase_rows}</tbody>
    </table>
    <p class="legend">Si el "Exceso" es positivo de forma consistente, las fases tienen
       poder predictivo para la rotacion sectorial.</p>
  </div>
</div></body></html>"""


# ============================================================
# 6. ORQUESTADOR
# ============================================================

def run_backtest(years: int = 20, top_n: int = 3, rebalance: str = "M",
                 synthetic: bool = False, yahoo_only: bool = False) -> Dict:
    _log(f"Backtest GPS_MACRO  (years={years}, top_n={top_n}, rebalance={rebalance}, "
         f"synthetic={synthetic}, yahoo_only={yahoo_only})")

    _log("1) Cargando datos...")
    if synthetic:
        macro, sectors, volumes = load_synthetic_data(years)
    else:
        macro, sectors, volumes = load_real_data(years, yahoo_only=yahoo_only)
        if yahoo_only:
            _log("   MODO YAHOO-ONLY: fase macro simplificada (Copper/Gold + VIX, sin FRED).")
    _log(f"   macro={len(macro)} series, sectores={len(sectors)}, volumenes={len(volumes)}")

    if config.BENCHMARK not in sectors:
        raise RuntimeError("No se pudo cargar el benchmark SPY; backtest abortado.")

    _log("2) Reconstruyendo fase + scores point-in-time...")
    points = reconstruct(macro, sectors, volumes, top_n, rebalance)
    if len(points) < 6:
        raise RuntimeError(f"Solo {len(points)} rebalanceos validos; insuficiente para backtest.")

    bench = sectors[config.BENCHMARK].dropna()
    trading = bench.index
    rets = _daily_returns_frame(sectors, trading)
    spy_ret = bench.pct_change()

    _log("3) Simulando estrategias...")
    sel_topn = {p.date: p.topn for p in points}
    sel_phase = {p.date: p.favored for p in points}
    topn_ret = _simulate(sel_topn, rets, trading)
    phase_ret = _simulate(sel_phase, rets, trading)
    # Benchmarks alineados al mismo periodo operado
    start_day = points[0].date
    spy_ret_bt = spy_ret.loc[spy_ret.index > start_day]
    has_rsp = "RSP" in sectors and not sectors["RSP"].dropna().empty
    if has_rsp:
        rsp_ret = sectors["RSP"].reindex(trading).pct_change()
        rsp_ret_bt = rsp_ret.loc[rsp_ret.index > start_day]
    else:
        _log("   (RSP no disponible; se omite el benchmark equiponderado)")

    _log("4) Calculando metricas...")
    benchmarks = {"SPY": spy_ret_bt}
    if has_rsp:
        benchmarks["RSP"] = rsp_ret_bt
    metrics = {
        "TOPN": perf_metrics(topn_ret, benchmarks),
        "PHASE": perf_metrics(phase_ret, benchmarks),
        "SPY": perf_metrics(spy_ret_bt),
    }
    if has_rsp:
        metrics["RSP"] = perf_metrics(rsp_ret_bt, {"SPY": spy_ret_bt})
    phase_stats = per_phase_stats(points, rets, spy_ret, trading)

    curves = {
        "TOPN": equity_curve(topn_ret, 1.0),
        "PHASE": equity_curve(phase_ret, 1.0),
        "SPY": equity_curve(spy_ret_bt, 1.0),
    }
    if has_rsp:
        curves["RSP"] = equity_curve(rsp_ret_bt, 1.0)

    results = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "config": {"years": years, "top_n": top_n, "rebalance": rebalance,
                   "synthetic": synthetic, "yahoo_only": yahoo_only},
        "n_rebalances": len(points),
        "period": {"start": str(points[0].date.date()), "end": str(points[-1].date.date())},
        "metrics": metrics,
        "per_phase": phase_stats,
        "phase_timeline": [
            {"date": str(p.date.date()), "phase": p.phase_code,
             "growth": round(p.growth, 3), "stress": round(p.stress, 3),
             "leader": p.leader, "leader_score": p.leader_score,
             "topn": p.topn}
            for p in points
        ],
        "equity_curves": {k: _downsample(v) for k, v in curves.items()},
    }

    # Guardar JSON
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_json = config.DATA_DIR / "backtest.json"
    out_json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    _log(f"   -> {out_json}")

    # Guardar HTML
    html = build_html_report(results, curves)
    out_html = config.DOCS_DIR / "backtest.html"
    out_html.write_text(html, encoding="utf-8")
    _log(f"   -> {out_html}")

    _print_summary(results)
    return results


def _print_summary(results: Dict) -> None:
    m = results["metrics"]
    strategies = [s for s in ("TOPN", "PHASE", "SPY", "RSP") if s in m]
    has_rsp = "RSP" in m
    width = 24 + 12 * len(strategies)
    print("\n" + "=" * width)
    print(f"  RESUMEN BACKTEST  ({results['period']['start']} -> {results['period']['end']})")
    print("=" * width)
    header = f"  {'Metrica':<22}" + "".join(f"{s:>12}" for s in strategies)
    print(header)
    print("  " + "-" * (width - 2))

    def row(label, key, pct=True):
        vals = []
        for s in strategies:
            v = m[s].get(key)
            if v is None:
                vals.append("-")
            elif pct:
                vals.append(f"{v*100:+.1f}%")
            else:
                vals.append(f"{v:.2f}")
        print(f"  {label:<22}" + "".join(f"{x:>12}" for x in vals))

    row("CAGR", "cagr")
    row("Retorno total", "total_return")
    row("Volatilidad anual", "ann_vol")
    row("Sharpe", "sharpe", pct=False)
    row("Max Drawdown", "max_drawdown")
    row("Hit-rate mens. vs SPY", "monthly_hit_rate_SPY")
    row("Exceso CAGR vs SPY", "excess_cagr_vs_SPY")
    if has_rsp:
        row("Hit-rate mens. vs RSP", "monthly_hit_rate_RSP")
        row("Exceso CAGR vs RSP", "excess_cagr_vs_RSP")
    print("=" * width)
    if results["config"]["synthetic"]:
        print("  (DATOS SINTETICOS - solo valida el motor, no son resultados reales)")
    elif results["config"].get("yahoo_only"):
        print("  (YAHOO-ONLY: datos reales, fase macro simplificada sin FRED)")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="GPS_MACRO backtest / validacion historica")
    ap.add_argument("--synthetic", action="store_true",
                    help="Usa 20 anos de datos sinteticos (valida la logica sin FRED ni Yahoo).")
    ap.add_argument("--years", type=int, default=20, help="Anos de historico a usar.")
    ap.add_argument("--top", type=int, default=3, help="Numero de sectores en la estrategia TOPN.")
    ap.add_argument("--rebalance", choices=["W", "M", "Q"], default="M",
                    help="Frecuencia de rebalanceo (semanal/mensual/trimestral).")
    ap.add_argument("--yahoo-only", action="store_true", dest="yahoo_only",
                    help="Omite FRED: fase macro simplificada (Copper/Gold + VIX). "
                         "Datos de mercado REALES sin necesidad de FRED_API_KEY.")
    args = ap.parse_args()
    try:
        run_backtest(years=args.years, top_n=args.top, rebalance=args.rebalance,
                     synthetic=args.synthetic, yahoo_only=args.yahoo_only)
        return 0
    except Exception as e:
        _log(f"ERROR FATAL: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
