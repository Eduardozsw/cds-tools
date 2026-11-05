import json
import sqlite3
from pathlib import Path

import pytest

from cds_tools import config, store
from cds_tools.client import BlackboardClient

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Nenhum teste toca a sessão, o banco ou a pasta de estudos de verdade."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "data" / "state.json")
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "data" / "cds.db")
    monkeypatch.setattr(config, "DOWNLOAD_DIR", tmp_path / "downloads")
    monkeypatch.setattr(config, "STUDY_DIR", tmp_path / "faculdade")


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(store.SCHEMA)
    return conn


class FakeClient(BlackboardClient):
    """Responde a get_json com fixtures por caminho, sem rede."""

    def __init__(self, routes: dict[str, str | dict]):
        super().__init__()
        self.routes = routes

    async def get_json(self, path: str, params: dict | None = None) -> dict:
        route = self.routes[path]
        return load(route) if isinstance(route, str) else route
