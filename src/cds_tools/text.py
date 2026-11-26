import html
import re
from datetime import datetime
from html.parser import HTMLParser


def local_time(iso: str | None) -> str | None:
    """Converte o UTC da API para o horário local (ex.: 02:59Z -> 23:59 em Brasília)."""
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")


def html_to_text(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict] = []
        self._open: dict | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            a = dict(attrs)
            self._open = {"url": a.get("href") or "", "text": "", "bbfile": a.get("data-bbfile")}

    def handle_data(self, data):
        if self._open is not None:
            self._open["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._open is not None:
            self._open["text"] = self._open["text"].strip()
            self.links.append(self._open)
            self._open = None


def html_links(s: str | None) -> list[dict]:
    """Links <a> de um HTML: url, texto e o JSON `data-bbfile` que o Ultra põe em arquivos."""
    parser = _Links()
    parser.feed(s or "")
    return [link for link in parser.links if link["url"]]


def safe_name(s: str, max_len: int = 120) -> str:
    """Nome válido de arquivo/pasta no Windows."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s)
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    return s[:max_len].rstrip(". ") or "sem nome"
