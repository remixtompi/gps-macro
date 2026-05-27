"""
Persistencia incremental del historico (history.json).
Detecta cambios de fase y de sector lider para activar banners en el dashboard.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import config


def _empty_history() -> Dict:
    return {
        "version": 1,
        "phase_timeline": [],     # [{date, phase_id, phase_code, probabilities, growth, stress}]
        "leader_timeline": [],    # [{date, leader, score, runner_up}]
        "sentiment_timeline": [], # [{date, sentiment, label}]
        "events": [],             # [{date, type, message}]
    }


def load_history() -> Dict:
    if not config.HISTORY_PATH.exists():
        return _empty_history()
    try:
        h = json.loads(config.HISTORY_PATH.read_text(encoding="utf-8"))
        # rellenar campos faltantes para compatibilidad
        base = _empty_history()
        base.update(h)
        return base
    except Exception as e:
        print(f"[history_manager] history.json invalido, reiniciando: {e}")
        return _empty_history()


def save_history(history: Dict) -> None:
    config.HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.HISTORY_PATH.write_text(
        json.dumps(history, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _trim(timeline: List[Dict], keep_days: int = config.HISTORY_MAX_DAYS) -> List[Dict]:
    if not timeline:
        return timeline
    cutoff = (datetime.now(timezone.utc).date() -
              __import__("datetime").timedelta(days=keep_days)).isoformat()
    return [x for x in timeline if x.get("date", "") >= cutoff]


def append_snapshot(history: Dict, snapshot: Dict) -> Dict:
    """Anade datos diarios al historico y detecta cambios significativos."""
    today = _today_iso()
    phase = snapshot["macro"]["phase"]
    axes = snapshot["macro"]["axes"]
    sectors = snapshot["sectors"]
    sentiment = snapshot.get("sentiment", {})

    # ----- Phase timeline -----
    last_phase = history["phase_timeline"][-1] if history["phase_timeline"] else None
    entry = {
        "date": today,
        "phase_id": phase["id"],
        "phase_code": phase["code"],
        "probabilities": phase["probabilities"],
        "growth": axes["growth"],
        "stress": axes["stress"],
    }
    # Si ya hay entrada de hoy, actualizamos en lugar de duplicar
    if last_phase and last_phase.get("date") == today:
        history["phase_timeline"][-1] = entry
    else:
        history["phase_timeline"].append(entry)

    # Detectar cambio de fase
    phase_change = None
    prev_diff_phase = None
    for past in reversed(history["phase_timeline"][:-1]):
        if past.get("phase_id") != phase["id"]:
            prev_diff_phase = past
            break
    if prev_diff_phase is not None:
        # cambio si phase[-1] != phase[anteriores N] y prob > umbral
        recent = history["phase_timeline"][-config.PHASE_CHANGE_PERSISTENCE_DAYS:]
        if len(recent) >= config.PHASE_CHANGE_PERSISTENCE_DAYS:
            all_same = all(r["phase_id"] == phase["id"] for r in recent)
            high_prob = phase["probabilities"].get(str(phase["id"]),
                            phase["probabilities"].get(phase["id"], 0.0)) >= config.PHASE_CHANGE_THRESHOLD
            if all_same and high_prob and prev_diff_phase["phase_code"] != phase["code"]:
                phase_change = {
                    "from": prev_diff_phase["phase_code"],
                    "to": phase["code"],
                    "previous_date": prev_diff_phase["date"],
                }

    # ----- Leader timeline -----
    leader = sectors[0] if sectors else None
    runner_up = sectors[1] if len(sectors) > 1 else None
    leader_entry = {
        "date": today,
        "leader": leader["ticker"] if leader else None,
        "leader_score": leader["score"] if leader else None,
        "runner_up": runner_up["ticker"] if runner_up else None,
    }
    last_leader = history["leader_timeline"][-1] if history["leader_timeline"] else None
    if last_leader and last_leader.get("date") == today:
        history["leader_timeline"][-1] = leader_entry
    else:
        history["leader_timeline"].append(leader_entry)

    leader_change = None
    if last_leader and last_leader.get("date") != today:
        if leader and last_leader.get("leader") and leader["ticker"] != last_leader["leader"]:
            leader_change = {
                "from": last_leader["leader"],
                "to": leader["ticker"],
                "previous_date": last_leader["date"],
            }

    # ----- Sentiment timeline -----
    if sentiment:
        sent_entry = {
            "date": today,
            "sentiment": sentiment.get("sentiment"),
            "label": sentiment.get("label"),
            "divergence": sentiment.get("divergence"),
        }
        if history["sentiment_timeline"] and history["sentiment_timeline"][-1]["date"] == today:
            history["sentiment_timeline"][-1] = sent_entry
        else:
            history["sentiment_timeline"].append(sent_entry)

    # ----- Eventos -----
    events_to_add = []
    if phase_change:
        events_to_add.append({
            "date": today,
            "type": "phase_change",
            "message": f"Cambio de fase: {phase_change['from']} -> {phase_change['to']}",
        })
    if leader_change:
        events_to_add.append({
            "date": today,
            "type": "leader_change",
            "message": f"Nuevo lider sectorial: {leader_change['from']} -> {leader_change['to']}",
        })
    if sentiment.get("divergence"):
        # solo agregar si el ultimo evento no es la misma divergencia
        already = any(
            e.get("type") == "divergence" and e.get("date") == today for e in history["events"]
        )
        if not already:
            events_to_add.append({
                "date": today,
                "type": "divergence",
                "message": f"Divergencia macro/mercado detectada ({sentiment.get('divergence')})",
            })

    history["events"].extend(events_to_add)
    # Mantener solo ultimos 30 eventos
    history["events"] = history["events"][-30:]

    # Recortar timelines viejos
    history["phase_timeline"] = _trim(history["phase_timeline"])
    history["leader_timeline"] = _trim(history["leader_timeline"])
    history["sentiment_timeline"] = _trim(history["sentiment_timeline"])

    history["_alerts"] = {
        "phase_change": phase_change,
        "leader_change": leader_change,
        "divergence": sentiment.get("divergence") if sentiment else None,
    }

    return history
