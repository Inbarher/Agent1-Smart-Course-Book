from __future__ import annotations

import os
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"

def load_local_env() -> None:
    """Load local development secrets without printing or exporting them."""
    if not ENV_FILE.exists():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

load_local_env()
DATA_DIR = ROOT / os.getenv("SMART_COURSE_DATA_DIR", "data")
DB_PATH = DATA_DIR / "database" / "smart_course_book_v2.sqlite3"
LEGACY_DB_PATH = DATA_DIR / "database" / "smart_course_book.sqlite3"
for directory in (DATA_DIR / "database", DATA_DIR / "materials", DATA_DIR / "generated", DATA_DIR / "temp", DATA_DIR / "logs"):
    directory.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=DATA_DIR / "logs" / "smart-course-book.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    encoding="utf-8",
)
LOGGER = logging.getLogger("smart_course_book")

ALLOWED = {"presentation": {".pdf"}, "transcript": {".txt", ".md", ".srt", ".vtt"}, "recording": {".mp3", ".wav", ".m4a", ".mp4", ".mov", ".webm"}}
MAX_UPLOAD_BYTES = 250 * 1024 * 1024
