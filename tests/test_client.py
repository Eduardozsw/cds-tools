import asyncio

import pytest
from conftest import FakeClient

from cds_tools.client import BlackboardClient, BlackboardError, is_allowed

ALLOWED = [
    "/learn/api/public/v1/users/me",
    "/learn/api/public/v1/users/_1_1/courses",
    "/learn/api/public/v1/terms/_5_1",
    "/learn/api/public/v1/courses/_1_1/contents",
    "/learn/api/public/v1/courses/_1_1/contents?offset=100&limit=100",
    "/learn/api/public/v1/courses/_1_1/contents/_2_1",
    "/learn/api/public/v1/courses/_1_1/contents/_2_1/children",
    "/learn/api/public/v1/courses/_1_1/contents/_2_1/attachments",
    "/learn/api/public/v1/courses/_1_1/contents/_2_1/attachments/_3_1/download",
    "/learn/api/public/v1/courses/_1_1/announcements",
    "/learn/api/public/v1/calendars/items",
    "/learn/api/public/v2/courses/_1_1/gradebook/columns",
    "/learn/api/public/v2/courses/_1_1/gradebook/users/_9_1",
    "/bbcswebdav/pid-1-dt-content-rid-2_1/xid-2_1",
]

DENIED = [
    # Avaliações: tentativas, questões e envios nunca são acessados.
    "/learn/api/public/v2/courses/_1_1/gradebook/columns/_4_1/attempts",
    "/learn/api/public/v1/courses/_1_1/gradebook/columns/_4_1/attempts/_6_1",
    "/learn/api/public/v1/courses/_1_1/assessments/_7_1/questions",
    "/learn/api/v1/courses/_1_1/assessments",
    "/webapps/assessment/take/launch.jsp",
    # Atalhos para sair da lista.
    "/learn/api/public/v1/courses/../v1/courses/_1_1/assessments",
    "/learn/api/public/v1/courses/%2e%2e/assessments/_7_1/questions",
    "/learn/api/public/v1/users/me/extra",
]


@pytest.mark.parametrize("path", ALLOWED)
def test_allowed(path):
    assert is_allowed(path)


@pytest.mark.parametrize("path", DENIED)
def test_denied(path):
    assert not is_allowed(path)


def test_request_refuses_before_network(monkeypatch):
    async def no_network(*a, **k):
        raise AssertionError("não deveria chegar à rede")

    bb = BlackboardClient()
    monkeypatch.setattr(bb, "_send", no_network)
    with pytest.raises(BlackboardError, match="fora da lista"):
        asyncio.run(bb.request("/learn/api/public/v2/courses/_1_1/gradebook/columns/_4_1/attempts"))


def test_get_all_follows_paging():
    path = "/learn/api/public/v1/courses/_1_1/announcements"
    bb = FakeClient(
        {
            path: {"results": [{"id": "a"}], "paging": {"nextPage": f"{path}?offset=1"}},
            f"{path}?offset=1": {"results": [{"id": "b"}]},
        }
    )
    assert [x["id"] for x in asyncio.run(bb.get_all(path))] == ["a", "b"]


def test_get_all_empty_is_empty():
    bb = FakeClient({"/x": "contents_empty.json"})
    assert asyncio.run(bb.get_all("/x")) == []


def test_get_all_unknown_shape_is_error():
    # Formato desconhecido não pode virar lista vazia ("nada novo").
    bb = FakeClient({"/x": "contents_changed_shape.json"})
    with pytest.raises(BlackboardError, match="sem 'results'"):
        asyncio.run(bb.get_all("/x"))
