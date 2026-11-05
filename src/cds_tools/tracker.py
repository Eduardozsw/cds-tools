"""Sincroniza o Blackboard para o banco local e detecta o que mudou.

Cada item é lembrado por consumidor (watch, mcp, news) com uma impressão digital dos
campos que importam. Um item só volta como `changed` se esses campos mudarem.
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import PurePosixPath

from . import config, store, study
from .client import BlackboardClient, BlackboardError
from .text import html_to_text, local_time

V1 = f"{config.API}/v1"
V2 = f"{config.API}/v2"
KINDS = ("news", "deadline", "grade", "material")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def course_url(course_id: str) -> str:
    return f"{config.BASE_URL}/ultra/courses/{course_id}/outline"


# --------------------------------------------------------------------------- sync


async def _announcements(bb: BlackboardClient, cid: str) -> list[dict]:
    return [
        {
            "id": a["id"],
            "course_id": cid,
            "title": a.get("title"),
            "body": html_to_text(a.get("body")),
            "created": a.get("created"),
            "modified": a.get("modified"),
        }
        for a in await bb.get_all(f"{V1}/courses/{cid}/announcements")
    ]


async def _gradebook(bb: BlackboardClient, cid: str, user_id: str) -> tuple[list[dict], list[dict]]:
    columns = [
        {
            "id": c["id"],
            "course_id": cid,
            "name": c.get("name"),
            "due": (c.get("grading") or {}).get("due"),
            "possible": (c.get("score") or {}).get("possible"),
            "content_id": c.get("contentId"),
        }
        for c in await bb.get_all(f"{V2}/courses/{cid}/gradebook/columns")
    ]
    grades = []
    for g in await bb.get_all(f"{V2}/courses/{cid}/gradebook/users/{user_id}"):
        display = g.get("displayGrade") or {}
        grades.append(
            {
                "column_id": g["columnId"],
                "course_id": cid,
                "score": display.get("score", g.get("score")),
                "display": display.get("text"),
                "status": g.get("status"),
                "feedback": html_to_text(g.get("feedback")),
            }
        )
    return columns, grades


async def _contents(bb: BlackboardClient, cid: str) -> list[dict]:
    rows: list[dict] = []

    async def walk(parent: str | None, path: PurePosixPath) -> None:
        url = f"{V1}/courses/{cid}/contents" + (f"/{parent}/children" if parent else "")
        for c in await bb.get_all(url):
            handler = (c.get("contentHandler") or {}).get("id", "")
            if any(k in handler for k in study.SKIP_HANDLERS):  # avaliações viram prazos, não material
                continue
            title = c.get("title") or c["id"]
            if title == "ultraDocumentBody":
                title = path.name or "Documento"
            rows.append(
                {
                    "id": c["id"],
                    "course_id": cid,
                    "parent_id": parent,
                    "title": title,
                    "handler": handler,
                    "path": path.as_posix(),
                    "modified": c.get("modified"),
                }
            )
            if c.get("hasChildren"):
                try:
                    await walk(c["id"], path / title)
                except BlackboardError:
                    pass  # pasta restrita: segue com o resto

    await walk(None, PurePosixPath())
    return rows


async def _sync_news(bb, db, cid, user_id) -> dict:
    rows = await _announcements(bb, cid)
    store.replace_course_rows(db, "announcements", cid, rows)
    return {"news": len(rows)}


async def _sync_gradebook(bb, db, cid, user_id) -> dict:
    columns, grades = await _gradebook(bb, cid, user_id)
    store.replace_course_rows(db, "columns", cid, columns)
    store.replace_course_rows(db, "grades", cid, grades)
    return {
        "deadlines": sum(1 for c in columns if c["due"]),
        "grades": sum(1 for g in grades if g["score"] is not None),
    }


async def _sync_contents(bb, db, cid, user_id) -> dict:
    rows = await _contents(bb, cid)
    store.replace_course_rows(db, "contents", cid, rows)
    return {"materials": len(rows)}


async def sync(bb: BlackboardClient, all_terms: bool = False) -> dict:
    """Atualiza o banco com as disciplinas do período atual. Erros por disciplina não interrompem."""
    db = store.connect()
    me = await bb.me()
    synced_at = now_utc()
    courses = []
    for period, subject, course in await study.list_courses(bb, all_terms):
        cid = course["id"]
        stats: dict = {"course_id": cid, "course": subject, "period": period, "errors": [], "restricted": []}
        store.upsert_course(
            db,
            {
                "id": cid,
                "code": course.get("courseId"),
                "name": course.get("name"),
                "subject": subject,
                "period": period,
                "url": course_url(cid),
                "synced_at": synced_at,
            },
        )
        for name, step in (("news", _sync_news), ("gradebook", _sync_gradebook), ("contents", _sync_contents)):
            try:
                stats.update(await step(bb, db, cid, me["id"]))
            except BlackboardError as e:
                if e.status == 403:  # a disciplina não libera isso para alunos: não é falha
                    stats["restricted"].append(name)
                else:
                    stats["errors"].append(f"{name}: {str(e)[:150]}")
        db.commit()
        courses.append(stats)
    store.set_meta(db, "last_sync", synced_at)
    db.commit()
    return {"synced_at": synced_at, "courses": courses}


# --------------------------------------------------------------------------- mudanças


def _fingerprint(*values) -> str:
    return hashlib.sha1(json.dumps(values, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def _items(db: sqlite3.Connection, kinds: tuple[str, ...]) -> list[tuple[str, str, str, dict]]:
    """(kind, item_id, fingerprint, evento) de tudo que está no banco."""
    out = []
    if "news" in kinds:
        for r in db.execute("select a.*, c.subject from announcements a join courses c on c.id = a.course_id"):
            out.append(
                (
                    "news",
                    r["id"],
                    _fingerprint(r["title"], r["body"]),
                    {
                        "course_id": r["course_id"],
                        "course": r["subject"],
                        "title": r["title"],
                        "published_at": local_time(r["created"]),
                        "body": r["body"],
                        "url": course_url(r["course_id"]),
                    },
                )
            )
    if "deadline" in kinds:
        for r in db.execute(
            "select k.*, c.subject from columns k join courses c on c.id = k.course_id where k.due is not null"
        ):
            out.append(
                (
                    "deadline",
                    r["id"],
                    _fingerprint(r["name"], r["due"]),
                    {
                        "course_id": r["course_id"],
                        "course": r["subject"],
                        "title": r["name"],
                        "due": local_time(r["due"]),
                        "possible": r["possible"],
                        "url": course_url(r["course_id"]),
                    },
                )
            )
    if "grade" in kinds:
        for r in db.execute(
            "select g.*, k.name, k.possible, c.subject from grades g "
            "join columns k on k.id = g.column_id join courses c on c.id = g.course_id "
            "where g.score is not null or g.status is not null"
        ):
            out.append(
                (
                    "grade",
                    r["column_id"],
                    _fingerprint(r["score"], r["display"], r["status"]),
                    {
                        "course_id": r["course_id"],
                        "course": r["subject"],
                        "title": r["name"],
                        "score": r["score"],
                        "possible": r["possible"],
                        "display": r["display"],
                        "grade_status": r["status"],
                        "feedback": r["feedback"],
                        "url": course_url(r["course_id"]),
                    },
                )
            )
    if "material" in kinds:
        for r in db.execute(
            "select t.*, c.subject from contents t join courses c on c.id = t.course_id "
            "where t.handler not in ('resource/x-bb-folder', 'resource/x-bb-lesson')"
        ):
            # A raiz da disciplina fica gravada como "." (PurePosixPath().as_posix()); mudar isso
            # alteraria a impressão digital e geraria eventos falsos de "alterado".
            out.append(
                (
                    "material",
                    r["id"],
                    _fingerprint(r["title"], r["path"]),
                    {
                        "course_id": r["course_id"],
                        "course": r["subject"],
                        "title": r["title"],
                        "topic": r["path"] if r["path"] not in ("", ".") else None,
                        "kind": r["handler"].replace("resource/x-bb-", ""),
                        "url": course_url(r["course_id"]),
                    },
                )
            )
    return out


def has_state(db: sqlite3.Connection, consumer: str) -> bool:
    return db.execute("select 1 from seen where consumer = ? limit 1", (consumer,)).fetchone() is not None


def changes(
    db: sqlite3.Connection, consumer: str, *, mark: bool = True, baseline: bool = False, kinds: tuple[str, ...] = KINDS
) -> list[dict]:
    """Eventos novos/alterados desde a última vez que `consumer` olhou.

    baseline=True registra o estado atual sem emitir nada.
    """
    seen = {
        (r["kind"], r["item_id"]): r["fingerprint"]
        for r in db.execute("select kind, item_id, fingerprint from seen where consumer = ?", (consumer,))
    }
    detected_at = now_utc()
    events, marks = [], []
    for kind, item_id, fp, data in _items(db, kinds):
        before = seen.get((kind, item_id))
        if before == fp:
            continue
        marks.append((consumer, kind, item_id, fp, detected_at))
        if not baseline:
            events.append(
                {
                    "type": kind,
                    "status": "new" if before is None else "changed",
                    "item_id": item_id,
                    **data,
                    "detected_at": detected_at,
                }
            )
    if mark or baseline:
        db.executemany("insert or replace into seen values (?, ?, ?, ?, ?)", marks)
        db.commit()
    order = {k: n for n, k in enumerate(KINDS)}
    events.sort(key=lambda e: (order[e["type"]], e["course"] or "", e.get("due") or "", e["title"] or "", e["item_id"]))
    return events
