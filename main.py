import sys
import math
from collections import Counter
import ezdxf
from ezdxf import path as ezdxf_path
import cv2
import numpy as np
import xml.etree.ElementTree as ET

import os
os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = ""
os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"

# --- CROSS-PLATFORM MINGW DLL LOADER (WINDOWS ONLY) ---
if sys.platform == "win32":
    import shutil
    gcc_path = shutil.which("g++") or shutil.which("gcc")
    if gcc_path:
        try:
            os.add_dll_directory(os.path.dirname(gcc_path))
        except Exception:
            pass

try:
    from svgelements import SVG, Path as SVGPath
    HAS_SVG = True
except ImportError:
    HAS_SVG = False

try:
    import gerber
    HAS_GERBER = True
except ImportError:
    HAS_GERBER = False

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QGridLayout, QLabel, QLineEdit, QPushButton, QFileDialog, 
    QTextEdit, QGroupBox, QSplitter, QMessageBox, QComboBox,
    QDialog, QCheckBox, QScrollArea, QDialogButtonBox, QGraphicsView, QGraphicsScene
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QPointF
from PyQt5.QtGui import QPainter, QPainterPath, QPen, QColor, QBrush, QFont, QTransform

try:
    from shapely.geometry import Polygon as ShapelyPolygon, MultiPolygon, LineString, Point
    from shapely.ops import unary_union
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False


# ==============================================================================
# LASERGRBL STYLE 2D CANVAS VISUALIZER (PyQt5 QGraphicsView)
# ==============================================================================
class GCodeCanvas(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)

        # 1. Atur batas area scene agar dapat dipan tanpa batas scrollbar
        self.scene.setSceneRect(-5000, -5000, 10000, 10000)

        # 2. Sembunyikan scrollbar bawaan
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Config Render
        self.setRenderHint(QPainter.Antialiasing, False)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setFocusPolicy(Qt.StrongFocus)
        
        # Invert Sumbu Y (Layar Y-down ke CNC Y-up)
        self.scale(1, -1)

        # State Panning & Mouse Tracking
        self.is_panning = False
        self.last_mouse_pos = QPointF()
        self.cursor_pos = QPointF(0.0, 0.0)
        self.mco_pos = QPointF(0.0, 0.0)
        
        self.setMouseTracking(True)
        self.is_auto_process = False

    def plot_all_gcode(self, gcode_text):
        """Memetakan seluruh G-Code (G0 Rapid Travel & G1 Cutting) langsung dari teks G-Code"""
        self.scene.clear()
        if not gcode_text.strip():
            return

        path_cutting = QPainterPath()  # G1
        path_travel = QPainterPath()   # G0

        cur_x, cur_y = 0.0, 0.0
        cur_mode = "G0" # Default modal

        for line in gcode_text.splitlines():
            # Clean comment
            line = line.split(';')[0].strip()
            if not line:
                continue

            tokens = line.split()
            has_x, has_y = False, False
            next_x, next_y = cur_x, cur_y

            for token in tokens:
                prefix = token[0].upper()
                val_str = token[1:]
                
                if prefix == 'G':
                    try:
                        g_num = int(float(val_str))
                        if g_num == 0: cur_mode = "G0"
                        elif g_num == 1: cur_mode = "G1"
                    except ValueError: pass
                elif prefix == 'X':
                    try: next_x = float(val_str); has_x = True
                    except ValueError: pass
                elif prefix == 'Y':
                    try: next_y = float(val_str); has_y = True
                    except ValueError: pass

            if has_x or has_y:
                if cur_mode == "G1":
                    path_cutting.moveTo(cur_x, cur_y)
                    path_cutting.lineTo(next_x, next_y)
                else:  # G0 Rapid
                    path_travel.moveTo(cur_x, cur_y)
                    path_travel.lineTo(next_x, next_y)

                cur_x, cur_y = next_x, next_y

        pen_g1 = QPen(QColor("#0000FF"), 0) # Biru untuk G1 Cutting
        pen_g0 = QPen(QColor("#FF0000"), 0, Qt.DashLine) # Merah Putus-putus untuk G0 Travel

        self.scene.addPath(path_cutting, pen_g1)
        self.scene.addPath(path_travel, pen_g0)

        if not self.scene.itemsBoundingRect().isEmpty():
            self.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)

    # --- PANNING MEKANISME VIA SCROLLBAR ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_panning = True
            self.last_mouse_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.is_panning:
            delta = event.pos() - self.last_mouse_pos
            self.last_mouse_pos = event.pos()

            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())

            self.viewport().update()
            event.accept()
            return

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

    def keyPressEvent(self, event):
        if event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_A:
            if not self.scene.itemsBoundingRect().isEmpty():
                self.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)
            event.accept()
        elif event.modifiers() == Qt.ControlModifier and event.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self.scale(1.10, 1.10)
            self.viewport().update()
            event.accept()
        elif event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_Minus:
            self.scale(0.90, 0.90)
            self.viewport().update()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _calculate_dynamic_step(self, target_px=60):
        """Menghitung step grid (mm) agar jarak antar garis di layar ~60px"""
        zoom_scale = abs(self.transform().m11())
        if zoom_scale <= 0:
            zoom_scale = 1.0

        # Berapa mm dalam kisaran target_px layar
        raw_units = target_px / zoom_scale

        # Logaritma basis 10 untuk menentukan orde besaran
        exponent = math.floor(math.log10(raw_units))
        fraction = raw_units / (10 ** exponent)

        # Bulatkan ke kelipatan terdekat (1, 2, atau 5)
        if fraction < 1.5:
            nice_fraction = 1.0
        elif fraction < 3.5:
            nice_fraction = 2.0
        elif fraction < 7.5:
            nice_fraction = 5.0
        else:
            nice_fraction = 10.0

        grid_step = nice_fraction * (10 ** exponent)
        return grid_step, zoom_scale

    def drawBackground(self, painter, rect):
        painter.fillRect(rect, QColor("#fbfbdb"))

        # Hitung step grid dinamis
        grid_step, zoom_scale = self._calculate_dynamic_step(target_px=50)
        major_step = grid_step * 5

        # Hitung rentang garis
        left = math.floor(rect.left() / grid_step) * grid_step
        top = math.floor(rect.top() / grid_step) * grid_step
        right = rect.right()
        bottom = rect.bottom()

        pen_minor = QPen(QColor("#eae3b5"), 0, Qt.SolidLine)
        pen_major = QPen(QColor("#dcd295"), 0, Qt.SolidLine)
        pen_axis = QPen(QColor("#a8b3cb"), 0, Qt.SolidLine)

        # Garis Vertikal
        x = left
        while x <= right:
            # Gunakan math.isclose untuk cek kelipatan major (menghindari error floating point)
            is_major = math.isclose((x / major_step) % 1.0, 0, abs_tol=1e-5) or math.isclose(x, 0, abs_tol=1e-5)
            painter.setPen(pen_major if is_major else pen_minor)
            painter.drawLine(QPointF(x, bottom), QPointF(x, top))
            x += grid_step

        # Garis Horizontal
        y = top
        while y <= bottom:
            is_major = math.isclose((y / major_step) % 1.0, 0, abs_tol=1e-5) or math.isclose(y, 0, abs_tol=1e-5)
            painter.setPen(pen_major if is_major else pen_minor)
            painter.drawLine(QPointF(left, y), QPointF(right, y))
            y += grid_step

        # Sumbu Utama X=0 & Y=0
        painter.setPen(pen_axis)
        painter.drawLine(QPointF(left, 0), QPointF(right, 0))
        painter.drawLine(QPointF(0, bottom), QPointF(0, top))

        # Crosshair Origin (0,0)
        pen_cross = QPen(QColor("blue"), 2 / zoom_scale)
        painter.setPen(pen_cross)
        size = 12 / zoom_scale
        painter.drawLine(QPointF(-size, 0), QPointF(size, 0))
        painter.drawLine(QPointF(0, -size), QPointF(0, size))

    def drawForeground(self, painter, rect):
        painter.save()
        painter.setWorldTransform(QTransform())

        vw = self.viewport().width()
        vh = self.viewport().height()

        margin = 40
        font = QFont("Consolas", 7)
        painter.setFont(font)

        # Background Ruler (Kiri & Bawah)
        painter.fillRect(0, 0, margin, vh, QColor("#fbfbdb"))
        painter.fillRect(0, vh - margin, vw, margin, QColor("#fbfbdb"))

        pen_text = QPen(QColor("#333333"))
        painter.setPen(pen_text)
        painter.drawText(10, vh - 12, "mm")

        # --- A. RULER X & Y DYNAMIC SCALE ---
        # Hitung step ideal untuk label teks angka (jarak antar angka idealnya ~80px)
        step_val, _ = self._calculate_dynamic_step(target_px=80)

        # --- A. RULER X (BAGIAN BAWAH) ---
        start_pt = self.mapToScene(margin, 0)
        end_pt = self.mapToScene(vw, 0)

        first_x = math.floor(start_pt.x() / step_val) * step_val
        curr_x = first_x
        while curr_x <= end_pt.x():
            target_px = self.mapFromScene(QPointF(curr_x, 0)).x()
            if margin <= target_px <= vw:
                painter.drawLine(int(target_px), vh - margin, int(target_px), vh - margin + 5)
                
                txt_val = f"{curr_x:.1f}" if step_val < 1 else f"{int(round(curr_x))}"
                painter.drawText(int(target_px) - 30, vh - margin + 8, 60, 20, Qt.AlignCenter, txt_val)
            curr_x += step_val

        # --- B. RULER Y (BAGIAN KIRI) ---
        start_y_pt = self.mapToScene(0, vh - margin)
        end_y_pt = self.mapToScene(0, 0)

        first_y = math.floor(start_y_pt.y() / step_val) * step_val
        curr_y = first_y
        while curr_y <= end_y_pt.y():
            target_px = self.mapFromScene(QPointF(0, curr_y)).y()
            if 0 <= target_px <= vh - margin:
                painter.drawLine(margin - 5, int(target_px), margin, int(target_px))
                
                txt_val = f"{curr_y:.1f}" if step_val < 1 else f"{int(round(curr_y))}"
                painter.drawText(0, int(target_px) - 10, margin - 8, 20, Qt.AlignRight | Qt.AlignVCenter, txt_val)
            curr_y += step_val

        # --- OVERLAY STATUS ZOOM & PTR (Pojok Kanan Atas) ---
        box_w, box_h = 160, 45
        box_x = vw - box_w - 15
        box_y = 15

        painter.setBrush(QBrush(QColor(230, 230, 220, 180)))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(box_x, box_y, box_w, box_h, 6, 6)

        painter.setPen(pen_text)
        
        # Hitung persentase Zoom Scale
        zoom_pct = int(round(abs(self.transform().m11()) * 100))
    
        # Baris 1: Pointer Coordinates (PTR)
        painter.drawText(box_x + 10, box_y + 18, "PTR")
        painter.drawText(box_x + 60, box_y + 18, f"{self.cursor_pos.x():8.3f}")
        painter.drawText(box_x + 110, box_y + 18, f"{self.cursor_pos.y():8.3f}")

        # Baris 2: Zoom Scale (%)
        painter.drawText(box_x + 10, box_y + 36, "Zoom")
        painter.drawText(box_x + 60, box_y + 36, f"{zoom_pct}%")

        # Overlay Basic Usage (Pojok Kanan Bawah)
        info_w, info_h = 240, 110
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

    def plot_data(self, parsed_shapes):
        """Memetakan geometri solid (Desain)"""
        self.scene.clear()
        if not parsed_shapes:
            return

        pen_shape = QPen(QColor("#B22222"), 0) # Merah Bata
        brush_shape = QBrush(QColor(178, 34, 34, 180))

        for item in parsed_shapes:
            shape_type = item[0] if isinstance(item, tuple) else type(item).__name__
            data = item[1] if isinstance(item, tuple) else None

            if shape_type == 'CIRCLE':
                center, radius = data
                self.scene.addEllipse(
                    center[0] - radius, center[1] - radius,
                    radius * 2, radius * 2, pen_shape, brush_shape
                )
            elif shape_type == 'POLYGON':
                pts = data
                if len(pts) >= 3:
                    path = QPainterPath()
                    path.moveTo(pts[0][0], pts[0][1])
                    for p in pts[1:]:
                        path.lineTo(p[0], p[1])
                    path.closeSubpath()
                    self.scene.addPath(path, pen_shape, brush_shape)
            elif shape_type == 'LINE':
                pts = data
                if len(pts) >= 2:
                    path = QPainterPath()
                    path.moveTo(pts[0][0], pts[0][1])
                    for p in pts[1:]:
                        path.lineTo(p[0], p[1])
                    self.scene.addPath(path, pen_shape)

        if not self.scene.itemsBoundingRect().isEmpty():
            self.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)

    def plot_toolpaths(self, toolpaths):
        """Memetakan jalur pemotongan Laser (Toolpaths)"""
        self.scene.clear()
        if not toolpaths:
            return

        path_cutting = QPainterPath()
        pen_g1 = QPen(QColor("#0000FF"), 0) # Biru Laser Path

        for path in toolpaths:
            if len(path) >= 2:
                path_cutting.moveTo(path[0][0], path[0][1])
                for pt in path[1:]:
                    path_cutting.lineTo(pt[0], pt[1])

        self.scene.addPath(path_cutting, pen_g1)

        if not self.scene.itemsBoundingRect().isEmpty():
            self.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)


# ==============================================================================
# HELPER FUNCTIONS & DIALOGS
# ==============================================================================
def scan_dxf_layers(filepath):
    doc = ezdxf.readfile(filepath)
    msp = doc.modelspace()
    counts = Counter()

    def _count(entity):
        layer_name = getattr(entity.dxf, 'layer', '0') or '0'
        counts[layer_name] += 1

    for entity in msp:
        if entity.dxftype() == 'INSERT':
            try:
                for virt_ent in entity.virtual_entities():
                    _count(virt_ent)
            except Exception:
                pass
        else:
            _count(entity)

    if not counts:
        for entity in doc.paperspace():
            _count(entity)

    return counts

def scan_brd_layers(filepath):
    tree = ET.parse(filepath)
    root = tree.getroot()
    counts = Counter()
    layer_map = {}
    object_counts = Counter()

    for layer_def in root.iter('layer'):
        num = layer_def.get('number')
        name = layer_def.get('name')
        if num and name: layer_map[num] = name

    for elem in root.iter():
        if elem.tag in ['wire', 'pad', 'via', 'smd', 'polygon', 'circle', 'text']:
            object_counts[elem.tag] += 1

        if 'layer' in elem.attrib:
            num = elem.attrib['layer']
            name = layer_map.get(num, f"Layer_{num}")
            counts[name] += 1
            if elem.tag == 'smd':
                if num == '1': 
                    counts[layer_map.get('29', 'tStop')] += 1
                    counts[layer_map.get('31', 'tCream')] += 1
                elif num == '16':
                    counts[layer_map.get('30', 'bStop')] += 1
                    counts[layer_map.get('32', 'bCream')] += 1
        elif elem.tag in ['pad', 'via']:
            for l_implicit in ['17', '18', '1', '16', '29', '30']:
                counts[layer_map.get(l_implicit, f"Layer_{l_implicit}")] += 1

    return {k: v for k, v in counts.items() if v > 0}, layer_map, object_counts


_LAYER_DEFAULT_EXCLUDE_KEYWORDS = (
    'paste', 'hole', 'drill', 'silk', 'dimension', 'comment',
    'keepout', 'assembly', 'courtyard', 'doc', 'note', 'bplace', 'tplace',
    'bnames', 'tnames', 'bvalues', 'tvalues', 'document'
)


class LayerSelectionDialog(QDialog):
    def __init__(self, layer_counts, object_counts=None, selected_layers=None, selected_objects=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pilih Layer & Objek")
        self.resize(450, 550)

        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Vertical)

        # --- Panel Layer ---
        layer_box = QGroupBox("Layers")
        layer_layout = QVBoxLayout(layer_box)
        scroll_layer = QScrollArea()
        scroll_layer.setWidgetResizable(True)
        w_layer = QWidget()
        l_layer = QVBoxLayout(w_layer)
        
        self.layer_cbs = {}
        for lname in sorted(layer_counts.keys()):
            cb = QCheckBox(f"{lname} ({layer_counts[lname]})")
            
            # Jika ada state sebelumnya, pakai state tersebut. Jika belum, pakai default keyword filter
            if selected_layers is not None:
                cb.setChecked(lname in selected_layers)
            else:
                cb.setChecked(not any(kw in lname.lower() for kw in _LAYER_DEFAULT_EXCLUDE_KEYWORDS))
                
            l_layer.addWidget(cb)
            self.layer_cbs[lname] = cb
            
        l_layer.addStretch(1)
        scroll_layer.setWidget(w_layer)
        layer_layout.addWidget(scroll_layer)
        splitter.addWidget(layer_box)

        # --- Panel Object ---
        self.obj_cbs = {}
        if object_counts:
            obj_box = QGroupBox("Objects")
            obj_layout = QVBoxLayout(obj_box)
            for obj_name in sorted(object_counts.keys()):
                cb = QCheckBox(f"{obj_name.capitalize()} ({object_counts[obj_name]})")
                
                # Mengingat centang objek sebelumnya
                if selected_objects is not None:
                    cb.setChecked(obj_name in selected_objects)
                else:
                    cb.setChecked(obj_name != 'text') # Default: text di-uncheck
                    
                obj_layout.addWidget(cb)
                self.obj_cbs[obj_name] = cb
            splitter.addWidget(obj_box)

        layout.addWidget(splitter)
        
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_included_layers(self):
        # Kembalikan semua layer yang dicentang
        return [n for n, cb in self.layer_cbs.items() if cb.isChecked()]

    def get_included_objects(self):
        if not self.obj_cbs: return None
        return [n for n, cb in self.obj_cbs.items() if cb.isChecked()]


# ==============================================================================
# PARSER WORKER THREAD
# ==============================================================================
class ParserWorkerThread(QThread):
    finished = pyqtSignal(list, str, list)
    error = pyqtSignal(str)

    def __init__(self, filepath, config):
        super().__init__()
        self.filepath = filepath
        self.config = config
        self.diagnostics = {
            "layers_seen": Counter(),
            "layers_included": Counter(),
            "layers_excluded": Counter(),
            "parse_errors": 0,
            "outline_source": None,
        }

    def run(self):
        try:
            spot_size = self.config.get("spot_size_x", 0.06)
            max_error = self.config.get("flattening_max_error", 0.01)
            laser_mode = self.config.get("laser_mode", 0)

            raw_geometries = []
            ext = self.filepath.lower().split('.')[-1]

            if ext == 'dxf':
                self._parse_dxf(raw_geometries, max_error)
            elif ext == 'brd':
                self._parse_brd(raw_geometries)
            elif ext in ['gbr', 'gtl', 'gbl', 'gbp', 'gbo']:
                if not HAS_GERBER:
                    self.error.emit("Library 'pcb-tools' belum terinstal!\nKetik di terminal: pip install pcb-tools")
                    return
                self._parse_gerber(raw_geometries)
            elif ext in ['png', 'jpg', 'jpeg']:
                self._parse_raster(raw_geometries) 
            elif ext == 'svg':
                if not HAS_SVG:
                    self.error.emit("Library 'svgelements' belum terinstal!")
                    return
                self._parse_svg(raw_geometries)
            else:
                self.error.emit("Format file tidak didukung!")
                return

            if not raw_geometries:
                self.error.emit("Tidak ada entitas geometri yang berhasil diekstrak!")
                return

            cleaned_geometries = []
            if HAS_SHAPELY and self.config.get("use_drl_rules", False):
                try:
                    shapely_polys = []
                    for item in raw_geometries:
                        if isinstance(item, tuple):
                            shape_type, data = item
                            if shape_type == 'CIRCLE':
                                center, radius = data
                                circ = Point(center).buffer(max(radius, 0.001), resolution=32)
                                if circ.is_valid:
                                    shapely_polys.append(circ)
                            elif shape_type == 'POLYGON':
                                if len(data) >= 3:
                                    poly_obj = ShapelyPolygon(data)
                                    if not poly_obj.is_valid:
                                        poly_obj = poly_obj.buffer(0)
                                    if poly_obj.is_valid and not poly_obj.is_empty:
                                        shapely_polys.append(poly_obj)
                            elif shape_type == 'LINE':
                                if len(data) >= 2:
                                    line_obj = LineString(data)
                                    line_buf = line_obj.buffer(1, cap_style=1, join_style=1)
                                    if line_buf.is_valid and not line_buf.is_empty:
                                        shapely_polys.append(line_buf)
                        elif isinstance(item, ShapelyPolygon):
                            if not item.is_valid:
                                item = item.buffer(0)
                            if item.is_valid and not item.is_empty:
                                shapely_polys.append(item)
                        elif isinstance(item, LineString):
                            line_buf = item.buffer(0.02, cap_style=2, join_style=2)
                            if line_buf.is_valid and not line_buf.is_empty:
                                shapely_polys.append(line_buf)

                    if shapely_polys:
                        batch_size = 250
                        merged_poly = shapely_polys[0]
                        for i in range(1, len(shapely_polys), batch_size):
                            batch = shapely_polys[i:i+batch_size]
                            batch.append(merged_poly)
                            merged_poly = unary_union(batch)
                            
                        try:
                            step1 = merged_poly.buffer(0.02, join_style=2)
                            step2 = step1.buffer(0.03, join_style=2, mitre_limit=3.0)
                            merged_poly = step2.buffer(-0.05, join_style=1)
                        except Exception as e:
                            print(f"Healing warning: {e}")
                        
                        def extract_coords(geom):
                            if isinstance(geom, ShapelyPolygon):
                                if not geom.is_empty:
                                    cleaned_geometries.append(('POLYGON', list(geom.exterior.coords)))
                                    for interior in geom.interiors:
                                        cleaned_geometries.append(('POLYGON', list(interior.coords)))
                            elif isinstance(geom, MultiPolygon):
                                for p in geom.geoms:
                                    if not p.is_empty:
                                        cleaned_geometries.append(('POLYGON', list(p.exterior.coords)))
                                        for interior in p.interiors:
                                            cleaned_geometries.append(('POLYGON', list(interior.coords)))

                        extract_coords(merged_poly)
                        
                except Exception as e:
                    print(f"Union warning: {e}")

            if not cleaned_geometries:
                cleaned_geometries = raw_geometries

            parsed_shapes = []
            
            if laser_mode == 0:  
                for item in cleaned_geometries:
                    shape_type = item[0] if isinstance(item, tuple) else type(item).__name__
                    data = item[1] if isinstance(item, tuple) else None

                    if shape_type == 'POLYGON' and isinstance(item, tuple):
                        if HAS_SHAPELY and len(data) >= 3:                           
                            try:
                                poly = ShapelyPolygon(data)
                                if not poly.is_valid: poly = poly.buffer(0)
                                offset_poly = poly.buffer(spot_size / 2.0, cap_style=1, join_style=1)
                                
                                if isinstance(offset_poly, ShapelyPolygon) and not offset_poly.is_empty:
                                    parsed_shapes.append(('POLYGON', list(offset_poly.exterior.coords)))
                                    for interior in offset_poly.interiors:
                                        parsed_shapes.append(('POLYGON', list(interior.coords)))
                                elif isinstance(offset_poly, MultiPolygon):
                                    for sub_p in offset_poly.geoms:
                                        if not sub_p.is_empty:
                                            parsed_shapes.append(('POLYGON', list(sub_p.exterior.coords)))
                                            for interior in sub_p.interiors:
                                                parsed_shapes.append(('POLYGON', list(interior.coords)))
                                else:
                                    parsed_shapes.append(('POLYGON', data))
                            except:
                                parsed_shapes.append(('POLYGON', data))
                        else:
                            parsed_shapes.append(('POLYGON', data))
                    else:
                        parsed_shapes.append(item if isinstance(item, tuple) else ('POLYGON', list(item.exterior.coords)))
            
            elif laser_mode == 1:
                parsed_shapes = cleaned_geometries
                
            elif laser_mode == 2:
                if HAS_SHAPELY and cleaned_geometries:
                    try:
                        shapely_copper = []
                        for item in cleaned_geometries:
                            shape_type = item[0] if isinstance(item, tuple) else type(item).__name__
                            data = item[1] if isinstance(item, tuple) else None
                            if shape_type == 'POLYGON' and len(data) >= 3:
                                poly = ShapelyPolygon(data)
                                if not poly.is_valid: poly = poly.buffer(0)
                                if poly.is_valid: shapely_copper.append(poly)
                            elif shape_type == 'CIRCLE':
                                center, radius = data
                                poly = Point(center).buffer(max(radius, 0.001), resolution=32)
                                if poly.is_valid: shapely_copper.append(poly)
                                
                        if shapely_copper:
                            unified_copper = unary_union(shapely_copper)
                            minx, miny, maxx, maxy = unified_copper.bounds
                            
                            margin = 2.0  
                            bbox_poly = ShapelyPolygon([
                                (minx - margin, miny - margin),
                                (maxx + margin, miny - margin),
                                (maxx + margin, maxy + margin),
                                (minx - margin, maxy + margin)
                            ])
                            
                            negative_area = bbox_poly.difference(unified_copper)
                            
                            def extract_coords(geom):
                                if isinstance(geom, ShapelyPolygon) and not geom.is_empty:
                                    parsed_shapes.append(('POLYGON', list(geom.exterior.coords)))
                                    for interior in geom.interiors:
                                        parsed_shapes.append(('POLYGON', list(interior.coords)))
                                elif isinstance(geom, MultiPolygon):
                                    for p in geom.geoms:
                                        if not p.is_empty:
                                            parsed_shapes.append(('POLYGON', list(p.exterior.coords)))
                                            for interior in p.interiors:
                                                parsed_shapes.append(('POLYGON', list(interior.coords)))
                            
                            extract_coords(negative_area)
                        else:
                            parsed_shapes = cleaned_geometries
                    except Exception as e:
                        print(f"Error pada Negative Clearance: {e}")
                        parsed_shapes = cleaned_geometries
                else:
                    parsed_shapes = cleaned_geometries

            if not parsed_shapes:
                parsed_shapes = raw_geometries

            parsed_shapes = self._normalize_to_origin(parsed_shapes)
            gcode_str, toolpaths = self.generate_hatching_gcode(parsed_shapes, self.config, laser_mode)
            self.finished.emit(parsed_shapes, gcode_str, toolpaths)

        except Exception as e:
            self.error.emit(str(e))

    def _parse_gerber(self, raw_geometries):
        import builtins
        _original_open = builtins.open
        
        def _safe_open(*args, **kwargs):
            if len(args) > 1 and args[1] == 'rU':
                args = list(args)
                args[1] = 'r'
                args = tuple(args)
            if kwargs.get('mode') == 'rU':
                kwargs['mode'] = 'r'
            return _original_open(*args, **kwargs)

        try:
            builtins.open = _safe_open
            cam = gerber.read(self.filepath)
            builtins.open = _original_open
            cam.to_metric()
            
            def get_arc_points(start, end, center, direction, num_points=16):
                cx, cy = center
                sx, sy = start
                ex, ey = end
                
                angle_start = math.atan2(sy - cy, sx - cx)
                angle_end = math.atan2(ey - cy, ex - cx)
                
                if direction == 'counterclockwise':
                    if angle_end <= angle_start:
                        angle_end += 2 * math.pi
                else:
                    if angle_end >= angle_start:
                        angle_start += 2 * math.pi
                        
                pts = []
                r = math.hypot(sx - cx, sy - cy)
                
                for i in range(num_points + 1):
                    t = i / num_points
                    angle = angle_start + (angle_end - angle_start) * t
                    pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
                    
                return pts

            for prim in cam.primitives:
                p_type = type(prim).__name__
                
                if p_type == 'Line':
                    p1, p2 = prim.start, prim.end
                    width = 0
                    if hasattr(prim, 'aperture') and prim.aperture:
                        width = getattr(prim.aperture, 'diameter', getattr(prim.aperture, 'width', 0))
                    
                    if width > 0 and HAS_SHAPELY:
                        line = LineString([p1, p2])
                        poly = line.buffer(width / 2.0, cap_style=1, join_style=1)
                        if poly.is_valid and not poly.is_empty:
                            raw_geometries.append(('POLYGON', list(poly.exterior.coords)))
                    else:
                        raw_geometries.append(('LINE', [p1, p2]))
                
                elif p_type == 'Circle':
                    center = prim.position
                    radius = getattr(prim, 'radius', getattr(prim, 'diameter', 0) / 2.0)
                    if radius > 0:
                        raw_geometries.append(('CIRCLE', (center, radius)))
                        
                elif p_type == 'Rectangle':
                    x, y = prim.position
                    w, h = prim.width, prim.height
                    pts = [(x-w/2, y-h/2), (x+w/2, y-h/2), (x+w/2, y+h/2), (x-w/2, y+h/2)]
                    raw_geometries.append(('POLYGON', pts))
                    
                elif p_type == 'Obround':
                    x, y = prim.position
                    w, h = prim.width, prim.height
                    if HAS_SHAPELY:
                        if w > h:
                            line = LineString([(x - w/2 + h/2, y), (x + w/2 - h/2, y)])
                            poly = line.buffer(h/2, resolution=16)
                        else:
                            line = LineString([(x, y - h/2 + w/2), (x, y + h/2 - w/2)])
                            poly = line.buffer(w/2, resolution=16)
                        if poly.is_valid and not poly.is_empty:
                            raw_geometries.append(('POLYGON', list(poly.exterior.coords)))
                    else:
                        pts = [(x-w/2, y-h/2), (x+w/2, y-h/2), (x+w/2, y+h/2), (x-w/2, y+h/2)]
                        raw_geometries.append(('POLYGON', pts))
                        
                elif p_type == 'Region':
                    pts = []
                    for b_prim in prim.primitives:
                        b_type = type(b_prim).__name__
                        if b_type == 'Line':
                            if not pts: pts.append(b_prim.start)
                            pts.append(b_prim.end)
                        elif b_type == 'Arc':
                            arc_pts = get_arc_points(b_prim.start, b_prim.end, b_prim.center, b_prim.direction)
                            if not pts: pts.extend(arc_pts)
                            else: pts.extend(arc_pts[1:])
                    if len(pts) >= 3:
                        raw_geometries.append(('POLYGON', pts))

                elif p_type == 'Arc':
                    width = 0
                    if hasattr(prim, 'aperture') and prim.aperture:
                        width = getattr(prim.aperture, 'diameter', getattr(prim.aperture, 'width', 0))
                        
                    arc_pts = get_arc_points(prim.start, prim.end, prim.center, prim.direction)
                    if width > 0 and HAS_SHAPELY:
                        line = LineString(arc_pts)
                        poly = line.buffer(width / 2.0, cap_style=1, join_style=1)
                        if poly.is_valid and not poly.is_empty:
                            raw_geometries.append(('POLYGON', list(poly.exterior.coords)))
                    else:
                        raw_geometries.append(('LINE', arc_pts))
                        
        except Exception as e:
            self.diagnostics["parse_errors"] += 1
            print(f"Gerber Parsing Error: {e}")

    def _parse_brd(self, raw_geometries):
        tree = ET.parse(self.filepath)
        root = tree.getroot()
        diag = self.diagnostics
        included_layers = self.config.get("included_layers")
        
        layer_map = {}
        for layer_def in root.iter('layer'):
            num = layer_def.get('number')
            name = layer_def.get('name')
            if num and name: layer_map[num] = name

        def is_item_valid(item):
            included_objects = self.config.get("included_objects")
            if included_objects is not None and item.tag not in included_objects:
                return False
            
            l_num = item.get('layer')
            valid = False
            
            if l_num:
                lname = layer_map.get(str(l_num), f"Layer_{l_num}")
                diag["layers_seen"][lname] += 1
                
                if included_layers is None or lname in included_layers:
                    valid = True
                
                if item.tag == 'smd' and included_layers is not None:
                    tstop = layer_map.get('29', 'tStop')
                    tcream = layer_map.get('31', 'tCream')
                    bstop = layer_map.get('30', 'bStop')
                    bcream = layer_map.get('32', 'bCream')
                    
                    if l_num == '1' and (tstop in included_layers or tcream in included_layers): 
                        valid = True
                    elif l_num == '16' and (bstop in included_layers or bcream in included_layers): 
                        valid = True

                if included_layers is not None:
                    if valid: 
                        diag["layers_included"][lname] += 1
                    else: 
                        diag["layers_excluded"][lname] += 1
                    
            else:
                if item.tag in ['pad', 'via']:
                    if included_layers is None:
                        valid = True
                    else:
                        implicit = [
                            layer_map.get('17', 'Pads'), layer_map.get('18', 'Vias'),
                            layer_map.get('1', 'Top'), layer_map.get('16', 'Bottom'),
                            layer_map.get('29', 'tStop'), layer_map.get('30', 'bStop')
                        ]
                        if any(l in included_layers for l in implicit):
                            valid = True
            return valid

        packages = {}
        for lib in root.iter('library'):
            lib_name = lib.get('name')
            for pkg in lib.iter('package'):
                packages[f"{lib_name}_{pkg.get('name')}"] = pkg

        def parse_rot(r_str):
            if not r_str: return 0.0, False
            r_str = r_str.upper()
            mirror = r_str.startswith('M')
            if mirror: r_str = r_str[1:]
            angle = float(r_str[1:]) if r_str.startswith('R') else 0.0
            return angle, mirror

        def transform(x, y, dx, dy, angle_deg, mirror):
            a = math.radians(angle_deg)
            if mirror: x = -x
            nx = x * math.cos(a) - y * math.sin(a)
            ny = x * math.sin(a) + y * math.cos(a)
            return nx + dx, ny + dy

        for elem in root.iter('element'):
            pkg = packages.get(f"{elem.get('library')}_{elem.get('package')}")
            if pkg is None: continue
            
            ex, ey = float(elem.get('x', 0)), float(elem.get('y', 0))
            erot, emirror = parse_rot(elem.get('rot'))
            
            for item in pkg:
                if not is_item_valid(item): continue
                
                if item.tag in ['pad', 'smd']:
                    px, py = float(item.get('x', 0)), float(item.get('y', 0))
                    tx, ty = transform(px, py, ex, ey, erot, emirror)
                    
                    if item.tag == 'smd':
                        dx, dy = float(item.get('dx', 0)) / 2.0, float(item.get('dy', 0)) / 2.0
                        c1, c2 = transform(-dx, -dy, 0, 0, erot, emirror), transform(dx, -dy, 0, 0, erot, emirror)
                        c3, c4 = transform(dx, dy, 0, 0, erot, emirror), transform(-dx, dy, 0, 0, erot, emirror)
                        raw_geometries.append(('POLYGON', [(tx+c1[0], ty+c1[1]), (tx+c2[0], ty+c2[1]), (tx+c3[0], ty+c3[1]), (tx+c4[0], ty+c4[1])]))
                    else:
                        drill = float(item.get('drill', 0.8))
                        diameter = float(item.get('diameter', drill * 1.6))
                        raw_geometries.append(('CIRCLE', ((tx, ty), diameter / 2.0)))
                        
                elif item.tag == 'wire':
                    x1, y1 = float(item.get('x1', 0)), float(item.get('y1', 0))
                    x2, y2 = float(item.get('x2', 0)), float(item.get('y2', 0))
                    tx1, ty1 = transform(x1, y1, ex, ey, erot, emirror)
                    tx2, ty2 = transform(x2, y2, ex, ey, erot, emirror)
                    width = float(item.get('width', 0))
                    
                    if width > 0 and HAS_SHAPELY:
                        line = LineString([(tx1, ty1), (tx2, ty2)])
                        poly = line.buffer(width / 2.0, cap_style=1, join_style=1)
                        if poly.is_valid and not poly.is_empty:
                            raw_geometries.append(('POLYGON', list(poly.exterior.coords)))
                    else:
                        raw_geometries.append(('LINE', [(tx1, ty1), (tx2, ty2)]))

        for section in ['plain', 'signals']:
            for sec_node in root.iter(section):
                for item in sec_node.iter():
                    if not is_item_valid(item): continue
                    
                    if item.tag == 'wire':
                        x1, y1 = float(item.get('x1', 0)), float(item.get('y1', 0))
                        x2, y2 = float(item.get('x2', 0)), float(item.get('y2', 0))
                        width = float(item.get('width', 0))
                        
                        if width > 0 and HAS_SHAPELY:
                            line = LineString([(x1, y1), (x2, y2)])
                            poly = line.buffer(width / 2.0, cap_style=1, join_style=1)
                            if poly.is_valid and not poly.is_empty:
                                raw_geometries.append(('POLYGON', list(poly.exterior.coords)))
                        else:
                            raw_geometries.append(('LINE', [(x1, y1), (x2, y2)]))
                    
                    elif item.tag == 'via':
                        x, y = float(item.get('x', 0)), float(item.get('y', 0))
                        drill = float(item.get('drill', 0.8))
                        raw_geometries.append(('CIRCLE', ((x, y), float(item.get('diameter', drill * 1.6)) / 2.0)))
                        
                    elif item.tag == 'polygon':
                        width = float(item.get('width', 0))
                        pts = [(float(v.get('x')), float(v.get('y'))) for v in item.iter('vertex')]
                        if len(pts) >= 3:
                            if HAS_SHAPELY:
                                poly = ShapelyPolygon(pts)
                                if width > 0: poly = poly.buffer(width / 2.0, join_style=1)
                                if poly.is_valid and not poly.is_empty:
                                    raw_geometries.append(('POLYGON', list(poly.exterior.coords)))
                            else:
                                raw_geometries.append(('POLYGON', pts))

    def _parse_dxf(self, raw_geometries, max_error):
        doc = ezdxf.readfile(self.filepath)
        msp = doc.modelspace()

        diag = self.diagnostics
        included_layers = self.config.get("included_layers")

        def _parse_entity(entity):
            dxftype = entity.dxftype()
            layer_name = getattr(entity.dxf, 'layer', '0') or '0'
            diag["layers_seen"][layer_name] += 1

            if included_layers is not None and layer_name not in included_layers:
                diag["layers_excluded"][layer_name] += 1
                return

            used = False
            try:
                if dxftype == 'CIRCLE':
                    center = (entity.dxf.center.x, entity.dxf.center.y)
                    radius = entity.dxf.radius
                    if radius > 0:
                        raw_geometries.append(('CIRCLE', (center, radius)))
                        used = True
                elif dxftype in ('SOLID', 'TRACE'):
                    pts = [
                        (entity.dxf.vtx0.x, entity.dxf.vtx0.y), (entity.dxf.vtx1.x, entity.dxf.vtx1.y),
                        (entity.dxf.vtx3.x, entity.dxf.vtx3.y), (entity.dxf.vtx2.x, entity.dxf.vtx2.y)
                    ]
                    pts.append(pts[0])
                    raw_geometries.append(('LINE', pts))
                    used = True
                elif dxftype in ('LWPOLYLINE', 'POLYLINE', 'LINE', 'ARC', 'ELLIPSE', 'SPLINE', 'HATCH'):
                    paths = ezdxf_path.make_paths_from_entity(entity) if hasattr(ezdxf_path, 'make_paths_from_entity') else [ezdxf_path.make_path(entity)]
                    for p in paths:
                        pts = [(pt.x, pt.y) for pt in p.flattening(distance=max_error)]
                        if len(pts) < 2: continue
                        is_closed = True if p.is_closed or math.hypot(pts[0][0]-pts[-1][0], pts[0][1]-pts[-1][1]) < 0.001 else False
                        if is_closed:
                            if pts[0] != pts[-1]: pts.append(pts[0])
                        
                        raw_geometries.append(('LINE', pts))
                        used = True
            except Exception:
                diag["parse_errors"] += 1
                return

            if used:
                diag["layers_included"][layer_name] += 1

        for entity in msp:
            if entity.dxftype() == 'INSERT':
                try:
                    for virt_ent in entity.virtual_entities(): _parse_entity(virt_ent)
                except Exception:
                    diag["parse_errors"] += 1
            else: _parse_entity(entity)
            
        if not raw_geometries:
            for entity in doc.paperspace(): _parse_entity(entity)

    def _parse_raster(self, raw_geometries):
        threshold_val = self.config.get("png_threshold", 0)
        scale = self.config.get("svg_png_scale", 0.2645)
        
        img = cv2.imread(self.filepath, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("File gambar tidak dapat dibaca.")
            
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        
        height, width = blurred.shape
        max_area = height * width * 0.40

        if threshold_val <= 0:
            _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        else:
            _, thresh = cv2.threshold(blurred, int(threshold_val), 255, cv2.THRESH_BINARY)

        contours, hierarchy = cv2.findContours(thresh, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)

        if hierarchy is not None:
            hierarchy = hierarchy[0]
            polygons = {}
            holes = {}   
            
            for i, cnt in enumerate(contours):
                area = cv2.contourArea(cnt)
                if area > 5.0 and len(cnt) >= 3:
                    pts = [((pt[0][0] * scale), ((height - pt[0][1]) * scale)) for pt in cnt]
                    parent_idx = hierarchy[i][3]
                    
                    if parent_idx == -1:
                        if area < max_area: 
                            polygons[i] = polygons.get(i, pts)
                    else:
                        if parent_idx not in holes:
                            holes[parent_idx] = []
                        holes[parent_idx].append(pts)
            
            for parent_idx, exterior in polygons.items():
                interiors = holes.get(parent_idx, [])
                raw_geometries.append(ShapelyPolygon(exterior, interiors))
                
    def _parse_svg(self, raw_geometries):
        scale = self.config.get("svg_png_scale", 0.2645)
        svg = SVG.parse(self.filepath)
        
        for element in svg.elements():
            try:
                if type(element).__name__ in ('Path', 'Polygon', 'Polyline', 'Rect', 'Circle', 'Ellipse'):
                    path = SVGPath(element)
                    path.reify()
                    pts = []
                    length = path.length()
                    if length > 0:
                        samples = max(int(length), 10)
                        for i in range(samples + 1):
                            pt = path.point(i / samples)
                            pts.append((pt.x * scale, -pt.y * scale))
                    if len(pts) >= 3:
                        raw_geometries.append(('POLYGON', pts))
            except Exception:
                pass

    def generate_hatching_gcode(self, parsed_shapes, config, laser_mode):
        spot_size = config.get("spot_size_x", 0.06)
        max_error = config.get("flattening_max_error", 0.01)

        gcode = []
        gcode.append("; --- Generated by LoGoRo Laser Parser ---")
        gcode.append(f"; Mode: {['Isolation Routing', 'Full Hatching', 'Negative Clearance'][laser_mode]}")
        gcode.append(f"; Precision: {max_error} mm | Spot Size: {spot_size} mm")
        gcode.append("G21 ; Millimeters")
        gcode.append("G90 ; Absolute positioning")
        gcode.append("M5  ; Laser OFF")
        gcode.append("G0 X0.0000 Y0.0000 ; Home")
        gcode.append("")

        passes = []
        for i, power in enumerate(config.get('phases_power', [])):
            passes.append((f"Phase {i+1}", power))

        toolpaths = []

        for shape_type, data in parsed_shapes:
            if shape_type == 'CIRCLE':
                center, radius = data
                if laser_mode == 0:
                    pts = []
                    segments = max(16, int(2 * math.pi * radius / 0.05))
                    for i in range(segments + 1):
                        angle = 2 * math.pi * i / segments
                        pts.append((center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)))
                    toolpaths.append(pts)
                else:
                    curr_r = radius
                    while curr_r > 0:
                        pts = []
                        segments = max(16, int(2 * math.pi * curr_r / 0.05))
                        for i in range(segments + 1):
                            angle = 2 * math.pi * i / segments
                            pts.append((center[0] + curr_r * math.cos(angle), center[1] + curr_r * math.sin(angle)))
                        toolpaths.append(pts)
                        curr_r -= spot_size

            elif shape_type == 'POLYGON':
                pts = data
                if laser_mode == 0:
                    toolpaths.append(pts)
                else:
                    if HAS_SHAPELY and len(pts) >= 3:
                        try:
                            poly = ShapelyPolygon(pts).buffer(0)
                            curr_poly = poly
                            while not curr_poly.is_empty:
                                if isinstance(curr_poly, ShapelyPolygon):
                                    toolpaths.append(list(curr_poly.exterior.coords))
                                elif isinstance(curr_poly, MultiPolygon):
                                    for sub_p in curr_poly.geoms:
                                        toolpaths.append(list(sub_p.exterior.coords))
                                curr_poly = curr_poly.buffer(-spot_size)
                        except Exception:
                            toolpaths.append(pts)
                    else:
                        toolpaths.append(pts)

            elif shape_type == 'LINE':
                toolpaths.append(data)

        for pass_name, power in passes:
            gcode.append(f"; --------------------------------------------------")
            gcode.append(f"; {pass_name} - Power S{power}")
            gcode.append(f"; --------------------------------------------------")
            
            for path in toolpaths:
                if not path or len(path) < 2:
                    continue
                
                start_pt = path[0]
                gcode.append("M5")
                gcode.append(f"G0 X{start_pt[0]:.4f} Y{start_pt[1]:.4f}")
                gcode.append(f"M4 S{int(power)}")
                
                for pt in path[1:]:
                    gcode.append(f"G1 X{pt[0]:.4f} Y{pt[1]:.4f} F{config['max_feedrate_g1']}")
                
                gcode.append("M5 ; Laser OFF")
                gcode.append("")

        gcode.append("M5 ; Ensure Laser OFF")
        gcode.append("G0 X0.0000 Y0.0000 ; Home")
        gcode.append("M30 ; End Program")

        return "\n".join(gcode), toolpaths

    def _normalize_to_origin(self, geometries):
        """Menggeser seluruh geometri agar titik kiri-bawah tepat di (0,0)"""
        min_x = float('inf')
        min_y = float('inf')

        # 1. Cari titik terkecil (min_x, min_y)
        for item in geometries:
            shape_type, data = item
            if shape_type == 'CIRCLE':
                center, radius = data
                min_x = min(min_x, center[0] - radius)
                min_y = min(min_y, center[1] - radius)
            elif shape_type in ('POLYGON', 'LINE'):
                for x, y in data:
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)

        if min_x == float('inf') or min_y == float('inf'):
            return geometries

        # 2. Translasikan seluruh titik dikurangi min_x dan min_y
        normalized = []
        for item in geometries:
            shape_type, data = item
            if shape_type == 'CIRCLE':
                center, radius = data
                new_center = (center[0] - min_x, center[1] - min_y)
                normalized.append(('CIRCLE', (new_center, radius)))
            elif shape_type in ('POLYGON', 'LINE'):
                new_pts = [(x - min_x, y - min_y) for x, y in data]
                normalized.append((shape_type, new_pts))

        return normalized


# ==============================================================================
# MAIN WINDOW APPLICATION
# ==============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sculpfun C1 Mini - Multi-Format (DXF/BRD/GBR/PNG/SVG) to PCB G-Code")
        self.resize(1250, 850)

        self.is_auto_process = False

        self.current_parsed_shapes = []
        self.current_toolpaths = []
        self.filepath = ""
        self.worker = None
        self.layer_config = {"included_layers": None, "outline_layer": "AUTO"}

        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # --- LEFT PANEL (SETTINGS & G-CODE EDITOR) ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        config_box = QGroupBox("Config Panel")
        config_grid = QGridLayout(config_box)

        config_grid.addWidget(QLabel("Laser Working Mode"), 0, 0)
        self.combo_mode = QComboBox()
        self.combo_mode.addItems([
            "Isolation Routing (Parit/Cepat)", 
            "Full Hatching (Arsir Penuh)", 
            "Negative Clearance (Area Kosong)"
        ])
        config_grid.addWidget(self.combo_mode, 0, 1)
        self.combo_mode.currentIndexChanged.connect(self.auto_convert_gcode)

        self.inputs = {}
        default_config = [
            ("Curve Precision Error (mm)", "flattening_max_error", "0.01"),
            ("Spot Size X (mm)", "spot_size_x", "0.06"),
            ("Spot Size Y (mm)", "spot_size_y", "0.08"),
            ("PNG Threshold (0=Auto Otsu)", "png_threshold", "0"),
            ("SVG/PNG Scale (px to mm)", "svg_png_scale", "0.2645"),
            ("Max G0 Feedrate (mm/min)", "max_feedrate_g0", "3000"),
            ("Max G1 Feedrate (mm/min)", "max_feedrate_g1", "1000"),
            ("Acceleration (mm/s²)", "acceleration", "500"),
            ("Laser Power Max ($30)", "laser_power_max", "1000"),
        ]

        row = 1
        for label, key, val in default_config:
            config_grid.addWidget(QLabel(label), row, 0)
            txt_input = QLineEdit(val)
            config_grid.addWidget(txt_input, row, 1)
            self.inputs[key] = txt_input
            row += 1

        drl_layout = QHBoxLayout()
        self.chk_drl = QCheckBox("DRL Test")
        self.btn_check_drl = QPushButton("Check")
        self.btn_check_drl.clicked.connect(self.run_drl_check)
        
        drl_layout.addWidget(self.chk_drl)
        drl_layout.addWidget(self.btn_check_drl)
        config_grid.addLayout(drl_layout, row, 0, 1, 2)
		
        left_layout.addWidget(config_box)

        btn_layout = QHBoxLayout()
        self.btn_open = QPushButton("Open File")
        self.btn_open.clicked.connect(self.open_file)
        btn_layout.addWidget(self.btn_open)

        self.btn_layers = QPushButton("Pilih Layer...")
        self.btn_layers.setToolTip("Pilih layer mana yang diproses (DXF/BRD).")
        self.btn_layers.clicked.connect(self.configure_layers)
        self.btn_layers.setEnabled(False)
        btn_layout.addWidget(self.btn_layers)

        self.btn_convert = QPushButton("Convert G-Code")
        self.btn_convert.clicked.connect(self.convert_file_to_gcode)
        btn_layout.addWidget(self.btn_convert)

        self.btn_export = QPushButton("Export")
        self.btn_export.clicked.connect(self.export_gcode)
        btn_layout.addWidget(self.btn_export)

        left_layout.addLayout(btn_layout)
        
        self.phase_inputs = [] 
        # --- LASER PHASES WINDOW ---
        phases_group = QGroupBox("Laser Phases (Passes)")
        phases_outer_layout = QVBoxLayout(phases_group)
        self.phases_content_widget = QWidget()
        self.phases_layout = QVBoxLayout(self.phases_content_widget)
        self.phases_layout.setContentsMargins(0, 0, 0, 0)
        scroll_phases = QScrollArea()
        scroll_phases.setWidgetResizable(True)
        scroll_phases.setWidget(self.phases_content_widget)
        scroll_phases.setMaximumHeight(120) 
        scroll_phases.setFrameShape(QScrollArea.NoFrame) 
        self.btn_tambah_phase = QPushButton("➕ Tambah Phase")
        self.btn_tambah_phase.setStyleSheet("background-color: #2980b9; color: white;")
        self.btn_tambah_phase.clicked.connect(self.add_phase)

        phases_outer_layout.addWidget(scroll_phases)
        phases_outer_layout.addWidget(self.btn_tambah_phase)

        self.phase_inputs = []
        self.add_phase("700")

        left_layout.addWidget(phases_group) 

        editor_box = QGroupBox("G-Code Preview")
        editor_layout = QVBoxLayout(editor_box)
        self.gcode_editor = QTextEdit()
        self.gcode_editor.setPlaceholderText("G-Code hasil konversi...")
        editor_layout.addWidget(self.gcode_editor)

        left_layout.addWidget(editor_box)
        splitter.addWidget(left_panel)

        # --- RIGHT PANEL (PyQt5 GCodeCanvas Visualizer) ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        preview_layout = QHBoxLayout()
        preview_layout.addWidget(QLabel("Preview Mode:"))
        self.combo_preview = QComboBox()
        self.combo_preview = QComboBox()
        self.combo_preview.addItems([
            "Design (Solid Area)", 
            "G-Code (Laser Toolpaths)", 
            "ALL G-Code "
        ])
        self.combo_preview.currentIndexChanged.connect(self.update_preview)
        preview_layout.addWidget(self.combo_preview)
        preview_layout.addStretch()
        right_layout.addLayout(preview_layout)

        # Penggantian FastPlotCanvas dengan GCodeCanvas
        self.canvas = GCodeCanvas(self)
        right_layout.addWidget(self.canvas)

        splitter.addWidget(right_panel)
        splitter.setSizes([450, 800])

    def auto_convert_gcode(self):
        """Memicu konversi tanpa menampilkan QMessageBox success"""
        if self.filepath:
            self.is_auto_process = True
            self.convert_file_to_gcode()
        
    def add_phase(self, default_power=False):
        if not isinstance(default_power, str):
            default_power = "1000" 

        phase_index = len(self.phase_inputs) + 1
        
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        
        lbl = QLabel(f"Phase {phase_index} Power (S):")
        inp = QLineEdit(default_power)
        
        btn_del = QPushButton("🗑️")
        btn_del.setFixedWidth(30)
        btn_del.setStyleSheet("color: red;")
        btn_del.clicked.connect(lambda: self.remove_phase(row_widget, inp))
        
        row_layout.addWidget(lbl)
        row_layout.addWidget(inp)
        row_layout.addWidget(btn_del)
        
        self.phases_layout.addWidget(row_widget)
        self.phase_inputs.append(inp)

        if hasattr(self, 'filepath') and self.filepath:
            self.auto_convert_gcode()

    def remove_phase(self, row_widget, inp):
        if len(self.phase_inputs) <= 1:
            QMessageBox.warning(self, "Warning", "Minimal harus ada 1 Laser Phase!")
            return

        if inp in self.phase_inputs:
            self.phase_inputs.remove(inp)
            
        row_widget.deleteLater()

        for i, input_field in enumerate(self.phase_inputs):
            parent_widget = input_field.parentWidget()
            if parent_widget:
                label = parent_widget.findChild(QLabel)
                if label:
                    label.setText(f"Phase {i + 1} Power (S):")

        self.auto_convert_gcode()

    def get_config_dict(self):
        try:
            return {
                "laser_mode": self.combo_mode.currentIndex(),
                "flattening_max_error": float(self.inputs["flattening_max_error"].text()),
                "spot_size_x": float(self.inputs["spot_size_x"].text()),
                "spot_size_y": float(self.inputs["spot_size_y"].text()),
                "png_threshold": int(self.inputs["png_threshold"].text()),
                "svg_png_scale": float(self.inputs["svg_png_scale"].text()),
                "max_feedrate_g0": float(self.inputs["max_feedrate_g0"].text()),
                "max_feedrate_g1": float(self.inputs["max_feedrate_g1"].text()),
                "acceleration": float(self.inputs["acceleration"].text()),
                "laser_power_max": int(self.inputs["laser_power_max"].text()),
                "use_drl_rules": self.chk_drl.isChecked(),
                "phases_power": [int(inp.text()) for inp in self.phase_inputs]
            }
        except ValueError as e:
            QMessageBox.critical(self, "Input Error", f"Format parameter tidak valid: {str(e)}")
            return None

    def open_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Buka File", "", 
            "Supported Files (*.dxf *.brd *.gbr *.gtl *.gbl *.png *.svg);;DXF Files (*.dxf);;BRD Files (*.brd);;Gerber Files (*.gbr *.gtl *.gbl *.gbp *.gbo);;PNG Files (*.png);;SVG Files (*.svg)"
        )
        if not filepath:
            return

        self.filepath = filepath
        self.layer_config = {"included_layers": None, "outline_layer": "AUTO"}
        
        is_layered_file = filepath.lower().endswith('.dxf') or filepath.lower().endswith('.brd')
        self.btn_layers.setEnabled(is_layered_file)

        if is_layered_file:
            if not self._prompt_layer_selection(filepath):
                return

        self.convert_file_to_gcode()

    def _prompt_layer_selection(self, filepath):
        try:
            object_counts = None
            if filepath.lower().endswith('.dxf'):
                layer_counts = scan_dxf_layers(filepath)
            elif filepath.lower().endswith('.brd'):
                layer_counts, _, object_counts = scan_brd_layers(filepath)
        except Exception as e:
            QMessageBox.critical(self, "Gagal Membaca File", f"Tidak dapat memindai layer:\n{str(e)}")
            return False

        if not layer_counts:
            QMessageBox.warning(self, "Warning", "Tidak ada entitas ber-layer yang ditemukan di dalam file ini.")
            return False

        # Ambil state terlampir jika ada
        curr_layers = self.layer_config.get("included_layers")
        curr_objs = self.layer_config.get("included_objects")

        # Pass curr_layers & curr_objs ke dialog
        dialog = LayerSelectionDialog(layer_counts, object_counts, curr_layers, curr_objs, self)
        if dialog.exec_() != QDialog.Accepted:
            return False

        # Simpan hasil pilihan baru ke layer_config
        self.layer_config = {
            "included_layers": dialog.get_included_layers(),
            "included_objects": dialog.get_included_objects(),
            "outline_layer": "AUTO" 
        }
        return True

    def configure_layers(self):
        is_layered_file = self.filepath.lower().endswith('.dxf') or self.filepath.lower().endswith('.brd')
        if not self.filepath or not is_layered_file:
            QMessageBox.warning(self, "Warning", "Buka file DXF atau BRD terlebih dahulu!")
            return
        
        if self._prompt_layer_selection(self.filepath):
            self.convert_file_to_gcode()

    def run_drl_check(self):
        if not self.current_parsed_shapes:
            QMessageBox.warning(self, "DRL Check", "Muat file terlebih dahulu (Viewer mode).")
            return
            
        if not HAS_SHAPELY:
            QMessageBox.warning(self, "DRL Check", "Library Shapely tidak terinstal. DRL tidak dapat dijalankan.")
            return

        try:
            spot_size = float(self.inputs["spot_size_x"].text())
            min_clearance = spot_size
        except ValueError:
            min_clearance = 0.06

        shapely_geoms = []
        for shape_type, data in self.current_parsed_shapes:
            try:
                if shape_type == 'LINE':
                    geom = LineString(data)
                    if geom.is_valid: shapely_geoms.append(geom)
                elif shape_type == 'POLYGON':
                    geom = ShapelyPolygon(data)
                    if not geom.is_valid: geom = geom.buffer(0)
                    if geom.is_valid: shapely_geoms.append(geom)
                elif shape_type == 'CIRCLE':
                    center, radius = data
                    geom = Point(center).buffer(max(radius, 0.001), resolution=32)
                    if geom.is_valid: shapely_geoms.append(geom)
            except Exception:
                continue

        violations = 0
        total_geoms = len(shapely_geoms)
        
        for i in range(total_geoms):
            for j in range(i + 1, total_geoms):
                dist = shapely_geoms[i].distance(shapely_geoms[j])
                if 0.001 < dist < min_clearance:
                    violations += 1

        if violations > 0:
            msg = f"Ditemukan Pelanggaran DRL!\n\nAda {violations} area yang jarak antar jalurnya lebih kecil dari ukuran spot laser (< {min_clearance} mm).\nIni berisiko menyebabkan short-circuit (jalur menyatu/tidak terpotong sempurna)."
            QMessageBox.warning(self, "DRL Check Result", msg)
        else:
            QMessageBox.information(self, "DRL Check Result", "Desain Aman!\nTidak ditemukan jalur yang jaraknya melanggar batas spot size laser.")

    def convert_file_to_gcode(self):
        if not self.filepath:
            QMessageBox.warning(self, "Warning", "Buka file terlebih dahulu!")
            return

        config = self.get_config_dict()
        if not config:
            return

        if self.filepath.lower().endswith('.dxf') or self.filepath.lower().endswith('.brd'):
            config.update(self.layer_config)

        self.btn_open.setEnabled(False)
        self.btn_layers.setEnabled(False)
        self.btn_convert.setEnabled(False)
        self.btn_convert.setText("Memproses...")

        self.worker = ParserWorkerThread(self.filepath, config)
        self.worker.finished.connect(self.on_processing_finished)
        self.worker.error.connect(self.on_processing_error)
        self.worker.start()

    def on_processing_finished(self, parsed_shapes, gcode_str, toolpaths):
        self.current_parsed_shapes = parsed_shapes
        self.current_toolpaths = toolpaths
        
        self.update_preview()
        
        self.gcode_editor.clear()
        self.gcode_editor.setText(gcode_str)

        self.btn_open.setEnabled(True)
        is_layered_file = self.filepath.lower().endswith('.dxf') or self.filepath.lower().endswith('.brd')
        self.btn_layers.setEnabled(is_layered_file)
        self.btn_convert.setEnabled(True)
        self.btn_convert.setText("Convert G-Code")

        if not self.is_auto_process:
            summary = f"Berhasil memproses file!\nTotal Elemen Jalur: {len(self.current_parsed_shapes)}"

            diag = getattr(self.worker, "diagnostics", None) if self.worker else None
            if diag and diag.get("layers_seen"):
                summary += "\n\n--- Rincian Layer Terdeteksi ---"
                for layer_name in sorted(diag["layers_seen"].keys()):
                    seen = diag["layers_seen"][layer_name]
                    included = diag["layers_included"].get(layer_name, 0)
                    excluded = diag["layers_excluded"].get(layer_name, 0)
                    if excluded:
                        summary += f"\n{layer_name}: {seen} entitas (dilewati/di-filter)"
                    else:
                        summary += f"\n{layer_name}: {seen} entitas ({included} terpakai)"
                if diag.get("parse_errors"):
                    summary += f"\nEntitas gagal dibaca: {diag['parse_errors']}"

            QMessageBox.information(self, "Success", summary)
        
        self.is_auto_process = False

    def on_processing_error(self, err_msg):
        self.btn_open.setEnabled(True)
        is_layered_file = self.filepath.lower().endswith('.dxf') or self.filepath.lower().endswith('.brd') if self.filepath else False
        self.btn_layers.setEnabled(is_layered_file)
        self.btn_convert.setEnabled(True)
        self.btn_convert.setText("2. Convert G-Code")
        QMessageBox.critical(self, "Parsing Error", f"Gagal membaca file: {err_msg}")

    def export_gcode(self):
        gcode_content = self.gcode_editor.toPlainText()
        if not gcode_content.strip():
            QMessageBox.warning(self, "Warning", "Tidak ada G-Code untuk diekspor!")
            return

        filepath, _ = QFileDialog.getSaveFileName(
            self, "Simpan G-Code Presisi Tinggi", "", "G-Code Files (*.gcode *.ngc *.nc);;All Files (*)"
        )
        if filepath:
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(gcode_content)
                QMessageBox.information(self, "Success", f"File G-Code berhasil disimpan ke:\n{filepath}")
            except Exception as e:
                QMessageBox.critical(self, "Save Error", f"Gagal menyimpan file: {str(e)}")
                
    def update_preview(self):
        idx = self.combo_preview.currentIndex()
        if idx == 0:
            self.canvas.plot_data(self.current_parsed_shapes)
        elif idx == 1:
            if hasattr(self, 'current_toolpaths') and self.current_toolpaths:
                self.canvas.plot_toolpaths(self.current_toolpaths)
            else:
                self.canvas.plot_toolpaths([])
        elif idx == 2:
            gcode_text = self.gcode_editor.toPlainText()
            self.canvas.plot_all_gcode(gcode_text)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())