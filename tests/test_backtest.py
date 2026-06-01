"""
Tests del motor de backtest.
Rapidos: validan la mecanica (anti-lookahead, metricas, simulacion) sin descargar datos.
"""
import pandas as pd

from src import backtest as bt


def _trading_index(n=40):
    return pd.bdate_range("2020-01-01", periods=n)


def test_perf_metrics_constant_return():
    """Una serie con +0.1% diario debe dar CAGR y vol coherentes."""
    idx = _trading_index(252)
    r = pd.Series(0.001, index=idx)
    m = bt.perf_metrics(r, {"SPY": r})
    assert m["total_return"] > 0
    # (1.001^252 - 1) ~ 0.286
    assert abs(m["total_return"] - 0.286) < 0.02
    assert m["max_drawdown"] == 0.0  # nunca baja
    assert m["monthly_hit_rate_SPY"] == 0.0  # vs si misma, > es falso
    assert m["excess_cagr_vs_SPY"] == 0.0  # vs si misma, exceso nulo


def test_perf_metrics_multiple_benchmarks():
    """Debe generar claves separadas por cada benchmark."""
    idx = _trading_index(252)
    r = pd.Series(0.001, index=idx)
    b = pd.Series(0.0005, index=idx)
    m = bt.perf_metrics(r, {"SPY": b, "RSP": b})
    assert "excess_cagr_vs_SPY" in m and "excess_cagr_vs_RSP" in m
    assert m["excess_cagr_vs_SPY"] > 0  # r rinde mas que b


def test_equity_curve_compounds():
    idx = _trading_index(4)
    r = pd.Series([0.0, 0.10, -0.05, 0.0], index=idx)
    eq = bt.equity_curve(r, 1.0)
    assert abs(eq.iloc[-1] - (1.0 * 1.10 * 0.95)) < 1e-9


def test_simulate_no_lookahead():
    """La seleccion fijada en el rebalanceo `d` NO debe afectar al retorno del propio dia `d`,
    solo a partir de `d+1`. Probamos que el primer dia post-rebalanceo es el que cuenta."""
    idx = _trading_index(10)
    # XLK sube fuerte solo el dia 5; el resto plano
    xlk = pd.Series(1.0, index=idx)
    xlk.iloc[5] = 1.0  # precio
    prices = pd.Series([100, 100, 100, 100, 100, 110, 110, 110, 110, 110], index=idx, dtype=float)
    rets = pd.DataFrame({"XLK": prices.pct_change()}, index=idx)

    # Rebalanceo el dia 4 (indice 4): seleccion XLK. Debe capturar el salto del dia 5.
    selections = {idx[4]: ["XLK"]}
    port = bt._simulate(selections, rets, idx)
    # el retorno del dia 5 (salto +10%) debe estar presente
    assert abs(port.loc[idx[5]] - 0.10) < 1e-9
    # dias previos al rebalanceo no deben estar en la serie operada
    assert (port.index > idx[4]).all()


def test_simulate_equal_weight():
    """Dos sectores equiponderados: el retorno de cartera es la media."""
    idx = _trading_index(6)
    pa = pd.Series([100, 100, 110, 110, 110, 110], index=idx, dtype=float)
    pb = pd.Series([100, 100, 100, 90, 90, 90], index=idx, dtype=float)
    rets = pd.DataFrame({"XLK": pa.pct_change(), "XLF": pb.pct_change()}, index=idx)
    selections = {idx[1]: ["XLK", "XLF"]}
    port = bt._simulate(selections, rets, idx)
    # dia 2: XLK +10%, XLF 0% -> media +5%
    assert abs(port.loc[idx[2]] - 0.05) < 1e-9
    # dia 3: XLK 0%, XLF -10% -> media -5%
    assert abs(port.loc[idx[3]] - (-0.05)) < 1e-9


def test_adapt_weights_normalized():
    """Los pesos adaptativos (bull y bear) deben sumar 1.0."""
    assert abs(sum(bt._ADAPT_BULL_W.values()) - 1.0) < 1e-9
    assert abs(sum(bt._ADAPT_BEAR_W.values()) - 1.0) < 1e-9
    # En bull, sin sesgo de fase; en bear, sesgo de fase reforzado.
    assert bt._ADAPT_BULL_W["phase_alignment"] == 0.0
    assert bt._ADAPT_BEAR_W["phase_alignment"] > bt._ADAPT_BULL_W["phase_alignment"]
    assert bt._ADAPT_BULL_W["momentum"] > bt._ADAPT_BEAR_W["momentum"]


def test_adapt_select_bull_vs_bear():
    """En bull manda el momentum; en bear, el alineamiento de fase (defensa)."""
    components = {
        "XLK": {"mansfield_rs": 0.5, "momentum": 0.9, "cross_rank": 0.8,
                "breadth": 0.5, "volume_flow": 0.3, "phase_alignment": -1.0},  # ciclico fuerte, a evitar en defensa
        "XLP": {"mansfield_rs": 0.1, "momentum": -0.2, "cross_rank": -0.3,
                "breadth": 0.0, "volume_flow": 0.0, "phase_alignment": 1.0},   # defensivo favorecido
    }
    bull_pick = bt._adapt_select(components, bull=True, top_n=1)
    bear_pick = bt._adapt_select(components, bull=False, top_n=1)
    assert bull_pick == ["XLK"]   # en bull gana el momentum
    assert bear_pick == ["XLP"]   # en bear gana la defensa (phase_alignment)


def test_rebalance_dates_warmup():
    """Las fechas de rebalanceo deben respetar el warm-up inicial."""
    trading = pd.bdate_range("2010-01-01", periods=600)
    dates = bt._rebalance_dates(trading, "M", warmup_days=280)
    assert len(dates) > 0
    assert all(d >= trading[280] for d in dates)
