import asyncio
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse, urlsplit

import httpx

from . import auth, config

LOGIN_HINT = (
    "Sessão do Blackboard expirada ou inexistente. Use a tool `cds_login` "
    "(ou rode `cds-tools login` no terminal) e entre na sua conta no navegador que abrir."
)

HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
    ),
}


# Únicos caminhos que o cliente acessa: material, avisos, notas e calendário.
# Tentativas, questões e envios de avaliação ficam de fora; isto é a garantia de somente
# leitura, não as instruções do agente.
_ID = r"[^/]+"
ALLOWED_PATHS = [
    re.compile(p)
    for p in (
        r"/learn/api/public/v1/users/me",
        rf"/learn/api/public/v1/users/{_ID}/courses",
        rf"/learn/api/public/v1/terms/{_ID}",
        rf"/learn/api/public/v1/courses/{_ID}/contents(/{_ID}(/children)?)?",
        rf"/learn/api/public/v1/courses/{_ID}/contents/{_ID}/attachments(/{_ID}(/download)?)?",
        rf"/learn/api/public/v1/courses/{_ID}/announcements",
        r"/learn/api/public/v1/calendars/items",
        rf"/learn/api/public/v2/courses/{_ID}/gradebook/columns",
        rf"/learn/api/public/v2/courses/{_ID}/gradebook/users/{_ID}",
        r"/bbcswebdav/.+",  # arquivos do curso
    )
]


def is_allowed(path: str) -> bool:
    p = urlsplit(path).path
    if any(seg in (".", "..") for seg in unquote(p).split("/")):
        return False
    return any(r.fullmatch(p) for r in ALLOWED_PATHS)


class AuthError(RuntimeError):
    pass


class BlackboardError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _load_cookies() -> httpx.Cookies | None:
    if not config.STATE_FILE.exists():
        return None
    state = json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
    host = urlparse(config.BASE_URL).hostname or ""
    jar = httpx.Cookies()
    for c in state.get("cookies", []):
        domain = c["domain"].lstrip(".")
        if host == domain or host.endswith("." + domain):
            jar.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
    return jar


def _auth_failed(resp: httpx.Response | None) -> bool:
    if resp is None or resp.status_code == 401:
        return True
    if resp.is_redirect:
        location = resp.headers.get("location", "").lower()
        return any(k in location for k in ("login", "auth", "sso", "saml"))
    return "/webapps/login" in resp.url.path


class BlackboardClient:
    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._user: dict | None = None
        self._refresh_lock = asyncio.Lock()

    async def reset(self) -> None:
        if self._http is not None:
            await self._http.aclose()
        self._http = None
        self._user = None

    async def _send(self, path: str, params: dict | None, follow: bool) -> httpx.Response | None:
        if self._http is None:
            cookies = _load_cookies()
            if cookies is None:
                return None
            self._http = httpx.AsyncClient(base_url=config.BASE_URL, cookies=cookies, headers=HEADERS, timeout=30)
        return await self._http.get(path, params=params, follow_redirects=follow)

    async def request(self, path: str, params: dict | None = None, *, follow_redirects: bool = False) -> httpx.Response:
        if not is_allowed(path):
            raise BlackboardError(f"caminho fora da lista permitida (somente leitura de material): {path}")
        resp = await self._send(path, params, follow_redirects)
        if _auth_failed(resp):
            # Tenta renovar em silêncio (headless) antes de pedir login manual.
            async with self._refresh_lock:
                user = await auth.refresh_session(interactive=False, timeout=30)
            await self.reset()
            if user is None:
                raise AuthError(LOGIN_HINT)
            resp = await self._send(path, params, follow_redirects)
            if _auth_failed(resp):
                raise AuthError(LOGIN_HINT)
        assert resp is not None
        if resp.status_code >= 400:
            raise BlackboardError(f"HTTP {resp.status_code} em {path}: {resp.text[:300]}", resp.status_code)
        return resp

    async def get_json(self, path: str, params: dict | None = None) -> dict:
        resp = await self.request(path, params)
        try:
            return resp.json()
        except ValueError:
            raise BlackboardError(f"Resposta não-JSON em {path} (HTTP {resp.status_code}): {resp.text[:200]}") from None

    async def get_all(self, path: str, params: dict | None = None, max_items: int = 500) -> list[dict]:
        """Segue `paging.nextPage` das APIs públicas até acabar ou atingir max_items."""
        items: list[dict] = []
        data = await self.get_json(path, params)
        while True:
            # Formato desconhecido vira erro, nunca uma lista vazia que parece "nada novo".
            if not isinstance(data.get("results"), list):
                raise BlackboardError(f"resposta sem 'results' em {path}: {str(data)[:200]}")
            items.extend(data["results"])
            next_page = (data.get("paging") or {}).get("nextPage")
            if not next_page or len(items) >= max_items:
                return items[:max_items]
            data = await self.get_json(next_page)

    async def me(self) -> dict:
        if self._user is None:
            self._user = await self.get_json(f"{config.API}/v1/users/me")
        return self._user

    async def download(self, path: str, dest_dir: Path, filename: str) -> Path:
        resp = await self.request(path, follow_redirects=True)
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip() or "arquivo"
        dest = dest_dir / safe
        dest.write_bytes(resp.content)
        return dest
