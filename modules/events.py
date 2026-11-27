from datetime import date

from database.database import Database


EVENT_STATUSES = (
    "Geplant",
    "Vorbereitung",
    "Bereit zur Ausgabe",
    "Läuft",
    "Rückgabe ausstehend",
    "Abgeschlossen",
    "Abgesagt",
)

INACTIVE_EVENT_STATUSES = {"Abgeschlossen", "Abgesagt"}


class ReservationConflictError(ValueError):
    def __init__(self, conflicts: list[dict]):
        self.conflicts = conflicts
        message = "; ".join(
            f"{item['article_name']}: benötigt {item['requested']}, "
            f"verfügbar {item['available']}"
            for item in conflicts
        )
        super().__init__(f"Reservierungskonflikt: {message}")


def validate_event(data: dict, items: list[dict]) -> tuple[dict, list[dict]]:
    event = {
        "name": str(data.get("name") or "").strip(),
        "description": str(data.get("description") or "").strip(),
        "location": str(data.get("location") or "").strip(),
        "start_date": str(data.get("start_date") or "").strip(),
        "end_date": str(data.get("end_date") or "").strip(),
        "contact": str(data.get("contact") or "").strip(),
        "organizer": str(data.get("organizer") or "").strip(),
        "status": str(data.get("status") or "Geplant").strip(),
        "notes": str(data.get("notes") or "").strip(),
    }
    if not event["name"]:
        raise ValueError("Bitte gib einen Eventnamen ein.")
    if event["status"] not in EVENT_STATUSES:
        raise ValueError("Der ausgewählte Eventstatus ist ungültig.")
    try:
        start = date.fromisoformat(event["start_date"])
        end = date.fromisoformat(event["end_date"])
    except ValueError as exc:
        raise ValueError("Start- und Enddatum müssen gültige Datumsangaben sein.") from exc
    if end < start:
        raise ValueError("Das Enddatum darf nicht vor dem Startdatum liegen.")

    normalized_items = []
    seen_articles = set()
    for item in items:
        try:
            article_id = int(item["article_id"])
            required = int(item.get("required_quantity", 1))
            issued = int(item.get("issued_quantity", 0))
            returned = int(item.get("returned_quantity", 0))
            missing = int(item.get("missing_quantity", 0))
            damaged = int(item.get("damaged_quantity", 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Eine Event-Artikelzuordnung enthält ungültige Mengen.") from exc
        if article_id in seen_articles:
            raise ValueError("Ein Artikel darf einem Event nur einmal zugeordnet werden.")
        if required < 1 or min(issued, returned, missing, damaged) < 0:
            raise ValueError("Mengen müssen gültig und dürfen nicht negativ sein.")
        if issued > required or returned + missing > issued or damaged > returned:
            raise ValueError(
                "Ausgabe, Rückgabe, Fehlmenge und Schaden passen nicht zur benötigten Menge."
            )
        seen_articles.add(article_id)
        normalized_items.append({
            "article_id": article_id,
            "required_quantity": required,
            "issued_quantity": issued,
            "returned_quantity": returned,
            "missing_quantity": missing,
            "damaged_quantity": damaged,
            "notes": str(item.get("notes") or "").strip(),
        })
    return event, normalized_items


def reservation_conflicts(
    db: Database,
    event_data: dict,
    items: list[dict],
    event_id: int | None = None,
) -> list[dict]:
    if event_data.get("status") in INACTIVE_EVENT_STATUSES:
        return []
    conflicts = []
    for item in items:
        article_id = int(item["article_id"])
        requested = int(item["required_quantity"])
        article_rows = db.query(
            "SELECT name,quantity FROM articles WHERE id=?", (article_id,)
        )
        if not article_rows:
            raise ValueError(f"Der zugeordnete Artikel #{article_id} existiert nicht mehr.")
        article = article_rows[0]
        reserved_rows = db.query(
            """SELECT COALESCE(SUM(ei.required_quantity),0) AS reserved
               FROM event_items ei
               JOIN events e ON e.id=ei.event_id
               WHERE ei.article_id=?
                 AND e.status NOT IN ('Abgeschlossen','Abgesagt')
                 AND e.start_date<=? AND e.end_date>=?
                 AND (? IS NULL OR e.id<>?)""",
            (
                article_id, event_data["end_date"], event_data["start_date"],
                event_id, event_id,
            ),
        )
        available = max(0, int(article["quantity"]) - int(reserved_rows[0]["reserved"]))
        if requested > available:
            conflicts.append({
                "article_id": article_id,
                "article_name": article["name"],
                "requested": requested,
                "available": available,
            })
    return conflicts


def save_event(
    db: Database,
    data: dict,
    items: list[dict],
    event_id: int | None = None,
) -> int:
    event, normalized_items = validate_event(data, items)
    if event["status"] in INACTIVE_EVENT_STATUSES and any(
        item["issued_quantity"] > item["returned_quantity"] + item["missing_quantity"]
        for item in normalized_items
    ):
        raise ValueError("Ein Event mit noch offenen Rückgaben kann nicht abgeschlossen oder abgesagt werden.")
    connection = db.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        conflicts = reservation_conflicts(db, event, normalized_items, event_id)
        if conflicts:
            raise ReservationConflictError(conflicts)

        if event_id is None:
            cursor = connection.execute(
                """INSERT INTO events(
                       name,description,location,start_date,end_date,contact,organizer,status,notes
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                tuple(event.values()),
            )
            saved_event_id = cursor.lastrowid
        else:
            exists = connection.execute(
                "SELECT id FROM events WHERE id=?", (event_id,)
            ).fetchone()
            if not exists:
                raise ValueError("Der Event wurde zwischenzeitlich gelöscht.")
            connection.execute(
                """UPDATE events SET name=?,description=?,location=?,start_date=?,end_date=?,
                       contact=?,organizer=?,status=?,notes=? WHERE id=?""",
                (*event.values(), event_id),
            )
            saved_event_id = event_id

        existing_items = {
            row["article_id"]: row
            for row in connection.execute(
                "SELECT id,article_id FROM event_items WHERE event_id=?",
                (saved_event_id,),
            ).fetchall()
        }
        new_article_ids = {item["article_id"] for item in normalized_items}
        for article_id, existing in existing_items.items():
            if article_id not in new_article_ids:
                connection.execute("DELETE FROM event_items WHERE id=?", (existing["id"],))
        for item in normalized_items:
            existing = existing_items.get(item["article_id"])
            values = (
                item["required_quantity"], item["issued_quantity"],
                item["returned_quantity"], item["missing_quantity"],
                item["damaged_quantity"], item["notes"],
            )
            if existing:
                connection.execute(
                    """UPDATE event_items SET required_quantity=?,issued_quantity=?,
                           returned_quantity=?,missing_quantity=?,damaged_quantity=?,notes=?
                       WHERE id=?""",
                    (*values, existing["id"]),
                )
            else:
                connection.execute(
                    """INSERT INTO event_items(
                           event_id,article_id,required_quantity,issued_quantity,
                           returned_quantity,missing_quantity,damaged_quantity,notes
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (saved_event_id, item["article_id"], *values),
                )
        connection.commit()
        return saved_event_id
    except Exception:
        connection.rollback()
        raise


def delete_event(db: Database, event_id: int) -> None:
    outstanding = db.query(
        "SELECT COUNT(*) AS count FROM event_items WHERE event_id=? AND issued_quantity>0",
        (event_id,),
    )[0]["count"]
    if outstanding:
        raise ValueError("Ein Event mit dokumentierter Ausgabe kann nicht gelöscht werden.")
    cursor = db.execute("DELETE FROM events WHERE id=?", (event_id,))
    if cursor.rowcount == 0:
        raise ValueError("Der Event wurde nicht gefunden.")
