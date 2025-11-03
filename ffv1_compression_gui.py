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

def toggle_pause():
    """Toggle pause/resume state."""
    if paused.get():
        paused.set(False)
        pause_button.config(text="⏸ Pause")
        status.set("Resuming compression...")
    else:
        paused.set(True)
        pause_button.config(text="▶ Resume")
        status.set("Paused...")


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

    total_files = sum(len(files) for _, _, files in os.walk(src))
    processed = 0

    for root, dirs, files in os.walk(src):
        rel_dir = os.path.relpath(root, src)
        out_dir = os.path.join(dst, rel_dir)
        os.makedirs(out_dir, exist_ok=True)

        for f in files:

            while paused.get():
                root_window.update()

            src_file = os.path.join(root, f)
            dst_file = os.path.join(out_dir, f)

            if f.lower().endswith(".avi"):
                dst_file = dst_file[:-4] + ".mkv"

                if skip_existing.get() and os.path.exists(dst_file):
                    status.set(f"⏭ Skipping existing: {os.path.relpath(dst_file, dst)}")
                    root_window.update_idletasks()
                    continue

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

            processed += 1
            status.set(f"Processed {processed}/{total_files} files...")
            root_window.update_idletasks()


    status.set("✅ Compression complete!")

# --- GUI setup ---
root_window = tk.Tk()
root_window.title("Offline FFV1 Batch Compressor")

input_path = tk.StringVar()
output_path = tk.StringVar()
status = tk.StringVar(value="Select folders and press Start")
paused = tk.BooleanVar(value=False)
skip_existing = tk.BooleanVar(value=True)

# Input folder
tk.Label(root_window, text="Input folder:").grid(row=0, column=0, sticky="e")
tk.Entry(root_window, textvariable=input_path, width=50).grid(row=0, column=1)
tk.Button(root_window, text="Browse", command=select_input_folder).grid(row=0, column=2)

# Output folder
tk.Label(root_window, text="Output folder:").grid(row=1, column=0, sticky="e")
tk.Entry(root_window, textvariable=output_path, width=50).grid(row=1, column=1)
tk.Button(root_window, text="Browse", command=select_output_folder).grid(row=1, column=2)


# Skip checkbox
tk.Checkbutton(root_window, text="Skip already compressed", variable=skip_existing).grid(
    row=2, column=0, columnspan=3, sticky="w", padx=5, pady=(5, 0)
)



# Buttons
tk.Button(root_window, text="Start Compression", command=compress_videos, bg="#4CAF50", fg="white").grid(
    row=2, column=0, columnspan=3, pady=10
)

pause_button = tk.Button(root_window, text="⏸ Pause", command=toggle_pause, bg="#FFC107")
pause_button.grid(row=3, column=2, pady=10)


tk.Label(root_window, textvariable=status, fg="blue").grid(row=3, column=0, columnspan=3)

root_window.mainloop()
