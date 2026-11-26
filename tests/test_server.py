import asyncio
import json

import pytest
from conftest import FakeClient

from cds_tools import server, submit
from cds_tools.client import BlackboardError
from cds_tools.server import mcp, parse_url

BB = "https://bb.cruzeirodosulvirtual.com.br"
CONTENT = "/learn/api/public/v1/courses/_1_1/contents/_2_1"
COLUMN = "/learn/api/public/v2/courses/_1_1/gradebook/columns/_4_1"
ATTEMPT = "/learn/api/v1/courses/_1_1/gradebook/attempts/_6_1"


def _file_link(name: str, rid: str) -> str:
    url = f"{BB}/bbcswebdav/pid-7-dt-asiobject-rid-{rid}/xid-{rid}"
    meta = json.dumps({"linkName": name, "mimeType": "application/pdf", "fileSize": 1234, "resourceUrl": url})
    return f"<a href=\"{url}\" data-bbfile='{meta}'>{name}</a>"


def _routes(attempts: list[dict]) -> dict:
    return {
        CONTENT: {
            "id": "_2_1",
            "title": "N2 - CASO",
            "contentHandler": {"id": "resource/x-bb-asmt-test-link", "gradeColumnId": "_4_1"},
        },
        COLUMN: {"score": {"possible": 100}, "grading": {"due": "2026-12-06T02:59:00.000Z", "attemptsAllowed": 1}},
        f"{COLUMN}/attempts": {"results": attempts},
        ATTEMPT: {
            "toolAttemptDetail": {
                "resource/x-bb-assessment": {
                    "questionAttempts": [
                        {"question": {"position": 2, "questionType": "essay", "points": 100,
                                      "questionText": {"rawText": "<p>INSIRA O ARQUIVO AQUI</p>"}}},
                        {"question": {"position": 1, "questionType": "presentation", "points": 0,
                                      "questionText": {"rawText": "<p>" + _file_link("ROTEIRO.pdf", "8_1")
                                                       + ' <a href="https://1drv.ms/f/x">IMAGENS</a></p>'}}},
                    ]
                }
            }
        },
    }


def test_get_assessment_reads_attempt(monkeypatch):
    monkeypatch.setattr(server, "bb", FakeClient(_routes([{"id": "_6_1", "status": "InProgress", "created": None}])))
    out = asyncio.run(server.get_assessment("_1_1", "_2_1"))
    assert out["possible"] == 100 and out["attempt"]["id"] == "_6_1"
    first, second = out["questions"]
    assert first["files"] == [
        {
            "name": "ROTEIRO.pdf",
            "mimeType": "application/pdf",
            "bytes": 1234,
            "url": f"{BB}/bbcswebdav/pid-7-dt-asiobject-rid-8_1/xid-8_1",
        }
    ]
    assert first["links"] == [{"text": "IMAGENS", "url": "https://1drv.ms/f/x"}]
    assert second["type"] == "essay" and second["text"] == "INSIRA O ARQUIVO AQUI"


def test_get_assessment_never_starts_attempt(monkeypatch):
    routes = _routes([])
    del routes[ATTEMPT]  # sem tentativa, nada além da lista pode ser consultado
    monkeypatch.setattr(server, "bb", FakeClient(routes))
    out = asyncio.run(server.get_assessment("_1_1", "_2_1"))
    assert out["attempt"] is None and "não inicia" in out["message"]


def test_download_file_refuses_other_hosts():
    with pytest.raises(BlackboardError, match="não é um arquivo"):
        asyncio.run(server.download_file("_1_1", "https://exemplo.com/bbcswebdav/x"))


@pytest.fixture
def answer(tmp_path):
    f = tmp_path / "RESPOSTA.txt"
    f.write_text("resposta", encoding="utf-8")
    return f


def _no_browser(*args):
    raise AssertionError("abriu o navegador")


def test_submit_preview_does_not_open_browser(monkeypatch, answer):
    monkeypatch.setattr(server, "bb", FakeClient(_routes([{"id": "_6_1", "status": "InProgress"}])))
    monkeypatch.setattr(submit, "submit_file", _no_browser)
    out = asyncio.run(server.submit_assessment("_1_1", "_2_1", str(answer)))
    assert out["submitted"] is False and out["attempt"] == "_6_1" and out["bytes"] == 8
    assert out["question"] == "INSIRA O ARQUIVO AQUI"


@pytest.mark.parametrize(
    ("attempts", "error"),
    [([], "nenhuma tentativa"), ([{"id": "_6_1", "status": "NeedsGrading"}], "não está em andamento")],
)
def test_submit_refuses_without_open_attempt(monkeypatch, answer, attempts, error):
    routes = _routes(attempts)
    monkeypatch.setattr(server, "bb", FakeClient(routes))
    monkeypatch.setattr(submit, "submit_file", _no_browser)
    with pytest.raises(BlackboardError, match=error):
        asyncio.run(server.submit_assessment("_1_1", "_2_1", str(answer), confirm=True))


def test_submit_refuses_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "bb", FakeClient(_routes([{"id": "_6_1", "status": "InProgress"}])))
    with pytest.raises(BlackboardError, match="não encontrado"):
        asyncio.run(server.submit_assessment("_1_1", "_2_1", str(tmp_path / "nada.txt"), confirm=True))


def test_submit_confirm_sends(monkeypatch, answer):
    routes = _routes([{"id": "_6_1", "status": "InProgress"}])
    routes[CONTENT]["parentId"] = "_3_1"
    routes["/learn/api/public/v1/courses/_1_1/contents/_3_1"] = {"title": "Trabalhos", "parentId": "_9_1"}
    routes["/learn/api/public/v1/courses/_1_1/contents/_9_1"] = {"title": "ROOT"}
    monkeypatch.setattr(server, "bb", FakeClient(routes))
    calls = []

    async def fake_submit(*args):
        calls.append(args)
        return {"confirmation": "abc123"}

    monkeypatch.setattr(submit, "submit_file", fake_submit)
    out = asyncio.run(server.submit_assessment("_1_1", "_2_1", str(answer), confirm=True))
    assert calls == [("_1_1", ["Trabalhos"], "N2 - CASO", "_6_1", answer.resolve())]
    assert out["submitted"] is True and out["confirmation"] == "abc123"


def test_tools_are_prefixed_and_read_only():
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names and all(n.startswith("cds_") for n in names)
    # GET bruto contornaria a lista de caminhos permitidos.
    assert "cds_api_get" not in names


def test_parse_url():
    url = (
        "https://bb.cruzeirodosulvirtual.com.br/ultra/courses/_123456_1/outline/edit/document/_2_1?courseId=_123456_1"
    )
    assert parse_url(url) == {"courses": "_123456_1", "document": "_2_1", "courseId": "_123456_1"}
