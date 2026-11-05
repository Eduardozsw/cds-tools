import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import auth, config, ics, store, study, tracker
from .client import BlackboardClient, BlackboardError
from .text import html_to_text, local_time, safe_name

INSTRUCTIONS = f"""\
Acesso somente leitura ao Blackboard Ultra do aluno. Nunca inicie, abra ou leia questões de avaliações.

Para "o que há de novo?" use `cds_whats_new`; para prazos no calendário, `cds_export_ics`.

Pasta de estudos ({config.STUDY_DIR}): <período>/<matéria>/materiais e <período>/<matéria>/resumos.
- `cds_sync_materials` baixa o material das disciplinas para essa pasta (rode antes de resumir).
- Quando o usuário pedir um resumo: `cds_list_materials` para achar o arquivo, `cds_read_material`
  (em partes, seguindo `next_page`, até ler o material inteiro), escreva um resumo de estudo
  em markdown e grave com `cds_save_summary`.
- Um bom resumo de estudo traz: ideia central da unidade, definições, fórmulas e quando usar
  cada uma, exemplos resolvidos passo a passo, erros comuns e uma lista curta de autoteste.
"""

mcp = MCPServer("cds", instructions=INSTRUCTIONS)
bb = BlackboardClient()


def tool(fn):
    """Registra a tool como `cds_<nome>`, para não colidir com tools de outros MCPs do agente."""
    return mcp.tool(name=f"cds_{fn.__name__}")(fn)


V1 = f"{config.API}/v1"
V2 = f"{config.API}/v2"


def course_url(course_id: str) -> str:
    return f"{config.BASE_URL}/ultra/courses/{course_id}/outline"


def summarize_content(c: dict) -> dict:
    return {
        "id": c["id"],
        "title": c.get("title"),
        "type": (c.get("contentHandler") or {}).get("id"),
        "has_children": c.get("hasChildren", False),
        "available": (c.get("availability") or {}).get("available"),
        "modified": c.get("modified"),
    }


@tool
async def login() -> dict:
    """Abre o navegador para login no Blackboard (SSO) e salva a sessão.

    Use quando outra tool disser que a sessão expirou. O usuário tem alguns minutos
    para concluir o login na janela que abrir.
    """
    user = await auth.refresh_session(interactive=True, timeout=config.LOGIN_TIMEOUT)
    await bb.reset()
    if user is None:
        return {"ok": False, "message": "Login não concluído (janela fechada ou tempo esgotado)."}
    return {"ok": True, "id": user.get("id"), "name": (user.get("name") or {}).get("given")}


@tool
async def whoami() -> dict:
    """Retorna o usuário logado no Blackboard."""
    me = await bb.me()
    return {
        "id": me["id"],
        "userName": me.get("userName"),
        "name": me.get("name"),
        "email": (me.get("contact") or {}).get("email"),
    }


@tool
async def list_courses(limit: int = 30) -> list[dict]:
    """Lista as disciplinas do aluno, das acessadas mais recentemente para as mais antigas."""
    me = await bb.me()
    memberships = await bb.get_all(f"{V1}/users/{me['id']}/courses", {"expand": "course"}, max_items=500)
    memberships.sort(key=lambda m: m.get("lastAccessed") or "", reverse=True)
    out = []
    for m in memberships[:limit]:
        course = m.get("course") or {}
        cid = course.get("id") or m["courseId"]
        out.append(
            {
                "id": cid,
                "code": course.get("courseId"),
                "name": course.get("name"),
                "role": m.get("courseRoleId"),
                "last_accessed": m.get("lastAccessed"),
                "url": course_url(cid),
            }
        )
    return out


@tool
async def list_contents(course_id: str, parent_id: str | None = None) -> list[dict]:
    """Lista o conteúdo de uma disciplina (raiz) ou os filhos de uma pasta/módulo.

    course_id e parent_id estão no formato do Blackboard, ex.: `_123456_1`.
    """
    path = f"{V1}/courses/{course_id}/contents"
    if parent_id:
        path += f"/{parent_id}/children"
    return [summarize_content(c) for c in await bb.get_all(path)]


@tool
async def get_content(course_id: str, content_id: str) -> dict:
    """Detalha um item de conteúdo: texto do corpo, anexos e itens filhos."""
    base = f"{V1}/courses/{course_id}/contents/{content_id}"
    c = await bb.get_json(base)
    result = summarize_content(c) | {
        "description": html_to_text(c.get("description")),
        "body": html_to_text(c.get("body")),
    }
    try:
        result["attachments"] = [
            {"id": a["id"], "fileName": a.get("fileName"), "mimeType": a.get("mimeType")}
            for a in await bb.get_all(f"{base}/attachments")
        ]
    except BlackboardError:
        result["attachments"] = []
    if c.get("hasChildren"):
        result["children"] = [summarize_content(x) for x in await bb.get_all(f"{base}/children")]
    return result


@tool
async def download_attachment(course_id: str, content_id: str, attachment_id: str) -> dict:
    """Baixa um anexo de um conteúdo e retorna o caminho local do arquivo."""
    base = f"{V1}/courses/{course_id}/contents/{content_id}/attachments/{attachment_id}"
    meta = await bb.get_json(base)
    dest = await bb.download(f"{base}/download", config.DOWNLOAD_DIR / course_id, meta.get("fileName") or attachment_id)
    return {"path": str(dest), "mimeType": meta.get("mimeType"), "bytes": dest.stat().st_size}


@tool
async def upcoming_deadlines(days: int = 14) -> list[dict]:
    """Itens do calendário (entregas, provas, eventos) dos próximos `days` dias (máx. 112)."""
    days = max(1, min(days, 112))
    now = datetime.now(timezone.utc)
    items = await bb.get_all(
        f"{V1}/calendars/items",
        {"since": now.isoformat(), "until": (now + timedelta(days=days)).isoformat()},
    )
    items.sort(key=lambda i: i.get("end") or i.get("start") or "")
    return [
        {
            "title": i.get("title"),
            "course": i.get("calendarName"),
            "course_id": i.get("calendarId"),
            "type": i.get("type"),
            "start": local_time(i.get("start")),
            "end": local_time(i.get("end")),
        }
        for i in items
    ]


@tool
async def get_grades(course_id: str) -> list[dict]:
    """Notas do aluno numa disciplina, junto com nota máxima e prazo de cada coluna."""
    me = await bb.me()
    columns = {c["id"]: c for c in await bb.get_all(f"{V2}/courses/{course_id}/gradebook/columns")}
    grades = {g["columnId"]: g for g in await bb.get_all(f"{V2}/courses/{course_id}/gradebook/users/{me['id']}")}
    out = []
    for col_id, col in columns.items():
        g = grades.get(col_id, {})
        display = g.get("displayGrade") or {}
        out.append(
            {
                "column": col.get("name"),
                "possible": (col.get("score") or {}).get("possible"),
                "due": local_time((col.get("grading") or {}).get("due")),
                # Na v2 a nota vem em displayGrade.score (ou .text em escalas por letra).
                "score": display.get("score", g.get("score")),
                "display": display.get("text"),
                "status": g.get("status"),
                "feedback": html_to_text(g.get("feedback")),
            }
        )
    return out


@tool
async def list_announcements(course_id: str, limit: int = 10) -> list[dict]:
    """Avisos de uma disciplina, mais recentes primeiro."""
    items = await bb.get_all(f"{V1}/courses/{course_id}/announcements", max_items=100)
    items.sort(key=lambda a: a.get("created") or "", reverse=True)
    return [
        {"title": a.get("title"), "created": a.get("created"), "body": html_to_text(a.get("body"))}
        for a in items[:limit]
    ]


@tool
def parse_url(url: str) -> dict:
    """Extrai os IDs (`_123_1`) de uma URL do Blackboard Ultra, rotulados pelo segmento anterior."""
    parts = [p for p in re.split(r"[/?&=]", url) if p]
    ids = {}
    for prev, part in zip(parts, parts[1:]):
        if re.fullmatch(r"_\d+_\d+", part):
            ids.setdefault(prev, part)
    return ids


@tool
async def sync_materials(course_id: str | None = None, all_terms: bool = False, force: bool = False) -> list[dict]:
    """Baixa o material das disciplinas para a pasta de estudos (<período>/<matéria>/materiais).

    Sem argumentos, sincroniza todas as disciplinas do período atual. Arquivos já baixados são
    pulados (use force=True para baixar de novo). Avaliações nunca são abertas.
    """
    return await study.sync(bb, course_id, all_terms, force)


@tool
def list_materials(subject: str | None = None) -> list[dict]:
    """Lista materiais e resumos da pasta de estudos. `subject` filtra por parte do nome da matéria."""
    return study.list_library(subject)


@tool
def read_material(path: str, start_page: int = 1, end_page: int | None = None) -> dict:
    """Extrai o texto de um material (PDF, .md, .txt) da pasta de estudos.

    `path` é o caminho como aparece em cds_list_materials. PDFs longos voltam em partes:
    se `next_page` vier preenchido, chame de novo com start_page=next_page.
    """
    return study.read(path, start_page, end_page)


@tool
def save_summary(subject: str, title: str, markdown: str) -> dict:
    """Grava um resumo em <período>/<matéria>/resumos/<title>.md.

    `subject` é o campo `materia` de cds_list_materials (ex.: '2026.2/Cálculo II').
    """
    return {"path": study.save_summary(subject, title, markdown), "root": str(config.STUDY_DIR)}


@tool
async def sync_data() -> dict:
    """Atualiza o banco local (avisos, prazos, notas e conteúdo do período atual)."""
    return await tracker.sync(bb)


@tool
async def whats_new(sync: bool = True, mark_seen: bool = True) -> dict:
    """Novidades desde a última vez que esta tool foi chamada: avisos, prazos, notas e materiais.

    Na primeira chamada só registra o estado atual (sem listar tudo que já existia).
    """
    if sync:
        await tracker.sync(bb)
    db = store.connect()
    if not tracker.has_state(db, "mcp"):
        tracker.changes(db, "mcp", baseline=True)
        return {
            "status": "baseline",
            "events": [],
            "message": "Estado atual registrado; as próximas chamadas trazem só o que mudar.",
        }
    events = tracker.changes(db, "mcp", mark=mark_seen)
    return {
        "status": "changes" if events else "no_changes",
        "last_sync": store.get_meta(db, "last_sync"),
        "events": events,
    }


@tool
def export_ics(filename: str = "blackboard-prazos.ics", alarm_hours: int = 24) -> dict:
    """Exporta os prazos do banco local como .ics (importável no Google Agenda/Outlook/celular).

    Grava em DOWNLOAD_DIR; `filename` é só o nome do arquivo. Rode cds_sync_data antes se precisar.
    """
    text, n = ics.build(store.connect(), alarm_hours)
    dest = config.DOWNLOAD_DIR / safe_name(Path(filename).name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8", newline="")
    return {"path": str(dest), "events": n}
