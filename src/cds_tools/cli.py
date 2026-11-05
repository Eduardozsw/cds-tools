"""Linha de comando: `cds-tools <comando>`. Sem comando, sobe o servidor MCP (stdio)."""

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import auth, config, ics, store, study, tracker
from .client import AuthError, BlackboardClient
from .text import local_time


def _out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------- login / sync


def cmd_login(args) -> int:
    _err(f"Abrindo navegador. Faça login em {config.BASE_URL} ...")
    user = asyncio.run(auth.refresh_session(interactive=True, timeout=config.LOGIN_TIMEOUT))
    if user is None:
        _err("Login não concluído.")
        return 1
    name = user.get("userName") or (user.get("name") or {}).get("given")
    print(f"OK: logado como {name}. Sessão salva em {config.STATE_FILE}")
    return 0


def cmd_install_browser(args) -> int:
    """Chromium do Playwright desta instalação, para quem não tem o Edge."""
    return subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"])


def cmd_sync(args) -> int:
    async def run():
        bb = BlackboardClient()
        result = await tracker.sync(bb, all_terms=args.all_terms)
        if args.materials:
            result["materials"] = await study.sync(bb, None, args.all_terms, False)
        return result

    result = asyncio.run(run())
    if args.json:
        _out(result)
    else:
        for c in result["courses"]:
            errs = f"  ERROS: {'; '.join(c['errors'])}" if c["errors"] else ""
            if len(c["restricted"]) == 3:
                print(f"{c['period']}/{c['course']}: sem acesso de aluno (ignorada)")
                continue
            errs += f"  (restrito: {', '.join(c['restricted'])})" if c["restricted"] else ""
            print(
                f"{c['period']}/{c['course']}: {c.get('news', '?')} avisos, {c.get('deadlines', '?')} prazos, "
                f"{c.get('grades', '?')} notas, {c.get('materials', '?')} itens de conteúdo{errs}"
            )
        for m in result.get("materials", []):
            print(f"  materiais {m['materia']}: {len(m['novos'])} novos")
    return 1 if any(c["errors"] for c in result["courses"]) else 0


# --------------------------------------------------------------------------- watch


def _watch_once(args) -> tuple[list[dict], dict]:
    """Sincroniza e devolve (eventos, resumo). Falha vira evento `error`, nunca silêncio."""
    events: list[dict] = []
    try:
        result = asyncio.run(tracker.sync(BlackboardClient()))
    except AuthError as e:
        events.append({"type": "error", "status": "failed", "stage": "auth", "message": str(e)})
        return events, {"type": "sync", "status": "failed", "event_count": len(events), "courses": []}
    except Exception as e:  # rede, API fora do ar etc.
        events.append(
            {
                "type": "error",
                "status": "failed",
                "stage": "network",
                "message": f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}",
            }
        )
        return events, {"type": "sync", "status": "failed", "event_count": len(events), "courses": []}

    for c in result["courses"]:
        for msg in c["errors"]:
            events.append(
                {
                    "type": "error",
                    "status": "failed",
                    "stage": "sync",
                    "course_id": c["course_id"],
                    "course": c["course"],
                    "message": msg,
                }
            )
    db = store.connect()
    baseline = args.baseline or not tracker.has_state(db, "watch") and args.first_run_baseline
    events += tracker.changes(db, "watch", baseline=baseline)
    failed = any(e["type"] == "error" for e in events)
    status = "failed" if failed else "baseline" if baseline else "changes" if events else "no_changes"
    keys = ("course_id", "course", "news", "deadlines", "grades", "materials", "errors", "restricted")
    courses = [{k: c.get(k) for k in keys} for c in result["courses"]]
    return events, {"type": "sync", "status": status, "event_count": len(events), "courses": courses}


def _describe(e: dict) -> str:
    t = e["type"]
    if t == "error":
        return f"[erro/{e['stage']}] {e.get('course') or ''} {e['message']}".strip()
    tag = "novo" if e["status"] == "new" else "alterado"
    if t == "news":
        return f"[aviso {tag}] {e['course']}: {e['title']}"
    if t == "deadline":
        return f"[prazo {tag}] {e['course']}: {e['title']} até {e['due']}"
    if t == "grade":
        nota = e["display"] or e["score"]
        total = f"/{e['possible']:g}" if e["possible"] else ""
        return f"[nota {tag}] {e['course']}: {e['title']} = {nota}{total}"
    return f"[material {tag}] {e['course']}: {e['title']}" + (f" ({e['topic']})" if e.get("topic") else "")


def _notify(events: list[dict]) -> None:
    """Notificação do Windows (balão na bandeja), sem dependências extras."""
    if not events or sys.platform != "win32":
        return

    title = f"Blackboard: {len(events)} novidade(s)"
    text = "\n".join(_describe(e) for e in events[:5]) + ("\n..." if len(events) > 5 else "")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing;"
        "$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information;"
        "$n.Visible = $true; $n.ShowBalloonTip(10000, $env:BB_N_TITLE, $env:BB_N_TEXT, 'Info');"
        "Start-Sleep -Seconds 10; $n.Dispose()"
    )
    env = {**os.environ, "BB_N_TITLE": title, "BB_N_TEXT": text[:250]}
    subprocess.Popen(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


# Muitos alunos sincronizando com frequência viram carga para o Blackboard: 30 min é o piso.
MIN_INTERVAL = 30 * 60


def _parse_interval(s: str) -> int:
    unit = {"s": 1, "m": 60, "h": 3600}.get(s[-1], 60)
    return max(int(s[:-1] if s[-1] in "smh" else s) * unit, MIN_INTERVAL)


def cmd_watch(args) -> int:
    if args.json and not args.once:
        _err("--json exige --once (use --jsonl no modo contínuo)")
        return 2
    args.first_run_baseline = False
    while True:
        events, summary = _watch_once(args)
        if args.json:
            _out({**summary, "events": events})
        elif args.jsonl:
            for e in events:
                _out(e)
            _out(summary)
        else:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            if summary["status"] == "baseline":
                print(f"{stamp} estado atual registrado; a partir de agora só aparecem mudanças.")
            elif not events:
                print(f"{stamp} nada novo.")
            for e in events:
                print(f"{stamp} {_describe(e)}")
        sys.stdout.flush()
        if args.notify:
            _notify([e for e in events if e["type"] != "error"] if summary["status"] != "failed" else events)
        if args.once:
            return 1 if args.fail_on_error and summary["status"] == "failed" else 0
        time.sleep(_parse_interval(args.interval))


# --------------------------------------------------------------------------- consultas offline


def _need_db() -> sqlite3.Connection:
    db = store.connect()
    if not store.get_meta(db, "last_sync"):
        _err("Banco vazio. Rode `cds-tools sync` primeiro.")
        sys.exit(1)
    return db


def _course_filter(course: str | None) -> tuple[str, list]:
    if not course:
        return "", []
    return " and (c.subject like ? or c.id = ? or c.code like ?)", [f"%{course}%", course, f"%{course}%"]


def cmd_deadlines(args) -> int:
    db = _need_db()
    where, params = _course_filter(args.course)
    since = "" if args.all else " and k.due >= ?"
    if not args.all:
        params.append(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
    rows = db.execute(
        "select k.*, c.subject, g.score, g.status from columns k join courses c on c.id = k.course_id "
        "left join grades g on g.column_id = k.id "
        f"where k.due is not null{since}{where} order by k.due",
        params,
    ).fetchall()
    data = [
        {
            "course": r["subject"],
            "title": r["name"],
            "due": local_time(r["due"]),
            "possible": r["possible"],
            "score": r["score"],
            "status": r["status"],
        }
        for r in rows
    ]
    if args.json:
        _out(data)
        return 0
    for d in data:
        done = " (nota lançada)" if d["score"] is not None else " (entregue)" if d["status"] else ""
        print(f"{d['due']}  {d['course']}: {d['title']}{done}")
    return 0


def cmd_grades(args) -> int:
    db = _need_db()
    where, params = _course_filter(args.course)
    rows = db.execute(
        "select k.name, k.possible, k.due, c.subject, g.score, g.display, g.status, g.feedback "
        "from columns k join courses c on c.id = k.course_id left join grades g on g.column_id = k.id "
        f"where 1=1{where} order by c.subject, k.due",
        params,
    ).fetchall()
    data = [dict(r) for r in rows if args.all or r["score"] is not None]
    if args.json:
        _out(data)
        return 0
    current = None
    for d in data:
        if d["subject"] != current:
            current = d["subject"]
            print(f"\n{current}")
        nota = d["display"] or ("-" if d["score"] is None else f"{d['score']:g}")
        total = f"/{d['possible']:g}" if d["possible"] else ""
        print(f"  {nota}{total}  {d['name']}")
    return 0


def cmd_news(args) -> int:
    db = _need_db()
    if args.unread:
        events = tracker.changes(db, "news", mark=args.mark_seen, kinds=("news",))
        rows = [e for e in events if not args.course or args.course.lower() in e["course"].lower()]
    else:
        where, params = _course_filter(args.course)
        rows = [
            {"course": r["subject"], "title": r["title"], "published_at": local_time(r["created"]), "body": r["body"]}
            for r in db.execute(
                "select a.*, c.subject from announcements a join courses c on c.id = a.course_id "
                f"where 1=1{where} order by a.created desc limit ?",
                [*params, args.limit],
            )
        ]
    if args.json:
        _out(rows)
        return 0
    for r in rows:
        print(f"{r['published_at']}  {r['course']}: {r['title']}")
        if args.bodies and r.get("body"):
            print("    " + r["body"].replace("\n", "\n    "))
    if args.unread and not rows:
        print("Nenhum aviso novo.")
    return 0


def cmd_ics(args) -> int:
    text, n = ics.build(_need_db(), alarm_hours=args.alarm)
    if args.out == "-":
        sys.stdout.write(text)
    else:
        Path(args.out).write_text(text, encoding="utf-8", newline="")
        _err(f"{n} prazos exportados para {Path(args.out).resolve()}")
    return 0


# --------------------------------------------------------------------------- init


def cmd_init(args) -> int:
    print("1/4 Verificando sessão...")
    try:
        me = asyncio.run(BlackboardClient().me())
        print(f"    já logado como {(me.get('name') or {}).get('given')}")
    except AuthError:
        if cmd_login(args):
            return 1
    print("2/4 Sincronizando avisos, prazos, notas e conteúdo...")
    sync_args = argparse.Namespace(all_terms=False, materials=False, json=False)
    cmd_sync(sync_args)
    print("3/4 Registrando estado atual (o watch só vai avisar o que mudar daqui pra frente)...")
    db = store.connect()
    tracker.changes(db, "watch", baseline=True)
    tracker.changes(db, "news", baseline=True, kinds=("news",))
    if not args.skip_materials:
        print(f"4/4 Baixando material para {config.STUDY_DIR} ...")
        for m in asyncio.run(study.sync(BlackboardClient(), None, False, False)):
            print(f"    {m['materia']}: {len(m['novos'])} novos")
    else:
        print("4/4 (material pulado)")

    cmd = _mcp_command()
    if not args.no_mcp:
        _setup_mcp(cmd)
    exe = subprocess.list2cmdline(cmd)
    print("\nPronto. Próximos passos opcionais:")
    print(f"  Prazos no calendário: {exe} ics --out prazos.ics   (importe no Google Agenda/Outlook)")
    print("  Aviso automático a cada 2 h (Agendador de Tarefas do Windows):")
    print(f'    schtasks /Create /SC HOURLY /MO 2 /TN "CDS Tools watch" /TR "{exe} watch --once --notify"')
    return 0


def _mcp_command() -> list[str]:
    """Comando que sobe o servidor. Instalado com `uv tool install`, não depende de nenhuma pasta."""
    found = shutil.which("cds-tools")
    # O do .venv do projeto só está no PATH dentro de `uv run`; aí vale o comando com --directory.
    # Caminho absoluto: o agente pode ter sido aberto antes de o uv pôr ~/.local/bin no PATH.
    if found and Path(sys.prefix).resolve() not in Path(found).resolve().parents:
        return [str(Path(found).resolve())]
    return ["uv", "--directory", str(Path(__file__).resolve().parents[2]), "run", "cds-tools"]


def _setup_mcp(cmd: list[str]) -> None:
    print("\nConfiguração MCP (Claude Desktop, Cursor e outros agentes aceitam este bloco):")
    print(json.dumps({"mcpServers": {"cds": {"command": cmd[0], "args": cmd[1:]}}}, indent=2))
    claude = shutil.which("claude")
    if not claude:
        return
    if sys.stdin.isatty() and input("Registrar no Claude Code agora? [S/n] ").strip().lower() not in ("", "s", "sim"):
        return
    r = subprocess.run([claude, "mcp", "add", "--scope", "user", "cds", "--", *cmd], capture_output=True, text=True)
    if r.returncode == 0:
        print("    MCP `cds` registrado no Claude Code (todas as pastas).")
    else:
        print(
            f"    Não deu para registrar ({(r.stderr or r.stdout).strip()[:200]}). "
            f"Rode: claude mcp add --scope user cds -- {subprocess.list2cmdline(cmd)}"
        )


# --------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cds-tools", description="Blackboard Ultra (Cruzeiro do Sul/Unipê)")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("serve", help="servidor MCP via stdio (padrão sem comando)")
    sub.add_parser("login", help="abre o navegador para o login SSO")
    sub.add_parser("install-browser", help='baixa o Chromium (use com BB_BROWSER_CHANNEL="" se não tiver o Edge)')

    s = sub.add_parser("init", help="login + sync + estado inicial + material")
    s.add_argument("--skip-materials", action="store_true")
    s.add_argument("--no-mcp", action="store_true", help="não registra o servidor MCP no Claude Code")

    s = sub.add_parser("sync", help="atualiza o banco local (avisos, prazos, notas, conteúdo)")
    s.add_argument("--materials", action="store_true", help="também baixa material para a pasta de estudos")
    s.add_argument("--all-terms", action="store_true", help="todos os períodos, não só o atual")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("watch", help="sincroniza e mostra o que mudou desde a última execução")
    s.add_argument("--once", action="store_true", help="uma execução só (para o Agendador de Tarefas)")
    s.add_argument("--interval", default="2h", help="intervalo no modo contínuo (ex.: 2h; mínimo 30m)")
    s.add_argument("--baseline", action="store_true", help="registra o estado atual sem emitir eventos")
    s.add_argument("--json", action="store_true", help="um documento JSON (exige --once)")
    s.add_argument("--jsonl", action="store_true", help="um evento JSON por linha")
    s.add_argument("--notify", action="store_true", help="notificação do Windows quando houver novidade")
    s.add_argument("--fail-on-error", action="store_true", help="código de saída 1 em falha")

    s = sub.add_parser("deadlines", help="prazos (do banco local)")
    s.add_argument("--course")
    s.add_argument("--all", action="store_true", help="inclui prazos passados")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("grades", help="notas (do banco local)")
    s.add_argument("--course")
    s.add_argument("--all", action="store_true", help="inclui colunas sem nota")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("news", help="avisos (do banco local)")
    s.add_argument("--course")
    s.add_argument("--unread", action="store_true", help="só os ainda não vistos")
    s.add_argument("--mark-seen", action="store_true", help="com --unread, marca como vistos")
    s.add_argument("--bodies", action="store_true", help="mostra o texto dos avisos")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("ics", help="exporta prazos como calendário .ics")
    s.add_argument("--out", default="blackboard.ics", help="arquivo de saída ('-' para stdout)")
    s.add_argument("--alarm", type=int, default=24, help="lembrete N horas antes (0 desliga)")
    return p


COMMANDS = {
    "login": cmd_login,
    "install-browser": cmd_install_browser,
    "init": cmd_init,
    "sync": cmd_sync,
    "watch": cmd_watch,
    "deadlines": cmd_deadlines,
    "grades": cmd_grades,
    "news": cmd_news,
    "ics": cmd_ics,
}


def run(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        return COMMANDS[args.cmd](args)
    except AuthError as e:
        _err(f"Sessão expirada: {e}\nRode `cds-tools login`.")
        return 1
