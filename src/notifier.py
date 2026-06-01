"""
Notificaciones push para GPS_MACRO (Telegram).

Envia un mensaje cuando el pipeline detecta un evento relevante:
  - Cambio de fase del ciclo economico.
  - Cambio de sector lider.
  - Divergencia macro vs mercado.

Diseno:
  - **Opcional y tolerante a fallos**: si no hay credenciales configuradas, no hace
    nada (no rompe el pipeline). Si el envio falla, se registra pero no aborta.
  - **Sin spam**: por defecto solo notifica cuando hay al menos un evento. Con
    GPS_NOTIFY_ALWAYS=1 envia ademas un resumen diario aunque no haya alertas.

Configuracion (variables de entorno / GitHub secrets):
  - TELEGRAM_BOT_TOKEN : token del bot (via @BotFather).
  - TELEGRAM_CHAT_ID   : tu chat id (via @userinfobot o getUpdates).
  - GPS_DASHBOARD_URL  : (opcional) URL del dashboard para incluir el enlace.
  - GPS_NOTIFY_ALWAYS  : (opcional) "1" para enviar resumen diario sin alertas.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

import requests

from . import config


TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Mapas rapidos ticker -> nombre y code -> label
_SECTOR_NAMES = {t: n for t, n, _s in config.SECTORS}
_PHASE_LABELS = {p["code"]: p["label"] for p in config.PHASES.values()}


def _sector_name(ticker: Optional[str]) -> str:
    if not ticker:
        return "?"
    return f"{ticker} ({_SECTOR_NAMES.get(ticker, ticker)})"


def _phase_label(code: Optional[str]) -> str:
    if not code:
        return "?"
    return _PHASE_LABELS.get(code, code)


def is_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
                and os.environ.get("TELEGRAM_CHAT_ID", "").strip())


def send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    """Envia un mensaje por Telegram. Devuelve True si se envio. No lanza excepciones."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("[notifier] Telegram no configurado (faltan TELEGRAM_BOT_TOKEN / CHAT_ID); omito.")
        return False
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            print("[notifier] Mensaje de Telegram enviado.")
            return True
        print(f"[notifier] Telegram respondio {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"[notifier] Error enviando Telegram: {e}")
        return False


def build_alert_message(snapshot: Dict, alerts: Dict) -> Optional[str]:
    """Construye el mensaje a partir del snapshot y los alerts del history_manager.

    Devuelve None si no hay ningun evento y GPS_NOTIFY_ALWAYS no esta activo.
    """
    phase_change = alerts.get("phase_change")
    leader_change = alerts.get("leader_change")
    divergence = alerts.get("divergence")
    always = os.environ.get("GPS_NOTIFY_ALWAYS", "").strip() == "1"

    has_event = bool(phase_change or leader_change or divergence)
    if not has_event and not always:
        return None

    date = snapshot.get("updated_at", "")
    macro = snapshot.get("macro", {})
    phase = macro.get("phase", {})
    sectors = snapshot.get("sectors", [])
    sentiment = snapshot.get("sentiment", {})

    lines: List[str] = [f"<b>🧭 GPS_MACRO</b> — {date}"]

    # ----- Eventos (lo urgente arriba) -----
    event_lines: List[str] = []
    if phase_change:
        event_lines.append(
            f"🔴 <b>CAMBIO DE FASE</b>: {_phase_label(phase_change.get('from'))} → "
            f"<b>{_phase_label(phase_change.get('to'))}</b>"
        )
    if leader_change:
        event_lines.append(
            f"🔄 <b>NUEVO LÍDER SECTORIAL</b>: {_sector_name(leader_change.get('from'))} → "
            f"<b>{_sector_name(leader_change.get('to'))}</b>"
        )
    if divergence:
        div_txt = {
            "market_better": "mercado optimista vs macro débil (cautela)",
            "macro_better": "macro fuerte vs mercado pesimista (posible oportunidad)",
        }.get(divergence, divergence)
        event_lines.append(f"⚠️ <b>DIVERGENCIA</b> macro/mercado: {div_txt}")

    if event_lines:
        lines.append("")
        lines.extend(event_lines)
    elif always:
        lines.append("\n<i>Sin eventos hoy. Resumen diario:</i>")

    # ----- Estado actual -----
    lines.append("")
    probs = phase.get("probabilities", {})
    pid = phase.get("id")
    prob = probs.get(str(pid), probs.get(pid, 0.0)) if pid is not None else 0.0
    lines.append(f"📊 Fase: <b>{phase.get('label', '?')}</b> ({prob*100:.0f}% prob)")

    if sectors:
        top3 = ", ".join(
            f"{s['ticker']} ({s['score']:.0f})" for s in sectors[:3]
        )
        lines.append(f"🏆 Top-3: {top3}")

    if sentiment:
        lines.append(
            f"🌡️ Sentimiento: <b>{sentiment.get('label', '?')}</b> "
            f"({sentiment.get('sentiment', 0.0):+.2f})"
        )

    # Aviso de fiabilidad de datos (si no es alta)
    dq = snapshot.get("data_quality", {})
    if dq and dq.get("label") and dq.get("label") != "alta":
        lines.append(
            f"\n🛑 <b>Fiabilidad de datos {dq['label'].upper()}</b> "
            f"({dq.get('reliability', 0)*100:.0f}%) — interpreta con cautela."
        )

    url = os.environ.get("GPS_DASHBOARD_URL", "").strip()
    if url:
        lines.append(f"\n🔗 {url}")

    return "\n".join(lines)


def notify(snapshot: Dict, history: Dict) -> bool:
    """Punto de entrada usado por el pipeline. Construye y envia el mensaje si procede."""
    alerts = history.get("_alerts", {}) or {}
    message = build_alert_message(snapshot, alerts)
    if message is None:
        print("[notifier] Sin eventos; no se envia notificacion.")
        return False
    if not is_configured():
        print("[notifier] Hay eventos pero Telegram no esta configurado; omito el envio.")
        return False
    return send_telegram(message)


def _self_test() -> int:
    """Envia un mensaje de prueba para verificar la configuracion del bot.
    Uso: python -m src.notifier --test"""
    if not is_configured():
        print("ERROR: faltan TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID en el entorno.")
        print("  PowerShell:  $env:TELEGRAM_BOT_TOKEN='...'; $env:TELEGRAM_CHAT_ID='...'")
        return 1
    msg = ("<b>🧭 GPS_MACRO</b> — mensaje de prueba ✅\n\n"
           "Si lees esto, las alertas push estan configuradas correctamente.")
    ok = send_telegram(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        sys.exit(_self_test())
    print("Uso: python -m src.notifier --test  (envia un mensaje de prueba)")
