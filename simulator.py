import tkinter as tk
from tkinter import filedialog, scrolledtext
import turtle
import time
import re

class GCodeSimulator:
    def __init__(self, root):
        self.root = root
        self.root.title("G-Code Simulator (Turtle + GRBL Laser/CNC)")
        self.root.geometry("1100x700")

        # --- KONFIGURASI LAYOUT (Split View) ---
        self.left_frame = tk.Frame(root, bg="#333", width=700)
        self.left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.right_frame = tk.Frame(root, bg="#f0f0f0", width=400)
        self.right_frame.pack(side=tk.RIGHT, fill=tk.Y)

        # --- SETUP TURTLE DI DALAM TKINTER ---
        self.canvas = tk.Canvas(self.left_frame, bg="white")
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.screen = turtle.TurtleScreen(self.canvas)
        self.screen.bgcolor("white")
        self.screen.tracer(0)
        
        self.t = turtle.RawTurtle(self.screen)
        self.t.speed(0)
        self.t.hideturtle()

        # --- VARIABEL STATE SIMULASI ---
        self.gcode_lines = []
        self.line_index = 0
        self.is_running = False
        self.is_paused = False
        self.start_time = 0
        self.elapsed_time = 0
        
        # State View (Pan/Zoom)
        self.zoom_level = 1.0
        self.pan_x = 0
        self.pan_y = 0
        self.base_view = 400

        self.current_step = 0
        self.total_steps = 0
        self.dx = 0
        self.dy = 0
        self.is_moving = False
        self.sim_speed = 2.0

        # --- STATE MESIN UNIVERSAL & GRBL ---
        self.unit_scale = 1.0       # 1.0 = MM (G21), 25.4 = Inch (G20)
        self.is_relative = False    # False = Absolute (G90), True = Incremental (G91)
        self.feed_rate = 1000.0     # Default Feedrate (mm/min)
        self.spindle_power = 0.0    # Nilai S (0-1000)
        self.laser_on = False       # True jika M3/M4 aktif
        self.active_g_mode = 0      # 0 = G00 (Rapid), 1 = G01 (Cutting/Linear)
        self.current_z = 10.0

        self.setup_ui_controls()
        self.reset_machine()

    def setup_ui_controls(self):
        tk.Label(self.right_frame, text="GRBL CONTROLLER", font=("Arial", 14, "bold"), bg="#f0f0f0").pack(pady=(15, 5))

        self.lbl_timer = tk.Label(self.right_frame, text="00:00.00", font=("Courier", 24, "bold"), bg="black", fg="#00ff00")
        self.lbl_timer.pack(fill=tk.X, padx=10, pady=5)
        
        self.lbl_status = tk.Label(self.right_frame, text="STATUS: IDLE", font=("Arial", 10, "bold"), fg="gray", bg="#f0f0f0")
        self.lbl_status.pack(pady=5)

        # BAGIAN 1: TOMBOL OPERASI
        btn_frame = tk.Frame(self.right_frame, bg="#f0f0f0")
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        tk.Button(btn_frame, text="📂 LOAD", command=self.load_file, bg="#ddd", height=2).grid(row=0, column=0, sticky="ew", padx=2, pady=2)
        tk.Button(btn_frame, text="▶ START", command=self.start_sim, bg="#4CAF50", fg="white", height=2).grid(row=0, column=1, sticky="ew", padx=2, pady=2)
        tk.Button(btn_frame, text="⏸ PAUSE", command=self.toggle_pause, bg="#FF9800", fg="white", height=2).grid(row=1, column=0, sticky="ew", padx=2, pady=2)
        tk.Button(btn_frame, text="⏹ STOP", command=self.stop_sim, bg="#F44336", fg="white", height=2).grid(row=1, column=1, sticky="ew", padx=2, pady=2)

        # BAGIAN 2: NAVIGASI (SPLIT KIRI-KANAN)
        nav_frame = tk.LabelFrame(self.right_frame, text="Navigation Control", bg="#f0f0f0", padx=5, pady=5)
        nav_frame.pack(fill=tk.X, padx=10, pady=5)

        nav_inner = tk.Frame(nav_frame, bg="#f0f0f0")
        nav_inner.pack(fill=tk.X)

        zoom_frame = tk.Frame(nav_inner, bg="#f0f0f0")
        zoom_frame.pack(side=tk.LEFT, padx=(5, 20))
        
        tk.Button(zoom_frame, text="🔍 Zoom (+)", command=lambda: self.adjust_view(zoom=0.8), width=10).pack(pady=2)
        tk.Button(zoom_frame, text="🔍 Zoom (-)", command=lambda: self.adjust_view(zoom=1.2), width=10).pack(pady=2)

        dpad_frame = tk.Frame(nav_inner, bg="#f0f0f0")
        dpad_frame.pack(side=tk.RIGHT, padx=5)
        
        tk.Button(dpad_frame, text="⬆", command=lambda: self.adjust_view(dy=50), width=4).grid(row=0, column=1)
        tk.Button(dpad_frame, text="⬅", command=lambda: self.adjust_view(dx=-50), width=4).grid(row=1, column=0)
        tk.Button(dpad_frame, text="➡", command=lambda: self.adjust_view(dx=50), width=4).grid(row=1, column=2)
        tk.Button(dpad_frame, text="⬇", command=lambda: self.adjust_view(dy=-50), width=4).grid(row=2, column=1)

        # BAGIAN 3: TERMINAL
        tk.Label(self.right_frame, text="G-Code Terminal:", bg="#f0f0f0", anchor="w").pack(fill=tk.X, padx=10, pady=(15,0))
        self.terminal = scrolledtext.ScrolledText(self.right_frame, height=12, font=("Consolas", 9), bg="black", fg="white")
        self.terminal.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.terminal.tag_config("highlight", background="yellow", foreground="black")

    def reset_machine(self):
        self.t.clear()
        self.t.penup()
        self.t.goto(0, 0)
        self.t.setheading(0)
        
        # Reset Variabel State Mesin
        self.unit_scale = 1.0       # Default G21 (mm)
        self.is_relative = False    # Default G90 (Absolute)
        self.feed_rate = 1000.0     # Default F1000
        self.spindle_power = 0.0
        self.laser_on = False
        self.active_g_mode = 0      # G00 Rapid Mode
        self.current_z = 10.0
        
        self.line_index = 0
        self.elapsed_time = 0
        self.is_moving = False
        
        self.screen.update()

    def load_file(self):
        path = filedialog.askopenfilename(filetypes=[("GCode Files", "*.gcode *.nc *.txt *.ngc")])
        if path:
            with open(path, 'r') as f:
                self.gcode_lines = f.readlines()
            
            self.terminal.delete(1.0, tk.END)
            self.terminal.insert(tk.END, "".join(self.gcode_lines))
            self.lbl_status.config(text=f"LOADED: {len(self.gcode_lines)} lines")
            self.reset_machine()

    def start_sim(self):
        if not self.gcode_lines: 
            self.lbl_status.config(text="NO FILE LOADED!", fg="red")
            return

        if self.is_paused:
            self.toggle_pause()
            return

        if self.is_running:
            return 

        self.reset_machine() 
        self.is_running = True
        self.is_paused = False
        self.start_time = time.time()
        self.lbl_status.config(text="RUNNING", fg="green")
        
        self.process_queue()

    def stop_sim(self):
        self.is_running = False
        self.lbl_status.config(text="STOPPED", fg="red")

    def toggle_pause(self):
        if self.is_running:
            self.is_paused = not self.is_paused
            state = "PAUSED" if self.is_paused else "RUNNING"
            self.lbl_status.config(text=state, fg="orange" if self.is_paused else "green")
            if not self.is_paused:
                self.start_time = time.time() - self.elapsed_time
                self.process_queue()

    def parse_gcode(self, line):
        # 1. Bersihkan komentar (;) dan dalam kurung ()
        line = line.split(';')[0]
        line = re.sub(r'\(.*?\)', '', line)
        line = line.upper().strip()
        
        if not line: return {}

        # 2. Parsing seluruh huruf parameter (G, M, X, Y, Z, F, S)
        coords = {}
        matches = re.findall(r'([GMXYZFS])\s*([-+]?[0-9]*\.?[0-9]+)', line)
        
        for axis, val in matches:
            coords[axis] = float(val)
        
        return coords

    def process_queue(self):
        if not self.is_running: return
        if self.is_paused:
            self.root.after(100, self.process_queue)
            return

        # MODE A: SEDANG ANIMASI GERAK (Meluncur)
        if self.is_moving:
            if self.current_step < self.total_steps:
                new_x = self.t.xcor() + self.dx
                new_y = self.t.ycor() + self.dy
                self.t.goto(new_x, new_y)
                self.current_step += 1
                self.update_timer()
                self.screen.update()
                self.root.after(20, self.process_queue) 
                return
            else:
                self.is_moving = False
                self.line_index += 1

        # MODE B: BACA BARIS BARU
        if self.line_index >= len(self.gcode_lines):
            self.stop_sim()
            self.lbl_status.config(text="COMPLETED", fg="blue")
            return

        line = self.gcode_lines[self.line_index]
        cmd = self.parse_gcode(line)
        self.highlight_line(self.line_index)

        # --- UPDATE STATE PARAMETER GRBL & G-CODE ---
        
        # 1. G-CODES Mode Unit & Position
        if 'G' in cmd:
            g_val = int(cmd['G'])
            if g_val == 20: self.unit_scale = 25.4   # Inches
            elif g_val == 21: self.unit_scale = 1.0   # Millimeters
            elif g_val == 90: self.is_relative = False # Absolute Positioning
            elif g_val == 91: self.is_relative = True  # Incremental/Relative Positioning
            elif g_val in (0, 1):
                self.active_g_mode = g_val           # simpan mode G00 atau G01

        # 2. M-CODES (Spindle/Laser Control)
        if 'M' in cmd:
            m_val = int(cmd['M'])
            if m_val in (3, 4):  # M3 (Constant Power), M4 (Dynamic Laser Mode)
                self.laser_on = True
            elif m_val == 5:     # M5 (Laser Off)
                self.laser_on = False

        # 3. FEEDRATE (F) & SPINDLE/LASER POWER (S)
        if 'F' in cmd:
            self.feed_rate = cmd['F']
            # Skala animasi berdasarkan nilai Feedrate
            self.sim_speed = max(0.5, min(10.0, self.feed_rate / 150.0))

        if 'S' in cmd:
            self.spindle_power = cmd['S']

        if 'Z' in cmd:
            self.current_z = cmd['Z']

        # --- UPDATE PENA BERDASARKAN MODE DARI G-CODE ---
        # Jika mode G01 dan Laser Aktif (atau Z <= 0 pada CNC): Gambar Garis
        is_cutting = (self.active_g_mode == 1 and (self.laser_on or self.current_z <= 0))

        if is_cutting:
            self.t.pencolor("black")
            self.t.width(2)
            self.t.pendown()
        else:
            # Mode Rapid (G00) / Travel: Tampilkan Jejak Merah Tipis
            self.t.pencolor("red")
            self.t.width(1)
            self.t.pendown()

        # --- KALKULASI KOORDINAT GERAKAN (MODAL G0/G1) ---
        start_x, start_y = self.t.pos()

        has_motion = ('X' in cmd or 'Y' in cmd)

        if has_motion:
            # Sumbu X
            if 'X' in cmd:
                val_x = cmd['X'] * self.unit_scale
                target_x = (start_x + val_x) if self.is_relative else val_x
            else:
                target_x = start_x

            # Sumbu Y
            if 'Y' in cmd:
                val_y = cmd['Y'] * self.unit_scale
                target_y = (start_y + val_y) if self.is_relative else val_y
            else:
                target_y = start_y

            distance = ((target_x - start_x)**2 + (target_y - start_y)**2)**0.5

            if distance > 0:
                self.total_steps = int(distance / self.sim_speed)
                if self.total_steps < 1: self.total_steps = 1
                
                self.dx = (target_x - start_x) / self.total_steps
                self.dy = (target_y - start_y) / self.total_steps
                
                self.current_step = 0
                self.is_moving = True
                self.root.after(10, self.process_queue)
                return

        # Jika hanya berupa setting parameter (misal G21, G90, M4, F1000) tanpa gerakan X/Y:
        self.line_index += 1
        self.root.after(1, self.process_queue)

    def update_timer(self):
        self.elapsed_time = time.time() - self.start_time
        mins, secs = divmod(int(self.elapsed_time), 60)
        mils = int((self.elapsed_time - int(self.elapsed_time)) * 100)
        self.lbl_timer.config(text=f"{mins:02}:{secs:02}.{mils:02}")

    def highlight_line(self, idx):
        self.terminal.tag_remove("highlight", "1.0", tk.END)
        self.terminal.tag_add("highlight", f"{idx+1}.0", f"{idx+1}.end")
        self.terminal.see(f"{idx+1}.0")

    def adjust_view(self, dx=0, dy=0, zoom=1.0):
        self.zoom_level *= zoom
        self.pan_x += dx * self.zoom_level
        self.pan_y += dy * self.zoom_level
        self.update_view_coords()

    def update_view_coords(self):
        w = self.base_view * self.zoom_level
        h = self.base_view * self.zoom_level
        
        llx = -w/2 + self.pan_x
        lly = -h/2 + self.pan_y
        urx = w/2 + self.pan_x
        ury = h/2 + self.pan_y
        
        self.screen.setworldcoordinates(llx, lly, urx, ury)
        self.screen.update()

if __name__ == "__main__":
    root = tk.Tk()
    app = GCodeSimulator(root)
    root.mainloop()