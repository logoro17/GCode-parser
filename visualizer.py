import sys
from PyQt5.QtWidgets import QGraphicsView, QGraphicsScene, QApplication
from PyQt5.QtGui import QPainter, QPainterPath, QPen, QColor, QBrush, QFont, QTransform
from PyQt5.QtCore import Qt, QPointF, QRectF

class GCodeCanvas(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)

        self.setFocusPolicy(Qt.StrongFocus)

        # 1. Atur batas scene sangat luas agar pan bebas tanpa terbatas scrollbar
        self.scene.setSceneRect(-5000, -5000, 10000, 10000)

        # 2. Matikan Scrollbar Bawaan (Pan full pakai mouse drag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Config Render
        self.setRenderHint(QPainter.Antialiasing, False)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        
        # Invert Y agar koordinat CNC Y-up
        self.scale(1, -1)

        # State Panning & Cursor Tracker
        self.is_panning = False
        self.last_mouse_pos = QPointF()
        self.cursor_pos = QPointF(0.0, 0.0)
        self.mco_pos = QPointF(0.0, 0.0)
        
        self.setMouseTracking(True)


    def keyPressEvent(self, event):
        # 1. Ctrl + A : Zoom Auto / Fit in View
        if event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_A:
            if not self.scene.itemsBoundingRect().isEmpty():
                self.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)
            event.accept()

        # 2. Ctrl + Plus / Ctrl + Equal : Zoom In (+10%)
        elif event.modifiers() == Qt.ControlModifier and event.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self.scale(1.10, 1.10)
            self.viewport().update()
            event.accept()

        # 3. Ctrl + Minus : Zoom Out (-10%)
        elif event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_Minus:
            self.scale(0.90, 0.90)
            self.viewport().update()
            event.accept()

        else:
            super().keyPressEvent(event)

    # --- PANNING MEKANISME DIRECT TRANSLATE ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_panning = True
            # Simpan posisi awal mouse di layar (Pixel)
            self.last_mouse_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # 1. Eksekusi Pan menggunakan ScrollBar (Presisi & Bebas Bug Translate)
        if self.is_panning:
            delta = event.pos() - self.last_mouse_pos
            self.last_mouse_pos = event.pos()

            # Geser nilai ScrollBar horizontal & vertikal sesuai selisih pixel mouse
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())

            self.viewport().update()
            event.accept()
            return

        # 2. Update koordinat PTR
        scene_pt = self.mapToScene(event.pos())
        self.cursor_pos = scene_pt
        self.viewport().update()

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        zoom_factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(zoom_factor, zoom_factor)
        self.viewport().update()

    def drawBackground(self, painter, rect):
        painter.fillRect(rect, QColor("#fbfbdb"))

        grid_step = 10
        major_step = 50

        left = int(rect.left()) - (int(rect.left()) % grid_step)
        top = int(rect.top()) - (int(rect.top()) % grid_step)
        right = int(rect.right())
        bottom = int(rect.bottom())

        pen_minor = QPen(QColor("#eae3b5"), 0, Qt.SolidLine)
        pen_major = QPen(QColor("#dcd295"), 0, Qt.SolidLine)
        pen_axis = QPen(QColor("#a8b3cb"), 0, Qt.SolidLine)

        for x in range(left, right, grid_step):
            painter.setPen(pen_major if x % major_step == 0 else pen_minor)
            painter.drawLine(x, bottom, x, top)

        for y in range(top, bottom, grid_step):
            painter.setPen(pen_major if y % major_step == 0 else pen_minor)
            painter.drawLine(left, y, right, y)

        painter.setPen(pen_axis)
        painter.drawLine(left, 0, right, 0)
        painter.drawLine(0, bottom, 0, top)

        pen_cross = QPen(QColor("blue"), 2)
        painter.setPen(pen_cross)
        painter.drawLine(-12, 0, 12, 0)
        painter.drawLine(0, -12, 0, 12)

    def drawForeground(self, painter, rect):
        painter.save()
        painter.setWorldTransform(QTransform())

        vw = self.viewport().width()
        vh = self.viewport().height()

        margin = 40
        font = QFont("Consolas", 8)
        painter.setFont(font)

        # 1. Background Ruler (Kiri & Bawah)
        painter.fillRect(0, 0, margin, vh, QColor("#fbfbdb"))        # Ruler Y (Kiri)
        painter.fillRect(0, vh - margin, vw, margin, QColor("#fbfbdb")) # Ruler X (Bawah)

        pen_text = QPen(QColor("#333333"))
        painter.setPen(pen_text)

        # Teks 'mm' di sudut kiri bawah tempat dua mistar bertemu
        painter.drawText(10, vh - 12, "mm")

        # --- A. RULER X (BAGIAN BAWAH) ---
        # Menghitung tick/koordinat X berdasarkan titik tengah viewport untuk semua area layar
        for x_px in range(margin, vw, 50):
            world_pt = self.mapToScene(x_px, 0)
            val_x = int(round(world_pt.x() / 50.0) * 50)
            target_px = self.mapFromScene(QPointF(val_x, 0)).x()
            
            if margin <= target_px <= vw:
                # Tanda strip tick mark bawah
                painter.drawLine(int(target_px), vh - margin, int(target_px), vh - margin + 5)
                # Teks angka koordinat X
                painter.drawText(int(target_px) - 25, vh - margin + 8, 50, 20, Qt.AlignCenter, str(val_x))

        # --- B. RULER Y (BAGIAN KIRI) ---
        for y_px in range(0, vh - margin, 50):
            world_pt = self.mapToScene(0, y_px)
            val_y = int(round(world_pt.y() / 50.0) * 50)
            target_px = self.mapFromScene(QPointF(0, val_y)).y()
            
            if 0 <= target_px <= vh - margin:
                # Tanda strip tick mark kiri
                painter.drawLine(margin - 5, int(target_px), margin, int(target_px))
                # Teks angka koordinat Y
                painter.drawText(0, int(target_px) - 10, margin - 8, 20, Qt.AlignRight | Qt.AlignVCenter, str(val_y))

        # --- C. OVERLAY STATUS MCO & PTR (Pojok Kanan Atas) ---
        box_w, box_h = 160, 45
        box_x = vw - box_w - 15
        box_y = 15

        painter.setBrush(QBrush(QColor(230, 230, 220, 180)))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(box_x, box_y, box_w, box_h, 6, 6)

        painter.setPen(pen_text)
        painter.drawText(box_x + 10, box_y + 18, "MCO")
        painter.drawText(box_x + 60, box_y + 18, f"{self.mco_pos.x():8.3f}")
        painter.drawText(box_x + 110, box_y + 18, f"{self.mco_pos.y():8.3f}")

        painter.drawText(box_x + 10, box_y + 36, "PTR")
        painter.drawText(box_x + 60, box_y + 36, f"{self.cursor_pos.x():8.3f}")
        painter.drawText(box_x + 110, box_y + 36, f"{self.cursor_pos.y():8.3f}")

        # --- D. OVERLAY BASIC USAGE & SHORTCUTS (Pojok Kanan Bawah) ---
        info_w, info_h = 240, 120
        info_x = vw - info_w - 15
        info_y = vh - info_h - margin - 15

        painter.setBrush(QBrush(QColor(230, 230, 220, 180)))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(info_x, info_y, info_w, info_h, 6, 6)

        painter.setPen(pen_text)
        lines = [
            "Basic usage",
            "ZOOM: Use mouse wheel (or two-finger gesture)",
            "PAN:  Click and drag (or touch and drag)",
            "JOG:  Double click in any point (or double tap)",
            "",
            "Keyboard Shortcuts",
            "Ctrl A: Zoom Auto  (100%)",
            "Ctrl +: Zoom In    (+10%)",
            "Ctrl -: Zoom Out   (-10%)"
        ]
        curr_y = info_y + 14
        for line in lines:
            font.setBold("Basic usage" in line or "Keyboard Shortcuts" in line)
            painter.setFont(font)
            painter.drawText(info_x + 10, curr_y, line)
            curr_y += 12

        painter.restore()

    def load_points(self, points):
        self.scene.clear()
        if not points:
            return

        path_cutting = QPainterPath()
        path_travel = QPainterPath()

        start_pt = points[0]
        current_pos = QPointF(start_pt.x, start_pt.y)

        for pt in points:
            next_pos = QPointF(pt.x, pt.y)
            if pt.type == 1:
                path_cutting.moveTo(current_pos)
                path_cutting.lineTo(next_pos)
            else:
                path_travel.moveTo(current_pos)
                path_travel.lineTo(next_pos)
            current_pos = next_pos

        pen_g1 = QPen(QColor("#0000FF"), 0)
        pen_g0 = QPen(QColor("#FF0000"), 0, Qt.DashLine)

        self.scene.addPath(path_cutting, pen_g1)
        self.scene.addPath(path_travel, pen_g0)
        self.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    view = GCodeCanvas()
    view.setWindowTitle("LoGoRo17 2D Canvas - Laser CNC Visualizer")
    view.resize(1000, 700)
    view.show()
    sys.exit(app.exec_())