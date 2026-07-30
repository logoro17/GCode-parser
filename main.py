import sys
import math
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
    QTextEdit, QGroupBox, QSplitter, QMessageBox, QComboBox
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
            elif ext == 'png':
                self._parse_png(raw_geometries)
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
                    for shape_type, data in raw_geometries:
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

                    if shapely_polys:
                        merged_poly = unary_union(shapely_polys)
                        
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
                for shape_type, data in cleaned_geometries:
                    if shape_type == 'POLYGON':
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
                        parsed_shapes.append((shape_type, data))
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
        
        def _parse_entity(entity):
            dxftype = entity.dxftype()
            try:
                if dxftype == 'CIRCLE':
                    center = (entity.dxf.center.x, entity.dxf.center.y)
                    radius = entity.dxf.radius
                    if radius > 0:
                        raw_geometries.append(('CIRCLE', (center, radius)))
                elif dxftype in ('SOLID', 'TRACE'):
                    pts = [
                        (entity.dxf.vtx0.x, entity.dxf.vtx0.y), (entity.dxf.vtx1.x, entity.dxf.vtx1.y),
                        (entity.dxf.vtx3.x, entity.dxf.vtx3.y), (entity.dxf.vtx2.x, entity.dxf.vtx2.y)
                    ]
                    raw_geometries.append(('POLYGON', pts))
                elif dxftype in ('LWPOLYLINE', 'POLYLINE', 'LINE', 'ARC', 'ELLIPSE', 'SPLINE', 'HATCH'):
                    paths = ezdxf_path.make_paths_from_entity(entity) if hasattr(ezdxf_path, 'make_paths_from_entity') else [ezdxf_path.make_path(entity)]
                    for p in paths:
                        pts = [(pt.x, pt.y) for pt in p.flattening(distance=max_error)]
                        if len(pts) < 2: continue
                        is_closed = True if p.is_closed or math.hypot(pts[0][0]-pts[-1][0], pts[0][1]-pts[-1][1]) < 0.001 else False
                        if is_closed:
                            if pts[0] != pts[-1]: pts.append(pts[0])
                            raw_geometries.append(('POLYGON', pts))
                        else:
                            raw_geometries.append(('LINE', pts))
            except Exception: pass

        for entity in msp:
            if entity.dxftype() == 'INSERT':
                try:
                    for virt_ent in entity.virtual_entities(): _parse_entity(virt_ent)
                except Exception: pass
            else: _parse_entity(entity)
            
        if not raw_geometries:
            for entity in doc.paperspace(): _parse_entity(entity)

    def _parse_png(self, raw_geometries):
        threshold_val = self.config.get("png_threshold", 0)
        scale = self.config.get("svg_png_scale", 0.2645)
        
        img = cv2.imread(self.filepath, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError("File gambar tidak dapat dibaca.")

        # Deteksi otomatis warna background dari piksel pojok kiri atas
        bg_color = img[0, 0]
        if bg_color < 127: 
            # Background gelap (hitam) -> gunakan THRESH_BINARY biasa
            thresh_type = cv2.THRESH_BINARY
        else: 
            # Background terang (putih) -> gunakan THRESH_BINARY_INV
            thresh_type = cv2.THRESH_BINARY_INV

        if threshold_val <= 0:
            _, thresh = cv2.threshold(img, 0, 255, thresh_type + cv2.THRESH_OTSU)
        else:
            _, thresh = cv2.threshold(img, int(threshold_val), 255, thresh_type)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        height = img.shape[0]

        for cnt in contours:
            if len(cnt) >= 3:
                pts = [((pt[0][0] * scale), ((height - pt[0][1]) * scale)) for pt in cnt]
                raw_geometries.append(('POLYGON', pts))

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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sculpfun C1 Mini - Multi-Format (DXF/PNG/SVG) to PCB G-Code")
        self.resize(1250, 850)

        self.current_parsed_shapes = []
        self.filepath = ""
        self.worker = None

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
        if filepath:
            self.filepath = filepath
            self.convert_file_to_gcode()

    def convert_file_to_gcode(self):
        if not self.filepath:
            QMessageBox.warning(self, "Warning", "Buka file terlebih dahulu!")
            return

        config = self.get_config_dict()
        if not config:
            return

        self.btn_open.setEnabled(False)
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
        self.btn_convert.setEnabled(True)
        self.btn_convert.setText("2. Convert G-Code")

        QMessageBox.information(
            self, "Success", 
            f"Berhasil memproses file!\nTotal Elemen Jalur: {len(self.current_parsed_shapes)}"
        )

    def on_processing_error(self, err_msg):
        self.btn_open.setEnabled(True)
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
