from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget


class RackGraphic(QWidget):
    entityDropped = Signal(str, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(720, 500)
        self.setMouseTracking(True)
        self.rack = None
        self.levels = []
        self._targets = []
        self._drag_source = None

    def set_layout(self, rack, levels):
        self.rack = rack
        self.levels = levels
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#202428"))
        self._targets = []

        if not self.rack:
            painter.setPen(QColor("#aeb8c2"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Wähle ein Lager und ein Regal aus.")
            return

        painter.setPen(QColor("#edf2f7"))
        painter.setFont(QFont("Segoe UI", 13, QFont.Bold))
        painter.drawText(24, 32, f"{self.rack['name']}  ·  {self.rack['width_cm']} × {self.rack['height_cm']} cm")
        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(QColor("#aeb8c2"))
        painter.drawText(24, 56, "Box auf einen Boden ziehen · Lampe/Artikel auf eine andere Box ziehen")

        margin_left, margin_right, margin_top, margin_bottom = 64, 36, 82, 42
        area_width = max(100, self.width() - margin_left - margin_right)
        area_height = max(120, self.height() - margin_top - margin_bottom)
        scale = min(
            area_width / max(1, self.rack["width_cm"]),
            area_height / max(1, self.rack["height_cm"]),
        )
        rack_width = max(100, int(self.rack["width_cm"] * scale))
        rack_height = max(120, int(self.rack["height_cm"] * scale))
        left = margin_left + max(0, (area_width - rack_width) // 2)
        bottom = margin_top + (area_height + rack_height) // 2
        top = bottom - rack_height
        right = left + rack_width

        painter.setPen(QPen(QColor("#9aa8b5"), 8, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(left, top, left, bottom)
        painter.drawLine(right, top, right, bottom)
        painter.drawLine(left - 8, bottom + 5, right + 8, bottom + 5)

        total_height = sum(max(1, level["height_cm"]) for level in self.levels)
        if total_height > self.rack["height_cm"]:
            painter.setPen(QColor("#ffb74d"))
            painter.drawText(24, self.height() - 12, "Achtung: Summe der Bodenhöhen überschreitet die Regalhöhe.")

        used = 0
        for level in self.levels:
            level_height = max(1, level["height_cm"])
            y_bottom = bottom - int(used * scale)
            y_top = bottom - int((used + level_height) * scale)
            band_height = max(24, y_bottom - y_top)
            painter.fillRect(left + 5, y_top, max(1, rack_width - 10), band_height, QColor("#293138"))
            painter.setPen(QPen(QColor("#aab5bf"), 5))
            painter.drawLine(left, y_top, right, y_top)
            self._targets.append((("level", level["id"]), (left + 4, y_top + 2, rack_width - 8, max(1, band_height - 4))))

            painter.setPen(QColor("#edf2f7"))
            painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
            painter.drawText(8, y_top + 17, f"{level['name']} · {level_height} cm")

            boxes = level["boxes"]
            if boxes:
                gap = 8
                available_width = rack_width - 28
                box_width = max(70, (available_width - gap * (len(boxes) - 1)) // len(boxes))
                box_height = max(28, min(band_height - 14, 136))
                box_y = y_bottom - box_height - 7
                for index, box in enumerate(boxes):
                    box_x = left + 14 + index * (box_width + gap)
                    rect = (box_x, box_y, box_width, box_height)
                    painter.setPen(QPen(QColor("#64b5f6"), 1))
                    painter.setBrush(QColor("#344955"))
                    painter.drawRoundedRect(*rect, 6, 6)
                    painter.setPen(QColor("#ffffff"))
                    painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
                    painter.drawText(box_x + 6, box_y + 17, box["name"][:24])
                    self._targets.append((("box", box["id"]), rect))

                    for article_index, article in enumerate(box["articles"][:3]):
                        article_y = box_y + 22 + article_index * 16
                        if article_y + 14 > box_y + box_height:
                            break
                        article_rect = (box_x + 4, article_y, box_width - 8, 15)
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(QColor("#81c784"))
                        painter.drawRoundedRect(*article_rect, 3, 3)
                        painter.setPen(QColor("#17251b"))
                        painter.setFont(QFont("Segoe UI", 7))
                        painter.drawText(article_rect[0] + 4, article_rect[1] + 11, article["name"][:26])
                        self._targets.append((("article", article["id"]), article_rect))
                    extra = len(box["articles"]) - 3
                    if extra > 0:
                        painter.setPen(QColor("#dce7ef"))
                        painter.setFont(QFont("Segoe UI", 7))
                        painter.drawText(box_x + 6, box_y + box_height - 5, f"+{extra} weitere")
            used += level_height

        if self._drag_source:
            painter.setPen(QColor("#90caf9"))
            painter.drawText(24, self.height() - 12, "Loslassen auf einem markierten Ziel zum Verschieben.")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_source = self._target_at(event.position().toPoint())
            if self._drag_source:
                self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_source:
            target = self._target_at(event.position().toPoint())
            source = self._drag_source
            self._drag_source = None
            if target and source[0] == "box" and target[0] == "level":
                self.entityDropped.emit(source[0], source[1], target[1])
            elif target and source[0] == "article" and target[0] == "box":
                self.entityDropped.emit(source[0], source[1], target[1])
            self.update()
        super().mouseReleaseEvent(event)

    def _target_at(self, point):
        for target, rect in reversed(self._targets):
            x, y, width, height = rect
            if x <= point.x() <= x + width and y <= point.y() <= y + height:
                return target
        return None
