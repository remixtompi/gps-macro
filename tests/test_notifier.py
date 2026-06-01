"""
Tests del notificador (construccion de mensajes, sin red).
"""
import os

from src import notifier


def _snapshot():
    return {
        "updated_at": "2026-06-01 12:30 UTC",
        "macro": {"phase": {"id": 3, "label": "Contraccion", "code": "CONTRACCION",
                            "probabilities": {"3": 0.58, "1": 0.2, "2": 0.12, "4": 0.1}}},
        "sectors": [
            {"ticker": "XLV", "score": 72.0},
            {"ticker": "XLP", "score": 68.0},
            {"ticker": "XLU", "score": 65.0},
        ],
        "sentiment": {"label": "Risk-Off", "sentiment": -0.35},
    }


def test_no_alerts_returns_none(monkeypatch):
    monkeypatch.delenv("GPS_NOTIFY_ALWAYS", raising=False)
    msg = notifier.build_alert_message(_snapshot(), {})
    assert msg is None


def test_always_sends_summary(monkeypatch):
    monkeypatch.setenv("GPS_NOTIFY_ALWAYS", "1")
    msg = notifier.build_alert_message(_snapshot(), {})
    assert msg is not None
    assert "Contraccion" in msg
    assert "XLV" in msg


def test_phase_change_message(monkeypatch):
    monkeypatch.delenv("GPS_NOTIFY_ALWAYS", raising=False)
    alerts = {"phase_change": {"from": "RECALENTAMIENTO", "to": "CONTRACCION"}}
    msg = notifier.build_alert_message(_snapshot(), alerts)
    assert msg is not None
    assert "CAMBIO DE FASE" in msg
    assert "Recalentamiento" in msg and "Contraccion" in msg


def test_leader_change_uses_sector_name(monkeypatch):
    monkeypatch.delenv("GPS_NOTIFY_ALWAYS", raising=False)
    alerts = {"leader_change": {"from": "XLK", "to": "XLV"}}
    msg = notifier.build_alert_message(_snapshot(), alerts)
    assert "NUEVO LÍDER" in msg
    assert "Salud" in msg  # nombre humano de XLV
    assert "Tecnologia" in msg  # nombre humano de XLK


def test_divergence_message(monkeypatch):
    monkeypatch.delenv("GPS_NOTIFY_ALWAYS", raising=False)
    alerts = {"divergence": "market_better"}
    msg = notifier.build_alert_message(_snapshot(), alerts)
    assert "DIVERGENCIA" in msg
    assert "cautela" in msg


def test_is_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notifier.is_configured() is False
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "y")
    assert notifier.is_configured() is True


def test_notify_no_send_without_config(monkeypatch):
    """Con eventos pero sin credenciales, notify no debe enviar ni romper."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    history = {"_alerts": {"phase_change": {"from": "EXPANSION", "to": "RECALENTAMIENTO"}}}
    assert notifier.notify(_snapshot(), history) is False
