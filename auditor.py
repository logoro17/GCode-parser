import tkinter as tk
from tkinter import filedialog, scrolledtext
import re
import math
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

class GCodeAuditorUltimate:
    def __init__(self, root):
        self.root = root
        self.root.title("G-Code Auditor: Ultimate Distance & Area Calculator")
        self.root.geometry("1300x800")

        # --- HEADER ---
        top_frame = tk.Frame(root, bg="#102027", pady=10)
        top_frame.pack(fill=tk.X)
        
        btn = tk.Button(top_frame, text="📂 LOAD G-CODE", command=self.load_file, 
                        font=("Segoe UI", 11, "bold"), bg="#00E676", fg="black", width=20)
        btn.pack(side=tk.LEFT, padx=20)

        # Label Header Real-time
        self.lbl_info = tk.Label(top_frame, text="Ready.", bg="#102027", fg="#cfd8dc", font=("Consolas", 11))
        self.lbl_info.pack(side=tk.LEFT, padx=10)

        # --- MAIN LAYOUT ---
        main_frame = tk.Frame(root, bg="#eceff1")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # KIRI: VISUALISASI
        left_frame = tk.Frame(main_frame, bg="white", bd=2, relief=tk.RIDGE)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.fig, self.ax = plt.subplots(figsize=(6, 6))
        self.canvas = FigureCanvasTkAgg(self.fig, master=left_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.toolbar = NavigationToolbar2Tk(self.canvas, left_frame)
        self.toolbar.update()

        # KANAN: TERMINAL DATA
        right_frame = tk.Frame(main_frame, bg="#212121", width=480)
        right_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        right_frame.pack_propagate(False)

        tk.Label(right_frame, text="AUDIT REPORT (DETAILED)", bg="#212121", fg="white", font=("Segoe UI", 10, "bold")).pack(pady=5)
        
        self.terminal = scrolledtext.ScrolledText(right_frame, width=40, font=("Consolas", 9), bg="#111", fg="#00ff00")
        self.terminal.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Styling Terminal
        self.terminal.tag_config("travel", foreground="#ff5252") # Merah
        self.terminal.tag_config("cut", foreground="#69f0ae")    # Hijau
        self.terminal.tag_config("header", foreground="white", font=("Consolas", 9, "bold"))
        self.terminal.tag_config("total", foreground="#FFD700", font=("Consolas", 11, "bold")) # Emas

    def load_file(self):
        path = filedialog.askopenfilename(filetypes=[("GCode", "*.gcode *.ngc *.nc *.txt")])
        if not path: return
        with open(path, 'r') as f: lines = f.readlines()
        self.process_gcode(lines, path.split('/')[-1])

    # --- MATH FUNCTIONS ---
    def get_params(self, line):
        params = {}
        for char in ['X', 'Y', 'I', 'J']:
            match = re.search(f'{char}([-+]?[0-9]*\.?[0-9]+)', line)
            if match: params[char] = float(match.group(1))
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

    def generate_arc_points(self, start, end, center_offset, is_ccw):
        sx, sy = start; ex, ey = end
        ix, iy = center_offset.get('I', 0), center_offset.get('J', 0)
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

    # --- MAIN LOGIC ---
    def process_gcode(self, lines, filename):
        self.ax.clear()
        self.ax.set_title(f"Audit: {filename}", fontsize=10, fontweight='bold')
        self.ax.set_aspect('equal')
        self.ax.grid(True, linestyle=':', alpha=0.3)
        
        self.terminal.delete(1.0, tk.END)
        self.terminal.insert(tk.END, f"FILE: {filename}\n", "header")
        self.terminal.insert(tk.END, "-"*50 + "\n", "header")

        cx, cy = 0, 0
        spindle_on = False
        
        object_counter = 0
        
        # --- TOTAL ACCUMULATORS (METRIK UTAMA) ---
        total_rapid_dist = 0.0   # Jarak G0 (Merah)
        total_cut_dist = 0.0     # Jarak G1/G2/G3 (Hitam)
        # Note: Total Keseluruhan = total_rapid_dist + total_cut_dist
        
        # Buffers
        current_poly_points = []
        current_block_xs = []; current_block_ys = []
        is_collecting_area = False 
        
        # Segment Trackers
        travel_accumulated_dist = 0.0
        travel_start_pos = (0, 0)
        is_traveling = True 

        for line in lines:
            clean_line = line.split('(')[0].split(';')[0].strip().upper()
            if not clean_line: continue
            
            # 1. Update Spindle
            if 'M03' in clean_line or 'M3' in clean_line: spindle_on = True
            elif 'M05' in clean_line or 'M5' in clean_line: spindle_on = False
            
            # 2. Parse Params
            params = self.get_params(clean_line)
            nx, ny = params.get('X'), params.get('Y')
            
            if nx is None and ny is None: continue
            
            tx = nx if nx is not None else cx
            ty = ny if ny is not None else cy
            
            # HITUNG JARAK LANGKAH INI (STEP DISTANCE)
            step_dist = math.sqrt((tx - cx)**2 + (ty - cy)**2)
            
            # 3. KLASIFIKASI MODE
            is_g1 = re.search(r'G0?1(\D|$)', clean_line)
            is_g2 = re.search(r'G0?2(\D|$)', clean_line)
            is_g3 = re.search(r'G0?3(\D|$)', clean_line)
            is_cut_code = is_g1 or is_g2 or is_g3
            
            mode = "TRAVEL"
            if is_cut_code and spindle_on:
                mode = "CUTTING"
            
            # --- AKUMULASI TOTAL JARAK ---
            if mode == "CUTTING":
                total_cut_dist += step_dist # Tambah ke Total Cutting
            else:
                total_rapid_dist += step_dist # Tambah ke Total Rapid
            
            # --- LOGIKA AREA & SEGMENT ---

            if mode == "CUTTING":
                # Tutup buku Travel jika sebelumnya Travel
                if is_traveling:
                    msg = (f"[TRAVEL] Dist: {travel_accumulated_dist:.2f} mm\n"
                           f"   From: ({travel_start_pos[0]:.1f}, {travel_start_pos[1]:.1f}) -> To: ({cx:.1f}, {cy:.1f})\n")
                    self.terminal.insert(tk.END, msg, "travel")
                    is_traveling = False
                    travel_accumulated_dist = 0.0
                
                # Area Buffer
                if not is_collecting_area:
                    is_collecting_area = True
                    current_poly_points = [(cx, cy)]
                    current_block_xs = [cx]; current_block_ys = [cy]
                
                points_to_add = []
                if is_g1: points_to_add = [(tx, ty)]
                elif is_g2 or is_g3: points_to_add = self.generate_arc_points((cx, cy), (tx, ty), params, is_ccw=bool(is_g3))
                
                current_poly_points.extend(points_to_add)
                for px, py in points_to_add:
                    current_block_xs.append(px); current_block_ys.append(py)

            elif mode == "TRAVEL":
                # Tutup buku Cutting (Hitung Luas)
                if is_collecting_area:
                    object_counter += 1
                    area = self.calculate_polygon_area(current_poly_points)
                    
                    # Gambar Bounding Box
                    if current_block_xs:
                        min_x = min(current_block_xs); max_x = max(current_block_xs)
                        min_y = min(current_block_ys); max_y = max(current_block_ys)
                        rect = patches.Rectangle((min_x, min_y), max_x-min_x, max_y-min_y, lw=0.8, ec='blue', fill=False, ls=':', alpha=0.7)
                        self.ax.add_patch(rect)
                        self.ax.text(min_x, min_y, str(object_counter), color='blue', fontsize=9, fontweight='bold', va='top', ha='right')

                    msg = f"[OBJECT #{object_counter}] Area: {area:.2f} mm²\n"
                    self.terminal.insert(tk.END, msg, "cut")
                    self.terminal.insert(tk.END, "-"*45 + "\n")
                    
                    is_collecting_area = False
                    current_poly_points = []
                    current_block_xs = []; current_block_ys = []
                
                if not is_traveling:
                    is_traveling = True
                    travel_start_pos = (cx, cy)
                    travel_accumulated_dist = 0.0
                
                travel_accumulated_dist += step_dist

            # VISUALISASI
            color = 'black' if mode == "CUTTING" else 'red'
            style = '-' if mode == "CUTTING" else '--'
            width = 1.5 if mode == "CUTTING" else 0.8
            self.ax.plot([cx, tx], [cy, ty], color=color, linestyle=style, linewidth=width)
            
            cx, cy = tx, ty

        # Handle EOF
        if is_collecting_area and len(current_poly_points) > 2:
            object_counter += 1
            area = self.calculate_polygon_area(current_poly_points)
            if current_block_xs:
                 min_x = min(current_block_xs); min_y = min(current_block_ys)
                 rect = patches.Rectangle((min_x, min_y), max(current_block_xs)-min_x, max(current_block_ys)-min_y, lw=0.8, ec='blue', fill=False, ls=':')
                 self.ax.add_patch(rect)
                 self.ax.text(min_x, min_y, str(object_counter), color='blue', fontsize=9, fontweight='bold', va='top', ha='right')
            msg = f"[OBJECT #{object_counter}] Area: {area:.2f} mm² (EOF)\n"
            self.terminal.insert(tk.END, msg, "cut")

        if is_traveling and travel_accumulated_dist > 0:
            msg = (f"[TRAVEL] Dist: {travel_accumulated_dist:.2f} mm (EOF)\n")
            self.terminal.insert(tk.END, msg, "travel")

        # --- GRAND TOTAL SUMMARY ---
        grand_total = total_rapid_dist + total_cut_dist
        
        summary_text = (
            f"FINAL AUDIT REPORT:\n"
            f"1. Total Objects Found : {object_counter}\n"
            f"2. Rapid Travel (Red)  : {total_rapid_dist:.2f} mm\n"
            f"3. Cutting Path (Black): {total_cut_dist:.2f} mm\n"
            f"---------------------------------------------\n"
            f"4. TOTAL JOB DISTANCE  : {grand_total:.2f} mm"
        )
        
        self.terminal.insert(tk.END, "\n" + "="*45 + "\n", "header")
        self.terminal.insert(tk.END, summary_text + "\n", "total")
        self.terminal.insert(tk.END, "="*45 + "\n", "header")
        self.terminal.see(tk.END)
        
        # Update Header Info
        self.lbl_info.config(text=f"Total Dist: {grand_total:.2f} mm (Rapid: {total_rapid_dist:.1f} + Cut: {total_cut_dist:.1f})")

        # Legend
        from matplotlib.lines import Line2D
        legend_elements = [
            Line2D([0],[0], color='black', lw=1.5, label='Cutting'),
            Line2D([0],[0], color='red', lw=0.8, linestyle='--', label='Rapid Travel'),
            patches.Rectangle((0,0),1,1, edgecolor='blue', facecolor='none', linestyle=':', label='Obj ID')
        ]
        self.ax.legend(handles=legend_elements, loc='upper right', fontsize='small')
        self.ax.plot(0, 0, 'go')
        self.canvas.draw()

if __name__ == "__main__":
    root = tk.Tk()
    app = GCodeAuditorUltimate(root)
    root.mainloop()
