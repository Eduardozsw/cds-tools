import asyncio

from cds_tools.server import mcp, parse_url


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
