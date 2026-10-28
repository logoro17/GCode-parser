import re
import math
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QTextEdit, QSplitter, QGraphicsItem
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QPen, QColor, QPainterPath, QTransform


class GCodeAuditorWidget(QWidget):
    def __init__(self, canvas_class, parent=None):
        super().__init__(parent)

        main_layout = QVBoxLayout(self)

        # Top Bar Status Info
        top_layout = QHBoxLayout()
        self.lbl_info = QLabel("Ready to Audit.")
        self.lbl_info.setFont(QFont("Consolas", 11))
        top_layout.addWidget(self.lbl_info)
        top_layout.addStretch()
        main_layout.addLayout(top_layout)

        # Main Splitter Layout
        splitter = QSplitter(Qt.Horizontal)

        # Left: Native PyQt5 GCodeCanvas Visualizer
        self.canvas = canvas_class(self)
        splitter.addWidget(self.canvas)

        # Right: Detailed Terminal Log
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        lbl_report = QLabel("INSPECTION REPORT (DETAILED)")
        lbl_report.setFont(QFont("Segoe UI", 10, QFont.Bold))
        right_layout.addWidget(lbl_report)

        self.terminal = QTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setStyleSheet("background-color: #111; color: #00ff00; font-family: Consolas;")
        right_layout.addWidget(self.terminal)

        splitter.addWidget(right_widget)
        splitter.setSizes([750, 450])
        main_layout.addWidget(splitter)

    def get_params(self, line):
        params = {}
        matches = re.findall(r'([GMXYZIJSF])\s*([-+]?[0-9]*\.?[0-9]+)', line)
        for char, val in matches:
            params[char] = float(val)
        return params

    def calculate_polygon_area(self, points):
        if len(points) < 3: return 0.0
        area = 0.0
        n = len(points)
        for i in range(n):
            j = (i + 1) % n
            area += points[i][0] * points[j][1]
            area -= points[j][0] * points[i][1]
        return abs(area) / 2.0

    def generate_arc_points(self, start, end, center_offset, is_ccw, scale=1.0):
        sx, sy = start; ex, ey = end
        ix = center_offset.get('I', 0) * scale
        iy = center_offset.get('J', 0) * scale
        cx = sx + ix; cy = sy + iy
        radius = math.sqrt(ix**2 + iy**2)
        start_angle = math.atan2(sy - cy, sx - cx)
        end_angle = math.atan2(ey - cy, ex - cx)
        
        if is_ccw:
            if end_angle <= start_angle: end_angle += 2 * math.pi
        else:
            if end_angle >= start_angle: end_angle -= 2 * math.pi
            
        steps = 20
        points = []
        for i in range(1, steps + 1):
            t = float(i) / steps
            angle = start_angle + t * (end_angle - start_angle)
            px = cx + radius * math.cos(angle)
            py = cy + radius * math.sin(angle)
            points.append((px, py))
        return points

    def process_gcode(self, lines, filename):
        self.canvas.scene.clear()
        self.terminal.clear()
        self.terminal.append(f"FILE: {filename}\n" + "-"*40)

        cx, cy, cz = 0.0, 0.0, 0.0
        unit_scale = 1.0
        is_relative = False
        laser_spindle_on = False
        active_g_mode = 0
        
        object_counter = 0
        total_rapid_dist = 0.0
        total_cut_dist = 0.0
        
        current_poly_points = []
        current_block_xs = []; current_block_ys = []
        is_collecting_area = False 
        
        travel_accumulated_dist = 0.0
        travel_start_pos = (0, 0)
        is_traveling = True 

        path_cutting = QPainterPath()
        path_travel = QPainterPath()
        pen_g1 = QPen(QColor("black"), 0)
        pen_g0 = QPen(QColor("#FF0000"), 0, Qt.DashLine)

        for line in lines:
            clean_line = line.split('(')[0].split(';')[0].strip().upper()
            if not clean_line: continue
            
            params = self.get_params(clean_line)
            if not params: continue

            if 'G' in params:
                g_val = int(params['G'])
                if g_val == 20: unit_scale = 25.4
                elif g_val == 21: unit_scale = 1.0
                elif g_val == 90: is_relative = False
                elif g_val == 91: is_relative = True
                elif g_val in (0, 1, 2, 3): active_g_mode = g_val

            if 'M' in params:
                m_val = int(params['M'])
                if m_val in (3, 4): laser_spindle_on = True
                elif m_val == 5: laser_spindle_on = False

            if 'Z' in params: cz = params['Z'] * unit_scale

            has_xy = ('X' in params or 'Y' in params)
            if not has_xy: continue

            tx = (cx + params['X']*unit_scale) if ('X' in params and is_relative) else params.get('X', cx)*unit_scale
            ty = (cy + params['Y']*unit_scale) if ('Y' in params and is_relative) else params.get('Y', cy)*unit_scale

            step_dist = math.sqrt((tx - cx)**2 + (ty - cy)**2)
            is_cut_code = (active_g_mode in (1, 2, 3))
            is_cutting_active = is_cut_code and (laser_spindle_on or cz <= 0)
            
            mode = "CUTTING" if is_cutting_active else "TRAVEL"
            
            if mode == "CUTTING": total_cut_dist += step_dist
            else: total_rapid_dist += step_dist
            
            if mode == "CUTTING":
                if is_traveling:
                    msg = f"[TRAVEL] Dist: {travel_accumulated_dist:.2f} mm\n   From: ({travel_start_pos[0]:.1f}, {travel_start_pos[1]:.1f}) -> To: ({cx:.1f}, {cy:.1f})"
                    self.terminal.append(msg)
                    is_traveling = False
                    travel_accumulated_dist = 0.0
                
                if not is_collecting_area:
                    is_collecting_area = True
                    current_poly_points = [(cx, cy)]
                    current_block_xs = [cx]; current_block_ys = [cy]
                
                points_to_add = [(tx, ty)] if active_g_mode == 1 else self.generate_arc_points((cx, cy), (tx, ty), params, is_ccw=(active_g_mode == 3), scale=unit_scale)
                current_poly_points.extend(points_to_add)
                for px, py in points_to_add:
                    current_block_xs.append(px); current_block_ys.append(py)

                path_cutting.moveTo(cx, cy)
                for px, py in points_to_add:
                    path_cutting.lineTo(px, py)

            elif mode == "TRAVEL":
                if is_collecting_area:
                    object_counter += 1
                    area = self.calculate_polygon_area(current_poly_points)
                    if current_block_xs and current_block_ys:
                        min_x = min(current_block_xs); max_x = max(current_block_xs)
                        min_y = min(current_block_ys); max_y = max(current_block_ys)
                        
                        # 1. Bounding Box (Rect)
                        self.canvas.scene.addRect(
                            min_x, min_y, max_x - min_x, max_y - min_y, 
                            QPen(QColor("blue"), 0, Qt.DotLine)
                        )
                        
                        # 2. Hitung Titik Tengah Objek (Center)
                        center_x = (min_x + max_x) / 2.0
                        center_y = (min_y + max_y) / 2.0
                        
                        # 3. Buat Text Item
                        text_item = self.canvas.scene.addText(str(object_counter))
                        text_item.setDefaultTextColor(QColor("blue"))
                        text_item.setFont(QFont("Consolas", 8, QFont.Bold))
                        
                        # Set Flag Abaikan Zoom
                        text_item.setFlag(QGraphicsItem.ItemIgnoresTransformations)
                        
                        # Posisikan langsung di koordinat (center_x, center_y)
                        # Tanpa offset height yang bikin ketarik Y-negatif
                        text_item.setPos(center_x, center_y)

                    self.terminal.append(f"[OBJECT #{object_counter}] Area: {area:.2f} mm²\n" + "-"*35)
                    is_collecting_area = False
                    current_poly_points = []; current_block_xs = []; current_block_ys = []

            cx, cy = tx, ty

        self.canvas.scene.addPath(path_cutting, pen_g1)
        self.canvas.scene.addPath(path_travel, pen_g0)

        if not self.canvas.scene.itemsBoundingRect().isEmpty():
            self.canvas.fitInView(self.canvas.scene.itemsBoundingRect(), Qt.KeepAspectRatio)

        grand_total = total_rapid_dist + total_cut_dist
        summary = (
            f"\nFINAL INSPECTION REPORT:\n"
            f"1. Total Objects     : {object_counter}\n"
            f"2. Rapid Travel (Red): {total_rapid_dist:.2f} mm\n"
            f"3. Cutting (Black)   : {total_cut_dist:.2f} mm\n"
            f"------------------------------------\n"
            f"TOTAL DISTANCE       : {grand_total:.2f} mm"
        )
        self.terminal.append(summary)
        self.lbl_info.setText(f"Total: {grand_total:.2f} mm (Cut: {total_cut_dist:.1f} | Rapid: {total_rapid_dist:.1f})")