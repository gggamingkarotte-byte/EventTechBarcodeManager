from pathlib import Path
import os
import sys


APP_NAME = "EventTech Barcode Manager"
APP_VERSION = "1.0.6"


def app_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "EventTechBarcodeManager"
    return Path.home() / ".eventtech_barcode_manager"


DATA_DIR = app_dir()
DATABASE_PATH = DATA_DIR / "eventtech.db"
FILES_DIR = DATA_DIR / "Dateien"
BACKUP_DIR = DATA_DIR / "Backups"

