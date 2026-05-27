"""
Pipeline principal del sistema GPS_MACRO.
Ejecutar con: python -m src.main
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime, timezone

from . import config
from . import data_fetcher
from . import macro_model
from . import sector_rotation
from . import divergence
from . import history_manager
from . import dashboard_builder


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def run(use_synthetic: bool = False) -> int:
    _log("Iniciando pipeline GPS_MACRO...")

    # 1) Descarga de datos
    if use_synthetic:
        from .synthetic_data import build_synthetic_macro, build_synthetic_sectors
        _log("Modo SINTETICO activo (sin FRED ni Yahoo).")
        macro_data = build_synthetic_macro()
        sector_prices = build_synthetic_sectors()
        sector_volumes = None
    else:
        _log("Descargando datos macro (FRED + Yahoo)...")
        macro_data = data_fetcher.fetch_all_macro()
        _log(f"  -> {len(macro_data)} series macro cargadas.")

        _log("Descargando precios sectoriales (11 ETFs + SPY)...")
        sector_prices = data_fetcher.fetch_all_sectors()
        _log(f"  -> {len(sector_prices)} sectores/benchmark cargados.")

        _log("Descargando volumenes (para OBV)...")
        try:
            sector_volumes = data_fetcher.fetch_sector_volumes()
            _log(f"  -> {len(sector_volumes)} series de volumen.")
        except Exception as e:
            _log(f"  -> Volumenes no disponibles ({e}); seguiremos sin OBV.")
            sector_volumes = None

    # 2) Modelo macro
    _log("Calculando modelo de fase del ciclo...")
    macro_payload = macro_model.run_macro_model(macro_data)
    _log(f"  -> Fase: {macro_payload['phase']['code']} "
         f"(growth={macro_payload['axes']['growth']:+.2f}, "
         f"stress={macro_payload['axes']['stress']:+.2f})")

    # 3) Rotacion sectorial
    _log("Calculando scores sectoriales...")
    sector_readings = sector_rotation.compute_sector_scores(
        sector_prices, sector_volumes, macro_payload["phase"]["id"]
    )
    sectors_payload = sector_rotation.sector_payload(sector_readings)
    if sectors_payload:
        leader = sectors_payload[0]
        _log(f"  -> Lider: {leader['ticker']} ({leader['name']}) score={leader['score']:.1f}")
    else:
        _log("  -> No se pudo calcular ningun sector.")

    # 4) Sentimiento + divergencia
    _log("Calculando sentimiento de mercado y divergencias...")
    sentiment_result = divergence.compute_sentiment(macro_data, sector_prices, sector_readings)
    sentiment_result = divergence.detect_divergence(
        macro_payload["axes"]["growth"],
        macro_payload["axes"]["stress"],
        sentiment_result,
    )
    sent_payload = divergence.sentiment_payload(
        sentiment_result,
        macro_payload["axes"]["growth"],
        macro_payload["axes"]["stress"],
    )
    _log(f"  -> Sentimiento: {sent_payload['label']} ({sent_payload['sentiment']:+.2f}) "
         f"divergencia={sent_payload['divergence']}")

    # 5) Sub-sectores (si justifica)
    subsectors_payload = []
    if sectors_payload and sectors_payload[0]["score"] >= config.SUBSECTOR_TRIGGER_SCORE:
        leader_ticker = sectors_payload[0]["ticker"]
        _log(f"Lider score >= {config.SUBSECTOR_TRIGGER_SCORE}; descargando sub-sectores de {leader_ticker}...")
        if use_synthetic:
            sub_prices = {}
        else:
            sub_prices = data_fetcher.fetch_subsector_prices(leader_ticker)
        if sub_prices:
            sub_names = {t: n for t, n in config.SUBSECTORS.get(leader_ticker, [])}
            bench = sector_prices[config.BENCHMARK]
            subsectors_payload = sector_rotation.compute_subsector_scores(sub_prices, sub_names, bench)
            _log(f"  -> {len(subsectors_payload)} sub-sectores calculados.")

    # 6) Snapshot completo
    snapshot = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "macro": macro_payload,
        "sectors": sectors_payload,
        "sentiment": sent_payload,
        "subsectors": subsectors_payload,
    }

    # 7) Historico
    _log("Actualizando historico...")
    history = history_manager.load_history()
    history = history_manager.append_snapshot(history, snapshot)
    history_manager.save_history(history)
    if history.get("_alerts", {}).get("phase_change"):
        _log(f"  !! ALERTA: cambio de fase detectado: {history['_alerts']['phase_change']}")
    if history.get("_alerts", {}).get("leader_change"):
        _log(f"  !! ALERTA: cambio de lider sectorial: {history['_alerts']['leader_change']}")

    # 8) Render del dashboard
    _log("Renderizando dashboard...")
    html = dashboard_builder.build_dashboard(snapshot, history, subsectors_payload)
    dashboard_builder.write_snapshot_files(snapshot, history, html)
    _log(f"  -> {config.INDEX_HTML}")

    _log("Pipeline finalizado correctamente.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="GPS_MACRO pipeline runner")
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Usa datos sinteticos en lugar de FRED/Yahoo (para tests locales sin API key).",
    )
    args = parser.parse_args()
    try:
        return run(use_synthetic=args.synthetic)
    except Exception as e:
        _log(f"ERROR FATAL: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
