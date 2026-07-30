import sys
import math
from collections import Counter
import ezdxf
from ezdxf import path as ezdxf_path
import cv2
import numpy as np

try:
    from svgelements import SVG, Path as SVGPath
    HAS_SVG = True
except ImportError:
    HAS_SVG = False

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QGridLayout, QLabel, QLineEdit, QPushButton, QFileDialog, 
    QTextEdit, QGroupBox, QSplitter, QMessageBox, QComboBox,
    QDialog, QCheckBox, QScrollArea, QDialogButtonBox, QAbstractItemView
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal

import matplotlib
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.collections import LineCollection, PatchCollection
from matplotlib.patches import Polygon as MplPolygon, Circle as MplCircle
from matplotlib.figure import Figure

try:
    from shapely.geometry import Polygon as ShapelyPolygon, MultiPolygon, LineString
    from shapely.ops import unary_union
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False


class ParserWorkerThread(QThread):
    finished = pyqtSignal(list, str)
    error = pyqtSignal(str)

    def __init__(self, filepath, config):
        super().__init__()
        self.filepath = filepath
        self.config = config
        # Diagnostics populated during parsing so the UI can show the user
        # exactly what was read from the file (per-layer counts, excluded
        # layers, entities that failed to parse, etc.)
        self.diagnostics = {
            "layers_seen": Counter(),      # layer_name -> raw entity count encountered
            "layers_included": Counter(),  # layer_name -> entities actually used
            "layers_excluded": Counter(),  # layer_name -> entities skipped by filter
            "parse_errors": 0,             # entities that raised and were skipped
            "outline_source": None,        # how the outline was determined
        }

    def run(self):
        try:
            spot_size = self.config.get("spot_size_x", 0.06)
            max_error = self.config.get("flattening_max_error", 0.01)
            laser_mode = self.config.get("laser_mode", 0)

            raw_geometries = []
            ext = self.filepath.lower().split('.')[-1]

            # 1. Routing Parsing Berdasarkan Ekstensi File
            if ext == 'dxf':
                self._parse_dxf(raw_geometries, max_error)
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

            # 2. Membersihkan dan Menggabungkan Geometri (Unary Union)
            cleaned_geometries = []
            if HAS_SHAPELY:
                try:
                    shapely_polys = []
                    # Handle struktur data campuran (tuple atau objek langsung)
                    for item in raw_geometries:
                        if isinstance(item, tuple):
                            shape_type, data = item
                            if shape_type == 'CIRCLE':
                                center, radius = data
                                circ = ShapelyPolygon(center).buffer(max(radius, 0.001), resolution=32)
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
                                    line_buf = line_obj.buffer(0.02, cap_style=2, join_style=2)
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
                        # Batch processing (chunking) untuk mencegah freeze
                        batch_size = 250
                        merged_poly = shapely_polys[0]
                        for i in range(1, len(shapely_polys), batch_size):
                            batch = shapely_polys[i:i+batch_size]
                            batch.append(merged_poly)
                            merged_poly = unary_union(batch)
                        
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

            # 3. Penerapan Mode Laser
            parsed_shapes = []
            if laser_mode == 0:  # Isolation Routing
                for item in cleaned_geometries:
                    shape_type = item[0] if isinstance(item, tuple) else type(item).__name__
                    data = item[1] if isinstance(item, tuple) else None

                    if shape_type == 'POLYGON' and isinstance(item, tuple):
                        if HAS_SHAPELY and len(data) >= 3:
                            try:
                                poly = ShapelyPolygon(data)
                                if not poly.is_valid:
                                    poly = poly.buffer(0)
                                offset_poly = poly.buffer(spot_size, cap_style=3, join_style=2)
                                
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
            else:
                parsed_shapes = cleaned_geometries

            if not parsed_shapes:
                parsed_shapes = raw_geometries

            gcode_str = self.generate_hatching_gcode(parsed_shapes, self.config, laser_mode)
            self.finished.emit(parsed_shapes, gcode_str)

        except Exception as e:
            self.error.emit(str(e))

    def _parse_dxf(self, raw_geometries, max_error):
        doc = ezdxf.readfile(self.filepath)
        msp = doc.modelspace()

        diag = self.diagnostics
        # None => proses semua layer (kompatibel dengan perilaku lama).
        included_layers = self.config.get("dxf_included_layers")
        # 'AUTO' (lama: poligon terbesar = outline), 'NONE' (jangan ekstrak outline),
        # atau nama layer spesifik yang dipilih user di dialog seleksi layer.
        outline_layer = self.config.get("dxf_outline_layer", "AUTO")

        temp_polygons = []  # list of (ShapelyPolygon, source_layer_name)
        temp_lines = []

        def _get_open_polyline_width(entity, dxftype):
            """Kembalikan lebar penuh (mm) sebuah polyline TERBUKA, yaitu jalur/trace
            tembaga yang digambar sebagai garis tengah + properti lebar, bukan sebagai
            poligon tertutup. Mengembalikan 0 jika tidak ada info lebar."""
            if dxftype != 'LWPOLYLINE':
                return 0.0
            try:
                const_w = getattr(entity.dxf, 'const_width', 0.0) or 0.0
                if const_w > 0.0005:
                    return const_w
                widths = []
                for pt in entity.get_points('xyseb'):
                    sw, ew = pt[2], pt[3]
                    if sw and sw > 0.0005: widths.append(sw)
                    if ew and ew > 0.0005: widths.append(ew)
                if widths:
                    return max(widths)
            except Exception:
                pass
            return 0.0

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
                    poly = ShapelyPolygon(pts)
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    if poly.is_valid and not poly.is_empty:
                        if isinstance(poly, ShapelyPolygon):
                            temp_polygons.append((poly, layer_name))
                        elif isinstance(poly, MultiPolygon):
                            for g in poly.geoms:
                                temp_polygons.append((g, layer_name))
                        used = True
                elif dxftype in ('LWPOLYLINE', 'POLYLINE', 'LINE', 'ARC', 'ELLIPSE', 'SPLINE', 'HATCH'):
                    paths = ezdxf_path.make_paths_from_entity(entity) if hasattr(ezdxf_path, 'make_paths_from_entity') else [ezdxf_path.make_path(entity)]
                    for p in paths:
                        pts = [(pt.x, pt.y) for pt in p.flattening(distance=max_error)]
                        if len(pts) < 2: continue
                        is_closed = True if p.is_closed or math.hypot(pts[0][0]-pts[-1][0], pts[0][1]-pts[-1][1]) < 0.001 else False
                        if is_closed:
                            if pts[0] != pts[-1]: pts.append(pts[0])
                            poly = ShapelyPolygon(pts)
                            if not poly.is_valid:
                                poly = poly.buffer(0)
                            if poly.is_valid and not poly.is_empty:
                                if isinstance(poly, ShapelyPolygon):
                                    temp_polygons.append((poly, layer_name))
                                elif isinstance(poly, MultiPolygon):
                                    for g in poly.geoms:
                                        temp_polygons.append((g, layer_name))
                                used = True
                        else:
                            # Jalur terbuka: jika entitas punya lebar (const_width /
                            # per-vertex width) -- umum untuk trace tembaga yang
                            # digambar sebagai centerline -- ubah menjadi poligon
                            # solid sesuai lebar aslinya. Jika tidak ada info lebar,
                            # pertahankan perilaku lama (garis tipis, dibuffer 0.02mm
                            # nanti pada tahap union).
                            width = _get_open_polyline_width(entity, dxftype)
                            handled = False
                            if width > 0.0005:
                                try:
                                    buffered = LineString(pts).buffer(width / 2.0, cap_style=2, join_style=2)
                                    if isinstance(buffered, ShapelyPolygon) and not buffered.is_empty:
                                        temp_polygons.append((buffered, layer_name))
                                        handled = True
                                    elif isinstance(buffered, MultiPolygon) and not buffered.is_empty:
                                        for g in buffered.geoms:
                                            temp_polygons.append((g, layer_name))
                                        handled = True
                                except Exception:
                                    handled = False
                            if not handled:
                                temp_lines.append(LineString(pts))
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
            
        if not raw_geometries and not temp_polygons and not temp_lines:
            for entity in doc.paperspace(): _parse_entity(entity)
            
        # --- Pemisahan Outline Papan vs Geometri Solid ---
        for line in temp_lines:
            raw_geometries.append(line)

        if temp_polygons:
            outline_polys = []
            solid_polys = []

            if outline_layer and outline_layer not in ('AUTO', 'NONE'):
                # Mode eksplisit: user memilih satu layer di dialog sebagai sumber outline.
                for poly, layer_name in temp_polygons:
                    if layer_name == outline_layer:
                        outline_polys.append(poly)
                    else:
                        solid_polys.append(poly)
                diag["outline_source"] = f"layer: {outline_layer}"
            elif outline_layer == 'NONE':
                # User memilih untuk TIDAK mengekstrak outline sama sekali.
                # Semua poligon tertutup diperlakukan sebagai geometri solid (pad/trace).
                solid_polys = [poly for poly, _ in temp_polygons]
                diag["outline_source"] = "none"
            else:
                # AUTO -- perilaku lama dipertahankan sebagai default: poligon dengan
                # area terbesar diasumsikan sebagai outline papan (mis. Edge.Cuts).
                sorted_polys = sorted(temp_polygons, key=lambda item: item[0].area, reverse=True)
                largest_poly, largest_layer = sorted_polys[0]
                if largest_poly.area > 0:
                    outline_polys.append(largest_poly)
                solid_polys = [poly for poly, _ in sorted_polys[1:]]
                diag["outline_source"] = f"auto (layer: {largest_layer})"

            for op in outline_polys:
                raw_geometries.append(LineString(op.exterior.coords))
            for sp in solid_polys:
                raw_geometries.append(sp)

    def _parse_raster(self, raw_geometries):
        threshold_val = self.config.get("png_threshold", 0)
        scale = self.config.get("svg_png_scale", 0.2645)
        
        img = cv2.imread(self.filepath, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("File gambar tidak dapat dibaca.")
            
        # Ubah BGR ke Grayscale agar merah (jalur) dan abu-abu (pad/teks) terbaca
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        
        height, width = blurred.shape
        max_area = height * width * 0.40

        # Gunakan THRESH_BINARY (tanpa INV) 
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
                            polygons[i] = pts
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
                            pts.append((pt.x * scale, -pt.y * scale)) # Inversi koordinat Y karena SVG 0,0 di atas kiri
                    if len(pts) >= 3:
                        raw_geometries.append(('POLYGON', pts))
            except Exception:
                pass

    def generate_hatching_gcode(self, parsed_shapes, config, laser_mode):
        spot_size = config.get("spot_size_x", 0.06)
        max_error = config.get("flattening_max_error", 0.01)

        gcode = []
        gcode.append("; --- Generated by Sculpfun PCB High-Precision Laser Parser ---")
        gcode.append(f"; Mode: {['Isolation Routing', 'Full Hatching', 'Negative Clearance'][laser_mode]}")
        gcode.append(f"; Precision: {max_error} mm | Spot Size: {spot_size} mm")
        gcode.append("G21 ; Millimeters")
        gcode.append("G90 ; Absolute positioning")
        gcode.append("M5  ; Laser OFF")
        gcode.append(f"G0 F{config['max_feedrate_g0']}")
        gcode.append("")

        passes = [
            ("Pass 1: Ablation / Striping", config['laser_power_pass1']),
            ("Pass 2: Clean Cut / Finishing", config['laser_power_pass2'])
        ]

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

        return "\n".join(gcode)


class FastPlotCanvas(FigureCanvas):
    def __init__(self, parent=None, title="High Precision PCB Solid View"):
        fig = Figure(figsize=(5, 4), dpi=100)
        self.axes = fig.add_subplot(111)
        self.title = title
        super().__init__(fig)
        self.setParent(parent)

    def plot_data(self, parsed_shapes, fill_color='red'):
        self.axes.clear()
        self.axes.set_title(self.title, fontsize=10, fontweight='bold')
        self.axes.set_xlabel("X (mm)")
        self.axes.set_ylabel("Y (mm)")
        self.axes.grid(True, linestyle='--', alpha=0.3)
        self.axes.set_aspect('equal', adjustable='box')

        patches = []
        lines = []

        for shape_type, data in parsed_shapes:
            if shape_type == 'CIRCLE':
                center, radius = data
                patches.append(MplCircle(center, radius))
            elif shape_type == 'POLYGON':
                pts = data
                patches.append(MplPolygon(pts, closed=True))
            elif shape_type == 'LINE':
                lines.append(data)

        if patches:
            pc = PatchCollection(patches, facecolors=fill_color, edgecolors=fill_color, alpha=0.9, antialiased=False)
            self.axes.add_collection(pc)

        if lines:
            lc = LineCollection(lines, colors=fill_color, linewidths=1.2, antialiased=False)
            self.axes.add_collection(lc)

        self.axes.autoscale()
        self.draw()


def scan_dxf_layers(filepath):
    """Baca cepat sebuah file DXF hanya untuk menghitung jumlah entitas per layer
    (termasuk entitas di dalam blok INSERT). Tidak melakukan konversi geometri
    apapun, jadi ini aman dan murah dijalankan di main thread sebelum parsing
    penuh dilakukan di worker thread."""
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


# Kata kunci layer yang secara default TIDAK dicentang, karena biasanya bukan
# merupakan tembaga/jalur yang perlu di-routing (paste mask, lubang bor, dsb).
# User tetap bisa mencentangnya kembali secara manual di dialog.
_DXF_LAYER_DEFAULT_EXCLUDE_KEYWORDS = (
    'paste', 'hole', 'drill', 'silk', 'dimension', 'comment',
    'keepout', 'assembly', 'courtyard', 'doc', 'note'
)


class LayerSelectionDialog(QDialog):
    """Dialog untuk memilih layer DXF mana yang akan diproses, serta layer mana
    (jika ada) yang merepresentasikan outline/batas papan. Dibuat karena file DXF
    dari software ECAD (Eagle/KiCad/dll) sering menaruh tembaga, paste mask,
    lubang bor, dan sebagainya dalam layer terpisah -- menggabungkan semuanya
    tanpa pandang bulu menghasilkan geometri yang kacau/menyatu."""

    def __init__(self, layer_counts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pilih Layer DXF")
        self.resize(420, 480)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "File DXF ini berisi beberapa layer. Pilih layer mana saja yang\n"
            "merupakan tembaga/jalur yang ingin diproses (default sudah\n"
            "disarankan berdasarkan nama layer)."
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)

        self.checkboxes = {}
        for layer_name in sorted(layer_counts.keys()):
            count = layer_counts[layer_name]
            cb = QCheckBox(f"{layer_name}  ({count} entitas)")
            lname_lower = layer_name.lower()
            default_checked = not any(kw in lname_lower for kw in _DXF_LAYER_DEFAULT_EXCLUDE_KEYWORDS)
            cb.setChecked(default_checked)
            scroll_layout.addWidget(cb)
            self.checkboxes[layer_name] = cb

        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

        outline_box = QGroupBox("Sumber Outline Papan")
        outline_layout = QVBoxLayout(outline_box)
        outline_layout.addWidget(QLabel("Layer mana yang merupakan batas/outline papan?"))
        self.combo_outline = QComboBox()
        self.combo_outline.addItem("Otomatis (poligon area terbesar)", "AUTO")
        self.combo_outline.addItem("Tidak ada / jangan ekstrak outline", "NONE")
        for layer_name in sorted(layer_counts.keys()):
            self.combo_outline.addItem(layer_name, layer_name)
        outline_layout.addWidget(self.combo_outline)
        layout.addWidget(outline_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_included_layers(self):
        included = [name for name, cb in self.checkboxes.items() if cb.isChecked()]
        # Jika user tanpa sengaja mencentang nol layer, jangan kunci ke daftar
        # kosong (yang berarti "tidak ada apa-apa") -- perlakukan sebagai semua.
        if not included:
            return None
        if len(included) == len(self.checkboxes):
            return None  # semua dicentang -> sama dengan tidak difilter
        return included

    def get_outline_layer(self):
        return self.combo_outline.currentData()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sculpfun C1 Mini - Multi-Format (DXF/PNG/SVG) to PCB G-Code")
        self.resize(1250, 850)

        self.current_parsed_shapes = []
        self.filepath = ""
        self.worker = None
        # None = belum ada filter (semua layer diproses, sama seperti perilaku lama).
        self.dxf_layer_config = {"dxf_included_layers": None, "dxf_outline_layer": "AUTO"}

        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        config_box = QGroupBox("Configuration: Sculpfun C1 Mini")
        config_grid = QGridLayout(config_box)

        config_grid.addWidget(QLabel("Laser Working Mode"), 0, 0)
        self.combo_mode = QComboBox()
        self.combo_mode.addItems([
            "0: Isolation Routing (Parit/Cepat)", 
            "1: Full Hatching (Arsir Penuh)", 
            "2: Negative Clearance (Area Kosong)"
        ])
        config_grid.addWidget(self.combo_mode, 0, 1)

        self.inputs = {}
        # Menambahkan parameter Scale dan Threshold ke UI
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
            ("Laser Power Pass 1 (S)", "laser_power_pass1", "700"),
            ("Laser Power Pass 2 (S)", "laser_power_pass2", "450"),
        ]

        row = 1
        for label, key, val in default_config:
            config_grid.addWidget(QLabel(label), row, 0)
            txt_input = QLineEdit(val)
            config_grid.addWidget(txt_input, row, 1)
            self.inputs[key] = txt_input
            row += 1

        left_layout.addWidget(config_box)

        btn_layout = QHBoxLayout()
        self.btn_open = QPushButton("1. Open File (DXF/PNG/SVG)")
        self.btn_open.clicked.connect(self.open_file)
        btn_layout.addWidget(self.btn_open)

        self.btn_layers = QPushButton("Layer DXF...")
        self.btn_layers.setToolTip("Pilih layer DXF mana yang diproses dan mana yang menjadi outline papan.")
        self.btn_layers.clicked.connect(self.configure_dxf_layers)
        self.btn_layers.setEnabled(False)
        btn_layout.addWidget(self.btn_layers)

        self.btn_convert = QPushButton("2. Convert G-Code")
        self.btn_convert.clicked.connect(self.convert_file_to_gcode)
        btn_layout.addWidget(self.btn_convert)

        self.btn_export = QPushButton("3. Export File")
        self.btn_export.clicked.connect(self.export_gcode)
        btn_layout.addWidget(self.btn_export)

        left_layout.addLayout(btn_layout)

        editor_box = QGroupBox("G-Code Preview")
        editor_layout = QVBoxLayout(editor_box)
        self.gcode_editor = QTextEdit()
        self.gcode_editor.setPlaceholderText("G-Code hasil konversi...")
        editor_layout.addWidget(self.gcode_editor)

        left_layout.addWidget(editor_box)
        splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        canvas_container = QWidget()
        canvas_vbox = QVBoxLayout(canvas_container)
        self.canvas = FastPlotCanvas(self, title="Geometry Solid View")
        self.toolbar = NavigationToolbar(self.canvas, self)
        canvas_vbox.addWidget(self.toolbar)
        canvas_vbox.addWidget(self.canvas)
        right_layout.addWidget(canvas_container)

        splitter.addWidget(right_panel)
        splitter.setSizes([450, 800])

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
                "laser_power_pass1": int(self.inputs["laser_power_pass1"].text()),
                "laser_power_pass2": int(self.inputs["laser_power_pass2"].text()),
            }
        except ValueError as e:
            QMessageBox.critical(self, "Input Error", f"Format parameter tidak valid: {str(e)}")
            return None

    def open_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Buka File", "", 
            "Supported Files (*.dxf *.png *.svg);;DXF Files (*.dxf);;PNG Files (*.png);;SVG Files (*.svg)"
        )
        if not filepath:
            return

        self.filepath = filepath
        # Reset filter setiap kali file baru dibuka.
        self.dxf_layer_config = {"dxf_included_layers": None, "dxf_outline_layer": "AUTO"}
        is_dxf = filepath.lower().endswith('.dxf')
        self.btn_layers.setEnabled(is_dxf)

        if is_dxf:
            if not self._prompt_dxf_layer_selection(filepath):
                # User membatalkan dialog seleksi layer -- jangan langsung memproses.
                return

        self.convert_file_to_gcode()

    def _prompt_dxf_layer_selection(self, filepath):
        """Scan layer DXF dan tampilkan dialog seleksi. Mengembalikan True jika
        user menekan OK (config disimpan ke self.dxf_layer_config), False jika
        dibatalkan atau file gagal dipindai."""
        try:
            layer_counts = scan_dxf_layers(filepath)
        except Exception as e:
            QMessageBox.critical(self, "Gagal Membaca DXF", f"Tidak dapat memindai layer DXF:\n{str(e)}")
            return False

        if not layer_counts:
            QMessageBox.warning(self, "Warning", "Tidak ada entitas yang ditemukan di dalam file DXF ini.")
            return False

        dialog = LayerSelectionDialog(layer_counts, self)
        if dialog.exec_() != QDialog.Accepted:
            return False

        self.dxf_layer_config = {
            "dxf_included_layers": dialog.get_included_layers(),
            "dxf_outline_layer": dialog.get_outline_layer(),
        }
        return True

    def configure_dxf_layers(self):
        if not self.filepath or not self.filepath.lower().endswith('.dxf'):
            QMessageBox.warning(self, "Warning", "Buka file DXF terlebih dahulu!")
            return
        self._prompt_dxf_layer_selection(self.filepath)

    def convert_file_to_gcode(self):
        if not self.filepath:
            QMessageBox.warning(self, "Warning", "Buka file terlebih dahulu!")
            return

        config = self.get_config_dict()
        if not config:
            return

        if self.filepath.lower().endswith('.dxf'):
            config.update(self.dxf_layer_config)

        self.btn_open.setEnabled(False)
        self.btn_layers.setEnabled(False)
        self.btn_convert.setEnabled(False)
        self.btn_convert.setText("Memproses...")

        self.worker = ParserWorkerThread(self.filepath, config)
        self.worker.finished.connect(self.on_processing_finished)
        self.worker.error.connect(self.on_processing_error)
        self.worker.start()

    def on_processing_finished(self, parsed_shapes, gcode_str):
        self.current_parsed_shapes = parsed_shapes
        self.canvas.plot_data(self.current_parsed_shapes, fill_color='red')
        
        self.gcode_editor.clear()
        self.gcode_editor.setText(gcode_str)

        self.btn_open.setEnabled(True)
        self.btn_layers.setEnabled(self.filepath.lower().endswith('.dxf'))
        self.btn_convert.setEnabled(True)
        self.btn_convert.setText("2. Convert G-Code")

        summary = f"Berhasil memproses file!\nTotal Elemen Jalur: {len(self.current_parsed_shapes)}"

        diag = getattr(self.worker, "diagnostics", None) if self.worker else None
        if diag and diag.get("layers_seen"):
            summary += "\n\n--- Rincian Layer DXF ---"
            for layer_name in sorted(diag["layers_seen"].keys()):
                seen = diag["layers_seen"][layer_name]
                included = diag["layers_included"].get(layer_name, 0)
                excluded = diag["layers_excluded"].get(layer_name, 0)
                if excluded:
                    summary += f"\n{layer_name}: {seen} entitas (dilewati/di-filter)"
                else:
                    summary += f"\n{layer_name}: {seen} entitas ({included} terpakai)"
            if diag.get("outline_source"):
                summary += f"\nSumber outline: {diag['outline_source']}"
            if diag.get("parse_errors"):
                summary += f"\nEntitas gagal dibaca: {diag['parse_errors']}"

        QMessageBox.information(self, "Success", summary)

    def on_processing_error(self, err_msg):
        self.btn_open.setEnabled(True)
        self.btn_layers.setEnabled(self.filepath.lower().endswith('.dxf') if self.filepath else False)
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


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
