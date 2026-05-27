"""
Descarga datos de FRED y Yahoo Finance con cache local y fallback.
Disenado para correr en GitHub Actions (sin estado) o localmente.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional

import pandas as pd

from . import config


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ============== UTILIDADES DE CACHE ==============

def _ensure_cache_dir() -> None:
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(key: str) -> Path:
    safe = key.replace("/", "_").replace(":", "_").replace("^", "_")
    return config.CACHE_DIR / f"{safe}.parquet"


def _cache_meta_path(key: str) -> Path:
    safe = key.replace("/", "_").replace(":", "_").replace("^", "_")
    return config.CACHE_DIR / f"{safe}.meta.json"


def _cache_get(key: str) -> Optional[pd.Series]:
    """Devuelve la serie cacheada si existe y no ha caducado."""
    meta_p = _cache_meta_path(key)
    data_p = _cache_path(key)
    if not meta_p.exists() or not data_p.exists():
        return None
    try:
        meta = json.loads(meta_p.read_text())
        ts = datetime.fromisoformat(meta["fetched_at"])
        if _utcnow() - ts > timedelta(hours=config.CACHE_TTL_HOURS):
            return None
        df = pd.read_parquet(data_p)
        if df.empty:
            return None
        s = df.iloc[:, 0]
        s.index = pd.to_datetime(s.index)
        return s
    except Exception:
        return None


def _cache_set(key: str, series: pd.Series) -> None:
    _ensure_cache_dir()
    try:
        df = series.to_frame(name=key)
        df.to_parquet(_cache_path(key))
        _cache_meta_path(key).write_text(
            json.dumps({"fetched_at": _utcnow().isoformat()})
        )
    except Exception:
        pass  # cache es best-effort


# ============== FRED ==============

class FREDError(Exception):
    pass


def get_fred_client():
    """Crea cliente FRED. Lanza FREDError si no hay API key."""
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        raise FREDError(
            "FRED_API_KEY no esta configurada. "
            "Obtenla gratis en https://fred.stlouisfed.org/docs/api/api_key.html"
        )
    try:
        from fredapi import Fred
    except ImportError as e:
        raise FREDError(f"fredapi no instalado: {e}")
    return Fred(api_key=api_key)


def fetch_fred_series(series_id: str, years: int = 12) -> pd.Series:
    """Descarga una serie de FRED con cache. Devuelve pd.Series indexado por fecha."""
    cached = _cache_get(f"fred_{series_id}")
    if cached is not None:
        return cached

    fred = get_fred_client()
    start = (_utcnow() - timedelta(days=365 * years)).date()
    for attempt in range(3):
        try:
            s = fred.get_series(series_id, observation_start=start)
            if s is None or len(s) == 0:
                raise FREDError(f"Serie vacia: {series_id}")
            s = s.dropna()
            s.name = series_id
            _cache_set(f"fred_{series_id}", s)
            return s
        except Exception as e:
            if attempt == 2:
                raise FREDError(f"FRED fallo para {series_id}: {e}")
            time.sleep(2 ** attempt)
    raise FREDError(f"No se pudo descargar {series_id}")


# ============== YAHOO FINANCE ==============

class YahooError(Exception):
    pass


def fetch_yahoo_close(ticker: str, years: int = 6) -> pd.Series:
    """Descarga cierres ajustados de Yahoo Finance. Con cache + fallback a Stooq."""
    cached = _cache_get(f"yh_{ticker}")
    if cached is not None:
        return cached

    try:
        import yfinance as yf
    except ImportError as e:
        raise YahooError(f"yfinance no instalado: {e}")

    end = _utcnow().date()
    start = end - timedelta(days=int(365 * years))

    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            df = yf.download(
                ticker,
                start=start.isoformat(),
                end=end.isoformat(),
                progress=False,
                auto_adjust=True,
                threads=False,
            )
            if df is None or df.empty:
                raise YahooError(f"Yahoo devolvio vacio para {ticker}")
            # Manejar MultiIndex que yfinance a veces devuelve
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            col = "Close" if "Close" in df.columns else df.columns[0]
            s = df[col].dropna()
            s.name = ticker
            s.index = pd.to_datetime(s.index).tz_localize(None)
            _cache_set(f"yh_{ticker}", s)
            return s
        except Exception as e:
            last_err = e
            time.sleep(1 + attempt)

    # Fallback a Stooq via pandas-datareader
    try:
        from pandas_datareader import data as pdr
        df = pdr.DataReader(ticker, "stooq", start, end)
        if df is not None and not df.empty:
            s = df["Close"].dropna().sort_index()
            s.name = ticker
            s.index = pd.to_datetime(s.index).tz_localize(None)
            _cache_set(f"yh_{ticker}", s)
            return s
    except Exception as e:
        last_err = e

    raise YahooError(f"Yahoo+Stooq fallaron para {ticker}: {last_err}")


def fetch_yahoo_volume(ticker: str, years: int = 4) -> pd.Series:
    """Descarga volumen para calculos de OBV."""
    cached = _cache_get(f"yhv_{ticker}")
    if cached is not None:
        return cached

    import yfinance as yf
    end = _utcnow().date()
    start = end - timedelta(days=int(365 * years))
    df = yf.download(
        ticker,
        start=start.isoformat(),
        end=end.isoformat(),
        progress=False,
        auto_adjust=False,
        threads=False,
    )
    if df is None or df.empty:
        raise YahooError(f"Yahoo volume vacio para {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    s = df["Volume"].dropna()
    s.name = ticker
    s.index = pd.to_datetime(s.index).tz_localize(None)
    _cache_set(f"yhv_{ticker}", s)
    return s


# ============== ORQUESTADOR DE ALTO NIVEL ==============

def fetch_all_macro() -> Dict[str, pd.Series]:
    """Descarga las 8 series macro principales + secundarias.

    Devuelve dict {id_interno: serie_diaria}.  Las series de FRED que son mensuales
    o semanales se rellenan con forward-fill a frecuencia diaria para alinearse.
    """
    out: Dict[str, pd.Series] = {}
    errors = []

    # Series principales
    for key, fred_id, _desc, _axis, _sign in config.MACRO_SERIES:
        try:
            s = fetch_fred_series(fred_id)
            out[key] = s
        except Exception as e:
            errors.append(f"{key}({fred_id}): {e}")

    # Series secundarias (no son criticas)
    for key, fred_id, _desc in config.MACRO_SECONDARY:
        try:
            out[key] = fetch_fred_series(fred_id)
        except Exception as e:
            errors.append(f"sec.{key}({fred_id}): {e}")

    # Copper/Gold ratio desde Yahoo (especial)
    try:
        hg = fetch_yahoo_close("HG=F")
        gc = fetch_yahoo_close("GC=F")
        joined = pd.concat({"HG": hg, "GC": gc}, axis=1).dropna()
        ratio = joined["HG"] / joined["GC"]
        ratio.name = "copper_gold"
        out["copper_gold"] = ratio
    except Exception as e:
        errors.append(f"copper_gold: {e}")

    # VIX
    try:
        out["vix"] = fetch_yahoo_close("^VIX")
    except Exception as e:
        errors.append(f"vix: {e}")

    if errors:
        print(f"[data_fetcher] {len(errors)} series con error:")
        for err in errors:
            print(f"  - {err}")

    return out


def fetch_all_sectors() -> Dict[str, pd.Series]:
    """Descarga precios cierre de los 11 ETFs sectoriales + SPY."""
    out: Dict[str, pd.Series] = {}
    for ticker, _name, _short in config.SECTORS:
        try:
            out[ticker] = fetch_yahoo_close(ticker)
        except Exception as e:
            print(f"[data_fetcher] No se pudo cargar {ticker}: {e}")
    try:
        out[config.BENCHMARK] = fetch_yahoo_close(config.BENCHMARK)
    except Exception as e:
        print(f"[data_fetcher] No se pudo cargar benchmark {config.BENCHMARK}: {e}")
    return out


def fetch_sector_volumes() -> Dict[str, pd.Series]:
    """Volumen para los 11 ETFs + SPY (para OBV)."""
    out: Dict[str, pd.Series] = {}
    for ticker, _, _ in config.SECTORS:
        try:
            out[ticker] = fetch_yahoo_volume(ticker)
        except Exception as e:
            print(f"[data_fetcher] No se pudo cargar volumen {ticker}: {e}")
    try:
        out[config.BENCHMARK] = fetch_yahoo_volume(config.BENCHMARK)
    except Exception as e:
        print(f"[data_fetcher] No se pudo cargar volumen benchmark: {e}")
    return out


def fetch_subsector_prices(lead_sector: str) -> Dict[str, pd.Series]:
    """Descarga precios de los sub-sectores del sector lider."""
    out: Dict[str, pd.Series] = {}
    subs = config.SUBSECTORS.get(lead_sector, [])
    for ticker, _name in subs:
        try:
            out[ticker] = fetch_yahoo_close(ticker, years=3)
        except Exception as e:
            print(f"[data_fetcher] sub {ticker}: {e}")
    return out
