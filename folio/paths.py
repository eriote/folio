from pathlib import Path
import os

DATA_DIR   = Path(os.environ.get("FOLIO_DATA_DIR", Path.home() / ".local" / "share" / "folio"))
DB_PATH    = DATA_DIR / "folio.db"
COVERS_DIR = DATA_DIR / "covers"
EPUBS_DIR  = DATA_DIR / "epubs"


def ensure_dirs():
    for d in (DATA_DIR, COVERS_DIR, EPUBS_DIR):
        d.mkdir(parents=True, exist_ok=True)
