"""Banco SQLite local: espelho do Blackboard (por disciplina) + estado do que já foi visto."""

import sqlite3

from . import config

SCHEMA = """
create table if not exists courses(
    id text primary key, code text, name text, subject text, period text, url text, synced_at text);
create table if not exists announcements(
    id text primary key, course_id text, title text, body text, created text, modified text);
create table if not exists columns(
    id text primary key, course_id text, name text, due text, possible real, content_id text);
create table if not exists grades(
    column_id text primary key, course_id text, score real, display text, status text, feedback text);
create table if not exists contents(
    id text primary key, course_id text, parent_id text, title text, handler text, path text, modified text);
-- O que cada consumidor (watch, mcp, news) já viu, com a impressão digital da versão vista.
create table if not exists seen(
    consumer text, kind text, item_id text, fingerprint text, seen_at text,
    primary key(consumer, kind, item_id));
create table if not exists meta(key text primary key, value text);
"""


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.DB_FILE)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def replace_course_rows(db: sqlite3.Connection, table: str, course_id: str, rows: list[dict]) -> None:
    """Troca as linhas de uma disciplina numa tabela-espelho (itens apagados no Blackboard somem)."""
    db.execute(f"delete from {table} where course_id = ?", (course_id,))
    if rows:
        cols = list(rows[0])
        db.executemany(
            f"insert or replace into {table} ({', '.join(cols)}) values ({', '.join('?' for _ in cols)})",
            [tuple(r[c] for c in cols) for r in rows],
        )


def upsert_course(db: sqlite3.Connection, row: dict) -> None:
    cols = list(row)
    db.execute(
        f"insert or replace into courses ({', '.join(cols)}) values ({', '.join('?' for _ in cols)})",
        tuple(row.values()),
    )


def get_meta(db: sqlite3.Connection, key: str) -> str | None:
    r = db.execute("select value from meta where key = ?", (key,)).fetchone()
    return r["value"] if r else None


def set_meta(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute("insert or replace into meta (key, value) values (?, ?)", (key, value))
