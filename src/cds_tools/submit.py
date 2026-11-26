"""Envio de arquivo como resposta de avaliação, pelo navegador.

Única operação de escrita do CDS. Não usa a API HTTP (o cliente continua só com GET): repete no
navegador, com o perfil persistente do login, o que o aluno faria à mão. Só retoma uma tentativa
que o aluno já abriu; se a página oferecer iniciar uma tentativa nova, para sem clicar.
"""

import logging
import re
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from . import config
from .client import LOGIN_HINT, AuthError, BlackboardClient, BlackboardError

log = logging.getLogger(__name__)

V1 = f"{config.API}/v1"
TIMEOUT_MS = 30_000

# Textos da interface do Ultra em português.
CONTINUE = re.compile(r"continuar tentativa", re.I)
EDITOR_PLACEHOLDER = "Use o editor para formatar sua resposta"
ATTACH = "Inserir/editar arquivos locais"
SAVED = re.compile(r"salvo pela última vez", re.I)
SUBMIT = re.compile(r"^\s*enviar\s*$", re.I)
RECEIPT = re.compile(r"número de confirmação:?\s*([0-9a-f]{16,})", re.I)


class SubmitError(BlackboardError):
    pass


async def content_folders(bb: BlackboardClient, course_id: str, content_id: str) -> list[str]:
    """Títulos das pastas entre a raiz da disciplina e o item, de cima para baixo.

    No Ultra, abrir a avaliação por link direto deixa a página vazia; é preciso clicar no
    item a partir do conteúdo da disciplina, abrindo as pastas no caminho.
    """
    c = await bb.get_json(f"{V1}/courses/{course_id}/contents/{content_id}")
    folders: list[str] = []
    parent_id = c.get("parentId")
    while parent_id:
        parent = await bb.get_json(f"{V1}/courses/{course_id}/contents/{parent_id}")
        parent_id = parent.get("parentId")
        if parent_id:  # a pasta sem pai é a ROOT, que não aparece na página
            folders.insert(0, parent["title"])
    return folders


def _first_line(e: Exception) -> str:
    # O "call log" do Playwright pode trazer cookies da sessão.
    return str(e).splitlines()[0]


async def submit_file(course_id: str, folders: list[str], title: str, attempt_id: str, file: Path) -> dict:
    """Anexa `file` à questão dissertativa da tentativa `attempt_id` e envia.

    Retorna o número de confirmação mostrado pelo Blackboard. Erro antes do clique final
    não envia nada (o anexo pode ficar salvo como rascunho da tentativa).
    """
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(config.PROFILE_DIR),
            headless=False,  # o aluno vê o envio acontecer
            channel=config.BROWSER_CHANNEL or None,
            no_viewport=True,
            args=["--start-maximized"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.set_default_timeout(TIMEOUT_MS)
        submitted = False
        try:
            await page.goto(f"{config.BASE_URL}/ultra/courses/{course_id}/outline", wait_until="domcontentloaded")
            if not page.url.startswith(f"{config.BASE_URL}/ultra"):
                raise AuthError(LOGIN_HINT)
            for name in folders:
                await page.get_by_text(name, exact=True).first.click()
            await page.get_by_text(title, exact=True).first.click()

            try:
                await page.get_by_role("button", name=CONTINUE).click()
            except PlaywrightError:
                raise SubmitError("botão 'Continuar tentativa' não apareceu; o CDS não inicia tentativas") from None
            await page.wait_for_url(re.compile(rf"/attempt/{re.escape(attempt_id)}"))

            await page.get_by_text(EDITOR_PLACEHOLDER).first.click()
            async with page.expect_file_chooser() as chooser:
                await page.get_by_role("button", name=ATTACH).click()
            await (await chooser.value).set_files(str(file))
            await page.get_by_role("dialog").get_by_role("button", name="Salvar").click()
            await page.get_by_text(file.name).first.wait_for()
            await page.get_by_text(SAVED).first.wait_for()

            await page.locator("button", has_text=SUBMIT).first.click()
            confirm = page.locator('[role="dialog"], [role="alertdialog"]').filter(has_text="Enviar teste")
            await confirm.locator("button", has_text=SUBMIT).click()
            submitted = True

            receipt = page.locator('[role="dialog"], [role="alertdialog"]').filter(has_text=RECEIPT)
            await receipt.wait_for()
            m = RECEIPT.search(await receipt.inner_text())
            return {"confirmation": m.group(1) if m else None}
        except PlaywrightError as e:
            shot = config.DATA_DIR / "submit-error.png"
            try:
                await page.screenshot(path=str(shot))
            except PlaywrightError:
                shot = None
            state = "o envio foi clicado, confira no Blackboard" if submitted else "nada foi enviado"
            raise SubmitError(f"{_first_line(e)} ({state}; tela em {shot})") from None
        finally:
            try:
                await ctx.close()
            except PlaywrightError:
                pass
