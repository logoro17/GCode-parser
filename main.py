import sys
import math
import ezdxf
from ezdxf import path as ezdxf_path

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QGridLayout, QLabel, QLineEdit, QPushButton, QFileDialog, 
    QTextEdit, QGroupBox, QSplitter, QMessageBox
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
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False


class DXFWorkerThread(QThread):
    finished = pyqtSignal(list, str)
    error = pyqtSignal(str)

    def __init__(self, dxf_filepath, config):
        super().__init__()
        self.dxf_filepath = dxf_filepath
        self.config = config

    def run(self):
        try:
            spot_size = self.config.get("spot_size_x", 0.06)
            max_error = self.config.get("flattening_max_error", 0.01)

            doc = ezdxf.readfile(self.dxf_filepath)
            all_entities = list(doc.modelspace()) + list(doc.paperspace())
            parsed_shapes = []

            def get_all_base_entities(entities):
                base = []
                for ent in entities:
                    if ent.dxftype() == 'INSERT':
                        try:
                            base.extend(get_all_base_entities(ent.virtual_entities()))
                        except Exception:
                            pass
                    else:
                        base.append(ent)
                return base

            entities_to_process = get_all_base_entities(all_entities)

            for entity in entities_to_process:
                dxftype = entity.dxftype()

                if dxftype == 'CIRCLE':
                    center = (entity.dxf.center.x, entity.dxf.center.y)
                    radius = entity.dxf.radius
                    parsed_shapes.append(('CIRCLE', (center, radius)))

                elif dxftype in ('SOLID', 'TRACE'):
                    try:
                        pts = [
                            (entity.dxf.vtx0.x, entity.dxf.vtx0.y),
                            (entity.dxf.vtx1.x, entity.dxf.vtx1.y),
                            (entity.dxf.vtx3.x, entity.dxf.vtx3.y),
                            (entity.dxf.vtx2.x, entity.dxf.vtx2.y)
                        ]
                        parsed_shapes.append(('POLYGON', pts))
                    except Exception:
                        pass

                elif dxftype in ('LWPOLYLINE', 'POLYLINE', 'LINE', 'ARC', 'ELLIPSE', 'SPLINE', 'HATCH'):
                    try:
                        paths = []
                        if hasattr(ezdxf_path, 'make_paths_from_entity'):
                            paths = ezdxf_path.make_paths_from_entity(entity)
                        else:
                            paths = [ezdxf_path.make_path(entity)]
                            
                        line_width = 0
                        if dxftype == 'LWPOLYLINE':
                            if entity.dxf.hasattr('const_width') and entity.dxf.const_width > 0:
                                line_width = entity.dxf.const_width
                            else:
                                try:
                                    if len(entity) > 0:
                                        first_vtx = entity.lwpoints[0]
                                        if len(first_vtx) >= 4:
                                            line_width = max(first_vtx[2], first_vtx[3]) 
                                except Exception:
                                    pass
                                    
                        for p in paths:
                            pts = [(pt.x, pt.y) for pt in p.flattening(distance=max_error)]
                            if len(pts) < 2:
                                continue
                                
                            is_closed = False
                            if len(pts) >= 3:
                                dist_start_end = math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])
                                if p.is_closed or dist_start_end < 0.001:
                                    is_closed = True
                                    
                            if line_width > 0 and HAS_SHAPELY:
                                if is_closed and pts[0] != pts[-1]:
                                    pts.append(pts[0])
                                poly = LineString(pts).buffer(line_width / 2.0)
                                if isinstance(poly, ShapelyPolygon):
                                    parsed_shapes.append(('POLYGON', list(poly.exterior.coords)))
                                elif isinstance(poly, MultiPolygon):
                                    for sub_p in poly.geoms:
                                        parsed_shapes.append(('POLYGON', list(sub_p.exterior.coords)))
                            else:
                                if is_closed:
                                    parsed_shapes.append(('POLYGON', pts))
                                else:
                                    parsed_shapes.append(('LINE', pts))
                    except Exception:
                        pass

            # Mengoper parameter self.config ke fungsi generator
            gcode_str = self.generate_hatching_gcode(parsed_shapes, self.config)
            self.finished.emit(parsed_shapes, gcode_str)

        except Exception as e:
            self.error.emit(str(e))

    def generate_hatching_gcode(self, parsed_shapes, config):
        spot_size = config.get("spot_size_x", 0.06)
        max_error = config.get("flattening_max_error", 0.01)

        gcode = []
        gcode.append("; --- Generated by Sculpfun PCB High-Precision Laser Parser ---")
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
        self.setWindowTitle("Sculpfun C1 Mini - DXF to PCB Solid Laser G-Code (Optimized)")
        self.resize(1250, 850)

        self.current_parsed_shapes = []
        self.dxf_filepath = ""
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

        self.inputs = {}
        default_config = [
            ("Curve Precision Error (mm)", "flattening_max_error", "0.01"),
            ("Spot Size X (mm)", "spot_size_x", "0.06"),
            ("Spot Size Y (mm)", "spot_size_y", "0.08"),
            ("Max G0 Feedrate (mm/min)", "max_feedrate_g0", "3000"),
            ("Max G1 Feedrate (mm/min)", "max_feedrate_g1", "1000"),
            ("Acceleration (mm/s²)", "acceleration", "500"),
            ("Laser Power Max ($30)", "laser_power_max", "1000"),
            ("Laser Power Pass 1 (S)", "laser_power_pass1", "700"),
            ("Laser Power Pass 2 (S)", "laser_power_pass2", "450"),
        ]

        row = 0
        for label, key, val in default_config:
            config_grid.addWidget(QLabel(label), row, 0)
            txt_input = QLineEdit(val)
            config_grid.addWidget(txt_input, row, 1)
            self.inputs[key] = txt_input
            row += 1

        left_layout.addWidget(config_box)

        btn_layout = QHBoxLayout()
        self.btn_open = QPushButton("1. Open DXF")
        self.btn_open.clicked.connect(self.open_dxf)
        btn_layout.addWidget(self.btn_open)

        self.btn_convert = QPushButton("2. Convert G-Code")
        self.btn_convert.clicked.connect(self.convert_dxf_to_gcode)
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

        dxf_container = QWidget()
        dxf_vbox = QVBoxLayout(dxf_container)
        self.dxf_canvas = FastPlotCanvas(self, title="DXF PCB Solid View")
        self.dxf_toolbar = NavigationToolbar(self.dxf_canvas, self)
        dxf_vbox.addWidget(self.dxf_toolbar)
        dxf_vbox.addWidget(self.dxf_canvas)
        right_layout.addWidget(dxf_container)

        splitter.addWidget(right_panel)
        splitter.setSizes([450, 800])

    def get_config_dict(self):
        try:
            return {
                "flattening_max_error": float(self.inputs["flattening_max_error"].text()),
                "spot_size_x": float(self.inputs["spot_size_x"].text()),
                "spot_size_y": float(self.inputs["spot_size_y"].text()),
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

    def open_dxf(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Buka File DXF", "", "DXF Files (*.dxf)")
        if filepath:
            self.dxf_filepath = filepath
            self.convert_dxf_to_gcode()

    def convert_dxf_to_gcode(self):
        if not self.dxf_filepath:
            QMessageBox.warning(self, "Warning", "Buka file DXF terlebih dahulu!")
            return

        config = self.get_config_dict()
        if not config:
            return

        self.btn_open.setEnabled(False)
        self.btn_convert.setEnabled(False)
        self.btn_convert.setText("Memproses...")

        self.worker = DXFWorkerThread(self.dxf_filepath, config)
        self.worker.finished.connect(self.on_processing_finished)
        self.worker.error.connect(self.on_processing_error)
        self.worker.start()

    def on_processing_finished(self, parsed_shapes, gcode_str):
        self.current_parsed_shapes = parsed_shapes
        self.dxf_canvas.plot_data(self.current_parsed_shapes, fill_color='red')
        
        self.gcode_editor.clear()
        self.gcode_editor.setText(gcode_str)

        self.btn_open.setEnabled(True)
        self.btn_convert.setEnabled(True)
        self.btn_convert.setText("2. Convert G-Code")

        QMessageBox.information(
            self, "Success", 
            f"Berhasil memproses DXF!\nTotal Bentuk Terdeteksi: {len(self.current_parsed_shapes)}"
        )

    def on_processing_error(self, err_msg):
        self.btn_open.setEnabled(True)
        self.btn_convert.setEnabled(True)
        self.btn_convert.setText("2. Convert G-Code")
        QMessageBox.critical(self, "Parsing Error", f"Gagal membaca file DXF: {err_msg}")

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