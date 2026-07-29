import tkinter as tk
from tkinter import filedialog, scrolledtext
import turtle
import time
import re

class GCodeSimulator:
    def __init__(self, root):
        self.root = root
        self.root.title("G-Code Simulator (Turtle + GRBL)")
        self.root.geometry("1100x700")

        # --- KONFIGURASI LAYOUT (Split View) ---
        # 1. Frame Kiri (Visualisasi)
        self.left_frame = tk.Frame(root, bg="#333", width=700)
        self.left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 2. Frame Kanan (Kontrol & Terminal)
        self.right_frame = tk.Frame(root, bg="#f0f0f0", width=400)
        self.right_frame.pack(side=tk.RIGHT, fill=tk.Y)

        # --- SETUP TURTLE DI DALAM TKINTER (Embedded) ---
        # Kita butuh Canvas Tkinter dulu
        self.canvas = tk.Canvas(self.left_frame, bg="white")
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Hubungkan Turtle ke Canvas ini (RawTurtle)
        self.screen = turtle.TurtleScreen(self.canvas)
        self.screen.bgcolor("white")
        self.screen.tracer(0) # Matikan animasi otomatis (wajib untuk performa)
        
        self.t = turtle.RawTurtle(self.screen)
        self.t.speed(0)
        self.t.hideturtle()

        # --- VARIABEL STATE ---
        self.gcode_lines = []
        self.line_index = 0
        self.is_running = False
        self.is_paused = False
        self.start_time = 0
        self.elapsed_time = 0
        
        # State Mesin
        self.current_z = 10.0 # Default aman (di atas)
        self.pos_x = 0.0
        self.pos_y = 0.0
        
        # State View (Pan/Zoom)
        self.zoom_level = 1.0
        self.pan_x = 0
        self.pan_y = 0
        self.base_view = 400 # Ukuran area pandang dasar

        self.setup_ui_controls()
        self.reset_machine()
        
        self.current_step = 0
        self.total_steps = 0
        self.dx = 0
        self.dy = 0
        self.is_moving = False # Status apakah sedang meluncur
        self.sim_speed = 2.0   # Kecepatan pixel per frame (Makin besar makin ngebut)
        
        # --- STATE MESIN UNIVERSAL ---
        self.unit_scale = 1.0     # 1.0 = MM (G21), 25.4 = Inch (G20)
        self.is_relative = False  # False = Absolute (G90), True = Incremental (G91)
        self.feed_rate = 1000     # Kecepatan default

    def setup_ui_controls(self):
        # --- HEADER & TIMER (Tetap Sama) ---
        tk.Label(self.right_frame, text="GRBL CONTROLLER", font=("Arial", 14, "bold"), bg="#f0f0f0").pack(pady=(15, 5))

        self.lbl_timer = tk.Label(self.right_frame, text="00:00.00", font=("Courier", 24, "bold"), bg="black", fg="#00ff00")
        self.lbl_timer.pack(fill=tk.X, padx=10, pady=5)
        
        self.lbl_status = tk.Label(self.right_frame, text="STATUS: IDLE", font=("Arial", 10, "bold"), fg="gray", bg="#f0f0f0")
        self.lbl_status.pack(pady=5)

        # --- BAGIAN 1: TOMBOL OPERASI (GRID 2x2) ---
        # Kita pakai Grid biar rapi kiri-kanan
        btn_frame = tk.Frame(self.right_frame, bg="#f0f0f0")
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        # Konfigurasi kolom biar lebar tombolnya sama rata (50:50)
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        # Baris 1
        tk.Button(btn_frame, text="📂 LOAD", command=self.load_file, bg="#ddd", height=2).grid(row=0, column=0, sticky="ew", padx=2, pady=2)
        tk.Button(btn_frame, text="▶ START", command=self.start_sim, bg="#4CAF50", fg="white", height=2).grid(row=0, column=1, sticky="ew", padx=2, pady=2)

        # Baris 2
        tk.Button(btn_frame, text="⏸ PAUSE", command=self.toggle_pause, bg="#FF9800", fg="white", height=2).grid(row=1, column=0, sticky="ew", padx=2, pady=2)
        tk.Button(btn_frame, text="⏹ STOP", command=self.stop_sim, bg="#F44336", fg="white", height=2).grid(row=1, column=1, sticky="ew", padx=2, pady=2)

        # --- BAGIAN 2: NAVIGASI (SPLIT KIRI-KANAN) ---
        nav_frame = tk.LabelFrame(self.right_frame, text="Navigation Control", bg="#f0f0f0", padx=5, pady=5)
        nav_frame.pack(fill=tk.X, padx=10, pady=5)

        # Container dalam nav_frame
        nav_inner = tk.Frame(nav_frame, bg="#f0f0f0")
        nav_inner.pack(fill=tk.X)

        # KIRI: Tombol Zoom (Atas Bawah)
        zoom_frame = tk.Frame(nav_inner, bg="#f0f0f0")
        zoom_frame.pack(side=tk.LEFT, padx=(5, 20)) # Padding kanan biar ada jarak sama D-pad
        
        tk.Button(zoom_frame, text="🔍 Zoom (+)", command=lambda: self.adjust_view(zoom=0.8), width=10).pack(pady=2)
        tk.Button(zoom_frame, text="🔍 Zoom (-)", command=lambda: self.adjust_view(zoom=1.2), width=10).pack(pady=2)

        # KANAN: D-Pad (Arah Panah)
        dpad_frame = tk.Frame(nav_inner, bg="#f0f0f0")
        dpad_frame.pack(side=tk.RIGHT, padx=5)
        
        # Grid D-Pad agar membentuk tanda +
        tk.Button(dpad_frame, text="⬆", command=lambda: self.adjust_view(dy=50), width=4).grid(row=0, column=1)
        tk.Button(dpad_frame, text="⬅", command=lambda: self.adjust_view(dx=-50), width=4).grid(row=1, column=0)
        tk.Button(dpad_frame, text="➡", command=lambda: self.adjust_view(dx=50), width=4).grid(row=1, column=2)
        tk.Button(dpad_frame, text="⬇", command=lambda: self.adjust_view(dy=-50), width=4).grid(row=2, column=1)

        # --- BAGIAN 3: TERMINAL ---
        tk.Label(self.right_frame, text="G-Code Terminal:", bg="#f0f0f0", anchor="w").pack(fill=tk.X, padx=10, pady=(15,0))
        self.terminal = scrolledtext.ScrolledText(self.right_frame, height=12, font=("Consolas", 9), bg="black", fg="white")
        self.terminal.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.terminal.tag_config("highlight", background="yellow", foreground="black")

    def reset_machine(self):
        self.t.clear()       # Hapus gambar lama (Clear Screen)
        self.t.penup()
        self.t.goto(0,0)     # Kembali ke Home
        self.t.setheading(0) # Reset arah kepala turtle
        
        # Reset Variabel State
        self.current_z = 10.0
        self.pos_x = 0.0
        self.pos_y = 0.0
        self.line_index = 0  # <--- PENTING: Ulang dari baris pertama
        self.elapsed_time = 0
        
        # Reset Navigasi View (Opsional, kalau mau zoom balik ke default)
        # self.zoom_level = 1.0 
        # self.pan_x, self.pan_y = 0, 0
        # self.update_view_coords() 
        
        self.screen.update()

    def load_file(self):
        # Tambahkan *.ngc di dalam string filetypes
        path = filedialog.askopenfilename(filetypes=[("GCode Files", "*.gcode *.nc *.txt *.ngc")])
        
        if path:
            with open(path, 'r') as f:
                self.gcode_lines = f.readlines()
            
            # Reset dan tampilkan di terminal
            self.terminal.delete(1.0, tk.END)
            self.terminal.insert(tk.END, "".join(self.gcode_lines))
            self.lbl_status.config(text=f"LOADED: {len(self.gcode_lines)} lines")
            self.reset_machine()

    def start_sim(self):
        # 1. Cek apakah ada file yang dimuat
        if not self.gcode_lines: 
            self.lbl_status.config(text="NO FILE LOADED!", fg="red")
            return

        # 2. Cek apakah sedang PAUSE? Kalau ya, RESUME saja.
        if self.is_paused:
            self.toggle_pause()
            return

        # 3. Cek apakah sedang JALAN? Kalau ya, abaikan tombol start.
        if self.is_running:
            return 

        # 4. KONDISI: STOPPED atau FINISHED (Restart)
        # Ini bagian yang kamu minta. 
        # Sebelum mulai, kita bersihkan layar & reset index ke 0.
        self.reset_machine() 

        # Mulai System
        self.is_running = True
        self.is_paused = False
        self.start_time = time.time() # Reset waktu start baru
        self.lbl_status.config(text="RUNNING", fg="green")
        
        # Jalankan Loop
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
        # 1. Bersihkan Komentar
        line = line.split(';')[0]
        line = re.sub(r'\(.*?\)', '', line)
        line = line.upper().strip()
        
        if not line: return {}

        # 2. Parsing Universal (G, M, X, Y, Z, F, S)
        coords = {}
        # Regex ini sekarang menangkap M dan F juga
        matches = re.findall(r'([GMXYZFS])\s*([-+]?[0-9]*\.?[0-9]+)', line)
        
        for axis, val in matches:
            coords[axis] = float(val)
        
        return coords

    def process_queue(self):
        # Cek status dasar
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
                self.line_index += 1 # Selesai gerak, lanjut baris berikut

        # MODE B: BACA BARIS BARU
        if self.line_index >= len(self.gcode_lines):
            self.stop_sim()
            self.lbl_status.config(text="COMPLETED", fg="blue")
            return

        line = self.gcode_lines[self.line_index]
        cmd = self.parse_gcode(line)
        self.highlight_line(self.line_index)

        # --- UPDATE STATE DARI G-CODE ---
        
        # 1. FEEDRATE (F) - Mengatur Kecepatan Simulasi
        if 'F' in cmd:
            # Konversi kasar: F1000 = Cepat, F100 = Lambat
            # Kita atur self.sim_speed (pixel per step)
            f_val = cmd['F']
            # Rumus konversi biar visualnya enak dilihat:
            self.sim_speed = f_val / 50.0 
            if self.sim_speed < 0.5: self.sim_speed = 0.5 # Jangan terlalu lambat
            if self.sim_speed > 10.0: self.sim_speed = 10.0 # Jangan terlalu ngebut

        # 2. M-CODES (M3/M5) - Opsional: Kontrol Pen
        # M3 = Spindle ON (Cutting), M5 = Spindle OFF (Travel)
        if 'M' in cmd:
            m_val = int(cmd['M'])
            if m_val == 3: # M03 Spindle ON
                self.current_z = -1 # Anggap Z turun (Cutting)
            elif m_val == 5: # M05 Spindle OFF
                self.current_z = 10 # Anggap Z naik (Travel)

        # 3. Z-AXIS (Warna)
        if 'Z' in cmd: self.current_z = cmd['Z']
        
        # Update Warna Pena
        if self.current_z <= 0:
            self.t.pencolor("black"); self.t.width(2); self.t.pendown()
        else:
            self.t.pencolor("red"); self.t.width(1); self.t.pendown()

        # --- PERSIAPAN GERAKAN ---
        start_x, start_y = self.t.pos()
        
        # Logika Absolute (G90) vs Relative (G91)
        # (Pastikan self.unit_scale dan self.is_relative ada di __init__)
        scale = getattr(self, 'unit_scale', 1.0) 
        is_rel = getattr(self, 'is_relative', False)

        # Target X
        if 'X' in cmd:
            val_x = cmd['X'] * scale
            target_x = (start_x + val_x) if is_rel else val_x
        else:
            target_x = start_x

        # Target Y
        if 'Y' in cmd:
            val_y = cmd['Y'] * scale
            target_y = (start_y + val_y) if is_rel else val_y
        else:
            target_y = start_y

        # --- EKSEKUSI ---
        distance = ((target_x - start_x)**2 + (target_y - start_y)**2)**0.5

        if distance > 0:
            # Kalau ada jarak, mulai animasi
            self.total_steps = int(distance / self.sim_speed)
            if self.total_steps < 1: self.total_steps = 1
            
            self.dx = (target_x - start_x) / self.total_steps
            self.dy = (target_y - start_y) / self.total_steps
            
            self.current_step = 0
            self.is_moving = True
            self.root.after(10, self.process_queue)
        else:
            # PENTING: Jika tidak ada gerakan (misal cuma set F100 atau M03)
            # Langsung lanjut ke baris berikutnya TANPA DELAY
            self.line_index += 1
            self.root.after(1, self.process_queue) # Langsung proses baris selanjutnya

    def update_timer(self):
        self.elapsed_time = time.time() - self.start_time
        mins, secs = divmod(int(self.elapsed_time), 60)
        mils = int((self.elapsed_time - int(self.elapsed_time)) * 100)
        self.lbl_timer.config(text=f"{mins:02}:{secs:02}.{mils:02}")

    def highlight_line(self, idx):
        # Scroll terminal otomatis ke baris yang sedang jalan
        self.terminal.tag_remove("highlight", "1.0", tk.END)
        self.terminal.tag_add("highlight", f"{idx+1}.0", f"{idx+1}.end")
        self.terminal.see(f"{idx+1}.0")

    # --- LOGIKA ZOOM / PAN (Point 4) ---
    def adjust_view(self, dx=0, dy=0, zoom=1.0):
        self.zoom_level *= zoom
        self.pan_x += dx * self.zoom_level
        self.pan_y += dy * self.zoom_level
        self.update_view_coords()

    def update_view_coords(self):
        # Mengubah sistem koordinat dunia Turtle untuk efek Zoom/Pan
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

