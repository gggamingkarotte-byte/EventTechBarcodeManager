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


def load_article_modes(db, article_id: int) -> list[dict]:
    modes = []
    for row in db.query(
        "SELECT id,name,channel_count FROM dmx_modes WHERE article_id=? ORDER BY id",
        (article_id,),
    ):
        mode = dict(row)
        mode["channels"] = [
            dict(channel) for channel in db.query(
                """SELECT id,channel_number,function_name,value_min,value_max,description
                   FROM dmx_channels WHERE mode_id=? ORDER BY channel_number""",
                (mode["id"],),
            )
        ]
        modes.append(mode)
    return modes


def validate_article_modes(modes: list[dict]) -> list[dict]:
    if not isinstance(modes, list):
        raise ValueError("Die DMX-Modi haben ein ungültiges Format.")
    validated = []
    seen_mode_ids = set()
    for mode in modes:
        if not isinstance(mode, dict):
            raise ValueError("Ein DMX-Modus ist ungültig.")
        name = str(mode.get("name", "")).strip()
        if not name:
            raise ValueError("Jeder DMX-Modus benötigt einen Namen.")
        try:
            channel_count = int(mode.get("channel_count", 0))
        except (TypeError, ValueError):
            raise ValueError(f"Die Kanalanzahl des Modus „{name}“ ist ungültig.") from None
        if not 1 <= channel_count <= 512:
            raise ValueError(f"Der Modus „{name}“ muss 1 bis 512 Kanäle haben.")
        mode_id = mode.get("id")
        if mode_id is not None:
            try:
                mode_id = int(mode_id)
            except (TypeError, ValueError):
                raise ValueError(f"Die ID des Modus „{name}“ ist ungültig.") from None
            if mode_id in seen_mode_ids:
                raise ValueError(f"Der Modus „{name}“ ist doppelt vorhanden.")
            seen_mode_ids.add(mode_id)
        channels = mode.get("channels", [])
        if not isinstance(channels, list):
            raise ValueError(f"Die Kanäle des Modus „{name}“ sind ungültig.")
        validated_channels = []
        seen_numbers = set()
        seen_channel_ids = set()
        for channel in channels:
            if not isinstance(channel, dict):
                raise ValueError(f"Ein Kanal im Modus „{name}“ ist ungültig.")
            function_name = str(channel.get("function_name", "")).strip()
            if not function_name:
                raise ValueError(f"Jeder Kanal im Modus „{name}“ benötigt eine Funktion.")
            try:
                number = int(channel.get("channel_number", 0))
                value_min = int(channel.get("value_min", 0))
                value_max = int(channel.get("value_max", 255))
            except (TypeError, ValueError):
                raise ValueError(f"Die Werte für Kanal „{function_name}“ sind ungültig.") from None
            if not 1 <= number <= channel_count:
                raise ValueError(
                    f"Kanal {number} liegt außerhalb des Modus „{name}“ mit {channel_count} Kanälen."
                )
            if number in seen_numbers:
                raise ValueError(f"Kanal {number} ist im Modus „{name}“ doppelt vorhanden.")
            seen_numbers.add(number)
            if not 0 <= value_min <= value_max <= 255:
                raise ValueError(f"Der Wertebereich von Kanal {number} muss zwischen 0 und 255 liegen.")
            channel_id = channel.get("id")
            if channel_id is not None:
                try:
                    channel_id = int(channel_id)
                except (TypeError, ValueError):
                    raise ValueError(f"Die ID von Kanal {number} ist ungültig.") from None
                if channel_id in seen_channel_ids:
                    raise ValueError(f"Kanal {number} ist im Modus „{name}“ doppelt vorhanden.")
                seen_channel_ids.add(channel_id)
            validated_channels.append({
                "id": channel_id,
                "channel_number": number,
                "function_name": function_name,
                "value_min": value_min,
                "value_max": value_max,
                "description": str(channel.get("description") or "").strip(),
            })
        validated.append({
            "id": mode_id,
            "name": name,
            "channel_count": channel_count,
            "channels": validated_channels,
        })
    return validated


def save_article_modes(db, article_id: int, modes: list[dict]):
    modes = validate_article_modes(modes)
    existing_modes = {
        row["id"]: dict(row) for row in db.query(
            "SELECT id,name,channel_count FROM dmx_modes WHERE article_id=?",
            (article_id,),
        )
    }
    existing_channels_by_mode = {
        mode_id: {
            row["id"]: dict(row) for row in db.query(
                """SELECT id,channel_number,function_name,value_min,value_max,description
                   FROM dmx_channels WHERE mode_id=?""",
                (mode_id,),
            )
        }
        for mode_id in existing_modes
    }
    for mode in modes:
        mode_id = mode["id"]
        if mode_id is None:
            if any(channel["id"] is not None for channel in mode["channels"]):
                raise ValueError("Ein neuer DMX-Modus kann keine bestehenden Kanäle enthalten.")
            continue
        if mode_id not in existing_modes:
            raise ValueError("Ein DMX-Modus gehört nicht zu diesem Artikel.")
        if any(channel["id"] is not None and channel["id"] not in existing_channels_by_mode[mode_id]
               for channel in mode["channels"]):
            raise ValueError("Ein DMX-Kanal gehört nicht zu diesem Modus.")
    retained_mode_ids = set()

    for mode in modes:
        mode_id = mode["id"]
        if mode_id is None:
            mode_id = create_mode(db, article_id, mode["name"], mode["channel_count"])
            for channel in mode["channels"]:
                db.execute(
                    """INSERT INTO dmx_channels
                       (mode_id,channel_number,function_name,value_min,value_max,description)
                       VALUES(?,?,?,?,?,?)""",
                    (
                        mode_id, channel["channel_number"], channel["function_name"],
                        channel["value_min"], channel["value_max"], channel["description"],
                    ),
                )
            continue
        if mode_id not in existing_modes:
            raise ValueError("Ein DMX-Modus gehört nicht zu diesem Artikel.")
        retained_mode_ids.add(mode_id)
        old_mode = existing_modes[mode_id]
        if (old_mode["name"], old_mode["channel_count"]) != (mode["name"], mode["channel_count"]):
            db.execute(
                "UPDATE dmx_modes SET name=?,channel_count=? WHERE id=? AND article_id=?",
                (mode["name"], mode["channel_count"], mode_id, article_id),
            )

        existing_channels = existing_channels_by_mode[mode_id]
        retained_channel_ids = set()
        for channel in mode["channels"]:
            channel_id = channel["id"]
            values = (
                channel["channel_number"], channel["function_name"],
                channel["value_min"], channel["value_max"], channel["description"],
            )
            if channel_id is None:
                db.execute(
                    """INSERT INTO dmx_channels
                       (mode_id,channel_number,function_name,value_min,value_max,description)
                       VALUES(?,?,?,?,?,?)""",
                    (mode_id, *values),
                )
                continue
            retained_channel_ids.add(channel_id)
            old_channel = existing_channels[channel_id]
            old_values = tuple(old_channel[key] for key in (
                "channel_number", "function_name", "value_min", "value_max", "description",
            ))
            if old_values != values:
                db.execute(
                    """UPDATE dmx_channels
                       SET channel_number=?,function_name=?,value_min=?,value_max=?,description=?
                       WHERE id=? AND mode_id=?""",
                    (*values, channel_id, mode_id),
                )
        for channel_id in existing_channels.keys() - retained_channel_ids:
            db.execute("DELETE FROM dmx_channels WHERE id=? AND mode_id=?", (channel_id, mode_id))

    for mode_id in existing_modes.keys() - retained_mode_ids:
        db.execute("DELETE FROM dmx_modes WHERE id=? AND article_id=?", (mode_id, article_id))
