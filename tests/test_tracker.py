import asyncio

from conftest import FakeClient

from cds_tools import tracker

ROOT = "/learn/api/public/v1/courses/_1_1/contents"


def test_contents_skips_assessments_forums_and_lti():
    bb = FakeClient({ROOT: "contents_root.json", f"{ROOT}/_100_1/children": "contents_unidade1.json"})
    rows = asyncio.run(tracker._contents(bb, "_1_1"))
    assert [(r["title"], r["path"]) for r in rows] == [
        ("Unidade 1", "."),
        ("Material teórico", "Unidade 1"),
        ("Unidade 1", "Unidade 1"),  # ultraDocumentBody herda o nome da pasta
    ]


def _add_course(db):
    db.execute("insert into courses (id, subject, period) values ('_1_1', 'Cálculo II', '2026.2')")


def _announce(db, title: str, body: str = "texto"):
    db.execute(
        "insert or replace into announcements (id, course_id, title, body, created) "
        "values ('_a_1', '_1_1', ?, ?, '2026-08-10T12:00:00Z')",
        (title, body),
    )


def test_changes_baseline_then_new_then_changed(db):
    _add_course(db)
    _announce(db, "Bem-vindos")
    assert tracker.changes(db, "t", baseline=True) == []
    assert tracker.changes(db, "t") == []

    _announce(db, "Bem-vindos", "texto corrigido")
    [ev] = tracker.changes(db, "t")
    assert (ev["type"], ev["status"], ev["course"]) == ("news", "changed", "Cálculo II")
    assert tracker.changes(db, "t") == []

    db.execute(
        "insert into announcements (id, course_id, title, body, created) "
        "values ('_b_1', '_1_1', 'Prova adiada', 'x', '2026-08-11T12:00:00Z')"
    )
    [ev] = tracker.changes(db, "t")
    assert (ev["status"], ev["title"]) == ("new", "Prova adiada")


def test_changes_without_mark_repeats(db):
    _add_course(db)
    tracker.changes(db, "t", baseline=True)
    _announce(db, "Aviso")
    assert len(tracker.changes(db, "t", mark=False)) == 1
    assert len(tracker.changes(db, "t", mark=False)) == 1


def test_consumers_are_independent(db):
    _add_course(db)
    _announce(db, "Aviso")
    tracker.changes(db, "watch", baseline=True)
    assert tracker.has_state(db, "watch")
    assert not tracker.has_state(db, "mcp")


def test_root_material_has_no_topic(db):
    _add_course(db)
    db.execute(
        "insert into contents (id, course_id, title, handler, path) "
        "values ('_m_1', '_1_1', 'Plano de ensino', 'resource/x-bb-file', '.')"
    )
    [ev] = tracker.changes(db, "t")
    assert ev["topic"] is None
