from pathlib import Path
import os

DATA_DIR   = Path(os.environ.get("FOLIO_DATA_DIR", Path.home() / ".local" / "share" / "folio"))
DB_PATH    = DATA_DIR / "folio.db"
COVERS_DIR = DATA_DIR / "covers"
EPUBS_DIR  = DATA_DIR / "epubs"

CONFIG_DIR     = Path.home() / ".config" / "folio"
DEVICES_CONFIG = CONFIG_DIR / "devices.json"
PREFS_FILE     = CONFIG_DIR / "prefs.json"


def ensure_dirs():
    for d in (DATA_DIR, COVERS_DIR, EPUBS_DIR, CONFIG_DIR):
        d.mkdir(parents=True, exist_ok=True)
