def create_mode(db, article_id: int, name: str, channel_count: int) -> int:
    cursor = db.execute(
        "INSERT INTO dmx_modes(article_id,name,channel_count) VALUES(?,?,?)",
        (article_id, name, channel_count),
    )
    return cursor.lastrowid


def set_channel(db, mode_id: int, number: int, function_name: str,
                value_min: int = 0, value_max: int = 255, description: str = ""):
    existing = db.query(
        "SELECT id FROM dmx_channels WHERE mode_id=? AND channel_number=?",
        (mode_id, number),
    )
    if existing:
        db.execute(
            """UPDATE dmx_channels SET function_name=?,value_min=?,value_max=?,description=?
               WHERE id=?""",
            (function_name, value_min, value_max, description, existing[0]["id"]),
        )
    else:
        db.execute(
            """INSERT INTO dmx_channels(mode_id,channel_number,function_name,value_min,value_max,description)
               VALUES(?,?,?,?,?,?)""",
            (mode_id, number, function_name, value_min, value_max, description),
        )
