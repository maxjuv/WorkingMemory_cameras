import os
import shutil
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox

def select_input_folder():
    path = filedialog.askdirectory(title="Select Input Data Folder")
    if path:
        input_path.set(path)

def select_output_folder():
    path = filedialog.askdirectory(title="Select Output Folder")
    if path:
        output_path.set(path)

def compress_videos():
    src = input_path.get()
    dst = output_path.get()

    if not src or not dst:
        messagebox.showerror("Error", "Please select both input and output folders.")
        return

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        messagebox.showerror("Error", "FFmpeg not found. Please install it and ensure it's in PATH.")
        return

    for root, dirs, files in os.walk(src):
        rel_dir = os.path.relpath(root, src)
        out_dir = os.path.join(dst, rel_dir)
        os.makedirs(out_dir, exist_ok=True)

        for f in files:
            src_file = os.path.join(root, f)
            dst_file = os.path.join(out_dir, f)

            if f.lower().endswith(".avi"):
                dst_file = dst_file[:-4] + "_ffv1.mkv"
                cmd = [
                    ffmpeg, "-y", "-i", src_file,
                    "-c:v", "ffv1", "-level", "3", "-coder", "1", "-context", "1", "-g", "1",
                    dst_file
                ]
                status.set(f"Compressing: {os.path.relpath(src_file, src)}")
                root_window.update_idletasks()
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                if not os.path.exists(dst_file):
                    shutil.copy2(src_file, dst_file)

    status.set("✅ Compression complete!")

# --- GUI setup ---
root_window = tk.Tk()
root_window.title("Offline FFV1 Batch Compressor")

input_path = tk.StringVar()
output_path = tk.StringVar()
status = tk.StringVar(value="Select folders and press Start")

tk.Label(root_window, text="Input folder:").grid(row=0, column=0, sticky="e")
tk.Entry(root_window, textvariable=input_path, width=50).grid(row=0, column=1)
tk.Button(root_window, text="Browse", command=select_input_folder).grid(row=0, column=2)

tk.Label(root_window, text="Output folder:").grid(row=1, column=0, sticky="e")
tk.Entry(root_window, textvariable=output_path, width=50).grid(row=1, column=1)
tk.Button(root_window, text="Browse", command=select_output_folder).grid(row=1, column=2)

tk.Button(root_window, text="Start Compression", command=compress_videos, bg="#4CAF50", fg="white").grid(
    row=2, column=0, columnspan=3, pady=10
)

tk.Label(root_window, textvariable=status, fg="blue").grid(row=3, column=0, columnspan=3)

root_window.mainloop()
