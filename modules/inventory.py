import json


ARTICLE_STATUSES = ("Einsatzbereit", "Defekt", "In Reparatur", "Wartung erforderlich", "Ausgemustert")


def create_article(db, data: dict) -> int:
    barcode = (data.get("barcode") or "").strip()
    if barcode:
        if db.query("SELECT 1 FROM articles WHERE barcode=?", (barcode,)) or db.query(
            "SELECT 1 FROM barcodes WHERE code=?", (barcode,)
        ):
            raise ValueError("Dieser Barcode ist bereits einem Artikel oder Lagerplatz zugeordnet.")
    cursor = db.execute(
        """
        INSERT INTO articles(name,manufacturer,model,category,barcode,quantity,box_id,
                             description,technical_data,status)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            data["name"], data.get("manufacturer", ""), data.get("model", ""),
            data.get("category", ""), barcode or None, int(data.get("quantity", 1)),
            data.get("box_id"), data.get("description", ""), data.get("technical_data", ""),
            data.get("status", "Einsatzbereit"),
        ),
    )
    article_id = cursor.lastrowid
    if barcode:
        db.execute(
            "INSERT INTO barcodes(code,code_type,article_id,entity_type,entity_id) VALUES(?,?,?,?,?)",
            (barcode, data.get("barcode_type", "Code 128"), article_id, "article", article_id),
        )
    return article_id


def update_article(db, article_id: int, data: dict):
    barcode = (data.get("barcode") or "").strip()
    if barcode:
        row = db.query("SELECT id FROM articles WHERE barcode=? AND id<>?", (barcode, article_id))
        row2 = db.query("SELECT id FROM barcodes WHERE code=? AND article_id<>?", (barcode, article_id))
        if row or row2:
            raise ValueError("Dieser Barcode ist bereits zugewiesen.")
    db.execute(
        """
        UPDATE articles SET name=?,manufacturer=?,model=?,category=?,barcode=?,quantity=?,
        box_id=?,description=?,technical_data=?,status=? WHERE id=?
        """,
        (
            data["name"], data.get("manufacturer", ""), data.get("model", ""),
            data.get("category", ""), barcode or None, int(data.get("quantity", 1)),
            data.get("box_id"), data.get("description", ""), data.get("technical_data", ""),
            data.get("status", "Einsatzbereit"), article_id,
        ),
    )
    db.execute("DELETE FROM barcodes WHERE article_id=? AND entity_type='article'", (article_id,))
    if barcode:
        db.execute(
            "INSERT INTO barcodes(code,code_type,article_id,entity_type,entity_id) VALUES(?,?,?,?,?)",
            (barcode, data.get("barcode_type", "Code 128"), article_id, "article", article_id),
        )


def delete_article(db, article_id: int):
    db.execute("DELETE FROM articles WHERE id=?", (article_id,))


def find_by_barcode(db, code: str):
    rows = db.query("SELECT * FROM articles WHERE barcode=?", (code.strip(),))
    return dict(rows[0]) if rows else None


def duplicate_article(db, article_id: int) -> int:
    row = db.query("SELECT * FROM articles WHERE id=?", (article_id,))
    if not row:
        raise ValueError("Artikel nicht gefunden.")
    data = dict(row[0])
    data["name"] = data["name"] + " (Kopie)"
    data["barcode"] = ""
    data["id"] = None
    return create_article(db, data)
