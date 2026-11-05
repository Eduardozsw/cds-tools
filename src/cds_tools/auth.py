"""Login via navegador com perfil persistente.

O perfil guarda os cookies do SSO entre execuções; depois de cada login os cookies
do Blackboard são exportados para STATE_FILE, que o cliente HTTP usa.
"""

import asyncio
import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from . import config

log = logging.getLogger(__name__)

ME_PATH = f"{config.API}/v1/users/me"

# URL absoluta: o Ultra define <base> apontando para o CDN, então um caminho
# relativo iria para ultra.content.blackboardcdn.com e voltaria 403.
FETCH_ME_JS = """async (url) => {
  const r = await fetch(url, {headers: {Accept: 'application/json'}});
  return {status: r.status, user: r.ok ? await r.json() : null};
}"""


async def _check_me(ctx) -> tuple[int | None, dict | None]:
    """Chama users/me de dentro de uma aba do Blackboard, como o próprio Ultra faz."""
    for page in ctx.pages:
        if page.url.startswith(config.BASE_URL):
            try:
                r = await page.evaluate(FETCH_ME_JS, config.BASE_URL + ME_PATH)
                return r["status"], r["user"]
            except PlaywrightError:
                continue  # aba no meio de uma navegação
    return None, None


def _short(url: str) -> str:
    # Só host + caminho: a query string do SSO pode carregar tokens.
    s = urlsplit(url)
    return f"{s.netloc}{s.path}"


def _where(ctx) -> list[str]:
    return [_short(p.url) for p in ctx.pages]


# Requisições não-documento que ainda interessam para entender o fluxo de login.
_AUTH_HINT = re.compile(r"login|logon|sso|saml|oauth|auth|token|callback|session|/learn/api", re.I)
_SKIP_TYPES = {"image", "stylesheet", "font", "script", "media", "manifest"}


def _attach_trace(ctx) -> Path:
    """Registra navegações, redirecionamentos e nomes de cookies em login-trace.log.

    Nunca grava valores de cookie nem query strings.
    """
    path = config.DATA_DIR / "login-trace.log"
    trace = logging.getLogger(f"{__name__}.trace")
    handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    trace.addHandler(handler)

    def on_response(resp) -> None:
        req = resp.request
        if req.resource_type in _SKIP_TYPES:
            return
        if req.resource_type != "document" and not _AUTH_HINT.search(_short(req.url)):
            return
        line = f"{resp.status} {req.method} {req.resource_type} {_short(req.url)}"
        if location := resp.headers.get("location"):
            line += f" -> {_short(urljoin(req.url, location))}"
        if set_cookie := resp.headers.get("set-cookie"):
            names = sorted({c.split("=", 1)[0].strip() for c in set_cookie.split("\n")})
            line += f" | set-cookie: {', '.join(names)}"
        trace.info(line)

    def watch(page) -> None:
        trace.info(f"aba aberta: {_short(page.url) or '(vazia)'}")
        page.on("framenavigated", lambda f: f.parent_frame is None and trace.info(f"navegou: {_short(f.url)}"))
        page.on("close", lambda _: trace.info("aba fechada"))

    for page in ctx.pages:
        watch(page)
    ctx.on("page", watch)
    ctx.on("response", on_response)
    log.info("registrando navegação em %s", path)
    return path


async def refresh_session(interactive: bool, timeout: float) -> dict | None:
    """Abre o Blackboard no perfil persistente e espera a sessão ficar válida.

    Com interactive=False roda headless e só funciona se o SSO ainda lembrar do login.
    Retorna o usuário logado, ou None se não deu certo dentro do prazo.
    """
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(config.PROFILE_DIR),
            headless=not interactive,
            channel=config.BROWSER_CHANNEL or None,
            no_viewport=interactive,
        )
        if interactive:
            _attach_trace(ctx)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(config.LOGIN_URL, wait_until="domcontentloaded")
            deadline = time.monotonic() + timeout
            last_status = None
            while time.monotonic() < deadline:
                if interactive and not ctx.pages:
                    log.warning("janela fechada antes de o login ser detectado")
                    return None
                status, user = await _check_me(ctx)
                if status != last_status:
                    last_status = status
                    log.info("users/me -> HTTP %s | abas: %s", status, _where(ctx))
                if user is not None:
                    await ctx.storage_state(path=config.STATE_FILE)
                    return user
                await asyncio.sleep(2)
            log.warning("tempo esgotado esperando o login (último status: %s)", last_status)
            return None
        except PlaywrightError as e:
            # Janela fechada pelo usuário, timeout de navegação etc.
            # Só a primeira linha: o "call log" do Playwright inclui os cookies da sessão.
            log.warning("login abortado: %s", str(e).splitlines()[0])
            return None
        finally:
            try:
                await ctx.close()
            except PlaywrightError:
                pass
