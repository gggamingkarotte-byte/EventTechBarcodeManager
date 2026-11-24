def warehouse_path(db, box_id: int | None):
    if not box_id:
        return ""
    row = db.query(
        """
        SELECT w.name AS warehouse, r.name AS rack, l.name AS level, b.name AS box
        FROM boxes b
        JOIN levels l ON l.id=b.level_id
        JOIN racks r ON r.id=l.rack_id
        JOIN warehouses w ON w.id=r.warehouse_id
        WHERE b.id=?
        """,
        (box_id,),
    )
    if not row:
        return ""
    values = row[0]
    return f"{values['warehouse']} / {values['rack']} / {values['level']} / {values['box']}"
