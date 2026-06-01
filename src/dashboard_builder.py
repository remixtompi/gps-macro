"""
Renderiza el dashboard HTML usando Jinja2.
Tambien escribe los JSON (snapshot + history) que el JS del cliente consume.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config


PHASE_LABELS = [
    ("1", "Expansion"),
    ("2", "Recalentamiento"),
    ("3", "Contraccion"),
    ("4", "Desaceleracion"),
]
PHASE_COLORS = {
    "1": config.PHASES[1]["color"],
    "2": config.PHASES[2]["color"],
    "3": config.PHASES[3]["color"],
    "4": config.PHASES[4]["color"],
}


def _score_color(score: float) -> str:
    """Color para score [-1, +1] -> rojo -> amarillo -> verde."""
    s = max(-1.0, min(1.0, score))
    if s >= 0:
        # 0 -> #475569 (gris)  ;  +1 -> #16a34a (verde)
        return f"rgba(22, 163, 74, {0.20 + 0.65 * s})"
    else:
        return f"rgba(220, 38, 38, {0.20 + 0.65 * abs(s)})"


def _score_color_100(score_100: float) -> str:
    """Color para score 0-100."""
    s = (score_100 - 50.0) / 50.0
    return _score_color(s)


def _next_run_text() -> str:
    """Aproxima la proxima ejecucion programada."""
    now = datetime.now(timezone.utc)
    # Cron diario: 12:30 UTC L-V
    target = now.replace(hour=12, minute=30, second=0, microsecond=0)
    while target <= now or target.weekday() >= 5:
        target += timedelta(days=1)
    return target.strftime("%Y-%m-%d %H:%M UTC")


def _normalize_phase_probs(probs: Dict) -> Dict[str, float]:
    """Asegura que las claves de probabilidades sean strings."""
    out = {}
    for k, v in probs.items():
        out[str(k)] = float(v)
    return out


def _normalize_macro_for_template(macro: Dict) -> Dict:
    macro = dict(macro)
    phase = dict(macro["phase"])
    phase["probabilities"] = _normalize_phase_probs(phase["probabilities"])
    macro["phase"] = phase
    return macro


def build_dashboard(snapshot: Dict, history: Dict, subsectors: Optional[List[Dict]] = None) -> str:
    """Renderiza el HTML del dashboard y devuelve el texto."""
    config.TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    env = Environment(
        loader=FileSystemLoader(str(config.TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.globals["score_color"] = _score_color
    env.globals["score_color_100"] = _score_color_100

    macro = _normalize_macro_for_template(snapshot["macro"])

    template = env.get_template("dashboard.html.j2")
    html = template.render(
        macro=macro,
        sectors=snapshot["sectors"],
        sentiment=snapshot.get("sentiment"),
        subsectors=subsectors or [],
        alerts=history.get("_alerts", {}),
        events=history.get("events", []),
        phase_labels=PHASE_LABELS,
        phase_colors=PHASE_COLORS,
        updated_at=snapshot.get("updated_at", "—"),
        data_quality=snapshot.get("data_quality", {}),
        next_run=_next_run_text(),
        snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        history_json=json.dumps({
            "phase_timeline": history.get("phase_timeline", []),
            "sentiment_timeline": history.get("sentiment_timeline", []),
        }, ensure_ascii=False),
    )
    return html


def write_snapshot_files(snapshot: Dict, history: Dict, html: str) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    # history se escribe desde history_manager.save_history; aqui solo el HTML.
    config.INDEX_HTML.write_text(html, encoding="utf-8")
