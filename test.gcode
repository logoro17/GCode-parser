; Setup Awal
G21 ; Set satuan ke Millimeter (Penting untuk kalkulasi jarak)
G90 ; Set ke Absolute Positioning
G17 ; Pilih XY Plane
G0 Z5 ; Angkat Tool ke ketinggian aman (Z > 0, Garis Merah/Travel)

; --- SHAPE 1: KOTAK LUAR (50x50mm) ---
; Gerakan Cepat (Rapid) ke titik awal
G0 X10 Y10 F2000 ; Travel (Merah) ke posisi (10,10) dengan kecepatan 2000mm/min

; Mulai Memotong (Pen Down)
G1 Z-1 F500 ; Turunkan Z ke -1 (Hitam/Cutting). Kecepatan potong 500mm/min
G1 X60 Y10 ; Potong ke kanan
G1 X60 Y60 ; Potong ke atas
G1 X10 Y60 ; Potong ke kiri
G1 X10 Y10 ; Kembali ke titik awal kotak

; Selesai Memotong (Pen Up)
G0 Z5 ; Angkat Z ke 5 (Merah/Travel)

; --- SHAPE 2: SEGITIGA DALAM ---
; Pindah posisi (Travel)
G0 X25 Y25 ; Travel (Merah) ke titik awal segitiga

; Mulai Memotong (Pen Down)
G1 Z-1 ; Turunkan Z (Hitam). Feedrate ikut yang terakhir (F500)
G1 X45 Y25 ; Alas segitiga
G1 X35 Y50 ; Puncak segitiga
G1 X25 Y25 ; Kembali ke titik awal segitiga

; Selesai Memotong (Pen Up)
G0 Z5 ; Angkat Z (Merah)

; --- END PROGRAM ---
G0 X0 Y0 ; Kembali ke Home (Travel/Merah)
M2 ; End of Program
