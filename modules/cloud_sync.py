import json
import urllib.error
from urllib.parse import urlsplit
import urllib.request

from database.database import SYNC_TABLES, Database


SYNC_SPECS = {
    "warehouses": {"fields": ("name", "barcode", "description"), "refs": {}},
    "racks": {
        "fields": ("name", "barcode", "width_cm", "height_cm"),
        "refs": {"warehouse_id": "warehouses"},
    },
    "levels": {
        "fields": ("name", "barcode", "height_cm"),
        "refs": {"rack_id": "racks"},
    },
    "boxes": {"fields": ("name", "barcode", "description"), "refs": {"level_id": "levels"}},
    "articles": {
        "fields": (
            "name", "manufacturer", "model", "category", "barcode", "quantity",
            "description", "technical_data", "status",
        ),
        "refs": {"box_id": "boxes"},
    },
    "barcodes": {
        "fields": ("code", "code_type", "entity_type", "label", "scale"),
        "refs": {"article_id": "articles"},
    },
    "movements": {
        "fields": ("quantity", "action", "timestamp"),
        "refs": {"article_id": "articles", "from_box_id": "boxes", "to_box_id": "boxes"},
    },
    "dmx_modes": {
        "fields": ("name", "channel_count"),
        "refs": {"article_id": "articles"},
    },
    "dmx_channels": {
        "fields": ("channel_number", "function_name", "value_min", "value_max", "description"),
        "refs": {"mode_id": "dmx_modes"},
    },
    "repairs": {
        "fields": (
            "date", "error_description", "damage_type", "measures", "spare_parts",
            "costs", "repair_status", "completion_date", "internal_notes",
        ),
        "refs": {"article_id": "articles"},
    },
    "scans": {
        "fields": ("timestamp", "barcode", "scan_type", "action"),
        "refs": {"article_id": "articles", "box_id": "boxes"},
    },
    "inventories": {
        "fields": ("name", "started_at", "ended_at"),
        "refs": {"warehouse_id": "warehouses"},
    },
    "inventory_items": {
        "fields": ("barcode", "expected_quantity", "scanned_quantity"),
        "refs": {"inventory_id": "inventories", "article_id": "articles"},
    },
}

SYNC_DEFAULTS = {
    "racks": {"width_cm": 120, "height_cm": 200},
    "levels": {"height_cm": 40},
    "barcodes": {"label": None, "scale": 100},
}

if set(SYNC_SPECS) != set(SYNC_TABLES):
    raise RuntimeError("Die lokale Sync-Schemadefinition ist unvollständig.")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _local_payload(db: Database, table: str, row: dict) -> dict:
    spec = SYNC_SPECS[table]
    payload = {field: row.get(field) for field in spec["fields"]}
    for field, parent_table in spec["refs"].items():
        parent_id = row.get(field)
        if parent_id is None:
            payload[field] = None
            continue
        parent = db.query(f"SELECT sync_uuid FROM {parent_table} WHERE id=?", (parent_id,))
        if not parent:
            raise ValueError(f"Synchronisierung abgebrochen: Bezug {parent_table}#{parent_id} fehlt.")
        payload[field] = parent[0]["sync_uuid"]
    return payload


def local_snapshot(db: Database) -> dict[str, dict]:
    states = {}
    for table in SYNC_TABLES:
        for row in db.query(f"SELECT * FROM {table}"):
            record = dict(row)
            entity_uuid = record.get("sync_uuid")
            if not entity_uuid:
                raise ValueError(f"Ein Datensatz in {table} hat keine Synchronisations-ID.")
            states[entity_uuid] = {
                "entity_type": table,
                "state": {"deleted": False, "data": _local_payload(db, table, record)},
            }
    for row in db.query("SELECT entity_uuid,entity_type FROM sync_tombstones"):
        states[row["entity_uuid"]] = {
            "entity_type": row["entity_type"],
            "state": {"deleted": True, "data": None},
        }
    return states


def plan_sync(db: Database, snapshot: dict[str, dict], remote_records: list[dict]) -> dict:
    cached = {
        row["entity_uuid"]: {
            "entity_type": row["entity_type"],
            "state": json.loads(row["baseline_json"]),
            "revision": row["remote_revision"],
        }
        for row in db.query("SELECT * FROM sync_state")
    }
    remote = {record["id"]: record for record in remote_records}
    pushes = []
    conflicts = []

    for entity_uuid in set(snapshot) | set(remote) | set(cached):
        local_item = snapshot.get(entity_uuid)
        base = cached.get(entity_uuid)
        local_state = local_item["state"] if local_item else None
        if local_state is None and base and base["state"]["deleted"]:
            local_state = base["state"]
        remote_record = remote.get(entity_uuid)
        remote_state = (
            {"deleted": bool(remote_record["deleted"]), "data": remote_record.get("data")}
            if remote_record else None
        )
        entity_type = (
            local_item["entity_type"] if local_item else
            remote_record["entity_type"] if remote_record else
            base["entity_type"] if base else None
        )

        if not entity_type:
            continue
        if entity_type not in SYNC_SPECS:
            raise ValueError(f"Unbekannter Cloud-Datensatztyp: {entity_type}")
        if base is None:
            if local_state is not None and remote_state is None:
                if not local_state["deleted"]:
                    pushes.append(_make_push(entity_uuid, entity_type, local_state, None))
            elif local_state is not None and remote_state is not None:
                if local_state != remote_state:
                    conflicts.append(_make_conflict(entity_uuid, entity_type, local_state, remote_record))
            continue

        if remote_record is None:
            if local_state is not None:
                conflicts.append(_make_conflict(entity_uuid, entity_type, local_state, None))
            continue

        local_changed = local_state != base["state"]
        remote_changed = remote_state != base["state"]
        if local_changed and remote_changed and local_state != remote_state:
            conflicts.append(_make_conflict(entity_uuid, entity_type, local_state, remote_record))
        elif local_changed and local_state != remote_state:
            pushes.append(
                _make_push(entity_uuid, entity_type, local_state, remote_record["revision"])
            )

    return {"pushes": pushes, "conflicts": conflicts}


def _make_push(entity_uuid: str, entity_type: str, state: dict, expected_revision):
    return {
        "id": entity_uuid,
        "entity_type": entity_type,
        "data": state["data"],
        "deleted": state["deleted"],
        "expected_revision": expected_revision,
    }


def _make_conflict(entity_uuid: str, entity_type: str, local_state, remote_record):
    remote_state = None
    revision = None
    if remote_record:
        remote_state = {
            "deleted": bool(remote_record["deleted"]),
            "data": remote_record.get("data"),
        }
        revision = remote_record["revision"]
    return {
        "id": entity_uuid,
        "entity_type": entity_type,
        "local": local_state,
        "remote": remote_state,
        "remote_revision": revision,
    }


def _local_id(db: Database, table: str, entity_uuid: str):
    rows = db.query(f"SELECT id FROM {table} WHERE sync_uuid=?", (entity_uuid,))
    return rows[0]["id"] if rows else None


def _remote_to_local_data(db: Database, table: str, data: dict) -> dict:
    spec = SYNC_SPECS[table]
    defaults = SYNC_DEFAULTS.get(table, {})
    local_data = {
        field: data.get(field, defaults.get(field))
        for field in spec["fields"]
    }
    for field, parent_table in spec["refs"].items():
        parent_uuid = data.get(field)
        if parent_uuid is None:
            local_data[field] = None
            continue
        parent_id = _local_id(db, parent_table, parent_uuid)
        if parent_id is None:
            raise ValueError(
                f"Cloud-Datensatz {table} kann nicht übernommen werden: "
                f"übergeordneter {parent_table}-Datensatz fehlt."
            )
        local_data[field] = parent_id
    if table == "barcodes":
        local_data["entity_id"] = (
            local_data.get("article_id") if local_data.get("entity_type") == "article" else None
        )
    return local_data


def _apply_remote_live_record(db: Database, record: dict):
    table = record["entity_type"]
    if table not in SYNC_SPECS:
        raise ValueError(f"Unbekannter Cloud-Datensatztyp: {table}")
    data = _remote_to_local_data(db, table, record["data"])
    entity_uuid = record["id"]
    local_id = _local_id(db, table, entity_uuid)
    if local_id is None:
        columns = list(data) + ["sync_uuid"]
        values = [data[column] for column in data] + [entity_uuid]
        placeholders = ",".join("?" for _ in columns)
        db.connection.execute(
            f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})", values
        )
    else:
        assignments = ",".join(f"{column}=?" for column in data)
        db.connection.execute(
            f"UPDATE {table} SET {assignments} WHERE id=?",
            [data[column] for column in data] + [local_id],
        )


def apply_cloud_snapshot(db: Database, remote_records: list[dict]):
    records_by_type = {table: [] for table in SYNC_TABLES}
    for record in remote_records:
        if record["entity_type"] not in SYNC_SPECS:
            raise ValueError(f"Unbekannter Cloud-Datensatztyp: {record['entity_type']}")
        records_by_type[record["entity_type"]].append(record)

    db.connection.execute("BEGIN")
    try:
        for table in SYNC_TABLES:
            for record in records_by_type[table]:
                if not record["deleted"]:
                    _apply_remote_live_record(db, record)

        for table in reversed(SYNC_TABLES):
            for record in records_by_type[table]:
                if record["deleted"]:
                    db.connection.execute(
                        f"DELETE FROM {table} WHERE sync_uuid=?", (record["id"],)
                    )

        db.connection.execute("DELETE FROM sync_state")
        for record in remote_records:
            state = {
                "deleted": bool(record["deleted"]),
                "data": record.get("data") if not record["deleted"] else None,
            }
            db.connection.execute(
                """INSERT INTO sync_state(entity_uuid,entity_type,baseline_json,remote_revision)
                   VALUES(?,?,?,?)""",
                (
                    record["id"], record["entity_type"], canonical_json(state),
                    record["revision"],
                ),
            )
        db.connection.execute("DELETE FROM sync_tombstones")
        db.connection.commit()
    except Exception:
        db.connection.rollback()
        raise


class SupabaseSyncClient:
    def __init__(self, project_url: str, public_key: str, shared_key: str, timeout: int = 20):
        self.project_url = project_url.rstrip("/")
        self.public_key = public_key.strip()
        self.shared_key = shared_key
        self.timeout = timeout
        parsed_url = urlsplit(self.project_url)
        if parsed_url.scheme != "https" or not parsed_url.hostname or parsed_url.username or parsed_url.password:
            raise ValueError("Die Supabase-Projektadresse muss mit https:// beginnen.")
        if not self.public_key or not self.shared_key:
            raise ValueError("Supabase-Public-Key und Organisationsschlüssel müssen eingetragen sein.")

    def _request(self, body: dict) -> dict:
        url = f"{self.project_url}/functions/v1/eventtech-sync"
        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "apikey": self.public_key,
                "x-eventtech-shared-key": self.shared_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Cloud-Synchronisierung fehlgeschlagen (HTTP {exc.code}): {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cloud-Synchronisierung fehlgeschlagen: {exc}") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Die Cloud hat eine ungültige Antwort zurückgegeben.")
        return result

    def pull(self) -> list[dict]:
        response = self._request({"action": "pull"})
        records = response.get("records")
        if not isinstance(records, list):
            raise RuntimeError("Die Cloud-Antwort enthält keine Datensatzliste.")
        return records

    def push(self, changes: list[dict]) -> list[dict]:
        if not changes:
            return []
        conflicts = []
        for offset in range(0, len(changes), 200):
            response = self._request({"action": "push", "changes": changes[offset:offset + 200]})
            chunk_conflicts = response.get("conflicts", [])
            if not isinstance(chunk_conflicts, list):
                raise RuntimeError("Die Cloud-Antwort zu Konflikten ist ungültig.")
            conflicts.extend(chunk_conflicts)
        return conflicts
