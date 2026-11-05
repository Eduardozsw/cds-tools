from cds_tools import ics


def test_build(db):
    db.execute("insert into courses (id, subject) values ('_1_1', 'Cálculo II')")
    db.execute(
        "insert into columns (id, course_id, name, due, possible) values "
        "('_c_1', '_1_1', 'Atividade 1, parte A; com um título bem comprido para forçar a quebra', "
        "'2026-09-01T02:59:00.000Z', 10)"
    )
    db.execute("insert into columns (id, course_id, name) values ('_c_2', '_1_1', 'Sem prazo')")
    text, n = ics.build(db, alarm_hours=24)
    assert n == 1
    assert text.count("BEGIN:VEVENT") == 1
    assert "DTEND:20260901T025900Z" in text
    assert r"Atividade 1\, parte A\; com" in text.replace("\r\n ", "")
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert "TRIGGER:-PT24H" in text


def test_build_without_alarm(db):
    db.execute("insert into courses (id, subject) values ('_1_1', 'X')")
    db.execute("insert into columns (id, course_id, name, due) values ('_c_1', '_1_1', 'A', '2026-09-01T00:00:00Z')")
    text, _ = ics.build(db, alarm_hours=0)
    assert "VALARM" not in text
