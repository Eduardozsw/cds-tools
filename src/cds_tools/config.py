import os
from pathlib import Path

BASE_URL = os.environ.get("BB_BASE_URL", "https://bb.cruzeirodosulvirtual.com.br").rstrip("/")
API = "/learn/api/public"

# Entrada SAML direta (Microsoft Entra), a mesma que o link do portal usa.
LOGIN_URL = os.environ.get("BB_LOGIN_URL", f"{BASE_URL}/auth-saml/saml/login?apiVersion=1&redirectUrl={BASE_URL}/ultra")


def _default_data_dir() -> Path:
    new, legacy = Path.home() / ".cds-tools", Path.home() / ".blackboard-mcp"
    # Instalações anteriores ao nome cds-tools continuam com a sessão e o banco que já têm.
    return legacy if legacy.exists() and not new.exists() else new


# Fora do repositório por padrão: aqui ficam cookies que dão acesso total à conta.
DATA_DIR = Path(os.environ.get("BB_DATA_DIR") or _default_data_dir())
PROFILE_DIR = DATA_DIR / "profile"
STATE_FILE = DATA_DIR / "state.json"
DB_FILE = Path(os.environ.get("BB_DB") or DATA_DIR / "blackboard.db")
DOWNLOAD_DIR = Path(os.environ.get("BB_DOWNLOAD_DIR") or DATA_DIR / "downloads")

# Pasta de estudos: <período>/<matéria>/{materiais,resumos}
STUDY_DIR = Path(os.environ.get("BB_STUDY_DIR") or Path.home() / "faculdade")

# "msedge" usa o Edge instalado no Windows; "" usa o Chromium baixado pelo Playwright.
BROWSER_CHANNEL = os.environ.get("BB_BROWSER_CHANNEL", "msedge")

LOGIN_TIMEOUT = float(os.environ.get("BB_LOGIN_TIMEOUT", "300"))
