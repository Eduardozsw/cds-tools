"""Pasta de estudos: espelha o material das disciplinas em STUDY_DIR/<período>/<matéria>/.

Estrutura de cada matéria:
    materiais/   arquivos baixados, na mesma hierarquia de pastas do Blackboard
    resumos/     resumos gerados a pedido do usuário

Nunca entra em avaliações (testes, tarefas, fóruns, LTI): só baixa material de leitura.
"""

import json
import logging
import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

import httpx

from . import config
from .client import BlackboardClient, BlackboardError
from .text import html_to_text, safe_name

log = logging.getLogger(__name__)
logging.getLogger("pypdf").setLevel(logging.ERROR)

V1 = f"{config.API}/v1"

# Tipos de conteúdo que não são material de leitura (ou que abririam uma avaliação).
SKIP_HANDLERS = ("asmt", "assignment", "lti", "survey", "discussion", "forum", "journal", "blog", "wiki", "toollink")
EXT = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/msword": ".doc",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/zip": ".zip",
    "text/plain": ".txt",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}
TEXT_SUFFIXES = {".md", ".txt"}
MIN_DOC_CHARS = 200  # corpo de documento menor que isso é só "veja o anexo"


def period_name(term_name: str) -> str:
    """'2 Semestre 2026' / '1º Semestre 2026' -> '2026.2' / '2026.1'."""
    year = re.search(r"20\d\d", term_name)
    sem = re.search(r"([12])\s*º?\s*Semestre", term_name, re.I)
    if year and sem:
        return f"{year.group()}.{sem.group(1)}"
    return safe_name(term_name)


def subject_name(course: dict) -> str:
    """'Cálculo Diferencial e Integral II - 80h_T1_26_2' -> 'Cálculo Diferencial e Integral II'."""
    name = course.get("name") or course.get("courseId") or course["id"]
    return safe_name(re.split(r"\s+-\s+", name)[0])


def _is_bb_file(url: str) -> bool:
    s = urlsplit(url)
    return s.netloc == urlsplit(config.BASE_URL).netloc and s.path.startswith("/bbcswebdav/")


def _filename(resp: httpx.Response) -> str | None:
    disp = resp.headers.get("content-disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", disp) or re.search(r'filename="?([^";]+)', disp)
    return unquote(m.group(1)) if m else None


def resolve(rel: str) -> Path:
    """Caminho relativo à pasta de estudos, sem deixar escapar dela."""
    root = config.STUDY_DIR.resolve()
    path = (root / rel).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"caminho fora da pasta de estudos: {rel}")
    return path


async def list_courses(bb: BlackboardClient, all_terms: bool = False) -> list[tuple[str, str, dict]]:
    """(período, matéria, curso) das disciplinas do período mais recente, ou de todos."""
    me = await bb.me()
    memberships = await bb.get_all(f"{V1}/users/{me['id']}/courses", {"expand": "course"})
    terms: dict[str, str] = {}
    out = []
    for m in memberships:
        course = m.get("course") or {}
        term_id = course.get("termId")
        if not term_id:  # coordenação, hubs e afins não têm período
            continue
        if term_id not in terms:
            terms[term_id] = period_name((await bb.get_json(f"{V1}/terms/{term_id}")).get("name", term_id))
        out.append((terms[term_id], subject_name(course), course))
    if out and not all_terms:
        latest = max(p for p, _, _ in out)
        out = [x for x in out if x[0] == latest]
    # Duas turmas com o mesmo nome no mesmo período: diferencia pelo código.
    seen: dict[tuple[str, str], int] = {}
    for p, s, _ in out:
        seen[(p, s)] = seen.get((p, s), 0) + 1
    return [(p, s if seen[(p, s)] == 1 else safe_name(f"{s} ({c.get('courseId')})"), c) for p, s, c in out]


class _CourseSync:
    def __init__(self, bb: BlackboardClient, course: dict, root: Path, force: bool):
        self.bb = bb
        self.cid = course["id"]
        self.mat = root / "materiais"
        self.force = force
        self.manifest_path = self.mat / ".manifest.json"
        self.manifest: dict[str, str] = (
            json.loads(self.manifest_path.read_text(encoding="utf-8")) if self.manifest_path.exists() else {}
        )
        self.new: list[str] = []
        self.existing = 0
        self.skipped = 0
        self.links: list[str] = []

    def _known(self, key: str) -> bool:
        return not self.force and key in self.manifest and (self.mat / self.manifest[key]).exists()

    def _save(self, key: str, rel: PurePosixPath, name: str, data: bytes) -> None:
        dest = self.mat / rel / name
        n = 2
        while dest.exists() and self.manifest.get(key) != (rel / dest.name).as_posix():
            dest = dest.with_name(f"{Path(name).stem} ({n}){Path(name).suffix}")
            n += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        self.manifest[key] = (rel / dest.name).as_posix()
        self.new.append(self.manifest[key])

    async def _download(self, key: str, url: str, rel: PurePosixPath, title: str, use_title: bool) -> None:
        if self._known(key):
            self.existing += 1
            return
        resp = await self.bb.request(url, follow_redirects=True)
        ctype = resp.headers.get("content-type", "").split(";")[0].strip()
        if ctype == "text/html":  # player de vídeo embutido e afins
            self.skipped += 1
            return
        server_name = _filename(resp)
        ext = Path(server_name).suffix if server_name else EXT.get(ctype, "")
        name = f"{title}{ext}" if use_title or not server_name else server_name
        self._save(key, rel, safe_name(name), resp.content)

    async def walk(self, parent_id: str | None = None, rel: PurePosixPath = PurePosixPath()) -> None:
        path = f"{V1}/courses/{self.cid}/contents" + (f"/{parent_id}/children" if parent_id else "")
        for c in await self.bb.get_all(path):
            handler = (c.get("contentHandler") or {}).get("id", "")
            if any(k in handler for k in SKIP_HANDLERS):
                self.skipped += 1
                continue
            title = c.get("title") or c["id"]
            if title == "ultraDocumentBody":  # corpo da pasta pai, sem título próprio
                title = rel.name or "Documento"
            title = safe_name(title)
            try:
                await self._item(c, handler, title, rel)
                if c.get("hasChildren"):
                    await self.walk(c["id"], rel / title)
            except BlackboardError as e:
                log.warning("pulando %s (%s): %s", title, c["id"], e)
                self.skipped += 1

    async def _item(self, c: dict, handler: str, title: str, rel: PurePosixPath) -> None:
        if handler == "resource/x-bb-externallink":
            url = (c.get("contentHandler") or {}).get("url") or ""
            if _is_bb_file(url):
                await self._download(c["id"], urlsplit(url).path, rel, title, use_title=True)
            elif url:
                self.links.append(f"- [{title}]({url}) — {'/' if rel == PurePosixPath() else rel.as_posix()}")
            return

        base = f"{V1}/courses/{self.cid}/contents/{c['id']}"
        if handler == "resource/x-bb-file":
            for a in await self.bb.get_all(f"{base}/attachments"):
                await self._download(
                    a["id"], f"{base}/attachments/{a['id']}/download", rel, a.get("fileName") or title, use_title=False
                )
            return

        if handler == "resource/x-bb-document":
            body = c.get("body") or ""
            text = html_to_text(body)
            if len(text) >= MIN_DOC_CHARS:
                key = f"{c['id']}:body"
                if self._known(key):
                    self.existing += 1
                else:
                    self._save(key, rel, f"{title}.md", f"# {title}\n\n{text}\n".encode("utf-8"))
            # Arquivos embutidos no corpo do documento (Ultra guarda em /bbcswebdav/).
            for href in sorted(set(re.findall(r'href="([^"]+/bbcswebdav/[^"]+)"', body))):
                href = href.replace("&amp;", "&")
                await self._download(
                    f"{c['id']}:{urlsplit(href).path}", urlsplit(href).path, rel, title, use_title=False
                )

    def finish(self) -> None:
        self.mat.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        if self.links:
            (self.mat / "links.md").write_text("# Links externos\n\n" + "\n".join(self.links) + "\n", encoding="utf-8")


async def sync(bb: BlackboardClient, course_id: str | None, all_terms: bool, force: bool) -> list[dict]:
    courses = await list_courses(bb, all_terms=all_terms or bool(course_id))
    if course_id:
        courses = [x for x in courses if x[2]["id"] == course_id]
        if not courses:
            raise ValueError(f"disciplina {course_id} não encontrada entre as suas")
    report = []
    for period, subject, course in courses:
        root = config.STUDY_DIR / period / subject
        (root / "resumos").mkdir(parents=True, exist_ok=True)
        job = _CourseSync(bb, course, root, force)
        error = None
        try:
            await job.walk()
        except BlackboardError as e:
            # 403: a disciplina não libera o conteúdo para alunos
            error = "sem acesso de aluno" if e.status == 403 else str(e)[:200]
        finally:
            job.finish()
        report.append(
            {
                "materia": f"{period}/{subject}",
                "novos": job.new,
                "ja_existiam": job.existing,
                "ignorados": job.skipped,
                "links_externos": len(job.links),
                **({"erro": error} if error else {}),
            }
        )
    return report


def list_library(subject: str | None = None) -> list[dict]:
    root = config.STUDY_DIR
    if not root.exists():
        return []
    out = []
    for subj in sorted(p for p in root.glob("*/*") if p.is_dir()):
        rel = subj.relative_to(root).as_posix()
        if subject and subject.lower() not in rel.lower():
            continue
        mat, res = subj / "materiais", subj / "resumos"
        out.append(
            {
                "materia": rel,
                "materiais": sorted(
                    p.relative_to(root).as_posix() for p in mat.rglob("*") if p.is_file() and p.name != ".manifest.json"
                )
                if mat.exists()
                else [],
                "resumos": sorted(p.relative_to(root).as_posix() for p in res.glob("*.md")) if res.exists() else [],
            }
        )
    return out


def read(path: str, start_page: int = 1, end_page: int | None = None, max_chars: int = 60000) -> dict:
    file = resolve(path)
    if not file.is_file():
        raise FileNotFoundError(path)
    if file.suffix.lower() in TEXT_SUFFIXES:
        text = file.read_text(encoding="utf-8", errors="replace")
        return {"path": path, "text": text[:max_chars], "truncated": len(text) > max_chars}
    if file.suffix.lower() != ".pdf":
        raise ValueError(f"formato não suportado para leitura: {file.suffix}")

    from pypdf import PdfReader

    reader = PdfReader(file)
    total = len(reader.pages)
    last = min(end_page or total, total)
    parts, size, page = [], 0, start_page
    while page <= last:
        chunk = f"\n--- página {page} ---\n" + (reader.pages[page - 1].extract_text() or "")
        if parts and size + len(chunk) > max_chars:
            break
        parts.append(chunk)
        size += len(chunk)
        page += 1
    return {
        "path": path,
        "total_pages": total,
        "pages": f"{start_page}-{page - 1}",
        "next_page": page if page <= last else None,
        "text": "".join(parts),
    }


def save_summary(subject: str, title: str, markdown: str) -> str:
    folder = resolve(subject)
    if folder.parent.parent != config.STUDY_DIR.resolve():
        raise ValueError("subject deve ser '<período>/<matéria>', como aparece em list_materials")
    dest = folder / "resumos" / f"{safe_name(title)}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(markdown, encoding="utf-8")
    return dest.relative_to(config.STUDY_DIR.resolve()).as_posix()
