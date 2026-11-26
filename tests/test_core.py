import tempfile
import unittest
from pathlib import Path
import os
import sys
import base64
import json
import sqlite3
import re
import ssl
import urllib.error
import urllib.request
from io import BytesIO
from unittest.mock import MagicMock, patch

from database.database import Database
from modules.inventory import create_article, duplicate_article, update_article
from modules.barcode import validate_ean13, generate_unique_code, render_barcode_png, create_label_pdf
from modules.scanner import ScannerReader
from modules.dmx import create_mode, set_channel
from modules.ipad_scanner import IpadScannerServer
import qrcode
import zxingcpp
from PIL import Image
from modules.cloud_sync import apply_cloud_snapshot, canonical_json, local_snapshot, plan_sync
from modules.secret_store import protect_secret, unprotect_secret
from modules.supabase_config_file import decrypt_supabase_config, encrypt_supabase_config
from modules.usage_guides import (
    GUIDE_END, GUIDE_START, has_auto_usage_guide, update_auto_usage_guide,
)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tempdir.name) / "test.db")

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_duplicate_barcode_is_rejected(self):
        create_article(self.db, {"name": "Scheinwerfer", "barcode": "1234567890128"})
        with self.assertRaises(ValueError):
            create_article(self.db, {"name": "Zweiter", "barcode": "1234567890128"})

    def test_existing_database_gets_default_rack_and_shelf_dimensions(self):
        legacy_path = Path(self.tempdir.name) / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript(
                """
                CREATE TABLE warehouses (id INTEGER PRIMARY KEY, name TEXT NOT NULL, barcode TEXT, description TEXT);
                CREATE TABLE racks (id INTEGER PRIMARY KEY, warehouse_id INTEGER NOT NULL, name TEXT NOT NULL, barcode TEXT);
                CREATE TABLE levels (id INTEGER PRIMARY KEY, rack_id INTEGER NOT NULL, name TEXT NOT NULL, barcode TEXT);
                CREATE TABLE barcodes (
                    id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, code_type TEXT NOT NULL,
                    article_id INTEGER, entity_type TEXT, entity_id INTEGER
                );
                INSERT INTO warehouses(id,name) VALUES(1,'Hauptlager');
                INSERT INTO racks(id,warehouse_id,name) VALUES(1,1,'Regal 1');
                INSERT INTO levels(id,rack_id,name) VALUES(1,1,'Boden 1');
                INSERT INTO barcodes(id,code,code_type,entity_type) VALUES(1,'OLD-CODE','Code 128','custom');
                """
            )
        finally:
            connection.close()
        migrated = Database(legacy_path)
        try:
            rack = migrated.query("SELECT width_cm,height_cm FROM racks WHERE id=1")[0]
            level = migrated.query("SELECT height_cm FROM levels WHERE id=1")[0]
            old_barcode = migrated.query("SELECT label,scale FROM barcodes WHERE id=1")[0]
            self.assertEqual((rack["width_cm"], rack["height_cm"]), (120, 200))
            self.assertEqual(level["height_cm"], 40)
            self.assertEqual((old_barcode["label"], old_barcode["scale"]), (None, 100))
        finally:
            migrated.close()

    def test_rack_and_shelf_dimensions_are_included_in_cloud_snapshot(self):
        warehouse_id = self.db.execute(
            "INSERT INTO warehouses(name) VALUES(?)", ("Hauptlager",)
        ).lastrowid
        rack_id = self.db.execute(
            """INSERT INTO racks(warehouse_id,name,width_cm,height_cm)
               VALUES(?,?,?,?)""",
            (warehouse_id, "Regal 1", 180, 240),
        ).lastrowid
        self.db.execute(
            "INSERT INTO levels(rack_id,name,height_cm) VALUES(?,?,?)",
            (rack_id, "Boden 1", 60),
        )
        snapshot = local_snapshot(self.db)
        rack_state = next(item["state"]["data"] for item in snapshot.values()
                          if item["entity_type"] == "racks")
        level_state = next(item["state"]["data"] for item in snapshot.values()
                           if item["entity_type"] == "levels")
        self.assertEqual((rack_state["width_cm"], rack_state["height_cm"]), (180, 240))
        self.assertEqual(level_state["height_cm"], 60)

    def test_custom_barcode_label_and_scale_are_included_in_cloud_snapshot(self):
        self.db.execute(
            """INSERT INTO barcodes(code,code_type,entity_type,label,scale)
               VALUES(?,?,?,?,?)""",
            ("ET-CUSTOM-1", "Code 128", "custom", "Backuplampe", 125),
        )
        snapshot = local_snapshot(self.db)
        barcode_state = next(
            item["state"]["data"] for item in snapshot.values()
            if item["entity_type"] == "barcodes"
        )
        self.assertEqual(barcode_state["label"], "Backuplampe")
        self.assertEqual(barcode_state["scale"], 125)

    def test_duplicate_article_can_be_created_without_barcode(self):
        article_id = create_article(self.db, {"name": "Moving Head", "barcode": "MH001"})
        copy_id = duplicate_article(self.db, article_id)
        self.assertNotEqual(article_id, copy_id)
        copy = self.db.query("SELECT barcode FROM articles WHERE id=?", (copy_id,))
        self.assertFalse(copy[0]["barcode"])

    def test_ean13_validation(self):
        self.assertTrue(validate_ean13("4006381333931"))
        self.assertFalse(validate_ean13("4006381333932"))
        generated = generate_unique_code(self.db, "EAN-13")
        self.assertEqual(len(generated), 13)
        self.assertTrue(validate_ean13(generated))

    def test_generated_barcodes_are_scannable_and_present_in_label_pdf(self):
        labels = []
        for code_type in ("Code 128", "EAN-13", "QR"):
            code = generate_unique_code(self.db, code_type)
            image_bytes = render_barcode_png(code, code_type)
            with Image.open(BytesIO(image_bytes)) as image:
                decoded = zxingcpp.read_barcodes(image)
            self.assertTrue(decoded, f"{code_type} ließ sich nicht dekodieren")
            self.assertEqual(decoded[0].text, code)
            labels.append({"name": "Testgerät", "barcode": code, "code_type": code_type})

        destination = Path(self.tempdir.name) / "labels.pdf"
        create_label_pdf(destination, labels)
        self.assertIn(b"/Subtype /Image", destination.read_bytes())
        self.assertTrue(destination.is_file())

    def test_barcode_scaling_changes_png_dimensions_and_pdf_paginates(self):
        code = "ET-SCALE-123"
        with Image.open(BytesIO(render_barcode_png(code, "Code 128", 50))) as small:
            small_size = small.size
        with Image.open(BytesIO(render_barcode_png(code, "Code 128", 125))) as large:
            large_size = large.size
        self.assertGreater(large_size[0], small_size[0])
        self.assertGreater(large_size[1], small_size[1])
        items = [
            {
                "name": f"Etikett {index + 1}",
                "barcode": f"ET-DOC-{index:03}",
                "code_type": "QR" if index % 2 else "Code 128",
                "scale": 75 if index % 2 else 125,
            }
            for index in range(11)
        ]
        destination = Path(self.tempdir.name) / "multi-page-labels.pdf"
        create_label_pdf(destination, items)
        page_count = len(re.findall(rb"/Type\s*/Page\b", destination.read_bytes()))
        self.assertEqual(page_count, 2)

    def test_multiple_barcode_selection_can_be_added_and_scaled_together(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QItemSelectionModel
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        from main import MainWindow

        self.db.execute(
            """INSERT INTO barcodes(code,code_type,entity_type,label,scale)
               VALUES('ET-MULTI-1','Code 128','custom','Gerät 1',100),
                     ('ET-MULTI-2','Code 128','custom','Gerät 2',100)"""
        )
        with patch("main.Database", return_value=self.db):
            window = MainWindow()
        try:
            window.refresh_barcode_library()
            library_selection = window.barcode_library_table.selectionModel()
            for row in range(2):
                library_selection.select(
                    window.barcode_library_table.model().index(row, 0),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows,
                )
            window.add_selected_barcode_to_pdf()
            self.assertEqual(
                [item["barcode"] for item in window.barcode_document_items],
                ["ET-MULTI-2", "ET-MULTI-1"],
            )

            document_selection = window.barcode_document_table.selectionModel()
            for row in range(2):
                document_selection.select(
                    window.barcode_document_table.model().index(row, 0),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows,
                )
            window.barcode_document_scale.setValue(125)
            window.apply_document_scale_to_selection()
            self.assertEqual(
                [item["scale"] for item in window.barcode_document_items],
                [125, 125],
            )
        finally:
            window.close()
            app.processEvents()

    def test_pdf_label_position_uses_preview_drag_offsets(self):
        destination = Path(self.tempdir.name) / "positioned.pdf"
        positions = []
        for offset_x, offset_y in ((0, 0), (20, 30)):
            mock_pdf = MagicMock()
            with patch("modules.barcode.canvas.Canvas", return_value=mock_pdf):
                create_label_pdf(
                    destination,
                    [{
                        "name": "Cameo", "barcode": "ET-POSITION",
                        "code_type": "Code 128", "offset_x": offset_x, "offset_y": offset_y,
                    }],
                )
            image_call = mock_pdf.drawImage.call_args.args
            positions.append((image_call[1], image_call[2]))
        self.assertAlmostEqual(positions[1][0] - positions[0][0], 20)
        self.assertAlmostEqual(positions[1][1] - positions[0][1], -30)

    def test_pdf_label_name_and_value_are_centered_and_scale_with_barcode(self):
        destination = Path(self.tempdir.name) / "scaled-text.pdf"
        font_sizes = []
        text_positions = []
        for scale in (100, 125):
            mock_pdf = MagicMock()
            with patch("modules.barcode.canvas.Canvas", return_value=mock_pdf):
                create_label_pdf(
                    destination,
                    [{
                        "name": "Cameo", "barcode": "ET-CAMEO-1",
                        "code_type": "Code 128", "scale": scale,
                    }],
                )
            font_sizes.append([call.args[1] for call in mock_pdf.setFont.call_args_list])
            text_positions.append([call.args[0] for call in mock_pdf.drawCentredString.call_args_list])
        self.assertEqual(font_sizes[0], [9.0, 8.0])
        self.assertEqual(font_sizes[1], [11.25, 10.0])
        self.assertEqual(text_positions[0][0], text_positions[0][1])
        self.assertEqual(text_positions[1][0], text_positions[1][1])

    def test_scanner_prefix_and_suffix(self):
        reader = ScannerReader(prefix="PX", suffix="\r")
        self.assertEqual(reader.process("PX12345\r"), "12345")

    def test_auto_usage_guide_appends_category_tips_and_updates_without_duplicates(self):
        technical_data = update_auto_usage_guide(
            "Leistung: 300 W",
            enabled=True,
            name="Moving Head",
            manufacturer="Cameo",
            model="HydraBeam 400",
            category="Scheinwerfer",
        )
        self.assertIn("Leistung: 300 W", technical_data)
        self.assertIn("Allgemeine Bedienhinweise – Lichttechnik", technical_data)
        self.assertTrue(has_auto_usage_guide(technical_data))
        self.assertEqual(technical_data.count(GUIDE_START), 1)

        updated = update_auto_usage_guide(
            technical_data,
            enabled=True,
            name="Moving Head",
            manufacturer="Cameo",
            model="HydraBeam 500",
            category="Scheinwerfer",
        )
        self.assertEqual(updated.count(GUIDE_START), 1)
        self.assertIn("HydraBeam+500", updated)
        self.assertNotIn("HydraBeam+400", updated)
        self.assertTrue(updated.rstrip().endswith(GUIDE_END))

    def test_disabling_auto_usage_guide_removes_only_generated_section(self):
        technical_data = update_auto_usage_guide(
            "Eigene technische Daten",
            enabled=True,
            name="Lautsprecher",
            category="Audio",
        )
        without_guide = update_auto_usage_guide(technical_data, enabled=False)
        self.assertEqual(without_guide, "Eigene technische Daten")
        self.assertFalse(has_auto_usage_guide(without_guide))

    def test_article_form_saves_and_updates_auto_usage_guide(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from main import ArticleDialog

        app = QApplication.instance() or QApplication([])
        dialog = ArticleDialog(self.db)
        dialog.name.setText("Moving Head")
        dialog.manufacturer.setText("Cameo")
        dialog.model.setText("HydraBeam 400")
        dialog.category.setText("Scheinwerfer")
        dialog.technical.setPlainText("Leistung: 300 W")
        self.assertFalse(dialog.auto_usage_guide.isChecked())
        dialog.auto_usage_guide.setChecked(True)
        article_id = create_article(self.db, dialog.data())

        article = dict(self.db.query("SELECT * FROM articles WHERE id=?", (article_id,))[0])
        edit_dialog = ArticleDialog(self.db, article)
        self.assertTrue(edit_dialog.auto_usage_guide.isChecked())
        edit_dialog.model.setText("HydraBeam 500")
        update_article(self.db, article_id, edit_dialog.data())

        updated = self.db.query("SELECT technical_data FROM articles WHERE id=?", (article_id,))[0]
        self.assertEqual(updated["technical_data"].count(GUIDE_START), 1)
        self.assertIn("HydraBeam+500", updated["technical_data"])
        edit_dialog = ArticleDialog(
            self.db,
            dict(self.db.query("SELECT * FROM articles WHERE id=?", (article_id,))[0]),
        )
        edit_dialog.auto_usage_guide.setChecked(False)
        update_article(self.db, article_id, edit_dialog.data())
        final_data = self.db.query("SELECT technical_data FROM articles WHERE id=?", (article_id,))[0]
        self.assertEqual(final_data["technical_data"], "Leistung: 300 W")
        self.assertFalse(edit_dialog.auto_usage_guide.isChecked())

    def test_article_form_creates_and_edits_dmx_modes_and_channels(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from main import ArticleDialog, DmxModesDialog

        app = QApplication.instance() or QApplication([])
        article_dialog = ArticleDialog(self.db)
        article_dialog.name.setText("Moving Head")
        dmx_dialog = DmxModesDialog([], article_dialog)
        with patch("main.QInputDialog.getText", return_value=("Basic", True)):
            with patch("main.QInputDialog.getInt", return_value=(3, True)):
                dmx_dialog._add_mode()
        dmx_dialog._add_channel()
        dmx_dialog.channel_table.item(0, 1).setText("Pan")
        dmx_dialog._accept_changes()
        article_dialog.dmx_modes = dmx_dialog.modes

        article_id = create_article(self.db, article_dialog.data())
        mode = dict(self.db.query(
            "SELECT id,name,channel_count FROM dmx_modes WHERE article_id=?",
            (article_id,),
        )[0])
        channel = dict(self.db.query(
            "SELECT id,channel_number,function_name FROM dmx_channels WHERE mode_id=?",
            (mode["id"],),
        )[0])
        self.assertEqual((mode["name"], mode["channel_count"]), ("Basic", 3))
        self.assertEqual((channel["channel_number"], channel["function_name"]), (1, "Pan"))

        edit_dialog = ArticleDialog(
            self.db,
            dict(self.db.query("SELECT * FROM articles WHERE id=?", (article_id,))[0]),
        )
        self.assertEqual(edit_dialog.dmx_modes[0]["id"], mode["id"])
        edit_dialog.dmx_modes[0]["name"] = "Extended"
        edit_dialog.dmx_modes[0]["channels"][0]["function_name"] = "Tilt"
        update_article(self.db, article_id, edit_dialog.data())
        updated_mode = self.db.query(
            "SELECT id,name FROM dmx_modes WHERE article_id=?",
            (article_id,),
        )[0]
        updated_channel = self.db.query(
            "SELECT id,function_name FROM dmx_channels WHERE mode_id=?",
            (mode["id"],),
        )[0]
        self.assertEqual((updated_mode["id"], updated_mode["name"]), (mode["id"], "Extended"))
        self.assertEqual((updated_channel["id"], updated_channel["function_name"]), (channel["id"], "Tilt"))

    def test_dmx_mode_and_channel(self):
        article_id = create_article(self.db, {"name": "Moving Head"})
        mode_id = create_mode(self.db, article_id, "Basic", 9)
        set_channel(self.db, mode_id, 1, "Pan")
        channels = self.db.query("SELECT function_name FROM dmx_channels WHERE mode_id=?", (mode_id,))
        self.assertEqual(channels[0]["function_name"], "Pan")

    def test_cloud_sync_transfers_inventory_and_remaps_local_foreign_keys(self):
        warehouse_id = self.db.execute(
            "INSERT INTO warehouses(name,barcode) VALUES(?,?)", ("Hauptlager", "WH001")
        ).lastrowid
        rack_id = self.db.execute(
            "INSERT INTO racks(warehouse_id,name,barcode) VALUES(?,?,?)",
            (warehouse_id, "Regal 1", "RK001"),
        ).lastrowid
        level_id = self.db.execute(
            "INSERT INTO levels(rack_id,name,barcode) VALUES(?,?,?)",
            (rack_id, "Ebene 1", "LV001"),
        ).lastrowid
        box_id = self.db.execute(
            "INSERT INTO boxes(level_id,name,barcode) VALUES(?,?,?)",
            (level_id, "Box 1", "BX001"),
        ).lastrowid
        article_id = create_article(
            self.db,
            {"name": "Moving Head", "barcode": "MH001", "quantity": 4, "box_id": box_id},
        )
        snapshot = local_snapshot(self.db)
        plan = plan_sync(self.db, snapshot, [])
        self.assertEqual(len(plan["pushes"]), len(snapshot))
        remote = [
            {
                "id": entity_uuid,
                "entity_type": item["entity_type"],
                "data": item["state"]["data"],
                "revision": 1,
                "deleted": item["state"]["deleted"],
            }
            for entity_uuid, item in snapshot.items()
        ]
        second_db = Database(Path(self.tempdir.name) / "second-pc.db")
        try:
            apply_cloud_snapshot(second_db, remote)
            received = second_db.query("SELECT * FROM articles WHERE barcode='MH001'")
            self.assertEqual(received[0]["name"], "Moving Head")
            self.assertEqual(received[0]["quantity"], 4)
            self.assertNotEqual(received[0]["box_id"], None)
            received_box = second_db.query(
                "SELECT sync_uuid FROM boxes WHERE id=?", (received[0]["box_id"],)
            )
            self.assertEqual(
                received_box[0]["sync_uuid"],
                snapshot[next(
                    key for key, value in snapshot.items()
                    if value["entity_type"] == "articles"
                )]["state"]["data"]["box_id"],
            )
            self.assertEqual(
                plan_sync(second_db, local_snapshot(second_db), remote)["pushes"], []
            )
        finally:
            second_db.close()

    def test_cloud_records_from_before_dimensions_and_barcode_settings_get_defaults(self):
        remote = [
            {
                "id": "warehouse-uuid", "entity_type": "warehouses",
                "data": {"name": "Hauptlager", "barcode": None, "description": None},
                "revision": 1, "deleted": False,
            },
            {
                "id": "rack-uuid", "entity_type": "racks",
                "data": {"name": "Regal 1", "barcode": None, "warehouse_id": "warehouse-uuid"},
                "revision": 1, "deleted": False,
            },
            {
                "id": "level-uuid", "entity_type": "levels",
                "data": {"name": "Boden 1", "barcode": None, "rack_id": "rack-uuid"},
                "revision": 1, "deleted": False,
            },
            {
                "id": "barcode-uuid", "entity_type": "barcodes",
                "data": {
                    "code": "ET-OLD-1", "code_type": "Code 128",
                    "entity_type": "custom", "article_id": None,
                },
                "revision": 1, "deleted": False,
            },
        ]
        apply_cloud_snapshot(self.db, remote)
        rack = self.db.query("SELECT width_cm,height_cm FROM racks")[0]
        level = self.db.query("SELECT height_cm FROM levels")[0]
        barcode_row = self.db.query("SELECT label,scale FROM barcodes")[0]
        self.assertEqual((rack["width_cm"], rack["height_cm"]), (120, 200))
        self.assertEqual(level["height_cm"], 40)
        self.assertEqual((barcode_row["label"], barcode_row["scale"]), (None, 100))

    def test_cloud_sync_reports_two_sided_edits_as_manual_conflict(self):
        article_id = create_article(self.db, {"name": "Ursprünglicher Scheinwerfer"})
        baseline_snapshot = local_snapshot(self.db)
        entity_uuid, local_item = next(iter(baseline_snapshot.items()))
        baseline = local_item["state"]
        self.db.execute(
            """INSERT INTO sync_state(entity_uuid,entity_type,baseline_json,remote_revision)
               VALUES(?,?,?,1)""",
            (entity_uuid, "articles", canonical_json(baseline)),
        )
        self.db.execute("UPDATE articles SET name='Änderung auf PC 1' WHERE id=?", (article_id,))
        local = local_snapshot(self.db)
        remote_data = dict(baseline["data"])
        remote_data["name"] = "Änderung auf PC 2"
        result = plan_sync(
            self.db,
            local,
            [{"id": entity_uuid, "entity_type": "articles", "data": remote_data,
              "revision": 2, "deleted": False}],
        )
        self.assertEqual(result["pushes"], [])
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(result["conflicts"][0]["local"]["data"]["name"], "Änderung auf PC 1")
        self.assertEqual(result["conflicts"][0]["remote"]["data"]["name"], "Änderung auf PC 2")

    def test_cloud_sync_marks_local_deletes_as_tombstones(self):
        article_id = create_article(self.db, {"name": "Wird entfernt", "barcode": "DEL001"})
        baseline_snapshot = local_snapshot(self.db)
        remote = []
        for entity_uuid, item in baseline_snapshot.items():
            state = item["state"]
            remote.append({
                "id": entity_uuid,
                "entity_type": item["entity_type"],
                "data": state["data"],
                "revision": 1,
                "deleted": False,
            })
            self.db.execute(
                """INSERT INTO sync_state(entity_uuid,entity_type,baseline_json,remote_revision)
                   VALUES(?,?,?,1)""",
                (entity_uuid, item["entity_type"], canonical_json(state)),
            )
        self.db.execute("DELETE FROM articles WHERE id=?", (article_id,))
        result = plan_sync(self.db, local_snapshot(self.db), remote)
        deleted_types = {change["entity_type"] for change in result["pushes"] if change["deleted"]}
        self.assertIn("articles", deleted_types)
        self.assertIn("barcodes", deleted_types)

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI test")
    def test_cloud_shared_key_is_protected_with_windows_dpapi(self):
        secret = "sample shared organization key"
        self.assertEqual(unprotect_secret(protect_secret(secret)), secret)

    def test_cloud_shared_key_uses_linux_keyring(self):
        secret = "sample shared organization key"
        stored_secrets = {}
        keyring = MagicMock()
        keyring.set_password.side_effect = (
            lambda service, reference, value: stored_secrets.__setitem__(reference, value)
        )
        keyring.get_password.side_effect = (
            lambda service, reference: stored_secrets.get(reference)
        )
        with patch("modules.secret_store.sys.platform", "linux"), patch(
            "modules.secret_store._linux_keyring", return_value=keyring
        ):
            protected = protect_secret(secret)
            self.assertTrue(protected.startswith("keyring:v1:"))
            self.assertNotIn(secret, protected)
            self.assertEqual(unprotect_secret(protected), secret)

    def test_supabase_config_file_encrypts_and_restores_connection_and_shared_key(self):
        settings = {
            "project_url": "https://eventtech.supabase.co",
            "public_key": "sb_publishable_public_test_key_123456",
            "shared_key": "shared-organization-key-that-is-at-least-32-chars",
        }
        encrypted = encrypt_supabase_config(settings, "correct horse battery")

        self.assertNotIn(settings["shared_key"].encode(), encrypted)
        restored = decrypt_supabase_config(encrypted, "correct horse battery")
        self.assertEqual(restored["project_url"], settings["project_url"])
        self.assertEqual(restored["public_key"], settings["public_key"])
        self.assertEqual(restored["shared_key"], settings["shared_key"])

    def test_supabase_config_file_rejects_wrong_password_and_invalid_settings(self):
        settings = {
            "project_url": "https://eventtech.supabase.co",
            "public_key": "sb_publishable_public_test_key_123456",
        }
        encrypted = encrypt_supabase_config(settings, "correct horse battery")
        with self.assertRaisesRegex(ValueError, "Dateipasswort ist falsch"):
            decrypt_supabase_config(encrypted, "wrong password here")
        with self.assertRaisesRegex(ValueError, "mindestens 12 Zeichen"):
            encrypt_supabase_config(settings, "short")

    @patch(
        "modules.ipad_scanner.protect_secret",
        new=lambda value: base64.b64encode(value.encode("ascii")).decode("ascii"),
    )
    @patch(
        "modules.ipad_scanner.unprotect_secret",
        new=lambda value: base64.b64decode(value.encode("ascii")).decode("ascii"),
    )
    def test_ipad_scanner_requires_pairing_token_and_receives_barcode(self):
        received = []
        certificate_dir = Path(self.tempdir.name) / "scanner-certificate"
        server = IpadScannerServer(
            received.append, host="127.0.0.1",
            certificate_dir=certificate_dir, certificate_hosts=["127.0.0.1"],
        )
        server.start()
        try:
            url = f"http://127.0.0.1:{server.port}/?key={server.token}"
            with urllib.request.urlopen(url, timeout=3) as response:
                self.assertEqual(response.status, 200)
                page = response.read()
                self.assertIn(b"EventTech Scanner", page)
                self.assertIn(b"getUserMedia", page)
                self.assertIn(b'id="preview"', page)
                self.assertIn(b"https://127.0.0.1:", page)
            certificate_url = (
                f"http://127.0.0.1:{server.port}/certificate?key={server.token}"
            )
            with urllib.request.urlopen(certificate_url, timeout=3) as response:
                certificate = response.read()
                self.assertIn(b"BEGIN CERTIFICATE", certificate)
            protected_ca_key = (certificate_dir / "ca-key.pem").read_text(encoding="ascii")
            self.assertNotIn("PRIVATE KEY", protected_ca_key)
            self.assertIn(
                "PRIVATE KEY",
                base64.b64decode(protected_ca_key.encode("ascii")).decode("ascii"),
            )
            secure_context = ssl.create_default_context(cadata=certificate.decode("ascii"))
            secure_url = f"https://127.0.0.1:{server.https_port}/?key={server.token}"
            with urllib.request.urlopen(secure_url, context=secure_context, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(b"getUserMedia", response.read())
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.port}/api/scan",
                data=json.dumps({"code": "ET-123"}).encode(),
                headers={"Content-Type": "application/json", "X-EventTech-Token": server.token},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertEqual(json.loads(response.read())["code"], "ET-123")
            self.assertEqual(received, ["ET-123"])
            unauthorized = urllib.request.Request(
                f"http://127.0.0.1:{server.port}/api/scan",
                data=b'{"code":"forbidden"}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(unauthorized, timeout=3)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        finally:
            server.stop()

    @patch(
        "modules.ipad_scanner.protect_secret",
        new=lambda value: base64.b64encode(value.encode("ascii")).decode("ascii"),
    )
    @patch(
        "modules.ipad_scanner.unprotect_secret",
        new=lambda value: base64.b64decode(value.encode("ascii")).decode("ascii"),
    )
    def test_ipad_scanner_decodes_camera_image_locally(self):
        received = []
        server = IpadScannerServer(
            received.append,
            host="127.0.0.1",
            certificate_dir=Path(self.tempdir.name) / "scanner-certificate",
            certificate_hosts=["127.0.0.1"],
        )
        server.start()
        try:
            def decode_image(image):
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                payload = json.dumps({"image": base64.b64encode(buffer.getvalue()).decode()}).encode()
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.port}/api/photo",
                    data=payload,
                    headers={"Content-Type": "application/json", "X-EventTech-Token": server.token},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    return json.loads(response.read())

            result = decode_image(qrcode.make("CAMERA-123").get_image())
            self.assertEqual([item["code"] for item in result["codes"]], ["CAMERA-123"])
            self.assertIn("Abstand", result["codes"][0]["guidance"])
            blank = Image.new("RGB", (640, 480), "white")
            small_code = qrcode.make("SMALL-CODE").get_image().resize((100, 100))
            blank.paste(small_code, (270, 190))
            small_result = decode_image(blank)
            self.assertEqual(small_result["codes"][0]["code"], "SMALL-CODE")
            self.assertIn("näher heran", small_result["codes"][0]["guidance"])
            no_code = decode_image(Image.new("RGB", (640, 480), "white"))
            self.assertEqual(no_code["codes"], [])
            self.assertIn("näher heran", no_code["guidance"])
            self.assertEqual(received, [])
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
