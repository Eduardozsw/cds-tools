import html
import re
from datetime import datetime


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


def safe_name(s: str, max_len: int = 120) -> str:
    """Nome válido de arquivo/pasta no Windows."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s)
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    return s[:max_len].rstrip(". ") or "sem nome"
