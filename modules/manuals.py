from pathlib import Path
import shutil

from config import FILES_DIR


def add_document(db, article_id: int, source: Path, name: str = "") -> int:
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    destination_dir = FILES_DIR / "Anleitungen" / str(article_id)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    shutil.copy2(source, destination)
    cursor = db.execute(
        "INSERT INTO documents(article_id,name,kind,path) VALUES(?,?,?,?)",
        (article_id, name or source.name, source.suffix.lower().lstrip("."), str(destination)),
    )
    return cursor.lastrowid


def add_external_link(db, article_id: int, name: str, url: str) -> int:
    cursor = db.execute(
        "INSERT INTO documents(article_id,name,kind,external_url) VALUES(?,?,?,?)",
        (article_id, name, "link", url),
    )
    return cursor.lastrowid


def add_photo(db, article_id: int, source: Path) -> int:
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    destination_dir = FILES_DIR / "Fotos" / str(article_id)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    shutil.copy2(source, destination)
    cursor = db.execute(
        "INSERT INTO photos(article_id,name,path) VALUES(?,?,?)",
        (article_id, source.name, str(destination)),
    )
    return cursor.lastrowid
