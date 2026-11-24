def calculate_inventory(db, inventory_id: int) -> dict:
    items = db.query(
        "SELECT expected_quantity,scanned_quantity,barcode FROM inventory_items WHERE inventory_id=?",
        (inventory_id,),
    )
    missing = [dict(row) for row in items if row["scanned_quantity"] < row["expected_quantity"]]
    additional = [dict(row) for row in items if row["expected_quantity"] == 0 and row["scanned_quantity"] > 0]
    mismatches = [
        dict(row) for row in items
        if row["expected_quantity"] > 0 and row["scanned_quantity"] != row["expected_quantity"]
    ]
    return {"missing": missing, "additional": additional, "mismatches": mismatches}
