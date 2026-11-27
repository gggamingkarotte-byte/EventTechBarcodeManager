import sqlite3
import uuid
from pathlib import Path
from typing import Iterable

from config import DATABASE_PATH, DATA_DIR, BACKUP_DIR, FILES_DIR


SYNC_TABLES = (
    "warehouses", "racks", "levels", "boxes", "articles", "barcodes",
    "movements", "dmx_modes", "dmx_channels", "repairs", "scans",
    "inventories", "inventory_items", "events", "event_items",
)


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS warehouses (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    barcode TEXT UNIQUE,
    description TEXT
);
CREATE TABLE IF NOT EXISTS racks (
    id INTEGER PRIMARY KEY,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    barcode TEXT UNIQUE,
    width_cm INTEGER NOT NULL DEFAULT 120,
    height_cm INTEGER NOT NULL DEFAULT 200
);
CREATE TABLE IF NOT EXISTS levels (
    id INTEGER PRIMARY KEY,
    rack_id INTEGER NOT NULL REFERENCES racks(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    barcode TEXT UNIQUE,
    height_cm INTEGER NOT NULL DEFAULT 40
);
CREATE TABLE IF NOT EXISTS boxes (
    id INTEGER PRIMARY KEY,
    level_id INTEGER NOT NULL REFERENCES levels(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    barcode TEXT UNIQUE,
    description TEXT
);
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    manufacturer TEXT,
    model TEXT,
    category TEXT,
    barcode TEXT UNIQUE,
    quantity INTEGER NOT NULL DEFAULT 1,
    box_id INTEGER REFERENCES boxes(id) ON DELETE SET NULL,
    description TEXT,
    technical_data TEXT,
    status TEXT NOT NULL DEFAULT 'Einsatzbereit'
);
CREATE TABLE IF NOT EXISTS barcodes (
    id INTEGER PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    code_type TEXT NOT NULL,
    article_id INTEGER REFERENCES articles(id) ON DELETE CASCADE,
    entity_type TEXT,
    entity_id INTEGER,
    label TEXT,
    scale INTEGER NOT NULL DEFAULT 100
);
CREATE TABLE IF NOT EXISTS movements (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    from_box_id INTEGER REFERENCES boxes(id),
    to_box_id INTEGER REFERENCES boxes(id),
    quantity INTEGER NOT NULL DEFAULT 1,
    action TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dmx_modes (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    channel_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS dmx_channels (
    id INTEGER PRIMARY KEY,
    mode_id INTEGER NOT NULL REFERENCES dmx_modes(id) ON DELETE CASCADE,
    channel_number INTEGER NOT NULL,
    function_name TEXT NOT NULL,
    value_min INTEGER DEFAULT 0,
    value_max INTEGER DEFAULT 255,
    description TEXT
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT,
    external_url TEXT
);
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repairs (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    error_description TEXT,
    damage_type TEXT,
    measures TEXT,
    spare_parts TEXT,
    costs REAL,
    repair_status TEXT NOT NULL,
    completion_date TEXT,
    internal_notes TEXT
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    barcode TEXT NOT NULL,
    article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL,
    box_id INTEGER REFERENCES boxes(id) ON DELETE SET NULL,
    scan_type TEXT NOT NULL,
    action TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inventories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    warehouse_id INTEGER REFERENCES warehouses(id),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    report_path TEXT
);
CREATE TABLE IF NOT EXISTS inventory_items (
    id INTEGER PRIMARY KEY,
    inventory_id INTEGER NOT NULL REFERENCES inventories(id) ON DELETE CASCADE,
    article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL,
    barcode TEXT NOT NULL,
    expected_quantity INTEGER NOT NULL DEFAULT 0,
    scanned_quantity INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    location TEXT,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    contact TEXT,
    organizer TEXT,
    status TEXT NOT NULL DEFAULT 'Geplant',
    notes TEXT
);
CREATE TABLE IF NOT EXISTS event_items (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    required_quantity INTEGER NOT NULL DEFAULT 1 CHECK (required_quantity > 0),
    issued_quantity INTEGER NOT NULL DEFAULT 0 CHECK (issued_quantity >= 0),
    returned_quantity INTEGER NOT NULL DEFAULT 0 CHECK (returned_quantity >= 0),
    missing_quantity INTEGER NOT NULL DEFAULT 0 CHECK (missing_quantity >= 0),
    damaged_quantity INTEGER NOT NULL DEFAULT 0 CHECK (damaged_quantity >= 0),
    notes TEXT,
    UNIQUE(event_id, article_id)
);
CREATE TABLE IF NOT EXISTS sync_state (
    entity_uuid TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    baseline_json TEXT NOT NULL,
    remote_revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_tombstones (
    entity_uuid TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path = DATABASE_PATH):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        self._migrate_layout_columns()
        self._migrate_sync_columns()
        self.connection.commit()

    def _migrate_layout_columns(self):
        layout_columns = {
            "racks": {"width_cm": "INTEGER NOT NULL DEFAULT 120", "height_cm": "INTEGER NOT NULL DEFAULT 200"},
            "levels": {"height_cm": "INTEGER NOT NULL DEFAULT 40"},
            "barcodes": {"label": "TEXT", "scale": "INTEGER NOT NULL DEFAULT 100"},
        }
        for table, columns_to_add in layout_columns.items():
            existing = {
                row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")
            }
            for name, definition in columns_to_add.items():
                if name not in existing:
                    self.connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                    )

    def _migrate_sync_columns(self):
        for table in SYNC_TABLES:
            columns = {
                row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")
            }
            if "sync_uuid" not in columns:
                self.connection.execute(f"ALTER TABLE {table} ADD COLUMN sync_uuid TEXT")
            for row in self.connection.execute(
                f"SELECT id FROM {table} WHERE sync_uuid IS NULL OR sync_uuid = ''"
            ).fetchall():
                self.connection.execute(
                    f"UPDATE {table} SET sync_uuid=? WHERE id=?",
                    (str(uuid.uuid4()), row["id"]),
                )
            self.connection.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_sync_uuid ON {table}(sync_uuid)"
            )
            self.connection.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS trg_{table}_sync_uuid_insert
                AFTER INSERT ON {table}
                WHEN NEW.sync_uuid IS NULL OR NEW.sync_uuid = ''
                BEGIN
                    UPDATE {table} SET sync_uuid=(
                        lower(hex(randomblob(4))) || '-' ||
                        lower(hex(randomblob(2))) || '-4' ||
                        lower(substr(hex(randomblob(2)), 2)) || '-' ||
                        substr('89ab', abs(random()) % 4 + 1, 1) ||
                        lower(substr(hex(randomblob(2)), 2)) || '-' ||
                        lower(hex(randomblob(6)))
                    ) WHERE id=NEW.id;
                END
                """
            )
            self.connection.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS trg_{table}_sync_delete
                BEFORE DELETE ON {table}
                WHEN OLD.sync_uuid IS NOT NULL AND OLD.sync_uuid != ''
                BEGIN
                    INSERT OR REPLACE INTO sync_tombstones(entity_uuid,entity_type)
                    VALUES(OLD.sync_uuid, '{table}');
                END
                """
            )

    def close(self):
        self.connection.close()

    def execute(self, sql: str, parameters: Iterable = ()) -> sqlite3.Cursor:
        cursor = self.connection.execute(sql, tuple(parameters))
        self.connection.commit()
        return cursor

    def query(self, sql: str, parameters: Iterable = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, tuple(parameters)).fetchall())

    def backup(self, destination: Path | None = None) -> Path:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        if destination is None:
            from datetime import datetime
            destination = BACKUP_DIR / f"eventtech_{datetime.now():%Y%m%d_%H%M%S}.db"
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination) as target:
            self.connection.backup(target)
        return destination

    def restore(self, source: Path) -> None:
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(source)
        self.connection.close()
        with sqlite3.connect(source) as source_db, sqlite3.connect(self.path) as target_db:
            source_db.backup(target_db)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        self._migrate_layout_columns()
        self._migrate_sync_columns()
        self.connection.commit()
