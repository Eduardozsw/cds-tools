"""Exporta os prazos do banco local como calendário iCalendar (.ics)."""

import sqlite3
from datetime import datetime, timedelta, timezone

from . import config


def _esc(s: str | None) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Quebra linhas longas (RFC 5545: 75 octetos), continuando com espaço."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > 75:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n".join(out)


def _utc(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build(db: sqlite3.Connection, alarm_hours: int = 24) -> tuple[str, int]:
    """Um VEVENT por coluna do diário de notas com prazo. Retorna (texto, nº de eventos)."""
    host = config.BASE_URL.split("//")[-1]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//cds-tools//PT-BR",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Blackboard - prazos",
    ]
    rows = db.execute(
        "select k.*, c.subject from columns k join courses c on c.id = k.course_id "
        "where k.due is not null order by k.due"
    ).fetchall()
    for r in rows:
        due = _utc(r["due"])
        start = _utc((datetime.fromisoformat(r["due"].replace("Z", "+00:00")) - timedelta(minutes=30)).isoformat())
        summary = f"{r['subject']}: {r['name']}"
        desc = r["subject"] + (f"\nValor: {r['possible']:g}" if r["possible"] else "")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{r['id']}@{host}",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{start}",
            f"DTEND:{due}",
            f"SUMMARY:{_esc(summary)}",
            f"DESCRIPTION:{_esc(desc)}",
            f"URL:{config.BASE_URL}/ultra/courses/{r['course_id']}/outline",
        ]
        if alarm_hours > 0:
            lines += [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{_esc(r['name'])}",
                f"TRIGGER:-PT{alarm_hours}H",
                "END:VALARM",
            ]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n", len(rows)
