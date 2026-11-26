import sys
from pathlib import Path
import base64
from copy import deepcopy
import html
import json
import subprocess

from PySide6.QtCore import Qt, QObject, Signal, QThread, QTimer, QUrl
from PySide6.QtGui import QAction, QKeySequence, QPixmap, QBrush, QColor, QFont, QPen, QPainter, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
    QStackedWidget, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QLineEdit, QFormLayout, QDialog, QDialogButtonBox, QComboBox, QTextEdit, QCheckBox,
    QFileDialog, QMessageBox, QSpinBox, QTabWidget, QSplitter,
    QInputDialog, QGraphicsView, QGraphicsScene, QAbstractItemView,
    QScrollArea,
)

from config import APP_NAME, APP_VERSION, DATA_DIR
from database.database import Database
from modules.inventory import create_article, update_article, delete_article, duplicate_article, find_by_barcode, ARTICLE_STATUSES
from modules.warehouse import warehouse_path
from modules.rack_graphic import RackGraphic
from modules.scanner import ScannerReader
from modules.dmx import load_article_modes, validate_article_modes
from modules.live_camera import LiveCameraScanner
from modules.barcode import (
    generate_unique_code, render_barcode_png, create_label_pdf,
    MIN_BARCODE_SCALE, MAX_BARCODE_SCALE, LABELS_PER_PAGE,
)
from modules.exports import export_csv
from modules.ipad_scanner import IpadScannerServer, local_ipv4_addresses
from modules.cloud_sync import (
    SupabaseSyncClient, apply_cloud_snapshot, local_snapshot, plan_sync,
)
from modules.secret_store import protect_secret, secret_store_name, unprotect_secret
from modules.supabase_config_file import (
    decrypt_supabase_config,
    encrypt_supabase_config,
)
from modules.usage_guides import (
    has_auto_usage_guide, manufacturer_manual_search_url, update_auto_usage_guide,
)


class ScanBridge(QObject):
    barcode_received = Signal(str)


class CloudSyncWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, client: SupabaseSyncClient, action: str, changes=None, parent=None):
        super().__init__(parent)
        self.client = client
        self.action = action
        self.changes = changes or []

    def run(self):
        try:
            if self.action == "pull":
                self.completed.emit({"records": self.client.pull()})
                return
            conflicts = self.client.push(self.changes)
            if conflicts:
                self.completed.emit({"conflicts": conflicts})
                return
            self.completed.emit({"records": self.client.pull(), "conflicts": []})
        except Exception as exc:
            self.failed.emit(str(exc))


class SyncConflictDialog(QDialog):
    def __init__(self, conflicts: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cloud-Synchronisierung: Konflikte")
        self.resize(1000, 620)
        layout = QVBoxLayout(self)
        message = QLabel(
            "Einige Datensätze wurden auf mehreren PCs geändert. Wähle pro Zeile, welche Version gelten soll."
        )
        message.setWordWrap(True)
        layout.addWidget(message)
        self.table = QTableWidget(len(conflicts), 4)
        self.table.setHorizontalHeaderLabels(["Bereich", "Änderung auf diesem PC", "Änderung in der Cloud", "Behalten"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.conflicts = conflicts
        self.choices = []
        for index, conflict in enumerate(conflicts):
            local = conflict["local"]
            remote = conflict["remote"]
            entity_type = conflict["entity_type"]
            self.table.setItem(index, 0, QTableWidgetItem(self._record_title(entity_type, local or remote)))
            self.table.setItem(index, 1, QTableWidgetItem(self._describe(local)))
            self.table.setItem(index, 2, QTableWidgetItem(self._describe(remote)))
            choice = QComboBox()
            choice.addItem("Cloud-Version", "remote")
            choice.addItem("Diese PC-Version", "local")
            self.table.setCellWidget(index, 3, choice)
            self.choices.append(choice)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Auswahl übernehmen")
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _record_title(entity_type: str, state):
        if not state or state.get("deleted"):
            return f"{entity_type}: gelöschter Datensatz"
        data = state.get("data") or {}
        title = data.get("name") or data.get("barcode") or data.get("function_name") or ""
        return f"{entity_type}: {title}" if title else entity_type

    @staticmethod
    def _describe(state):
        if state is None:
            return "Kein Datensatz in dieser Cloud vorhanden"
        if state.get("deleted"):
            return "Gelöscht"
        data = state.get("data") or {}
        summary = [f"{key}: {value}" for key, value in data.items() if value is not None]
        return "\n".join(summary)[:1200]

    def selected_choices(self):
        return [choice.currentData() for choice in self.choices]


STYLESHEET = """
* { font-family: "Segoe UI"; font-size: 10pt; }
QMainWindow, QDialog, QWidget { background: #202428; color: #edf2f7; }
QListWidget { background: #171a1d; border: none; min-width: 220px; padding: 8px; }
QListWidget::item { padding: 14px 12px; border-radius: 6px; margin: 3px 0; }
QListWidget::item:selected { background: #1976d2; color: white; }
QLineEdit, QSpinBox, QComboBox, QTextEdit, QTableWidget {
    background: #2b3035; border: 1px solid #3d454d; border-radius: 5px; padding: 7px;
}
QTableWidget { gridline-color: #3d454d; }
QHeaderView::section { background: #343a40; padding: 8px; border: none; border-right: 1px solid #202428; }
QPushButton { background: #1976d2; border: none; border-radius: 5px; padding: 9px 14px; font-weight: 600; }
QPushButton:hover { background: #2687df; }
QPushButton:disabled { background: #555b60; }
.card { background: #2b3035; border-radius: 8px; }
"""


class DmxModesDialog(QDialog):
    def __init__(self, modes: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("DMX-Modi und Kanäle")
        self.resize(1050, 620)
        self.modes = deepcopy(modes)
        self.current_mode_index = -1

        self.mode_list = QListWidget()
        self.mode_list.currentRowChanged.connect(self._select_mode)
        mode_buttons = QHBoxLayout()
        add_mode = QPushButton("Modus hinzufügen")
        add_mode.clicked.connect(self._add_mode)
        edit_mode = QPushButton("Modus bearbeiten")
        edit_mode.clicked.connect(self._edit_mode)
        delete_mode = QPushButton("Modus löschen")
        delete_mode.clicked.connect(self._delete_mode)
        mode_buttons.addWidget(add_mode)
        mode_buttons.addWidget(edit_mode)
        mode_buttons.addWidget(delete_mode)

        self.channel_table = QTableWidget(0, 5)
        self.channel_table.setHorizontalHeaderLabels(
            ["Kanal", "Funktion", "Min.-Wert", "Max.-Wert", "Beschreibung"]
        )
        self.channel_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.channel_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.channel_table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self.channel_table.horizontalHeader().setStretchLastSection(True)
        channel_buttons = QHBoxLayout()
        add_channel = QPushButton("Kanal hinzufügen")
        add_channel.clicked.connect(self._add_channel)
        delete_channel = QPushButton("Ausgewählte Kanäle löschen")
        delete_channel.clicked.connect(self._delete_channels)
        channel_buttons.addWidget(add_channel)
        channel_buttons.addWidget(delete_channel)

        mode_column = QVBoxLayout()
        mode_column.addWidget(QLabel("DMX-Modi"))
        mode_column.addWidget(self.mode_list)
        mode_column.addLayout(mode_buttons)
        channel_column = QVBoxLayout()
        channel_column.addWidget(QLabel("Kanäle des ausgewählten Modus"))
        channel_column.addWidget(self.channel_table)
        channel_column.addLayout(channel_buttons)
        columns = QHBoxLayout()
        columns.addLayout(mode_column, 1)
        columns.addLayout(channel_column, 2)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_changes)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(columns)
        layout.addWidget(buttons)
        self._refresh_modes(0, store=False)

    def _store_current_mode(self):
        index = self.current_mode_index
        if not 0 <= index < len(self.modes):
            return
        channels = []
        for row in range(self.channel_table.rowCount()):
            id_item = self.channel_table.item(row, 0)
            channels.append({
                "id": id_item.data(Qt.UserRole) if id_item else None,
                "channel_number": self._item_text(row, 0),
                "function_name": self._item_text(row, 1),
                "value_min": self._item_text(row, 2),
                "value_max": self._item_text(row, 3),
                "description": self._item_text(row, 4),
            })
        self.modes[index]["channels"] = channels

    def _item_text(self, row, column):
        item = self.channel_table.item(row, column)
        return item.text().strip() if item else ""

    def _select_mode(self, index):
        if index == self.current_mode_index:
            return
        self._store_current_mode()
        self.current_mode_index = index if 0 <= index < len(self.modes) else -1
        self._render_channels()

    def _render_channels(self):
        mode = self.modes[self.current_mode_index] if self.current_mode_index >= 0 else None
        channels = mode["channels"] if mode else []
        self.channel_table.setRowCount(len(channels))
        for row, channel in enumerate(channels):
            values = (
                channel.get("channel_number", ""),
                channel.get("function_name", ""),
                channel.get("value_min", 0),
                channel.get("value_max", 255),
                channel.get("description", ""),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.UserRole, channel.get("id"))
                self.channel_table.setItem(row, column, item)

    def _refresh_modes(self, selected_index, store=True):
        if store:
            self._store_current_mode()
        self.current_mode_index = -1
        self.mode_list.blockSignals(True)
        self.mode_list.clear()
        for mode in self.modes:
            self.mode_list.addItem(self._mode_label(mode))
        self.mode_list.blockSignals(False)
        if self.modes:
            self.mode_list.setCurrentRow(max(0, min(selected_index, len(self.modes) - 1)))
        else:
            self.channel_table.setRowCount(0)

    @staticmethod
    def _mode_label(mode):
        return f"{mode.get('name', 'DMX-Modus')} ({mode.get('channel_count', 0)} Kanäle)"

    def _add_mode(self):
        self._store_current_mode()
        name, accepted = QInputDialog.getText(self, "DMX-Modus hinzufügen", "Modusname:")
        if not accepted:
            return
        if not name.strip():
            QMessageBox.warning(self, "DMX-Modus", "Bitte einen Modusnamen eingeben.")
            return
        count, accepted = QInputDialog.getInt(
            self, "DMX-Modus hinzufügen", "Kanalanzahl:", 1, 1, 512
        )
        if not accepted:
            return
        self.modes.append({
            "id": None,
            "name": name.strip(),
            "channel_count": count,
            "channels": [],
        })
        self._refresh_modes(len(self.modes) - 1, store=False)

    def _edit_mode(self):
        self._store_current_mode()
        index = self.mode_list.currentRow()
        if index < 0:
            QMessageBox.information(self, "DMX-Modus", "Bitte zuerst einen Modus auswählen.")
            return
        mode = self.modes[index]
        name, accepted = QInputDialog.getText(
            self, "DMX-Modus bearbeiten", "Modusname:", text=mode["name"]
        )
        if not accepted:
            return
        if not name.strip():
            QMessageBox.warning(self, "DMX-Modus", "Bitte einen Modusnamen eingeben.")
            return
        count, accepted = QInputDialog.getInt(
            self, "DMX-Modus bearbeiten", "Kanalanzahl:",
            mode["channel_count"], 1, 512,
        )
        if not accepted:
            return
        mode["name"] = name.strip()
        mode["channel_count"] = count
        self.mode_list.item(index).setText(self._mode_label(mode))

    def _delete_mode(self):
        self._store_current_mode()
        index = self.mode_list.currentRow()
        if index < 0:
            QMessageBox.information(self, "DMX-Modus", "Bitte zuerst einen Modus auswählen.")
            return
        self.current_mode_index = -1
        del self.modes[index]
        self._refresh_modes(index - 1, store=False)

    def _add_channel(self):
        self._store_current_mode()
        index = self.mode_list.currentRow()
        if index < 0:
            QMessageBox.information(self, "DMX-Kanal", "Bitte zuerst einen Modus auswählen.")
            return
        mode = self.modes[index]
        used_numbers = set()
        for channel in mode["channels"]:
            try:
                used_numbers.add(int(channel["channel_number"]))
            except (TypeError, ValueError):
                continue
        number = next((candidate for candidate in range(1, 513) if candidate not in used_numbers), None)
        if number is None:
            QMessageBox.warning(self, "DMX-Kanal", "Ein DMX-Universum kann höchstens 512 Kanäle haben.")
            return
        mode["channel_count"] = max(mode["channel_count"], number)
        mode["channels"].append({
            "id": None,
            "channel_number": number,
            "function_name": "",
            "value_min": 0,
            "value_max": 255,
            "description": "",
        })
        self.mode_list.item(index).setText(self._mode_label(mode))
        self.current_mode_index = index
        self._render_channels()
        self.channel_table.setCurrentCell(len(mode["channels"]) - 1, 1)
        self.channel_table.editItem(self.channel_table.currentItem())

    def _delete_channels(self):
        if self.mode_list.currentRow() < 0:
            QMessageBox.information(self, "DMX-Kanal", "Bitte zuerst einen Modus auswählen.")
            return
        selected_rows = sorted(
            {item.row() for item in self.channel_table.selectedItems()},
            reverse=True,
        )
        for row in selected_rows:
            self.channel_table.removeRow(row)
        self._store_current_mode()

    def _accept_changes(self):
        self._store_current_mode()
        try:
            self.modes = validate_article_modes(self.modes)
        except ValueError as exc:
            QMessageBox.warning(self, "DMX-Modi", str(exc))
            return
        self.accept()


class ArticleDialog(QDialog):
    def __init__(self, db: Database, article=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.article = article
        self.setWindowTitle("Artikel bearbeiten" if article else "Artikel hinzufügen")
        self.resize(760, 760)
        form = QFormLayout(self)
        self.name = QLineEdit(article["name"] if article else "")
        self.manufacturer = QLineEdit(article["manufacturer"] if article else "")
        self.model = QLineEdit(article["model"] if article else "")
        self.category = QLineEdit(article["category"] if article else "")
        self.barcode = QLineEdit(article["barcode"] if article and article["barcode"] else "")
        self.quantity = QSpinBox(); self.quantity.setRange(0, 100000)
        self.quantity.setValue(article["quantity"] if article else 1)
        self.status = QComboBox(); self.status.addItems(ARTICLE_STATUSES)
        if article:
            self.status.setCurrentText(article["status"])
        self.description = QTextEdit()
        self.description.setPlainText(article["description"] if article else "")
        self.technical = QTextEdit()
        self.technical.setPlainText(article["technical_data"] if article else "")
        self.auto_usage_guide = QCheckBox("Bedienhinweise automatisch ergänzen")
        self.auto_usage_guide.setToolTip(
            "Ergänzt allgemeine Hinweise passend zu Kategorie und Produkt sowie einen Suchlink zur Herstelleranleitung. "
            "Die Suchanfrage wird erst übertragen, wenn der Link geöffnet wird."
        )
        self.auto_usage_guide.setChecked(
            has_auto_usage_guide(article["technical_data"] if article else "")
        )
        self.dmx_modes = load_article_modes(db, article["id"]) if article else []
        self.dmx_button = QPushButton("DMX-Modi und Kanäle bearbeiten")
        self.dmx_button.clicked.connect(self.edit_dmx_modes)
        self.dmx_summary = QLabel()
        self._refresh_dmx_summary()
        form.addRow("Artikelname *", self.name)
        form.addRow("Hersteller", self.manufacturer)
        form.addRow("Modell", self.model)
        form.addRow("Kategorie", self.category)
        form.addRow("Barcode", self.barcode)
        form.addRow("Anzahl", self.quantity)
        form.addRow("Status", self.status)
        form.addRow("Beschreibung", self.description)
        form.addRow("Technische Daten", self.technical)
        form.addRow("", self.auto_usage_guide)
        form.addRow("DMX", self.dmx_button)
        form.addRow("", self.dmx_summary)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def data(self):
        name = self.name.text().strip()
        manufacturer = self.manufacturer.text().strip()
        model = self.model.text().strip()
        category = self.category.text().strip()
        technical_data = update_auto_usage_guide(
            self.technical.toPlainText().strip(),
            enabled=self.auto_usage_guide.isChecked(),
            name=name,
            manufacturer=manufacturer,
            model=model,
            category=category,
        )
        return {
            "name": name,
            "manufacturer": manufacturer,
            "model": model,
            "category": category,
            "barcode": self.barcode.text().strip(),
            "quantity": self.quantity.value(),
            "status": self.status.currentText(),
            "description": self.description.toPlainText().strip(),
            "technical_data": technical_data,
            "dmx_modes": deepcopy(self.dmx_modes),
            "box_id": self.article["box_id"] if self.article else None,
        }

    def edit_dmx_modes(self):
        dialog = DmxModesDialog(self.dmx_modes, self)
        if dialog.exec():
            self.dmx_modes = dialog.modes
            self._refresh_dmx_summary()

    def _refresh_dmx_summary(self):
        if not self.dmx_modes:
            self.dmx_summary.setText("Keine DMX-Modi hinterlegt.")
            return
        mode_count = len(self.dmx_modes)
        channel_count = sum(mode["channel_count"] for mode in self.dmx_modes)
        self.dmx_summary.setText(f"{mode_count} Modus/Modi, insgesamt {channel_count} Kanäle")


class TablePage(QWidget):
    def __init__(self, title: str, headers: list[str]):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(QLabel(title))
        self.table = QTableWidget()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

    def set_rows(self, rows):
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                item = QTableWidgetItem("" if value is None else str(value))
                self.table.setItem(row_index, column_index, item)


class RackLayoutDialog(QDialog):
    def __init__(self, db: Database, rack, parent=None):
        super().__init__(parent)
        self.db = db
        self.rack_id = rack["id"]
        self.deleted_level_ids = []
        self.setWindowTitle("Regal und Böden bearbeiten")
        self.resize(540, 460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(rack["name"])
        self.width = QSpinBox()
        self.width.setRange(10, 5000)
        self.width.setValue(rack["width_cm"])
        self.height = QSpinBox()
        self.height.setRange(20, 10000)
        self.height.setValue(rack["height_cm"])
        form.addRow("Regalname", self.name)
        form.addRow("Breite (cm)", self.width)
        form.addRow("Höhe (cm)", self.height)
        layout.addLayout(form)

        self.level_table = QTableWidget()
        self.level_table.setColumnCount(2)
        self.level_table.setHorizontalHeaderLabels(["Boden", "Höhe (cm)"])
        self.level_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.level_table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.level_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.level_table)
        for level in db.query(
            "SELECT id,name,height_cm FROM levels WHERE rack_id=? ORDER BY id", (rack["id"],)
        ):
            self._append_level(level["name"], level["height_cm"], level["id"])

        level_actions = QHBoxLayout()
        add_level = QPushButton("Boden hinzufügen")
        add_level.clicked.connect(self.add_level)
        remove_level = QPushButton("Leeren Boden entfernen")
        remove_level.clicked.connect(self.remove_level)
        level_actions.addWidget(add_level)
        level_actions.addWidget(remove_level)
        layout.addLayout(level_actions)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _append_level(self, name, height_cm, level_id=None):
        row = self.level_table.rowCount()
        self.level_table.insertRow(row)
        name_item = QTableWidgetItem(str(name))
        if level_id is not None:
            name_item.setData(Qt.UserRole, level_id)
        self.level_table.setItem(row, 0, name_item)
        self.level_table.setItem(row, 1, QTableWidgetItem(str(height_cm)))

    def add_level(self):
        row_count = self.level_table.rowCount()
        default_height = max(1, self.height.value() // (row_count + 1))
        self._append_level(f"Boden {row_count + 1}", default_height)

    def remove_level(self):
        row = self.level_table.currentRow()
        if row < 0:
            return
        level_id = self.level_table.item(row, 0).data(Qt.UserRole)
        if level_id is not None:
            count = self.db.query("SELECT COUNT(*) AS count FROM boxes WHERE level_id=?", (level_id,))[0]["count"]
            if count:
                QMessageBox.warning(
                    self, "Boden belegt",
                    "Dieser Boden enthält Boxen. Verschiebe oder entferne sie zuerst.",
                )
                return
            self.deleted_level_ids.append(level_id)
        self.level_table.removeRow(row)

    def save(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Ungültiger Regalname", "Bitte gib einen Regalnamen ein.")
            return
        levels = self.levels()
        if not levels:
            QMessageBox.warning(self, "Böden fehlen", "Ein Regal muss mindestens einen Boden haben.")
            return
        if any(not level["name"] or level["height_cm"] < 1 for level in levels):
            QMessageBox.warning(self, "Ungültige Bodenangabe", "Jeder Boden braucht einen Namen und eine Höhe.")
            return
        total_height = sum(level["height_cm"] for level in levels)
        if total_height > self.height.value():
            QMessageBox.warning(
                self, "Regalhöhe überschritten",
                f"Die Bodenhöhen ergeben {total_height} cm, das Regal ist aber nur {self.height.value()} cm hoch.",
            )
            return
        self.accept()

    def levels(self):
        rows = []
        for row in range(self.level_table.rowCount()):
            name_item = self.level_table.item(row, 0)
            height_item = self.level_table.item(row, 1)
            try:
                height_cm = int(height_item.text())
            except (AttributeError, ValueError):
                height_cm = 0
            rows.append({
                "id": name_item.data(Qt.UserRole),
                "name": name_item.text().strip(),
                "height_cm": height_cm,
            })
        return rows


class BarcodeDocumentPreview(QGraphicsView):
    itemScaleChanged = Signal(int, int)
    itemPositionChanged = Signal(int, float, float)
    PAGE_WIDTH = 595
    PAGE_HEIGHT = 842
    LABEL_WIDTH = 241
    LABEL_HEIGHT = 119
    LABEL_X = (42, 312)
    LABEL_TOP = 71
    LABEL_ROW_GAP = 139

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setBackgroundBrush(QColor("#3a4046"))
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumSize(420, 560)
        self._scene.setSceneRect(0, 0, self.PAGE_WIDTH, self.PAGE_HEIGHT)
        self._current_items = []
        self._current_page = 0
        self._barcode_rects = {}
        self._selected_item = None
        self._resize_state = None
        self._temporary_scale = None
        self._move_state = None
        self._temporary_offset = None

    def set_document_page(self, items, page_index):
        if page_index != self._current_page:
            self._selected_item = None
        self._current_items = items
        self._current_page = page_index
        self._scene.clear()
        self._barcode_rects = {}
        self._scene.addRect(
            0, 0, self.PAGE_WIDTH, self.PAGE_HEIGHT,
            QPen(QColor("#aeb8c2")), QBrush(QColor("#ffffff")),
        )
        self._scene.addText(
            f"Vorschau · Seite {page_index + 1}",
            QFont("Segoe UI", 9, QFont.Bold),
        ).setPos(24, 16)
        page_items = items[
            page_index * LABELS_PER_PAGE:(page_index + 1) * LABELS_PER_PAGE
        ]
        if not page_items:
            hint = self._scene.addText(
                "Füge links Barcodes zur PDF-Seite hinzu.",
                QFont("Segoe UI", 12),
            )
            hint.setDefaultTextColor(QColor("#4b5560"))
            hint.setPos(100, 400)
            self._fit_page()
            return

        for page_offset, item in enumerate(page_items):
            item_index = page_index * LABELS_PER_PAGE + page_offset
            column = page_offset % 2
            row = page_offset // 2
            base_x = self.LABEL_X[column]
            base_y = self.LABEL_TOP + row * self.LABEL_ROW_GAP
            if self._move_state and self._move_state["item_index"] == item_index:
                offset_x, offset_y = self._temporary_offset
            else:
                offset_x = float(item.get("offset_x", 0))
                offset_y = float(item.get("offset_y", 0))
            x = base_x + offset_x
            y = base_y + offset_y
            name = str(item.get("name") or "").strip()
            barcode_value = str(item.get("barcode", ""))
            if name == barcode_value:
                name = ""

            code_type = str(item.get("code_type", "Code 128"))
            image = QPixmap()
            image.loadFromData(render_barcode_png(str(item.get("barcode", "")), code_type), "PNG")
            scale_percent = (
                self._temporary_scale
                if self._resize_state and self._resize_state["item_index"] == item_index
                else int(item.get("scale", 100))
            )
            scale = scale_percent / 100
            if code_type.upper().replace("-", "").replace(" ", "") in ("QR", "QRCODE"):
                image_size = 22 * 2.8346 * scale
                image = image.scaled(
                    int(image_size), int(image_size), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                image_x = x + (self.LABEL_WIDTH - image.width()) / 2
                image_y = y + 27
            else:
                target_width = int(65 * 2.8346 * scale)
                target_height = int(18 * 2.8346 * scale)
                image = image.scaled(
                    target_width, target_height, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            image_x = x + (self.LABEL_WIDTH - image.width()) / 2
            image_center = image_x + image.width() / 2

            title = None
            title_height = 0
            if name:
                title = self._scene.addText(
                    name[:40],
                    QFont("Segoe UI", max(1, round(9 * scale)), QFont.Bold),
                )
                title.setDefaultTextColor(QColor("#202428"))
                title_height = title.boundingRect().height()

            code_text = self._scene.addText(
                barcode_value[:38],
                QFont("Segoe UI", max(1, round(8 * scale))),
            )
            code_text.setDefaultTextColor(QColor("#202428"))
            code_height = code_text.boundingRect().height()
            gap = 2.5 * scale
            group_height = title_height + image.height() + code_height + gap * (
                2 if title else 1
            )
            group_top = y + (self.LABEL_HEIGHT - group_height) / 2
            image_y = group_top + title_height + (gap if title else 0)
            if title:
                title.setPos(
                    image_center - title.boundingRect().width() / 2,
                    group_top,
                )
            self._scene.addPixmap(image).setPos(image_x, image_y)
            self._barcode_rects[item_index] = (
                image_x, image_y, image.width(), image.height()
            )

            code_text.setPos(
                image_center - code_text.boundingRect().width() / 2,
                image_y + image.height() + gap,
            )
            location = item.get("location")
            if location:
                location_text = self._scene.addText(str(location)[:36], QFont("Segoe UI", 6))
                location_text.setDefaultTextColor(QColor("#4b5560"))
                location_text.setPos(
                    x + self.LABEL_WIDTH - 7 - location_text.boundingRect().width(),
                    image_y + image.height() + gap,
                )
        self._draw_selection()
        self._fit_page()

    def mousePressEvent(self, event):
        point = self.mapToScene(event.position().toPoint())
        if event.button() == Qt.LeftButton and self._selected_item in self._barcode_rects:
            rect = self._barcode_rects[self._selected_item]
            handle = self._handle_at(point, rect)
            if handle:
                x, y, width, height = rect
                horizontal, vertical = handle
                anchor_x = x + width if horizontal < 0 else x
                self._resize_state = {
                    "item_index": self._selected_item,
                    "start_scale": int(self._current_items[self._selected_item].get("scale", 100)),
                    "start_width": width,
                    "anchor_x": anchor_x,
                }
                self._temporary_scale = self._resize_state["start_scale"]
                event.accept()
                return

        if event.button() == Qt.LeftButton:
            selected = next(
                (
                    index for index, (x, y, width, height) in self._barcode_rects.items()
                    if x <= point.x() <= x + width and y <= point.y() <= y + height
                ),
                None,
            )
            self._selected_item = selected
            if selected is not None:
                point = self.mapToScene(event.position().toPoint())
                item = self._current_items[selected]
                self._move_state = {
                    "item_index": selected,
                    "start_x": point.x(),
                    "start_y": point.y(),
                    "start_offset_x": float(item.get("offset_x", 0)),
                    "start_offset_y": float(item.get("offset_y", 0)),
                }
                self._temporary_offset = (
                    self._move_state["start_offset_x"],
                    self._move_state["start_offset_y"],
                )
            self.set_document_page(self._current_items, self._current_page)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_state:
            point = self.mapToScene(event.position().toPoint())
            state = self._resize_state
            width = abs(point.x() - state["anchor_x"])
            scale = round(state["start_scale"] * width / max(1, state["start_width"]))
            scale = max(MIN_BARCODE_SCALE, min(MAX_BARCODE_SCALE, scale))
            if scale != self._temporary_scale:
                self._temporary_scale = scale
                self.set_document_page(self._current_items, self._current_page)
            event.accept()
            return
        if self._move_state:
            point = self.mapToScene(event.position().toPoint())
            state = self._move_state
            offset_x, offset_y = self._clamp_offset(
                state["item_index"],
                state["start_offset_x"] + point.x() - state["start_x"],
                state["start_offset_y"] + point.y() - state["start_y"],
            )
            if self._temporary_offset != (offset_x, offset_y):
                self._temporary_offset = (offset_x, offset_y)
                self.set_document_page(self._current_items, self._current_page)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._resize_state:
            state = self._resize_state
            scale = self._temporary_scale
            self._resize_state = None
            self._temporary_scale = None
            if scale != state["start_scale"]:
                self.itemScaleChanged.emit(state["item_index"], scale)
            else:
                self.set_document_page(self._current_items, self._current_page)
            event.accept()
            return
        if event.button() == Qt.LeftButton and self._move_state:
            state = self._move_state
            offset_x, offset_y = self._temporary_offset
            self._move_state = None
            self._temporary_offset = None
            if (offset_x, offset_y) != (state["start_offset_x"], state["start_offset_y"]):
                self.itemPositionChanged.emit(state["item_index"], offset_x, offset_y)
            else:
                self.set_document_page(self._current_items, self._current_page)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _clamp_offset(self, item_index, offset_x, offset_y):
        page_offset = item_index % LABELS_PER_PAGE
        column = page_offset % 2
        row = page_offset // 2
        base_x = self.LABEL_X[column]
        base_y = self.LABEL_TOP + row * self.LABEL_ROW_GAP
        offset_x = max(-base_x, min(self.PAGE_WIDTH - base_x - self.LABEL_WIDTH, offset_x))
        offset_y = max(-base_y, min(self.PAGE_HEIGHT - base_y - self.LABEL_HEIGHT, offset_y))
        return offset_x, offset_y

    def _handle_at(self, point, rect):
        x, y, width, height = rect
        radius = 6
        for horizontal, vertical in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            handle_x = x if horizontal < 0 else x + width
            handle_y = y if vertical < 0 else y + height
            if abs(point.x() - handle_x) <= radius and abs(point.y() - handle_y) <= radius:
                return horizontal, vertical
        return None

    def _draw_selection(self):
        if self._selected_item not in self._barcode_rects:
            return
        x, y, width, height = self._barcode_rects[self._selected_item]
        self._scene.addRect(
            x, y, width, height, QPen(QColor("#1976d2"), 1), QBrush(Qt.NoBrush)
        )
        for handle_x, handle_y in ((x, y), (x + width, y), (x, y + height), (x + width, y + height)):
            self._scene.addRect(
                handle_x - 4, handle_y - 4, 8, 8,
                QPen(QColor("#1976d2"), 1), QBrush(QColor("#ffffff")),
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_page()

    def _fit_page(self):
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)


class BarcodeConfigDialog(QDialog):
    def __init__(self, barcode_row, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Barcode konfigurieren")
        form = QFormLayout(self)
        label = barcode_row["label"] or ""
        if label == barcode_row["code"]:
            label = ""
        self.label = QLineEdit(label)
        self.code = QLineEdit(barcode_row["code"])
        self.code.setReadOnly(True)
        self.code_type = QComboBox()
        self.code_type.addItems(["Code 128", "EAN-13", "QR"])
        self.code_type.setCurrentText(barcode_row["code_type"])
        self.scale = QSpinBox()
        self.scale.setRange(MIN_BARCODE_SCALE, MAX_BARCODE_SCALE)
        self.scale.setSuffix(" %")
        self.scale.setValue(barcode_row["scale"])
        form.addRow("Bezeichnung", self.label)
        form.addRow("Barcode", self.code)
        form.addRow("Typ", self.code_type)
        form.addRow("Skalierung", self.scale)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self):
        try:
            render_barcode_png(self.code.text(), self.code_type.currentText(), self.scale.value())
        except ValueError as exc:
            QMessageBox.warning(self, "Ungültige Barcode-Einstellungen", str(exc))
            return
        self.accept()


class ArticleDetailsDialog(QDialog):
    def __init__(self, db: Database, article: dict, parent=None):
        super().__init__(parent)
        self.db = db
        self.article = article
        self.setWindowTitle(f"Artikeldetails: {article['name']}")
        self.resize(960, 700)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        self._add_overview_tab(tabs)
        self._add_barcode_tab(tabs)
        self._add_dmx_tab(tabs)
        self._add_repair_tab(tabs)
        self._add_file_tab(tabs)
        self._add_history_tabs(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_overview_tab(self, tabs):
        content = QWidget()
        form = QFormLayout(content)
        values = (
            ("ID", self.article["id"]),
            ("Artikelname", self.article["name"]),
            ("Hersteller", self.article["manufacturer"]),
            ("Modell", self.article["model"]),
            ("Kategorie", self.article["category"]),
            ("Barcode", self.article["barcode"]),
            ("Anzahl", self.article["quantity"]),
            ("Status", self.article["status"]),
            ("Box-ID", self.article["box_id"]),
            ("Lagerort", warehouse_path(self.db, self.article["box_id"])),
        )
        for label, value in values:
            form.addRow(label, self._value_label(value))
        for label, key in (("Beschreibung", "description"), ("Technische Daten", "technical_data")):
            text = QTextEdit()
            text.setPlainText(self.article[key] or "")
            text.setReadOnly(True)
            text.setMaximumHeight(110)
            form.addRow(label, text)
        if has_auto_usage_guide(self.article["technical_data"] or ""):
            search_url = manufacturer_manual_search_url(
                self.article["name"], self.article["manufacturer"], self.article["model"]
            )
            manual_link = QLabel(
                f'<a href="{html.escape(search_url, quote=True)}">Herstelleranleitung online suchen</a>'
            )
            manual_link.setOpenExternalLinks(True)
            form.addRow("Herstelleranleitung", manual_link)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        tabs.addTab(scroll, "Allgemein")

    @staticmethod
    def _value_label(value):
        label = QLabel("—" if value is None or value == "" else str(value))
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label

    def _add_table_tab(self, tabs, title, headers, rows, empty_message):
        page = QWidget()
        layout = QVBoxLayout(page)
        if not rows:
            message = QLabel(empty_message)
            message.setWordWrap(True)
            layout.addWidget(message)
        else:
            table = QTableWidget(len(rows), len(headers))
            table.setHorizontalHeaderLabels(headers)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.setWordWrap(True)
            table.horizontalHeader().setStretchLastSection(True)
            for row_index, row in enumerate(rows):
                for column, value in enumerate(row):
                    table.setItem(
                        row_index, column,
                        QTableWidgetItem("—" if value is None or value == "" else str(value)),
                    )
            table.resizeColumnsToContents()
            layout.addWidget(table)
        tabs.addTab(page, title)

    def _add_barcode_tab(self, tabs):
        rows = self.db.query(
            """SELECT id,code,code_type,label,scale,entity_type,entity_id
               FROM barcodes WHERE article_id=? OR code=? ORDER BY id""",
            (self.article["id"], self.article["barcode"]),
        )
        self._add_table_tab(
            tabs, "Barcodes",
            ["ID", "Barcode", "Typ", "Bezeichnung", "Skalierung", "Zuordnung", "Zuordnungs-ID"],
            [tuple(row) for row in rows],
            "Für diesen Artikel sind keine zusätzlichen Barcode-Datensätze vorhanden.",
        )

    def _add_dmx_tab(self, tabs):
        rows = self.db.query(
            """SELECT m.id,m.name,m.channel_count,c.channel_number,c.function_name,
                      c.value_min,c.value_max,c.description
               FROM dmx_modes m LEFT JOIN dmx_channels c ON c.mode_id=m.id
               WHERE m.article_id=? ORDER BY m.id,c.channel_number""",
            (self.article["id"],),
        )
        self._add_table_tab(
            tabs, "DMX",
            ["Modus-ID", "Modus", "Kanäle", "Kanal", "Funktion", "Min.", "Max.", "Beschreibung"],
            [tuple(row) for row in rows],
            "Für diesen Artikel sind keine DMX-Modi gespeichert.",
        )

    def _add_repair_tab(self, tabs):
        rows = self.db.query(
            """SELECT id,date,error_description,damage_type,measures,spare_parts,costs,
                      repair_status,completion_date,internal_notes
               FROM repairs WHERE article_id=? ORDER BY date DESC,id DESC""",
            (self.article["id"],),
        )
        self._add_table_tab(
            tabs, "Reparaturen",
            ["ID", "Datum", "Fehler", "Schadensart", "Maßnahmen", "Ersatzteile",
             "Kosten", "Status", "Abschluss", "Interne Notizen"],
            [tuple(row) for row in rows],
            "Für diesen Artikel sind keine Reparaturen gespeichert.",
        )

    def _add_file_tab(self, tabs):
        documents = self.db.query(
            """SELECT id,name,kind,path,external_url FROM documents
               WHERE article_id=? ORDER BY id""",
            (self.article["id"],),
        )
        photos = self.db.query(
            "SELECT id,name,path FROM photos WHERE article_id=? ORDER BY id",
            (self.article["id"],),
        )
        rows = [
            (row["id"], "Dokument", row["name"], row["kind"], row["path"], row["external_url"])
            for row in documents
        ]
        rows.extend(
            (row["id"], "Foto", row["name"], "Bild", row["path"], None)
            for row in photos
        )
        self._add_table_tab(
            tabs, "Dokumente / Fotos",
            ["ID", "Art", "Name", "Typ", "Dateipfad", "Externe URL"],
            rows,
            "Für diesen Artikel sind keine Dokumente oder Fotos gespeichert.",
        )

    def _add_history_tabs(self, tabs):
        movements = self.db.query(
            """SELECT m.id,m.timestamp,m.action,m.quantity,m.from_box_id,fb.name,
                      m.to_box_id,tb.name
               FROM movements m
               LEFT JOIN boxes fb ON fb.id=m.from_box_id
               LEFT JOIN boxes tb ON tb.id=m.to_box_id
               WHERE m.article_id=? ORDER BY m.timestamp DESC,m.id DESC""",
            (self.article["id"],),
        )
        self._add_table_tab(
            tabs, "Bewegungen",
            ["ID", "Zeitpunkt", "Aktion", "Anzahl", "Von-Box-ID", "Von-Box",
             "Zu-Box-ID", "Zu-Box"],
            [tuple(row) for row in movements],
            "Für diesen Artikel sind keine Lagerbewegungen gespeichert.",
        )
        scans = self.db.query(
            """SELECT id,timestamp,barcode,box_id,scan_type,action FROM scans
               WHERE article_id=? ORDER BY timestamp DESC,id DESC""",
            (self.article["id"],),
        )
        self._add_table_tab(
            tabs, "Scan-Verlauf",
            ["ID", "Zeitpunkt", "Barcode", "Box-ID", "Scan-Art", "Aktion"],
            [tuple(row) for row in scans],
            "Für diesen Artikel sind keine Scans gespeichert.",
        )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = Database()
        self.scanner = ScannerReader(self.read_setting("scanner_prefix"), self.read_setting("scanner_suffix"))
        self._sync_worker = None
        self._sync_action = None
        self._sync_snapshot = None
        self._sync_client = None
        self._sync_automatic = False
        self.ipad_server = None
        self.scan_bridge = ScanBridge(self)
        self.scan_bridge.barcode_received.connect(self.process_ipad_scan)
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(1440, 900)
        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QHBoxLayout(root)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.menu = QListWidget()
        entries = ["Dashboard", "Lesemodus", "Artikel", "Lagerverwaltung", "Inventur",
                   "Reparaturen", "Barcode-Programmierung", "Scan-Verlauf", "Einstellungen",
                   "Regal-Grafik"]
        self.menu.addItems(entries)
        self.menu.currentRowChanged.connect(self.open_page)
        main_layout.addWidget(self.menu)

        self.pages = QStackedWidget()
        main_layout.addWidget(self.pages, 1)
        self.pages.addWidget(self.dashboard_page())
        self.pages.addWidget(self.reader_page())
        self.pages.addWidget(self.article_page())
        self.pages.addWidget(self.warehouse_page())
        self.pages.addWidget(self.inventory_page())
        self.pages.addWidget(self.repair_page())
        self.pages.addWidget(self.programming_page())
        self.pages.addWidget(self.scans_page())
        self.pages.addWidget(self.settings_page())
        self.pages.addWidget(self.rack_graphic_page())

        self.menu.setCurrentRow(0)
        self.install_barcode_shortcut()
        self.cloud_sync_timer = QTimer(self)
        self.cloud_sync_timer.setInterval(5 * 60 * 1000)
        self.cloud_sync_timer.timeout.connect(lambda: self.start_cloud_sync(automatic=True))
        self.cloud_sync_timer.start()
        if self.read_setting("supabase_url"):
            QTimer.singleShot(5000, lambda: self.start_cloud_sync(automatic=True))

    def open_page(self, index):
        self.pages.setCurrentIndex(index)
        if index != 0 and hasattr(self, "dashboard_camera"):
            self.dashboard_camera.stop_camera()
        if index == 0:
            self.refresh_dashboard()
        elif index == 2:
            self.refresh_articles()
        elif index == 3:
            self.refresh_warehouses()
        elif index == 6:
            self.refresh_barcode_library()
        elif index == 7:
            self.refresh_scans()
        elif index == 5:
            self.refresh_repairs()
        elif index == 9:
            self.refresh_rack_warehouse_choices()

    def install_barcode_shortcut(self):
        action = QAction(self)
        action.setShortcut(QKeySequence("Ctrl+S"))
        action.triggered.connect(lambda: self.menu.setCurrentRow(1))
        self.addAction(action)

    def dashboard_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(QLabel("Dashboard"))
        cards = QHBoxLayout()
        self.dashboard_labels = {}
        for key, title in [
            ("articles", "Artikel"), ("warehouses", "Lager"),
            ("boxes", "Lagerplätze"), ("defect", "Defekte Geräte"),
            ("repairs", "In Reparatur"),
        ]:
            card = QLabel()
            card.setProperty("class", "card")
            card.setAlignment(Qt.AlignCenter)
            card.setMinimumHeight(100)
            cards.addWidget(card)
            self.dashboard_labels[key] = card
            card.setText(title + "\n0")
        layout.addLayout(cards)
        quick = QHBoxLayout()
        actions = [
            ("Artikel scannen", lambda: self.menu.setCurrentRow(1)),
            ("Artikel hinzufügen", self.add_article),
            ("Barcode programmieren", lambda: self.menu.setCurrentRow(6)),
            ("Inventur starten", lambda: self.menu.setCurrentRow(4)),
            ("Lagerverwaltung", lambda: self.menu.setCurrentRow(3)),
            ("Reparaturverwaltung", lambda: self.menu.setCurrentRow(5)),
        ]
        for text, handler in actions:
            button = QPushButton(text)
            button.clicked.connect(handler)
            quick.addWidget(button)
        layout.addLayout(quick)
        layout.addWidget(QLabel("Live-Kamera-Scanner"))
        self.dashboard_camera = LiveCameraScanner()
        self.dashboard_camera.barcode_scanned.connect(self.process_camera_scan)
        layout.addWidget(self.dashboard_camera)
        layout.addWidget(QLabel("Letzte Scans"))
        self.recent_scans = TablePage("", ["Datum", "Barcode", "Artikel", "Lagerort", "Scan-Art", "Aktion"])
        layout.addWidget(self.recent_scans)
        return page

    def refresh_dashboard(self):
        counts = {
            "articles": self.db.query("SELECT COUNT(*) c FROM articles")[0]["c"],
            "warehouses": self.db.query("SELECT COUNT(*) c FROM warehouses")[0]["c"],
            "boxes": self.db.query("SELECT COUNT(*) c FROM boxes")[0]["c"],
            "defect": self.db.query("SELECT COUNT(*) c FROM articles WHERE status='Defekt'")[0]["c"],
            "repairs": self.db.query("SELECT COUNT(*) c FROM articles WHERE status='In Reparatur'")[0]["c"],
        }
        names = {"articles": "Artikel", "warehouses": "Lager", "boxes": "Lagerplätze", "defect": "Defekte Geräte", "repairs": "In Reparatur"}
        for key, value in counts.items():
            self.dashboard_labels[key].setText(f"{names[key]}\n{value}")
        rows = self.db.query(
            """SELECT s.timestamp,s.barcode,COALESCE(a.name,''),COALESCE(b.name,''),s.scan_type,s.action
               FROM scans s LEFT JOIN articles a ON a.id=s.article_id LEFT JOIN boxes b ON b.id=s.box_id
               ORDER BY s.id DESC LIMIT 10"""
        )
        self.recent_scans.set_rows([tuple(row) for row in rows])

    def reader_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(QLabel("Lesemodus"))
        self.scan_input = QLineEdit()
        self.scan_input.setPlaceholderText("Barcode scannen oder eingeben")
        self.scan_input.returnPressed.connect(self.process_normal_scan)
        layout.addWidget(self.scan_input)
        self.reader_result = QTextEdit()
        self.reader_result.setReadOnly(True)
        layout.addWidget(self.reader_result)
        button = QPushButton("Anleitung / Dokument öffnen")
        button.clicked.connect(self.open_first_document)
        layout.addWidget(button)
        return page

    def process_normal_scan(self):
        code = self.scanner.process(self.scan_input.text())
        self.handle_barcode_scan(code, "Normaler Scan", prompt_unknown=True)

    def process_ipad_scan(self, code: str):
        code = self.scanner.process(code)
        self.handle_barcode_scan(code, "Normaler Scan", prompt_unknown=False)

    def process_camera_scan(self, code: str):
        code = self.scanner.process(code)
        self.handle_barcode_scan(code, "Kamera-Scan", prompt_unknown=False)
        if code:
            self.dashboard_camera.status.setText(f"Kamera-Scan erfasst: {code}")

    def handle_barcode_scan(self, code: str, scan_type: str, prompt_unknown: bool):
        if not code:
            return
        article = find_by_barcode(self.db, code)
        box = self.db.query("SELECT * FROM boxes WHERE barcode=?", (code,))
        rack = self.db.query("SELECT * FROM racks WHERE barcode=?", (code,))
        level = self.db.query("SELECT * FROM levels WHERE barcode=?", (code,))
        warehouse = self.db.query("SELECT * FROM warehouses WHERE barcode=?", (code,))
        box_id = article["box_id"] if article else (box[0]["id"] if box else None)
        self.db.execute(
            "INSERT INTO scans(timestamp,barcode,article_id,box_id,scan_type,action) VALUES(?,?,?,?,?,?)",
            (__import__("datetime").datetime.now().isoformat(timespec="seconds"), code,
             article["id"] if article else None, box_id, scan_type,
             "Artikel erkannt" if article else "Lagerplatz erkannt" if box or rack or level or warehouse else "Unbekannt"),
        )
        if article:
            status = article["status"]
            warning = "\n\nWARNUNG: DEFEKTES GERÄT!" if status == "Defekt" else ""
            warning += "\n\nWARNUNG: GERÄT IST IN REPARATUR!" if status == "In Reparatur" else ""
            text = (
                f"Artikel: {article['name']}\nHersteller: {article['manufacturer'] or '-'}\n"
                f"Modell: {article['model'] or '-'}\nKategorie: {article['category'] or '-'}\n"
                f"Anzahl: {article['quantity']}\nStatus: {status}\n"
                f"Lagerort: {warehouse_path(self.db, article['box_id']) or '-'}\n\n"
                f"Beschreibung:\n{article['description'] or '-'}\n\n"
                f"Technische Daten:\n{article['technical_data'] or '-'}{warning}"
            )
            modes = self.db.query("SELECT name,channel_count FROM dmx_modes WHERE article_id=?", (article["id"],))
            if modes:
                text += "\n\nDMX-Modi:\n" + "\n".join(f"- {m['name']} ({m['channel_count']} Kanäle)" for m in modes)
            repairs = self.db.query(
                "SELECT date,error_description,repair_status FROM repairs WHERE article_id=? ORDER BY date DESC",
                (article["id"],),
            )
            if repairs:
                text += "\n\nReparaturhistorie:\n" + "\n".join(
                    f"- {r['date']}: {r['error_description']} [{r['repair_status']}]" for r in repairs
                )
            self.reader_result.setPlainText(text)
        else:
            self.reader_result.setPlainText("Barcode nicht erkannt.\nÜber die Artikelverwaltung kann ein neuer Artikel angelegt werden.")
            if prompt_unknown:
                reply = QMessageBox.question(self, "Unbekannter Barcode", "Barcode unbekannt. Jetzt neuen Artikel anlegen?",
                                             QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    self.menu.setCurrentRow(2)
                    self.article_placeholder_barcode = code
                    self.add_article()
        if hasattr(self, "scan_test_result") and scan_type == "Normaler Scan":
            self.scan_test_result.setText(f"Letzter Scan: {code}")
        if hasattr(self, "ipad_status") and scan_type == "Normaler Scan":
            self.ipad_status.setText(f"Mobiler Scan empfangen: {code}")
        if hasattr(self, "recent_scans"):
            self.refresh_dashboard()
        if hasattr(self, "scans_table"):
            self.refresh_scans()

    def open_first_document(self):
        code = self.scanner.process(self.scan_input.text())
        article = find_by_barcode(self.db, code)
        if not article:
            QMessageBox.information(self, "Dokument", "Zuerst einen bekannten Artikel scannen.")
            return
        docs = self.db.query("SELECT * FROM documents WHERE article_id=? LIMIT 1", (article["id"],))
        if not docs:
            QMessageBox.information(self, "Anleitung", "Für diesen Artikel ist keine Anleitung hinterlegt.")
            return
        path = docs[0]["path"]
        if path:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                QMessageBox.warning(self, "Dokument", f"Datei konnte nicht geöffnet werden:\n{path}")
        elif docs[0]["external_url"]:
            import webbrowser
            webbrowser.open(docs[0]["external_url"])

    def article_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(QLabel("Artikelverwaltung"))
        toolbar = QHBoxLayout()
        self.article_search = QLineEdit()
        self.article_search.setPlaceholderText("Artikel, Modell, Kategorie oder Barcode suchen")
        self.article_search.textChanged.connect(self.refresh_articles)
        toolbar.addWidget(self.article_search)
        for text, handler in [
            ("Hinzufügen", self.add_article), ("Details", self.show_article_details),
            ("Bearbeiten", self.edit_article),
            ("Duplizieren", self.duplicate_article), ("Löschen", self.delete_article),
        ]:
            button = QPushButton(text); button.clicked.connect(handler); toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.article_table = TablePage("", ["ID", "Artikelname", "Hersteller", "Modell", "Kategorie", "Barcode", "Anzahl", "Lagerort", "Status"])
        self.article_table.table.doubleClicked.connect(
            lambda _index: self.show_article_details()
        )
        layout.addWidget(self.article_table)
        return page

    def refresh_articles(self):
        term = getattr(self, "article_search", None)
        text = term.text().strip() if term else ""
        sql = """SELECT a.id,a.name,COALESCE(a.manufacturer,''),COALESCE(a.model,''),COALESCE(a.category,''),
                        COALESCE(a.barcode,''),a.quantity,COALESCE(b.name,''),a.status
                 FROM articles a LEFT JOIN boxes b ON b.id=a.box_id"""
        params = ()
        if text:
            sql += " WHERE a.name LIKE ? OR a.manufacturer LIKE ? OR a.model LIKE ? OR a.category LIKE ? OR a.barcode LIKE ?"
            needle = f"%{text}%"; params = (needle,) * 5
        sql += " ORDER BY a.name"
        rows = self.db.query(sql, params)
        self.article_table.set_rows([tuple(row) for row in rows])

    def selected_article_row(self):
        row = self.article_table.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Artikel", "Bitte einen Artikel auswählen.")
            return None
        article_id = int(self.article_table.table.item(row, 0).text())
        return dict(self.db.query("SELECT * FROM articles WHERE id=?", (article_id,))[0])

    def show_article_details(self):
        article = self.selected_article_row()
        if article:
            ArticleDetailsDialog(self.db, article, self).exec()

    def add_article(self):
        dialog = ArticleDialog(self.db, parent=self)
        if hasattr(self, "article_placeholder_barcode"):
            dialog.barcode.setText(self.article_placeholder_barcode)
            del self.article_placeholder_barcode
        if dialog.exec():
            data = dialog.data()
            if not data["name"]:
                QMessageBox.warning(self, "Fehler", "Der Artikelname ist erforderlich.")
                return
            try:
                create_article(self.db, data)
            except Exception as exc:
                QMessageBox.critical(self, "Fehler", str(exc))
                return
            self.refresh_articles()

    def edit_article(self):
        article = self.selected_article_row()
        if not article:
            return
        dialog = ArticleDialog(self.db, article, self)
        if dialog.exec():
            try:
                update_article(self.db, article["id"], dialog.data())
            except Exception as exc:
                QMessageBox.critical(self, "Fehler", str(exc))
                return
            self.refresh_articles()

    def duplicate_article(self):
        article = self.selected_article_row()
        if article:
            duplicate_article(self.db, article["id"])
            self.refresh_articles()

    def delete_article(self):
        article = self.selected_article_row()
        if article and QMessageBox.question(self, "Löschen", f"Artikel '{article['name']}' wirklich löschen?",
                                             QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            delete_article(self.db, article["id"])
            self.refresh_articles()

    def warehouse_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(QLabel("Lagerversaltung"))
        toolbar = QHBoxLayout()
        self.warehouse_search = QLineEdit()
        self.warehouse_search.setPlaceholderText("Barcode oder Lagerplatz suchen")
        toolbar.addWidget(self.warehouse_search)
        for text, handler in [
            ("Lager anlegen", self.add_warehouse),
            ("Box anlegen", self.add_box),
            ("Artikel umlagern", self.move_article),
            ("Leere Boxen", self.show_empty_boxes),
        ]:
            button = QPushButton(text); button.clicked.connect(handler); toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.warehouse_table = TablePage("", ["Lager", "Regal", "Ebene", "Box", "Box-Barcode", "Artikel"])
        layout.addWidget(self.warehouse_table)
        self.refresh_warehouses()
        return page

    def refresh_warehouses(self):
        rows = self.db.query(
            """SELECT w.name,r.name,l.name,b.name,COALESCE(b.barcode,''),
                      GROUP_CONCAT(a.name, ', ')
               FROM boxes b
               JOIN levels l ON l.id=b.level_id JOIN racks r ON r.id=l.rack_id
               JOIN warehouses w ON w.id=r.warehouse_id
               LEFT JOIN articles a ON a.box_id=b.id
               GROUP BY b.id ORDER BY w.name,r.name,l.name,b.name"""
        )
        self.warehouse_table.set_rows([tuple(row) for row in rows])

    def add_warehouse(self):
        name, ok = QInputDialog.getText(self, "Lager anlegen", "Lagername:")
        if not ok or not name.strip():
            return
        cursor = self.db.execute("INSERT INTO warehouses(name,barcode) VALUES(?,?)", (name.strip(), generate_unique_code(self.db)))
        warehouse_id = cursor.lastrowid
        rack_name, ok = QInputDialog.getText(self, "Regal anlegen", "Name des ersten Regals:")
        if ok and rack_name.strip():
            rack_cursor = self.db.execute(
                "INSERT INTO racks(warehouse_id,name,barcode) VALUES(?,?,?)",
                (warehouse_id, rack_name.strip(), generate_unique_code(self.db)),
            )
            level_name, ok = QInputDialog.getText(self, "Ebene anlegen", "Name der ersten Ebene:")
            if ok and level_name.strip():
                level_cursor = self.db.execute(
                    "INSERT INTO levels(rack_id,name,barcode) VALUES(?,?,?)",
                    (rack_cursor.lastrowid, level_name.strip(), generate_unique_code(self.db)),
                )
                box_name, ok = QInputDialog.getText(self, "Box anlegen", "Name der ersten Box:")
                if ok and box_name.strip():
                    self.db.execute(
                        "INSERT INTO boxes(level_id,name,barcode) VALUES(?,?,?)",
                        (level_cursor.lastrowid, box_name.strip(), generate_unique_code(self.db)),
                    )
        self.refresh_warehouses()

    def add_box(self):
        levels = self.db.query(
            """SELECT l.id,w.name||' / '||r.name||' / '||l.name
               FROM levels l JOIN racks r ON r.id=l.rack_id JOIN warehouses w ON w.id=r.warehouse_id"""
        )
        if not levels:
            QMessageBox.information(self, "Lagerplatz", "Zuerst ein Lager anlegen.")
            return
        dialog = QDialog(self); dialog.setWindowTitle("Box anlegen"); form = QFormLayout(dialog)
        level_choice = QComboBox(); level_choice.addItems([row[1] for row in levels]); form.addRow("Ebene", level_choice)
        name = QLineEdit(); form.addRow("Boxname", name)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
        if dialog.exec() and name.text().strip():
            level_id = levels[level_choice.currentIndex()][0]
            self.db.execute(
                "INSERT INTO boxes(level_id,name,barcode) VALUES(?,?,?)",
                (level_id, name.text().strip(), generate_unique_code(self.db)),
            )
            self.refresh_warehouses()

    def move_article(self):
        articles = self.db.query("SELECT id,name FROM articles ORDER BY name")
        boxes = self.db.query(
            """SELECT b.id,w.name||' / '||r.name||' / '||l.name||' / '||b.name
               FROM boxes b JOIN levels l ON l.id=b.level_id JOIN racks r ON r.id=l.rack_id
               JOIN warehouses w ON w.id=r.warehouse_id"""
        )
        if not articles or not boxes:
            QMessageBox.information(self, "Umlagerung", "Artikel und Lagerplätze werden benötigt.")
            return
        dialog = QDialog(self); dialog.setWindowTitle("Artikel umlagern"); form = QFormLayout(dialog)
        article_choice = QComboBox(); article_choice.addItems([row["name"] for row in articles])
        box_choice = QComboBox(); box_choice.addItems([row[1] for row in boxes])
        form.addRow("Artikel", article_choice); form.addRow("Neue Box", box_choice)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
        if dialog.exec():
            article = articles[article_choice.currentIndex()]
            box = boxes[box_choice.currentIndex()]
            old = self.db.query("SELECT box_id FROM articles WHERE id=?", (article["id"],))
            old_box = old[0]["box_id"] if old else None
            self.db.execute("UPDATE articles SET box_id=? WHERE id=?", (box[0], article["id"]))
            self.db.execute(
                "INSERT INTO movements(article_id,from_box_id,to_box_id,quantity,action,timestamp) VALUES(?,?,?,?,?,?)",
                (article["id"], old_box, box[0], 1, "Umlagern", __import__("datetime").datetime.now().isoformat()),
            )
            self.refresh_warehouses()

    def show_empty_boxes(self):
        rows = self.db.query(
            """SELECT w.name,r.name,l.name,b.name,COALESCE(b.barcode,'')
               FROM boxes b JOIN levels l ON l.id=b.level_id JOIN racks r ON r.id=l.rack_id
               JOIN warehouses w ON w.id=r.warehouse_id
               LEFT JOIN articles a ON a.box_id=b.id
               WHERE a.id IS NULL"""
        )
        self.warehouse_table.set_rows([tuple(row) for row in rows])

    def rack_graphic_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(QLabel("Regal-Grafik"))
        toolbar = QHBoxLayout()
        self.rack_warehouse_choice = QComboBox()
        self.rack_warehouse_choice.setMinimumWidth(180)
        self.rack_warehouse_choice.currentIndexChanged.connect(self.refresh_rack_choices)
        self.rack_choice = QComboBox()
        self.rack_choice.setMinimumWidth(180)
        self.rack_choice.currentIndexChanged.connect(self.refresh_rack_graphic)
        toolbar.addWidget(QLabel("Lager"))
        toolbar.addWidget(self.rack_warehouse_choice)
        toolbar.addWidget(QLabel("Regal"))
        toolbar.addWidget(self.rack_choice)
        create_button = QPushButton("Regal anlegen")
        create_button.clicked.connect(self.create_rack)
        toolbar.addWidget(create_button)
        edit_button = QPushButton("Maße / Böden bearbeiten")
        edit_button.clicked.connect(self.edit_rack_layout)
        toolbar.addWidget(edit_button)
        transfer_button = QPushButton("Umlagern …")
        transfer_button.clicked.connect(self.move_rack_entity_dialog)
        toolbar.addWidget(transfer_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.rack_graphic = RackGraphic()
        self.rack_graphic.entityDropped.connect(self.move_rack_entity)
        layout.addWidget(self.rack_graphic, 1)
        self.refresh_rack_warehouse_choices()
        return page

    def refresh_rack_warehouse_choices(self):
        if not hasattr(self, "rack_warehouse_choice"):
            return
        current_id = self.rack_warehouse_choice.currentData()
        warehouses = self.db.query("SELECT id,name FROM warehouses ORDER BY name")
        self.rack_warehouse_choice.blockSignals(True)
        self.rack_warehouse_choice.clear()
        for warehouse in warehouses:
            self.rack_warehouse_choice.addItem(warehouse["name"], warehouse["id"])
        if current_id is not None:
            index = self.rack_warehouse_choice.findData(current_id)
            if index >= 0:
                self.rack_warehouse_choice.setCurrentIndex(index)
        self.rack_warehouse_choice.blockSignals(False)
        self.refresh_rack_choices()

    def refresh_rack_choices(self):
        if not hasattr(self, "rack_choice"):
            return
        warehouse_id = self.rack_warehouse_choice.currentData()
        current_id = self.rack_choice.currentData()
        racks = self.db.query(
            "SELECT id,name FROM racks WHERE warehouse_id=? ORDER BY name", (warehouse_id,)
        ) if warehouse_id is not None else []
        self.rack_choice.blockSignals(True)
        self.rack_choice.clear()
        for rack in racks:
            self.rack_choice.addItem(rack["name"], rack["id"])
        if current_id is not None:
            index = self.rack_choice.findData(current_id)
            if index >= 0:
                self.rack_choice.setCurrentIndex(index)
        self.rack_choice.blockSignals(False)
        self.refresh_rack_graphic()

    def refresh_rack_graphic(self):
        if not hasattr(self, "rack_graphic"):
            return
        rack_id = self.rack_choice.currentData()
        rack_rows = self.db.query("SELECT * FROM racks WHERE id=?", (rack_id,)) if rack_id is not None else []
        if not rack_rows:
            self.rack_graphic.set_layout(None, [])
            return
        rack = dict(rack_rows[0])
        levels = []
        for row in self.db.query(
            "SELECT id,name,height_cm FROM levels WHERE rack_id=? ORDER BY id", (rack_id,)
        ):
            level = dict(row)
            level["boxes"] = []
            levels.append(level)
        level_by_id = {level["id"]: level for level in levels}
        for row in self.db.query(
            """SELECT b.id,b.level_id,b.name,b.barcode
               FROM boxes b JOIN levels l ON l.id=b.level_id
               WHERE l.rack_id=? ORDER BY b.name""",
            (rack_id,),
        ):
            box = dict(row)
            box["articles"] = []
            level_by_id[box["level_id"]]["boxes"].append(box)
        box_by_id = {
            box["id"]: box
            for level in levels
            for box in level["boxes"]
        }
        for article in self.db.query(
            """SELECT a.id,a.name,a.box_id FROM articles a
               JOIN boxes b ON b.id=a.box_id
               JOIN levels l ON l.id=b.level_id
               WHERE l.rack_id=? ORDER BY a.name""",
            (rack_id,),
        ):
            box_by_id[article["box_id"]]["articles"].append(dict(article))
        self.rack_graphic.set_layout(rack, levels)

    def create_rack(self):
        warehouse_id = self.rack_warehouse_choice.currentData()
        if warehouse_id is None:
            QMessageBox.information(self, "Lager auswählen", "Lege zuerst ein Lager an oder wähle eines aus.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Regal anlegen")
        form = QFormLayout(dialog)
        name = QLineEdit()
        width = QSpinBox()
        width.setRange(10, 5000)
        width.setValue(120)
        height = QSpinBox()
        height.setRange(20, 10000)
        height.setValue(200)
        shelf_count = QSpinBox()
        shelf_count.setRange(1, 50)
        shelf_count.setValue(4)
        form.addRow("Regalname", name)
        form.addRow("Breite (cm)", width)
        form.addRow("Höhe (cm)", height)
        form.addRow("Anzahl Böden", shelf_count)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if not dialog.exec():
            return
        if not name.text().strip():
            QMessageBox.warning(self, "Regalname fehlt", "Bitte gib einen Namen ein.")
            return
        rack_id = self.db.execute(
            """INSERT INTO racks(warehouse_id,name,barcode,width_cm,height_cm)
               VALUES(?,?,?,?,?)""",
            (warehouse_id, name.text().strip(), generate_unique_code(self.db), width.value(), height.value()),
        ).lastrowid
        count = shelf_count.value()
        shelf_height, remainder = divmod(height.value(), count)
        for index in range(count):
            level_height = shelf_height + (1 if index < remainder else 0)
            self.db.execute(
                "INSERT INTO levels(rack_id,name,barcode,height_cm) VALUES(?,?,?,?)",
                (rack_id, f"Boden {index + 1}", generate_unique_code(self.db), level_height),
            )
        self.refresh_rack_choices()
        index = self.rack_choice.findData(rack_id)
        if index >= 0:
            self.rack_choice.setCurrentIndex(index)

    def edit_rack_layout(self):
        rack_id = self.rack_choice.currentData()
        rows = self.db.query("SELECT * FROM racks WHERE id=?", (rack_id,)) if rack_id is not None else []
        if not rows:
            QMessageBox.information(self, "Regal auswählen", "Wähle zuerst ein Regal aus.")
            return
        dialog = RackLayoutDialog(self.db, rows[0], self)
        if not dialog.exec():
            return
        self.db.execute(
            "UPDATE racks SET name=?,width_cm=?,height_cm=? WHERE id=?",
            (dialog.name.text().strip(), dialog.width.value(), dialog.height.value(), rack_id),
        )
        for level_id in dialog.deleted_level_ids:
            self.db.execute("DELETE FROM levels WHERE id=?", (level_id,))
        for level in dialog.levels():
            if level["id"] is None:
                self.db.execute(
                    "INSERT INTO levels(rack_id,name,barcode,height_cm) VALUES(?,?,?,?)",
                    (rack_id, level["name"], generate_unique_code(self.db), level["height_cm"]),
                )
            else:
                self.db.execute(
                    "UPDATE levels SET name=?,height_cm=? WHERE id=?",
                    (level["name"], level["height_cm"], level["id"]),
                )
        self.refresh_rack_choices()
        selected = self.rack_choice.findData(rack_id)
        if selected >= 0:
            self.rack_choice.setCurrentIndex(selected)

    def move_rack_entity(self, entity_type, entity_id, target_id):
        if entity_type == "box":
            box = self.db.query("SELECT level_id FROM boxes WHERE id=?", (entity_id,))
            if not box or box[0]["level_id"] == target_id:
                return
            self.db.execute("UPDATE boxes SET level_id=? WHERE id=?", (target_id, entity_id))
        elif entity_type == "article":
            article = self.db.query("SELECT box_id,quantity FROM articles WHERE id=?", (entity_id,))
            if not article or article[0]["box_id"] == target_id:
                return
            old_box_id = article[0]["box_id"]
            self.db.execute("UPDATE articles SET box_id=? WHERE id=?", (target_id, entity_id))
            self.db.execute(
                """INSERT INTO movements(article_id,from_box_id,to_box_id,quantity,action,timestamp)
                   VALUES(?,?,?,?,?,?)""",
                (
                    entity_id, old_box_id, target_id,
                    article[0]["quantity"], "Umlagern",
                    __import__("datetime").datetime.now().isoformat(),
                ),
            )
        self.refresh_rack_graphic()

    def move_rack_entity_dialog(self):
        warehouse_id = self.rack_warehouse_choice.currentData()
        if warehouse_id is None:
            QMessageBox.information(self, "Lager auswählen", "Wähle zuerst ein Lager aus.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Box oder Artikel umlagern")
        form = QFormLayout(dialog)
        entity_type = QComboBox()
        entity_type.addItem("Box", "box")
        entity_type.addItem("Artikel / Lampe", "article")
        source = QComboBox()
        target = QComboBox()
        form.addRow("Was soll umgelagert werden?", entity_type)
        form.addRow("Eintrag", source)
        form.addRow("Neuer Lagerort", target)

        def refresh_choices():
            source.clear()
            target.clear()
            if entity_type.currentData() == "box":
                sources = self.db.query(
                    """SELECT b.id,w.name||' / '||r.name||' / '||l.name||' / '||b.name AS label
                       FROM boxes b JOIN levels l ON l.id=b.level_id
                       JOIN racks r ON r.id=l.rack_id JOIN warehouses w ON w.id=r.warehouse_id
                       WHERE w.id=? ORDER BY r.name,l.name,b.name""",
                    (warehouse_id,),
                )
                destinations = self.db.query(
                    """SELECT l.id,w.name||' / '||r.name||' / '||l.name AS label
                       FROM levels l JOIN racks r ON r.id=l.rack_id
                       JOIN warehouses w ON w.id=r.warehouse_id
                       WHERE w.id=? ORDER BY r.name,l.name""",
                    (warehouse_id,),
                )
            else:
                sources = self.db.query(
                    """SELECT a.id,a.name||' — '||w.name||' / '||r.name||' / '||l.name||' / '||b.name AS label
                       FROM articles a JOIN boxes b ON b.id=a.box_id
                       JOIN levels l ON l.id=b.level_id JOIN racks r ON r.id=l.rack_id
                       JOIN warehouses w ON w.id=r.warehouse_id
                       WHERE w.id=? ORDER BY a.name""",
                    (warehouse_id,),
                )
                destinations = self.db.query(
                    """SELECT b.id,w.name||' / '||r.name||' / '||l.name||' / '||b.name AS label
                       FROM boxes b JOIN levels l ON l.id=b.level_id
                       JOIN racks r ON r.id=l.rack_id JOIN warehouses w ON w.id=r.warehouse_id
                       WHERE w.id=? ORDER BY r.name,l.name,b.name""",
                    (warehouse_id,),
                )
            for row in sources:
                source.addItem(row["label"], row["id"])
            for row in destinations:
                target.addItem(row["label"], row["id"])

        entity_type.currentIndexChanged.connect(refresh_choices)
        refresh_choices()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if not source.count() or not target.count():
            QMessageBox.information(self, "Keine Lagerplätze", "Für diese Aktion werden Boxen und Böden benötigt.")
            return
        if dialog.exec():
            self.move_rack_entity(entity_type.currentData(), source.currentData(), target.currentData())

    def inventory_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(QLabel("Inventur"))
        toolbar = QHBoxLayout()
        for text, handler in [
            ("Inventur starten", self.start_inventory),
            ("Artikel erfassen", self.capture_inventory_scan),
            ("Inventurbericht exportieren", self.export_inventory),
        ]:
            button = QPushButton(text); button.clicked.connect(handler); toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.inventory_input = QLineEdit()
        self.inventory_input.setPlaceholderText("Inventur-Barcode scannen")
        self.inventory_input.returnPressed.connect(self.capture_inventory_scan)
        layout.addWidget(self.inventory_input)
        self.inventory_table = TablePage("", ["Barcode", "Artikel", "Soll", "Ist", "Abweichung"])
        layout.addWidget(self.inventory_table)
        self.current_inventory_id = None
        return page

    def start_inventory(self):
        warehouses = self.db.query("SELECT id,name FROM warehouses")
        if not warehouses:
            QMessageBox.information(self, "Inventur", "Zuerst ein Lager anlegen.")
            return
        dialog = QDialog(self); dialog.setWindowTitle("Inventur starten"); form = QFormLayout(dialog)
        warehouse_choice = QComboBox(); warehouse_choice.addItems([row["name"] for row in warehouses])
        form.addRow("Lager", warehouse_choice)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
        if dialog.exec():
            warehouse = warehouses[warehouse_choice.currentIndex()]
            from datetime import datetime
            cursor = self.db.execute(
                "INSERT INTO inventories(name,warehouse_id,started_at) VALUES(?,?,?)",
                (f"Inventur {datetime.now():%Y-%m-%d %H:%M}", warehouse["id"], datetime.now().isoformat()),
            )
            self.current_inventory_id = cursor.lastrowid
            self.db.execute("DELETE FROM inventory_items WHERE inventory_id=?", (self.current_inventory_id,))
            for article in self.db.query(
                """SELECT a.* FROM articles a JOIN boxes b ON b.id=a.box_id
                   JOIN levels l ON l.id=b.level_id JOIN racks r ON r.id=l.rack_id
                   WHERE r.warehouse_id=?""",
                (warehouse["id"],),
            ):
                self.db.execute(
                    "INSERT INTO inventory_items(inventory_id,article_id,barcode,expected_quantity,scanned_quantity) VALUES(?,?,?,?,0)",
                    (self.current_inventory_id, article["id"], article["barcode"] or "", article["quantity"]),
                )
            self.refresh_inventory_table()

    def capture_inventory_scan(self):
        code = self.scanner.process(self.inventory_input.text())
        if not code:
            return
        if not self.current_inventory_id:
            QMessageBox.information(self, "Inventur", "Bitte zuerst eine Inventur starten.")
            return
        article = find_by_barcode(self.db, code)
        if article:
            row = self.db.query(
                "SELECT id,scanned_quantity FROM inventory_items WHERE inventory_id=? AND article_id=?",
                (self.current_inventory_id, article["id"]),
            )
            if row:
                self.db.execute(
                    "UPDATE inventory_items SET scanned_quantity=scanned_quantity+1 WHERE id=?", (row[0]["id"],)
                )
            else:
                self.db.execute(
                    "INSERT INTO inventory_items(inventory_id,article_id,barcode,expected_quantity,scanned_quantity) VALUES(?,?,?,?,1)",
                    (self.current_inventory_id, article["id"], code, 0),
                )
        else:
            self.db.execute(
                "INSERT INTO inventory_items(inventory_id,article_id,barcode,expected_quantity,scanned_quantity) VALUES(?,?,?,?,1)",
                (self.current_inventory_id, None, code, 0),
            )
        self.db.execute(
            "INSERT INTO scans(timestamp,barcode,article_id,scan_type,action) VALUES(?,?,?,?,?)",
            (__import__("datetime").datetime.now().isoformat(), code, article["id"] if article else None,
             "Inventur", "Inventur erfasst"),
        )
        self.inventory_input.clear()
        self.refresh_inventory_table()

    def refresh_inventory_table(self):
        if not self.current_inventory_id:
            return
        rows = self.db.query(
            """SELECT i.barcode,COALESCE(a.name,'Unbekannter Artikel'),i.expected_quantity,i.scanned_quantity,
                      i.scanned_quantity-i.expected_quantity
               FROM inventory_items i LEFT JOIN articles a ON a.id=i.article_id
               WHERE i.inventory_id=? ORDER BY i.barcode""",
            (self.current_inventory_id,),
        )
        self.inventory_table.set_rows([tuple(row) for row in rows])

    def export_inventory(self):
        if not self.current_inventory_id:
            QMessageBox.information(self, "Inventur", "Keine Inventur ausgewählt.")
            return
        rows = [dict(row) for row in self.db.query(
            """SELECT i.barcode,COALESCE(a.name,'Unbekannter Artikel') AS artikel,
                      i.expected_quantity AS soll,i.scanned_quantity AS ist,
                      i.scanned_quantity-i.expected_quantity AS abweichung
               FROM inventory_items i LEFT JOIN articles a ON a.id=i.article_id
               WHERE i.inventory_id=?""",
            (self.current_inventory_id,),
        )]
        destination, _ = QFileDialog.getSaveFileName(self, "Inventur exportieren", "inventur.csv", "CSV-Dateien (*.csv)")
        if destination:
            export_csv(destination, ["barcode", "artikel", "soll", "ist", "abweichung"], rows)
            QMessageBox.information(self, "Export", "Inventur-CSV wurde erstellt.")

    def repair_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(QLabel("Reparatur- und Defektverwaltung"))
        toolbar = QHBoxLayout()
        for text, handler in [
            ("Reparatur eintragen", self.add_repair),
            ("Status aktualisieren", self.update_article_status),
        ]:
            button = QPushButton(text); button.clicked.connect(handler); toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.repair_table = TablePage("", ["Datum", "Artikel", "Fehler", "Schadensart", "Maßnahmen", "Kosten", "Status", "Abschluss"])
        layout.addWidget(self.repair_table)
        self.refresh_repairs()
        return page

    def refresh_repairs(self):
        rows = self.db.query(
            """SELECT r.date,a.name,COALESCE(r.error_description,''),COALESCE(r.damage_type,''),
                      COALESCE(r.measures,''),COALESCE(r.costs,''),r.repair_status,
                      COALESCE(r.completion_date,'')
               FROM repairs r JOIN articles a ON a.id=r.article_id ORDER BY r.date DESC"""
        )
        self.repair_table.set_rows([tuple(row) for row in rows])

    def add_repair(self):
        articles = self.db.query("SELECT id,name FROM articles ORDER BY name")
        if not articles:
            QMessageBox.information(self, "Reparatur", "Zuerst einen Artikel anlegen.")
            return
        dialog = QDialog(self); dialog.setWindowTitle("Reparatur eintragen")
        dialog.resize(650, 600); form = QFormLayout(dialog)
        article_choice = QComboBox(); article_choice.addItems([row["name"] for row in articles])
        error = QTextEdit(); damage = QLineEdit(); measures = QTextEdit()
        parts = QTextEdit(); costs = QLineEdit("0"); status = QComboBox()
        status.addItems(["Offen", "In Bearbeitung", "Abgeschlossen"])
        notes = QTextEdit()
        form.addRow("Artikel", article_choice); form.addRow("Fehlerbeschreibung", error)
        form.addRow("Schadensart", damage); form.addRow("Reparaturmaßnahmen", measures)
        form.addRow("Ersatzteile", parts); form.addRow("Kosten", costs); form.addRow("Status", status)
        form.addRow("Interne Notizen", notes)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
        if dialog.exec():
            from datetime import datetime
            try:
                cost_value = float(costs.text().replace(",", "."))
            except ValueError:
                cost_value = 0.0
            article = articles[article_choice.currentIndex()]
            completion = datetime.now().isoformat(timespec="seconds") if status.currentText() == "Abgeschlossen" else None
            self.db.execute(
                """INSERT INTO repairs(article_id,date,error_description,damage_type,measures,spare_parts,costs,
                                      repair_status,completion_date,internal_notes)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (article["id"], datetime.now().isoformat(timespec="seconds"), error.toPlainText(),
                 damage.text(), measures.toPlainText(), parts.toPlainText(), cost_value,
                 status.currentText(), completion, notes.toPlainText()),
            )
            self.db.execute("UPDATE articles SET status='In Reparatur' WHERE id=?", (article["id"],))
            self.db.execute(
                "INSERT INTO scans(timestamp,barcode,article_id,scan_type,action) VALUES(?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"),
                 self.db.query("SELECT barcode FROM articles WHERE id=?", (article["id"],))[0]["barcode"] or "",
                 article["id"], "Reparatur", "Reparatur erfasst"),
            )
            self.refresh_repairs()

    def update_article_status(self):
        article = self.selected_article_row() if hasattr(self, "article_table") else None
        if not article:
            articles = self.db.query("SELECT id,name,status FROM articles ORDER BY name")
            if not articles:
                QMessageBox.information(self, "Status", "Keine Artikel vorhanden.")
                return
            dialog = QDialog(self); dialog.setWindowTitle("Artikelstatus ändern"); form = QFormLayout(dialog)
            choice = QComboBox(); choice.addItems([row["name"] for row in articles])
            status = QComboBox(); status.addItems(ARTICLE_STATUSES)
            form.addRow("Artikel", choice); form.addRow("Status", status)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
            if dialog.exec():
                row = articles[choice.currentIndex()]
                self.db.execute("UPDATE articles SET status=? WHERE id=?", (status.currentText(), row["id"]))
                self.refresh_repairs()
        elif article:
            status, ok = QInputDialog.getItem(self, "Status", "Neuer Status:", ARTICLE_STATUSES,
                                              ARTICLE_STATUSES.index(article["status"]), False)
            if ok:
                self.db.execute("UPDATE articles SET status=? WHERE id=?", (status, article["id"]))
                self.refresh_articles()

    def programming_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(QLabel("Barcode-Programmierung"))
        self.barcode_document_items = []
        self.barcode_preview_page = 0
        tabs = QTabWidget()
        designer = QWidget()
        designer_layout = QVBoxLayout(designer)
        splitter = QSplitter(Qt.Horizontal)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        form = QFormLayout()
        self.generated_code = QLineEdit()
        self.generated_code.setReadOnly(True)
        type_choice = QComboBox(); type_choice.addItems(["Code 128", "EAN-13", "QR"])
        self.programming_type = type_choice
        self.programming_type.currentTextChanged.connect(self.invalidate_generated_code)
        self.generated_name = QLineEdit()
        self.generated_name.setPlaceholderText("Gerätename, z. B. Cameo")
        self.barcode_scale = QSpinBox()
        self.barcode_scale.setRange(MIN_BARCODE_SCALE, MAX_BARCODE_SCALE)
        self.barcode_scale.setValue(100)
        self.barcode_scale.setSuffix(" %")
        self.barcode_scale.valueChanged.connect(self.update_generated_preview)
        self.barcode_copies = QSpinBox()
        self.barcode_copies.setRange(1, 200)
        self.barcode_copies.setValue(1)
        generate = QPushButton("Eindeutigen Barcode generieren")
        generate.clicked.connect(self.generate_programming_code)
        form.addRow("Typ", type_choice)
        form.addRow("Bezeichnung", self.generated_name)
        form.addRow("Barcode", self.generated_code)
        form.addRow("Skalierung", self.barcode_scale)
        form.addRow("Anzahl gleicher Codes", self.barcode_copies)
        form.addRow(generate)
        controls_layout.addLayout(form)
        self.barcode_preview = QLabel("Noch kein Barcode generiert")
        self.barcode_preview.setAlignment(Qt.AlignCenter)
        self.barcode_preview.setMinimumHeight(100)
        self.barcode_preview.setMinimumWidth(240)
        self.barcode_preview.setStyleSheet("QLabel { background: white; color: #202428; border-radius: 5px; }")
        controls_layout.addWidget(self.barcode_preview)

        actions = QVBoxLayout()
        add_current = QPushButton("Aktuellen Code zur PDF hinzufügen")
        add_current.clicked.connect(self.add_current_barcode_to_pdf)
        save_current = QPushButton("In Barcode-Liste speichern")
        save_current.clicked.connect(self.save_generated_barcode)
        download_current = QPushButton("Aktuellen Barcode als PNG herunterladen")
        download_current.clicked.connect(self.download_generated_barcode)
        assign = QPushButton("Barcode einem Artikel zuweisen")
        assign.clicked.connect(self.assign_generated_code)
        actions.addWidget(add_current)
        actions.addWidget(save_current)
        actions.addWidget(download_current)
        actions.addWidget(assign)
        controls_layout.addLayout(actions)

        controls_layout.addWidget(QLabel("Inhalt des PDF-Dokuments"))
        self.barcode_document_table = QTableWidget()
        self.barcode_document_table.setColumnCount(5)
        self.barcode_document_table.setHorizontalHeaderLabels(
            ["Bezeichnung", "Typ", "Barcode", "Skalierung", "Position"]
        )
        self.barcode_document_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.barcode_document_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.barcode_document_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.barcode_document_table.horizontalHeader().setStretchLastSection(True)
        controls_layout.addWidget(self.barcode_document_table, 1)
        document_scale = QHBoxLayout()
        document_scale.addWidget(QLabel("Größe der Auswahl"))
        self.barcode_document_scale = QSpinBox()
        self.barcode_document_scale.setRange(MIN_BARCODE_SCALE, MAX_BARCODE_SCALE)
        self.barcode_document_scale.setValue(100)
        self.barcode_document_scale.setSuffix(" %")
        document_scale.addWidget(self.barcode_document_scale)
        apply_document_scale = QPushButton("Auf Auswahl anwenden")
        apply_document_scale.clicked.connect(self.apply_document_scale_to_selection)
        document_scale.addWidget(apply_document_scale)
        controls_layout.addLayout(document_scale)
        document_actions = QHBoxLayout()
        remove_items = QPushButton("Aus PDF entfernen")
        remove_items.clicked.connect(self.remove_barcode_document_items)
        clear_items = QPushButton("PDF leeren")
        clear_items.clicked.connect(self.clear_barcode_document)
        create_pdf = QPushButton("PDF erstellen …")
        create_pdf.clicked.connect(self.export_barcode_document)
        document_actions.addWidget(remove_items)
        document_actions.addWidget(clear_items)
        document_actions.addWidget(create_pdf)
        controls_layout.addLayout(document_actions)
        splitter.addWidget(controls)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        page_navigation = QHBoxLayout()
        self.barcode_previous_page = QPushButton("‹ Zurück")
        self.barcode_previous_page.clicked.connect(self.previous_barcode_page)
        self.barcode_page_label = QLabel("Seite 1 von 1")
        self.barcode_page_label.setAlignment(Qt.AlignCenter)
        self.barcode_next_page = QPushButton("Weiter ›")
        self.barcode_next_page.clicked.connect(self.next_barcode_page)
        page_navigation.addWidget(self.barcode_previous_page)
        page_navigation.addWidget(self.barcode_page_label, 1)
        page_navigation.addWidget(self.barcode_next_page)
        preview_layout.addLayout(page_navigation)
        preview_layout.addWidget(
            QLabel(
                "Barcode ziehen zum Verschieben; am Eckgriff skalieren. Name und Wert skalieren mit."
            )
        )
        self.barcode_document_preview = BarcodeDocumentPreview()
        self.barcode_document_preview.itemScaleChanged.connect(self.update_document_item_scale)
        self.barcode_document_preview.itemPositionChanged.connect(
            self.update_document_item_position
        )
        preview_layout.addWidget(self.barcode_document_preview, 1)
        splitter.addWidget(preview_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        designer_layout.addWidget(splitter)
        tabs.addTab(designer, "PDF gestalten")

        library = QWidget()
        library_layout = QVBoxLayout(library)
        library_toolbar = QHBoxLayout()
        refresh_library = QPushButton("Liste aktualisieren")
        refresh_library.clicked.connect(self.refresh_barcode_library)
        add_library_code = QPushButton("Ausgewählte Codes zur PDF hinzufügen")
        add_library_code.clicked.connect(self.add_selected_barcode_to_pdf)
        download_library_code = QPushButton("PNG herunterladen")
        download_library_code.clicked.connect(self.download_selected_barcode)
        configure_library_code = QPushButton("Konfigurieren")
        configure_library_code.clicked.connect(self.configure_selected_barcode)
        delete_library_code = QPushButton("Eigenen Code löschen")
        delete_library_code.clicked.connect(self.delete_selected_barcode)
        for button in (refresh_library, add_library_code, download_library_code,
                       configure_library_code, delete_library_code):
            library_toolbar.addWidget(button)
        library_layout.addLayout(library_toolbar)
        self.barcode_library_table = QTableWidget()
        self.barcode_library_table.setColumnCount(5)
        self.barcode_library_table.setHorizontalHeaderLabels(
            ["Bezeichnung", "Barcode", "Typ", "Skalierung", "Zuordnung"]
        )
        self.barcode_library_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.barcode_library_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.barcode_library_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.barcode_library_table.horizontalHeader().setStretchLastSection(True)
        self.barcode_library_table.doubleClicked.connect(
            lambda _index: self.add_selected_barcode_to_pdf()
        )
        library_layout.addWidget(self.barcode_library_table)
        tabs.addTab(library, "Barcode-Liste")
        tabs.currentChanged.connect(
            lambda index: self.refresh_barcode_library() if index == 1 else None
        )
        layout.addWidget(tabs)
        self.refresh_barcode_document()
        self.refresh_barcode_library()
        return page

    def generate_programming_code(self):
        try:
            code_type = self.programming_type.currentText()
            code = generate_unique_code(self.db, code_type)
            self.generated_code.setText(code)
            self.update_generated_preview()
        except Exception as exc:
            QMessageBox.critical(self, "Barcode-Generator", f"Barcode konnte nicht erstellt werden:\n{exc}")

    def invalidate_generated_code(self):
        self.generated_code.clear()
        self.barcode_preview.setPixmap(QPixmap())
        self.barcode_preview.setText("Barcode-Typ geändert. Bitte neu generieren.")

    def update_generated_preview(self):
        code = self.generated_code.text().strip()
        if not code:
            return
        try:
            image = QPixmap()
            image.loadFromData(
                render_barcode_png(
                    code, self.programming_type.currentText(), self.barcode_scale.value()
                ),
                "PNG",
            )
            self.barcode_preview.setText("")
            self.barcode_preview.setPixmap(
                image.scaled(270, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        except Exception as exc:
            QMessageBox.critical(self, "Barcode-Vorschau", f"Barcode konnte nicht angezeigt werden:\n{exc}")

    def add_barcode_document_item(self, item, copies=1):
        self.barcode_document_items.extend(dict(item) for _ in range(copies))
        self.barcode_preview_page = max(0, (len(self.barcode_document_items) - 1) // LABELS_PER_PAGE)
        self.refresh_barcode_document()

    def add_current_barcode_to_pdf(self):
        code = self.generated_code.text().strip()
        if not code:
            QMessageBox.information(self, "PDF gestalten", "Zuerst einen Barcode generieren.")
            return
        self.add_barcode_document_item(
            {
                "name": self.generated_name.text().strip(),
                "barcode": code,
                "code_type": self.programming_type.currentText(),
                "scale": self.barcode_scale.value(),
            },
            self.barcode_copies.value(),
        )

    def refresh_barcode_document(self):
        if not hasattr(self, "barcode_document_table"):
            return
        items = self.barcode_document_items
        self.barcode_document_table.setRowCount(len(items))
        for row, item in enumerate(items):
            values = (
                item.get("name", ""), item.get("code_type", ""),
                item.get("barcode", ""), f"{item.get('scale', 100)} %",
                f"{item.get('offset_x', 0):.0f}, {item.get('offset_y', 0):.0f} pt",
            )
            for column, value in enumerate(values):
                self.barcode_document_table.setItem(row, column, QTableWidgetItem(str(value)))
        page_count = max(1, (len(items) + LABELS_PER_PAGE - 1) // LABELS_PER_PAGE)
        self.barcode_preview_page = min(self.barcode_preview_page, page_count - 1)
        self.barcode_page_label.setText(
            f"Seite {self.barcode_preview_page + 1} von {page_count}"
        )
        self.barcode_previous_page.setEnabled(self.barcode_preview_page > 0)
        self.barcode_next_page.setEnabled(self.barcode_preview_page + 1 < page_count)
        self.barcode_document_preview.set_document_page(items, self.barcode_preview_page)

    def update_document_item_scale(self, item_index, scale):
        if 0 <= item_index < len(self.barcode_document_items):
            self.barcode_document_items[item_index]["scale"] = scale
            self.refresh_barcode_document()

    def apply_document_scale_to_selection(self):
        rows = {
            index.row()
            for index in self.barcode_document_table.selectionModel().selectedRows()
        }
        if not rows:
            QMessageBox.information(
                self, "Skalierung", "Wähle zuerst ein oder mehrere Etiketten im PDF-Inhalt aus."
            )
            return
        scale = self.barcode_document_scale.value()
        for row in rows:
            self.barcode_document_items[row]["scale"] = scale
        self.refresh_barcode_document()

    def update_document_item_position(self, item_index, offset_x, offset_y):
        if 0 <= item_index < len(self.barcode_document_items):
            self.barcode_document_items[item_index]["offset_x"] = offset_x
            self.barcode_document_items[item_index]["offset_y"] = offset_y
            self.refresh_barcode_document()

    def remove_barcode_document_items(self):
        rows = sorted(
            {index.row() for index in self.barcode_document_table.selectionModel().selectedRows()},
            reverse=True,
        )
        for row in rows:
            del self.barcode_document_items[row]
        self.refresh_barcode_document()

    def clear_barcode_document(self):
        self.barcode_document_items.clear()
        self.barcode_preview_page = 0
        self.refresh_barcode_document()

    def previous_barcode_page(self):
        self.barcode_preview_page = max(0, self.barcode_preview_page - 1)
        self.refresh_barcode_document()

    def next_barcode_page(self):
        page_count = max(1, (len(self.barcode_document_items) + LABELS_PER_PAGE - 1) // LABELS_PER_PAGE)
        self.barcode_preview_page = min(page_count - 1, self.barcode_preview_page + 1)
        self.refresh_barcode_document()

    def export_barcode_document(self):
        if not self.barcode_document_items:
            QMessageBox.information(self, "PDF erstellen", "Füge zuerst Barcodes zum PDF hinzu.")
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, "Barcode-PDF speichern", "barcodes.pdf", "PDF-Dateien (*.pdf)"
        )
        if destination:
            try:
                create_label_pdf(destination, self.barcode_document_items)
                QMessageBox.information(
                    self, "PDF erstellt",
                    f"{len(self.barcode_document_items)} Etiketten wurden in {destination} gespeichert.",
                )
            except Exception as exc:
                QMessageBox.critical(self, "PDF-Export", f"PDF konnte nicht erstellt werden:\n{exc}")

    def save_generated_barcode(self):
        code = self.generated_code.text().strip()
        if not code:
            QMessageBox.information(self, "Barcode-Liste", "Zuerst einen Barcode generieren.")
            return
        if self.db.query("SELECT 1 FROM barcodes WHERE code=?", (code,)):
            QMessageBox.information(self, "Barcode-Liste", "Dieser Barcode ist bereits in der Liste.")
            self.refresh_barcode_library()
            return
        try:
            self.db.execute(
                """INSERT INTO barcodes(code,code_type,entity_type,label,scale)
                   VALUES(?,?,'custom',?,?)""",
                (
                    code, self.programming_type.currentText(),
                    self.generated_name.text().strip() or None, self.barcode_scale.value(),
                ),
            )
            self.refresh_barcode_library()
        except Exception as exc:
            QMessageBox.critical(self, "Barcode speichern", f"Barcode konnte nicht gespeichert werden:\n{exc}")

    def download_generated_barcode(self):
        code = self.generated_code.text().strip()
        if not code:
            QMessageBox.information(self, "PNG herunterladen", "Zuerst einen Barcode generieren.")
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, "Barcode als PNG speichern", "barcode.png", "PNG-Bilder (*.png)"
        )
        if destination:
            try:
                Path(destination).write_bytes(
                    render_barcode_png(
                        code, self.programming_type.currentText(), self.barcode_scale.value()
                    )
                )
                QMessageBox.information(self, "PNG gespeichert", f"Barcode wurde gespeichert:\n{destination}")
            except Exception as exc:
                QMessageBox.critical(self, "PNG-Export", f"PNG konnte nicht gespeichert werden:\n{exc}")

    def assign_generated_code(self):
        code = self.generated_code.text().strip()
        if not code:
            QMessageBox.information(self, "Barcode", "Zuerst einen Barcode generieren.")
            return
        articles = self.db.query("SELECT id,name FROM articles ORDER BY name")
        if not articles:
            QMessageBox.information(self, "Barcode", "Zuerst einen Artikel anlegen.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Barcode zuweisen")
        form = QFormLayout(dialog)
        article_choice = QComboBox()
        for article in articles:
            article_choice.addItem(article["name"], article["id"])
        form.addRow("Artikel", article_choice)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if not dialog.exec():
            return
        article_id = article_choice.currentData()
        article = next(row for row in articles if row["id"] == article_id)
        existing = self.db.query("SELECT id,article_id FROM barcodes WHERE code=?", (code,))
        if self.db.query("SELECT 1 FROM articles WHERE barcode=? AND id<>?", (code, article_id)) or \
           (existing and existing[0]["article_id"] not in (None, article_id)):
            QMessageBox.critical(self, "Barcode", "Dieser Barcode ist bereits zugewiesen.")
            return
        self.db.execute("UPDATE articles SET barcode=? WHERE id=?", (code, article_id))
        if existing:
            self.db.execute(
                """UPDATE barcodes SET code_type=?,article_id=?,entity_type='article',
                   entity_id=?,label=?,scale=? WHERE id=?""",
                (
                    self.programming_type.currentText(), article_id, article_id,
                    article["name"], self.barcode_scale.value(), existing[0]["id"],
                ),
            )
        else:
            self.db.execute(
                """INSERT INTO barcodes(code,code_type,article_id,entity_type,entity_id,label,scale)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    code, self.programming_type.currentText(), article_id, "article", article_id,
                    article["name"], self.barcode_scale.value(),
                ),
            )
        self.refresh_barcode_library()
        QMessageBox.information(self, "Barcode", "Barcode wurde zugewiesen.")

    def refresh_barcode_library(self):
        if not hasattr(self, "barcode_library_table"):
            return
        rows = self.db.query(
            """SELECT b.id,b.code,b.code_type,b.scale,b.label,b.entity_type,b.article_id,
                      COALESCE(a.name,'') AS article_name
               FROM barcodes b LEFT JOIN articles a ON a.id=b.article_id
               ORDER BY b.id DESC"""
        )
        self.barcode_library_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            label = row["label"] or row["article_name"] or ""
            if label == row["code"]:
                label = ""
            relation = row["article_name"] or (
                "Eigener Barcode" if row["entity_type"] == "custom" else ""
            )
            values = (label, row["code"], row["code_type"], f"{row['scale']} %", relation)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value) if value else ("—" if column == 0 else ""))
                if column == 0:
                    item.setData(Qt.UserRole, row["id"])
                self.barcode_library_table.setItem(index, column, item)

    def selected_barcode_record(self):
        row = self.barcode_library_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Barcode-Liste", "Wähle zuerst einen Barcode aus.")
            return None
        barcode_id = self.barcode_library_table.item(row, 0).data(Qt.UserRole)
        rows = self.db.query("SELECT * FROM barcodes WHERE id=?", (barcode_id,))
        if not rows:
            QMessageBox.warning(self, "Barcode-Liste", "Der ausgewählte Eintrag ist nicht mehr vorhanden.")
            self.refresh_barcode_library()
            return None
        return rows[0]

    def add_selected_barcode_to_pdf(self):
        rows = sorted({
            index.row()
            for index in self.barcode_library_table.selectionModel().selectedRows()
        })
        if not rows:
            QMessageBox.information(
                self, "Barcode-Liste", "Wähle zuerst einen oder mehrere Barcodes aus."
            )
            return
        items = []
        copies = self.barcode_copies.value()
        for row in rows:
            barcode_id = self.barcode_library_table.item(row, 0).data(Qt.UserRole)
            records = self.db.query("SELECT * FROM barcodes WHERE id=?", (barcode_id,))
            if not records:
                QMessageBox.warning(
                    self, "Barcode-Liste",
                    "Ein ausgewählter Barcode ist nicht mehr vorhanden. Die Liste wird aktualisiert.",
                )
                self.refresh_barcode_library()
                return
            record = records[0]
            item = {
                "name": record["label"] if record["label"] != record["code"] else "",
                "barcode": record["code"],
                "code_type": record["code_type"],
                "scale": record["scale"],
            }
            items.extend(dict(item) for _ in range(copies))
        self.barcode_document_items.extend(items)
        self.barcode_preview_page = max(
            0, (len(self.barcode_document_items) - 1) // LABELS_PER_PAGE
        )
        self.refresh_barcode_document()

    def download_selected_barcode(self):
        record = self.selected_barcode_record()
        if not record:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, "Barcode als PNG speichern", "barcode.png", "PNG-Bilder (*.png)"
        )
        if destination:
            try:
                Path(destination).write_bytes(
                    render_barcode_png(record["code"], record["code_type"], record["scale"])
                )
                QMessageBox.information(self, "PNG gespeichert", f"Barcode wurde gespeichert:\n{destination}")
            except Exception as exc:
                QMessageBox.critical(self, "PNG-Export", f"PNG konnte nicht gespeichert werden:\n{exc}")

    def configure_selected_barcode(self):
        record = self.selected_barcode_record()
        if not record:
            return
        dialog = BarcodeConfigDialog(record, self)
        if not dialog.exec():
            return
        self.db.execute(
            "UPDATE barcodes SET label=?,code_type=?,scale=? WHERE id=?",
            (
                dialog.label.text().strip() or None,
                dialog.code_type.currentText(), dialog.scale.value(), record["id"],
            ),
        )
        self.refresh_barcode_library()

    def delete_selected_barcode(self):
        record = self.selected_barcode_record()
        if not record:
            return
        if record["article_id"] is not None or record["entity_type"] != "custom":
            QMessageBox.information(
                self, "Barcode löschen",
                "Zugeordnete Artikel-Barcodes können hier nicht gelöscht werden. Entferne sie beim Artikel.",
            )
            return
        if QMessageBox.question(
            self, "Eigenen Barcode löschen",
            f"Barcode {record['code']} wirklich aus der Liste löschen?",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            self.db.execute("DELETE FROM barcodes WHERE id=?", (record["id"],))
            self.refresh_barcode_library()

    def scans_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(QLabel("Scan-Verlauf"))
        toolbar = QHBoxLayout()
        self.scan_search = QLineEdit()
        self.scan_search.setPlaceholderText("Barcode oder Artikel filtern")
        self.scan_search.textChanged.connect(self.refresh_scans)
        toolbar.addWidget(self.scan_search)
        export = QPushButton("CSV exportieren"); export.clicked.connect(self.export_scans); toolbar.addWidget(export)
        layout.addLayout(toolbar)
        self.scans_table = TablePage("", ["Datum", "Barcode", "Artikel", "Lagerort", "Scan-Art", "Aktion"])
        layout.addWidget(self.scans_table)
        self.refresh_scans()
        return page

    def refresh_scans(self):
        term = getattr(self, "scan_search", None)
        text = term.text().strip() if term else ""
        sql = """SELECT s.timestamp,s.barcode,COALESCE(a.name,''),COALESCE(b.name,''),s.scan_type,s.action
                 FROM scans s LEFT JOIN articles a ON a.id=s.article_id LEFT JOIN boxes b ON b.id=s.box_id"""
        params = ()
        if text:
            sql += " WHERE s.barcode LIKE ? OR a.name LIKE ? OR s.action LIKE ?"
            needle = f"%{text}%"; params = (needle, needle, needle)
        sql += " ORDER BY s.id DESC"
        rows = self.db.query(sql, params)
        self.scans_table.set_rows([tuple(row) for row in rows])

    def export_scans(self):
        rows = [dict(row) for row in self.db.query(
            """SELECT s.timestamp AS datum,s.barcode,COALESCE(a.name,'') AS artikel,
                      COALESCE(b.name,'') AS lagerort,s.scan_type AS scan_art,s.action
               FROM scans s LEFT JOIN articles a ON a.id=s.article_id LEFT JOIN boxes b ON b.id=s.box_id
               ORDER BY s.id DESC"""
        )]
        destination, _ = QFileDialog.getSaveFileName(self, "Scan-Verlauf exportieren", "scans.csv", "CSV-Dateien (*.csv)")
        if destination:
            export_csv(destination, ["datum", "barcode", "artikel", "lagerort", "scan_art", "action"], rows)

    def settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(QLabel("Einstellungen und Datensicherung"))
        tabs = QTabWidget()
        scanner_tab = QWidget()
        scanner_layout = QVBoxLayout(scanner_tab)
        form = QFormLayout()
        self.scanner_device = QComboBox()
        self.scanner_device.addItem("HID-Tastatur / automatisch", "default")
        selected = self.db.query("SELECT value FROM settings WHERE key='scanner_device'")
        if selected and selected[0]["value"] != "default":
            self.scanner_device.addItem(selected[0]["value"], selected[0]["value"])
            self.scanner_device.setCurrentIndex(1)
        detect = QPushButton("Verfügbare Scanner-Geräte suchen")
        detect.clicked.connect(self.detect_scanner_devices)
        form.addRow("Bevorzugtes Gerät", self.scanner_device)
        form.addRow(detect)
        self.prefix = QLineEdit(self.read_setting("scanner_prefix"))
        self.suffix = QLineEdit(self.read_setting("scanner_suffix"))
        form.addRow("Scanner-Präfix", self.prefix)
        form.addRow("Scanner-Suffix", self.suffix)
        scanner_layout.addLayout(form)
        scanner_info = QLabel(
            "USB- und Bluetooth-HID-Scanner senden Barcodes wie eine Tastatur. "
            "Das Betriebssystem zeigt diese Eingaben nicht immer zuverlässig dem einzelnen Gerät zu; "
            "die Auswahl merkt das bevorzugte Gerät, die Erfassung erfolgt im fokussierten Scan-Feld."
        )
        scanner_info.setWordWrap(True)
        scanner_layout.addWidget(scanner_info)
        self.scanner_test_input = QLineEdit()
        self.scanner_test_input.setPlaceholderText("Scanner-Test: hier scannen und Enter drücken")
        self.scan_test_result = QLabel("Noch kein Testscan.")
        self.scanner_test_input.returnPressed.connect(self.test_scanner_input)
        scanner_layout.addWidget(self.scanner_test_input)
        scanner_layout.addWidget(self.scan_test_result)
        save = QPushButton("Scanner-Einstellungen speichern")
        save.clicked.connect(self.save_scanner_settings)
        scanner_layout.addWidget(save)
        scanner_layout.addStretch()
        tabs.addTab(scanner_tab, "Scanner auswählen")

        ipad_tab = QWidget()
        ipad_layout = QVBoxLayout(ipad_tab)
        ipad_info = QLabel(
            "Koppeln Sie ein iPad oder Android-Gerät im selben WLAN. Die mobile Seite kann Barcodes mit der "
            "Live-Kamera oder als Foto aufnehmen und lokal auf diesem Gerät auswerten. Für Live-Video muss das "
            "Geräte-Zertifikat einmalig auf dem Mobilgerät installiert werden. Alternativ können Sie einen "
            "Bluetooth-HID-Scanner mit dem Mobilgerät verbinden."
        )
        ipad_info.setWordWrap(True)
        self.ipad_status = QLabel("Mobil-Scanner ist nicht aktiv.")
        self.ipad_url = QLineEdit()
        self.ipad_url.setReadOnly(True)
        self.ipad_url.setPlaceholderText("Kopplung starten, um eine Adresse zu erhalten")
        self.ipad_https_url = QLineEdit()
        self.ipad_https_url.setReadOnly(True)
        self.ipad_https_url.setPlaceholderText("Sichere Live-Kamera-Adresse")
        self.ipad_certificate_url = QLineEdit()
        self.ipad_certificate_url.setReadOnly(True)
        self.ipad_certificate_url.setPlaceholderText("Adresse zum einmaligen Zertifikat-Download")
        ipad_buttons = QHBoxLayout()
        self.ipad_start_button = QPushButton("iPad / Android koppeln")
        self.ipad_start_button.clicked.connect(self.start_ipad_scanner)
        self.ipad_stop_button = QPushButton("Kopplung beenden")
        self.ipad_stop_button.clicked.connect(self.stop_ipad_scanner)
        self.ipad_stop_button.setEnabled(False)
        copy_url = QPushButton("Adresse kopieren")
        copy_url.clicked.connect(lambda: QApplication.clipboard().setText(self.ipad_url.text()))
        qr_button = QPushButton("Kopplungs-QR anzeigen")
        qr_button.clicked.connect(self.show_ipad_qr)
        ipad_buttons.addWidget(self.ipad_start_button)
        ipad_buttons.addWidget(self.ipad_stop_button)
        ipad_layout.addWidget(ipad_info)
        ipad_layout.addWidget(self.ipad_status)
        ipad_layout.addWidget(QLabel("Diese Adresse im Browser des Mobilgeräts öffnen:"))
        ipad_layout.addWidget(self.ipad_url)
        ipad_layout.addWidget(QLabel("HTTPS-Adresse für Live-Kamera:"))
        ipad_layout.addWidget(self.ipad_https_url)
        ipad_layout.addWidget(QLabel("Zertifikat einmalig auf dem iPad installieren:"))
        ipad_layout.addWidget(self.ipad_certificate_url)
        ipad_layout.addLayout(ipad_buttons)
        ipad_layout.addWidget(copy_url)
        ipad_layout.addWidget(qr_button)
        ipad_layout.addWidget(QLabel(
            "Beide Geräte müssen im selben WLAN sein. Zertifikat im Mobilbrowser herunterladen, in iPadOS unter "
            "Einstellungen → Profil geladen installieren und anschließend unter Allgemein → Info → "
            "Zertifikatsvertrauenseinstellungen vollständig aktivieren. Danach die HTTPS-Adresse öffnen. "
            "Die Kopplungsadresse enthält ein Sitzungstoken; nur im vertrauenswürdigen WLAN verwenden."
        ))
        ipad_layout.addStretch()
        tabs.addTab(ipad_tab, "iPad / Android")

        cloud_tab = QWidget()
        cloud_layout = QVBoxLayout(cloud_tab)
        cloud_form = QFormLayout()
        self.cloud_project_url = QLineEdit(self.read_setting("supabase_url"))
        self.cloud_project_url.setPlaceholderText("https://<projekt-id>.supabase.co")
        self.cloud_public_key = QLineEdit(self.read_setting("supabase_public_key"))
        self.cloud_public_key.setPlaceholderText("Supabase Publishable-/anon-Key")
        self.cloud_shared_key = QLineEdit()
        self.cloud_shared_key.setEchoMode(QLineEdit.Password)
        self.cloud_shared_key.setPlaceholderText("Gemeinsamer Organisationsschlüssel")
        protected_key = self.read_setting("cloud_shared_key_dpapi")
        if protected_key:
            try:
                self.cloud_shared_key.setText(unprotect_secret(protected_key))
            except Exception:
                self.cloud_shared_key.setPlaceholderText("Gespeicherter Schlüssel kann nicht entschlüsselt werden")
        cloud_form.addRow("Supabase-Projekt-URL", self.cloud_project_url)
        cloud_form.addRow("Öffentlicher Projekt-Key", self.cloud_public_key)
        cloud_form.addRow("Organisationsschlüssel", self.cloud_shared_key)
        cloud_layout.addLayout(cloud_form)

        key_buttons = QHBoxLayout()
        generate_key = QPushButton("Schlüssel erzeugen")
        generate_key.clicked.connect(self.generate_cloud_shared_key)
        copy_key = QPushButton("Schlüssel kopieren")
        copy_key.clicked.connect(lambda: QApplication.clipboard().setText(self.cloud_shared_key.text()))
        key_buttons.addWidget(generate_key)
        key_buttons.addWidget(copy_key)
        cloud_layout.addLayout(key_buttons)

        config_file_buttons = QHBoxLayout()
        export_config = QPushButton("Verbindung verschlüsselt exportieren")
        export_config.clicked.connect(self.export_cloud_settings_file)
        import_config = QPushButton("Verbindung aus Datei importieren")
        import_config.clicked.connect(self.import_cloud_settings_file)
        config_file_buttons.addWidget(export_config)
        config_file_buttons.addWidget(import_config)
        cloud_layout.addLayout(config_file_buttons)

        save_cloud = QPushButton("Cloud-Einstellungen speichern")
        save_cloud.clicked.connect(lambda: self.save_cloud_settings())
        self.sync_now_button = QPushButton("Jetzt synchronisieren")
        self.sync_now_button.clicked.connect(lambda: self.start_cloud_sync())
        cloud_layout.addWidget(save_cloud)
        cloud_layout.addWidget(self.sync_now_button)
        self.cloud_sync_status = QLabel("Cloud-Synchronisierung ist noch nicht eingerichtet.")
        self.cloud_sync_status.setWordWrap(True)
        cloud_layout.addWidget(self.cloud_sync_status)
        cloud_note = QLabel(
            "Kein Benutzer-Login: alle PCs verwenden denselben Schlüssel. Der Schlüssel wird auf diesem PC "
            f"mit {secret_store_name()} geschützt. Änderungen werden bei aktiver Cloud-Verbindung automatisch "
            "synchronisiert; ohne Internet bleiben sie lokal und werden später abgeglichen. Bei gleichzeitigen "
            "Änderungen fragt die App, welche Version gelten soll. Fotos und lokale Dokumentdateien werden "
            "nicht in die Cloud kopiert. Exportdateien enthalten den Organisationsschlüssel nur "
            "passwortverschlüsselt; verwende ein starkes Dateipasswort."
        )
        cloud_note.setWordWrap(True)
        cloud_layout.addWidget(cloud_note)
        cloud_layout.addStretch()
        tabs.addTab(cloud_tab, "Cloud-Synchronisierung")

        backup_tab = QWidget()
        backup_layout = QVBoxLayout(backup_tab)
        backup = QPushButton("Manuelles Datenbank-Backup")
        backup.clicked.connect(self.backup_database)
        restore = QPushButton("Datenbank wiederherstellen")
        restore.clicked.connect(self.restore_database)
        backup_layout.addWidget(backup)
        backup_layout.addWidget(restore)
        backup_layout.addStretch()
        tabs.addTab(backup_tab, "Datensicherung")

        layout.addWidget(tabs)
        layout.addStretch()
        return page

    def read_setting(self, key: str) -> str:
        rows = self.db.query("SELECT value FROM settings WHERE key=?", (key,))
        return rows[0]["value"] if rows else ""

    def detect_scanner_devices(self):
        if sys.platform != "win32":
            QMessageBox.information(
                self, "Scanner-Geräte",
                "Die automatische Geräteliste ist nur unter Windows verfügbar. "
                "Unter Linux funktionieren USB- und Bluetooth-HID-Scanner als Tastatureingabe "
                "im fokussierten Scan-Feld.",
            )
            return
        command = (
            "$devices = @(Get-PnpDevice -PresentOnly | "
            "Where-Object { ($_.Class -eq 'Keyboard' -or $_.Class -eq 'HIDClass') -and $_.Status -eq 'OK' } | "
            "Select-Object FriendlyName, InstanceId, Class); "
            "$json = ConvertTo-Json -InputObject $devices -Compress; "
            "[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($json))"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, timeout=15, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            encoded = result.stdout.decode("ascii").strip()
            device_json = base64.b64decode(encoded, validate=True).decode("utf-16le") if encoded else "[]"
            devices = json.loads(device_json)
            if isinstance(devices, dict):
                devices = [devices]
            current = self.scanner_device.currentData()
            self.scanner_device.clear()
            self.scanner_device.addItem("HID-Tastatur / automatisch", "default")
            for device in devices:
                friendly_name = str(device.get("FriendlyName") or "HID-Gerät")
                instance_id = str(device.get("InstanceId") or friendly_name)
                device_class = str(device.get("Class") or "HID")
                display = f"{friendly_name} ({device_class})"
                if self.scanner_device.findData(instance_id) < 0:
                    self.scanner_device.addItem(display, instance_id)
            index = self.scanner_device.findData(current)
            self.scanner_device.setCurrentIndex(index if index >= 0 else 0)
            if self.scanner_device.count() == 1:
                QMessageBox.information(self, "Scanner-Geräte", "Windows hat keine aktiven Tastatur-/HID-Geräte gemeldet.")
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "Scanner-Geräte", f"Geräte konnten nicht aufgelistet werden:\n{exc}")

    def test_scanner_input(self):
        code = self.scanner.process(self.scanner_test_input.text())
        self.scan_test_result.setText(f"Empfangen: {code}" if code else "Kein Barcode empfangen.")
        self.scanner_test_input.clear()

    def save_scanner_settings(self):
        prefix = self.prefix.text(); suffix = self.suffix.text()
        self.db.execute("INSERT INTO settings(key,value) VALUES('scanner_prefix',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (prefix,))
        self.db.execute("INSERT INTO settings(key,value) VALUES('scanner_suffix',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (suffix,))
        selected_device = self.scanner_device.currentData() or "default"
        self.db.execute("INSERT INTO settings(key,value) VALUES('scanner_device',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (selected_device,))
        self.scanner = ScannerReader(prefix, suffix)
        QMessageBox.information(self, "Einstellungen", "Scanner-Einstellungen gespeichert.")

    def generate_cloud_shared_key(self):
        import secrets
        self.cloud_shared_key.setText(secrets.token_urlsafe(36))
        self.cloud_sync_status.setText(
            "Neuer Schlüssel erstellt. Kopiere ihn in die Supabase-Function-Secrets und speichere ihn anschließend hier."
        )

    def save_cloud_settings(self, show_confirmation: bool = True) -> bool:
        project_url = self.cloud_project_url.text().strip().rstrip("/")
        public_key = self.cloud_public_key.text().strip()
        shared_key = self.cloud_shared_key.text()
        if not project_url.startswith("https://") or not public_key or len(shared_key) < 32:
            message = (
                "Bitte HTTPS-Projekt-URL, öffentlichen Supabase-Key und einen Organisationsschlüssel "
                "mit mindestens 32 Zeichen eintragen."
            )
            self.cloud_sync_status.setText(message)
            if show_confirmation:
                QMessageBox.warning(self, "Cloud-Einrichtung", message)
            return False
        try:
            protected = protect_secret(shared_key)
            self.db.execute(
                "INSERT INTO settings(key,value) VALUES('supabase_url',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (project_url,),
            )
            self.db.execute(
                "INSERT INTO settings(key,value) VALUES('supabase_public_key',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (public_key,),
            )
            self.db.execute(
                "INSERT INTO settings(key,value) VALUES('cloud_shared_key_dpapi',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (protected,),
            )
        except Exception as exc:
            message = f"Cloud-Einstellungen konnten nicht sicher gespeichert werden: {exc}"
            self.cloud_sync_status.setText(message)
            if show_confirmation:
                QMessageBox.critical(self, "Cloud-Einrichtung", message)
            return False
        self.cloud_sync_status.setText("Cloud-Einstellungen lokal und geschützt gespeichert.")
        if show_confirmation:
            QMessageBox.information(self, "Cloud-Einrichtung", "Cloud-Einstellungen wurden gespeichert.")
        return True

    def export_cloud_settings_file(self):
        project_url = self.cloud_project_url.text().strip()
        public_key = self.cloud_public_key.text().strip()
        shared_key = self.cloud_shared_key.text()
        settings = {"project_url": project_url, "public_key": public_key}
        if shared_key:
            settings["shared_key"] = shared_key

        password, accepted = QInputDialog.getText(
            self,
            "Supabase-Einstellungen exportieren",
            "Passwort für die verschlüsselte Datei (mindestens 12 Zeichen):",
            QLineEdit.Password,
        )
        if not accepted:
            return
        confirmation, accepted = QInputDialog.getText(
            self,
            "Passwort bestätigen",
            "Dateipasswort erneut eingeben:",
            QLineEdit.Password,
        )
        if not accepted:
            return
        if password != confirmation:
            QMessageBox.warning(self, "Supabase-Einstellungen", "Die Passwörter stimmen nicht überein.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Supabase-Einstellungen exportieren",
            "eventtech-supabase-settings.json",
            "Verschlüsselte JSON-Datei (*.json)",
        )
        if not path:
            return
        if Path(path).suffix.lower() != ".json":
            path = f"{path}.json"
        try:
            Path(path).write_bytes(encrypt_supabase_config(settings, password))
        except Exception as exc:
            QMessageBox.critical(
                self, "Supabase-Einstellungen", f"Datei konnte nicht gespeichert werden:\n{exc}"
            )
            return
        QMessageBox.information(
            self,
            "Supabase-Einstellungen",
            "Die Verbindungseinstellungen wurden verschlüsselt exportiert. "
            "Bewahre das Dateipasswort getrennt von der Datei auf.",
        )

    def import_cloud_settings_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Supabase-Einstellungen importieren",
            "",
            "Verschlüsselte JSON-Datei (*.json)",
        )
        if not path:
            return
        password, accepted = QInputDialog.getText(
            self,
            "Supabase-Einstellungen importieren",
            "Dateipasswort eingeben:",
            QLineEdit.Password,
        )
        if not accepted:
            return
        try:
            config_path = Path(path)
            if config_path.stat().st_size > 64 * 1024:
                raise ValueError("Die Konfigurationsdatei fehlt oder ist zu groß.")
            settings = decrypt_supabase_config(config_path.read_bytes(), password)
        except Exception as exc:
            QMessageBox.critical(
                self, "Supabase-Einstellungen", f"Datei konnte nicht importiert werden:\n{exc}"
            )
            return

        self.cloud_project_url.setText(settings["project_url"])
        self.cloud_public_key.setText(settings["public_key"])
        if settings.get("shared_key"):
            self.cloud_shared_key.setText(settings["shared_key"])

        if len(self.cloud_shared_key.text()) >= 32:
            if not self.save_cloud_settings(show_confirmation=False):
                QMessageBox.warning(self, "Supabase-Einstellungen", self.cloud_sync_status.text())
                return
            message = (
                "Die importierten Einstellungen wurden gespeichert. Der Organisationsschlüssel "
                f"liegt geschützt im {secret_store_name()}."
            )
        else:
            message = (
                "Projekt-URL und öffentlicher Schlüssel wurden geladen. Trage den "
                "Organisationsschlüssel ein und speichere anschließend die Cloud-Einstellungen."
            )
            self.cloud_sync_status.setText(message)
        QMessageBox.information(self, "Supabase-Einstellungen", message)

    def _set_cloud_sync_busy(self, busy: bool):
        self.menu.setEnabled(not busy)
        self.pages.setEnabled(not busy)
        self.sync_now_button.setEnabled(not busy)

    def _start_cloud_worker(self, action: str, changes=None):
        self._sync_action = action
        self._sync_worker = CloudSyncWorker(self._sync_client, action, changes, self)
        self._sync_worker.completed.connect(self._cloud_worker_completed)
        self._sync_worker.failed.connect(self._cloud_worker_failed)
        self._sync_worker.start()

    def start_cloud_sync(self, automatic: bool = False):
        if self._sync_worker and self._sync_worker.isRunning():
            return
        if not self.save_cloud_settings(show_confirmation=False):
            if not automatic:
                QMessageBox.information(
                    self, "Cloud-Synchronisierung",
                    "Richte zuerst Supabase-URL, öffentlichen Key und Organisationsschlüssel ein.",
                )
            return
        try:
            self._sync_client = SupabaseSyncClient(
                self.cloud_project_url.text().strip(),
                self.cloud_public_key.text().strip(),
                self.cloud_shared_key.text(),
            )
            self._sync_snapshot = local_snapshot(self.db)
        except Exception as exc:
            self.cloud_sync_status.setText(f"Synchronisierung konnte nicht vorbereitet werden: {exc}")
            if not automatic:
                QMessageBox.critical(self, "Cloud-Synchronisierung", str(exc))
            return
        self._sync_automatic = automatic
        self._set_cloud_sync_busy(True)
        self.cloud_sync_status.setText("Verbinde mit der Cloud …")
        self._start_cloud_worker("pull")

    def _cloud_worker_completed(self, result):
        if self._sync_worker and not self._sync_worker.isFinished():
            QTimer.singleShot(50, lambda result=result: self._cloud_worker_completed(result))
            return
        action = self._sync_action
        self._sync_worker = None
        self._sync_action = None
        if action == "pull":
            self._handle_cloud_snapshot(result.get("records", []))
            return
        conflicts = result.get("conflicts", [])
        if conflicts:
            reasons = sorted({str(item.get("reason", "Änderung")) for item in conflicts})
            if "duplicate_barcode" in reasons:
                message = (
                    "Mindestens ein Barcode ist bereits einem anderen Artikel oder Lagerplatz zugewiesen. "
                    "Ändere den doppelten Barcode und synchronisiere erneut. Lokale Daten bleiben erhalten."
                )
            else:
                message = (
                    f"{len(conflicts)} Cloud-Änderung(en) haben sich während des Syncs geändert "
                    f"({', '.join(reasons)}). Lokale Daten bleiben erhalten; bitte erneut synchronisieren."
                )
            self._finish_cloud_sync(message, error=True)
            return
        try:
            records = result.get("records", [])
            apply_cloud_snapshot(self.db, records)
        except Exception as exc:
            self._finish_cloud_sync(
                f"Cloud-Daten konnten nicht lokal übernommen werden: {exc}", error=True
            )
            return
        self._finish_cloud_sync(
            f"Synchronisierung abgeschlossen. {len(records)} Cloud-Datensätze abgeglichen."
        )

    def _handle_cloud_snapshot(self, remote_records: list[dict]):
        try:
            plan = plan_sync(self.db, self._sync_snapshot, remote_records)
        except Exception as exc:
            self._finish_cloud_sync(f"Cloud-Daten konnten nicht verglichen werden: {exc}", error=True)
            return
        pushes = list(plan["pushes"])
        if plan["conflicts"]:
            self._set_cloud_sync_busy(False)
            dialog = SyncConflictDialog(plan["conflicts"], self)
            if dialog.exec() != QDialog.Accepted:
                self._finish_cloud_sync("Synchronisierung abgebrochen; lokale Daten bleiben erhalten.")
                return
            for conflict, choice in zip(plan["conflicts"], dialog.selected_choices()):
                if choice == "local":
                    state = conflict["local"]
                    pushes.append({
                        "id": conflict["id"],
                        "entity_type": conflict["entity_type"],
                        "data": state["data"],
                        "deleted": state["deleted"],
                        "expected_revision": conflict["remote_revision"],
                    })
                elif conflict["remote"] is None:
                    pushes.append({
                        "id": conflict["id"],
                        "entity_type": conflict["entity_type"],
                        "data": None,
                        "deleted": True,
                        "expected_revision": None,
                    })

        if pushes:
            self._set_cloud_sync_busy(True)
            self.cloud_sync_status.setText(f"Übertrage {len(pushes)} lokale Änderung(en) …")
            self._start_cloud_worker("push", pushes)
            return
        try:
            apply_cloud_snapshot(self.db, remote_records)
        except Exception as exc:
            self._finish_cloud_sync(
                f"Cloud-Daten konnten nicht lokal übernommen werden: {exc}", error=True
            )
            return
        self._finish_cloud_sync(
            f"Synchronisierung abgeschlossen. {len(remote_records)} Cloud-Datensätze abgeglichen."
        )

    def _cloud_worker_failed(self, message: str):
        if self._sync_worker and not self._sync_worker.isFinished():
            QTimer.singleShot(50, lambda message=message: self._cloud_worker_failed(message))
            return
        self._sync_worker = None
        self._sync_action = None
        self._finish_cloud_sync(message, error=True)

    def _finish_cloud_sync(self, message: str, error: bool = False):
        self._set_cloud_sync_busy(False)
        self.cloud_sync_status.setText(message)
        if error and not self._sync_automatic:
            QMessageBox.critical(self, "Cloud-Synchronisierung", message)
        if not error:
            self.refresh_dashboard()
            self.refresh_articles()
            self.refresh_warehouses()
            self.refresh_repairs()
            self.refresh_scans()

    def start_ipad_scanner(self):
        if self.ipad_server:
            return
        try:
            addresses = local_ipv4_addresses() or ["127.0.0.1"]
            self.ipad_server = IpadScannerServer(
                self.scan_bridge.barcode_received.emit,
                certificate_dir=DATA_DIR / "scanner-certificate",
                certificate_hosts=addresses,
            )
            self.ipad_server.start()
        except OSError as exc:
            self.ipad_server = None
            QMessageBox.critical(self, "Mobil-Scanner", f"Der Scanner-Webserver konnte nicht gestartet werden:\n{exc}")
            return
        address = addresses[0]
        self.ipad_url.setText(f"http://{address}:{self.ipad_server.port}/?key={self.ipad_server.token}")
        self.ipad_https_url.setText(f"https://{address}:{self.ipad_server.https_port}/?key={self.ipad_server.token}")
        self.ipad_certificate_url.setText(
            f"http://{address}:{self.ipad_server.port}/certificate?key={self.ipad_server.token}"
        )
        self.ipad_status.setText("Mobil-Scanner aktiv. Zertifikat installieren und HTTPS-Adresse für Live-Kamera öffnen.")
        self.ipad_start_button.setEnabled(False)
        self.ipad_stop_button.setEnabled(True)

    def stop_ipad_scanner(self):
        if self.ipad_server:
            self.ipad_server.stop()
            self.ipad_server = None
        self.ipad_url.clear()
        self.ipad_https_url.clear()
        self.ipad_certificate_url.clear()
        self.ipad_status.setText("Mobil-Scanner ist nicht aktiv.")
        self.ipad_start_button.setEnabled(True)
        self.ipad_stop_button.setEnabled(False)

    def show_ipad_qr(self):
        url = self.ipad_url.text()
        if not url:
            QMessageBox.information(self, "Mobil-Scanner", "Bitte zuerst die Mobilgeräte-Kopplung starten.")
            return
        import qrcode
        from io import BytesIO
        buffer = BytesIO()
        qrcode.make(url).save(buffer, format="PNG")
        qr = QPixmap()
        qr.loadFromData(buffer.getvalue(), "PNG")
        dialog = QDialog(self)
        dialog.setWindowTitle("iPad / Android koppeln")
        layout = QVBoxLayout(dialog)
        label = QLabel("Scannen Sie diesen Code mit der Kamera oder einem QR-Scanner des Mobilgeräts:")
        label.setWordWrap(True)
        image = QLabel()
        image.setPixmap(qr.scaled(360, 360, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        image.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        layout.addWidget(image)
        dialog.exec()

    def backup_database(self):
        try:
            path = self.db.backup()
            QMessageBox.information(self, "Backup", f"Backup erstellt:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Backup", str(exc))

    def restore_database(self):
        source, _ = QFileDialog.getOpenFileName(self, "Backup wiederherstellen", "", "SQLite-Datenbank (*.db)")
        if source and QMessageBox.question(self, "Wiederherstellen", "Aktuelle Datenbank wirklich ersetzen?",
                                           QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            try:
                self.db.restore(source)
                QMessageBox.information(self, "Wiederherstellen", "Datenbank wurde wiederhergestellt.")
                self.refresh_dashboard()
            except Exception as exc:
                QMessageBox.critical(self, "Wiederherstellen", str(exc))

    def closeEvent(self, event):
        if self._sync_worker and self._sync_worker.isRunning():
            QMessageBox.information(
                self, "Synchronisierung läuft",
                "Bitte warte, bis die Cloud-Synchronisierung abgeschlossen ist, und schließe die App danach.",
            )
            event.ignore()
            return
        self.cloud_sync_timer.stop()
        self.dashboard_camera.stop_camera()
        self.stop_ipad_scanner()
        self.db.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
