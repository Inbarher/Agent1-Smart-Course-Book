"""Local entry point that also works with Codex's bundled Python runtime."""
import site
import os
from pathlib import Path

_project_packages = Path(__file__).resolve().parent / ".venv" / "Lib" / "site-packages"
if _project_packages.is_dir():
    site.addsitedir(str(_project_packages))

if not os.getenv("SSL_CERT_FILE"):
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except ModuleNotFoundError:
        pass

from app.main import run
run()
