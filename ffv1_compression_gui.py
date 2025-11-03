import os
import shutil
import subprocess
import threading
import queue
import time
import platform
import signal
import tkinter as tk
from tkinter import filedialog, messagebox

# --- Globals / thread-safe structures ---
status_q = queue.Queue()
current_proc = None
proc_lock = threading.Lock()
worker_thread = None

def set_status(msg):
    """Put a status message into the queue (thread-safe)."""
    status_q.put(msg)

def poll_status():
    """Poll the status queue and update the status StringVar on the main thread."""
    try:
        while True:
            msg = status_q.get_nowait()
            status.set(msg)
    except queue.Empty:
        pass
    root_window.after(100, poll_status)

def select_input_folder():
    path = filedialog.askdirectory(title="Select Input Data Folder")
    if path:
        input_path.set(path)

def select_output_folder():
    path = filedialog.askdirectory(title="Select Output Folder")
    if path:
        output_path.set(path)

def toggle_pause():
    """Toggle pause/resume state and attempt to pause/resume ffmpeg if running (POSIX only)."""
    if paused.get():
        # currently paused -> resume
        paused.set(False)
        pause_button.config(text="⏸ Pause")
        set_status("Resuming...")
        # try to SIGCONT current process on POSIX
        if platform.system() != "Windows":
            with proc_lock:
                p = current_proc
                try:
                    if p and p.poll() is None:
                        os.kill(p.pid, signal.SIGCONT)
                except Exception:
                    pass
    else:
        # currently running -> pause
        paused.set(True)
        pause_button.config(text="▶ Resume")
        set_status("Paused...")
        # try to SIGSTOP current process on POSIX
        if platform.system() != "Windows":
            with proc_lock:
                p = current_proc
                try:
                    if p and p.poll() is None:
                        os.kill(p.pid, signal.SIGSTOP)
                except Exception:
                    pass

def start_compression_thread():
    """Create and start worker thread; prevent multiple starts."""
    global worker_thread
    if worker_thread and worker_thread.is_alive():
        messagebox.showinfo("Already running", "Compression is already running.")
        return
    worker_thread = threading.Thread(target=compress_videos_worker, daemon=True)
    worker_thread.start()

def compress_videos_worker():
    """The worker that runs in a separate thread; safe to block here."""
    src = input_path.get()
    dst = output_path.get()

    if not src or not dst:
        messagebox.showerror("Error", "Please select both input and output folders.")
        return

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        messagebox.showerror("Error", "FFmpeg not found. Please install it and ensure it's in PATH.")
        return

    # Count total files for progress (optional)
    total_files = 0
    file_list = []
    for root, dirs, files in os.walk(src):
        for f in files:
            total_files += 1
            file_list.append((root, f))
    processed = 0

    set_status(f"Starting compression: {total_files} files found")

    for root, f in file_list:
        # allow user to cancel from GUI if needed (optional)
        if stop_requested.get():
            set_status("Stopping requested — exiting...")
            break

        # Wait while paused (safe in worker thread)
        while paused.get() and not stop_requested.get():
            time.sleep(0.1)

        rel_dir = os.path.relpath(root, src)
        out_dir = os.path.join(dst, rel_dir)
        os.makedirs(out_dir, exist_ok=True)

        src_file = os.path.join(root, f)
        dst_file = os.path.join(out_dir, f)

        if f.lower().endswith(".avi"):
            dst_file = dst_file[:-4] + ".mkv"

            # Skip if already exists and option enabled
            if skip_existing.get() and os.path.exists(dst_file):
                set_status(f"⏭ Skipping existing: {os.path.relpath(dst_file, dst)}")
                processed += 1
                continue

            set_status(f"Compressing: {os.path.relpath(src_file, src)}")
            cmd = [
                ffmpeg, "-y", "-i", src_file,
                "-c:v", "ffv1", "-level", "3", "-coder", "1", "-context", "1", "-g", "1",
                dst_file
            ]

            # Start process and keep handle
            try:
                with proc_lock:
                    # Use Popen so we can signal the process
                    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    global current_proc
                    current_proc = p

                # While process is running, listen for pause requests
                while True:
                    if stop_requested.get():
                        # Attempt to terminate the process gracefully
                        with proc_lock:
                            if current_proc and current_proc.poll() is None:
                                try:
                                    current_proc.terminate()
                                except Exception:
                                    pass
                        break

                    # If paused and on POSIX, ensure process is stopped (SIGSTOP was attempted in toggle)
                    if paused.get():
                        # If on Windows, we cannot reliably SIGSTOP; so just wait until resume
                        # On POSIX, toggle_pause already sent SIGSTOP; we can just sleep here.
                        time.sleep(0.2)
                        continue

                    # if not paused, wait for short interval and recheck
                    ret = current_proc.poll()
                    if ret is not None:
                        break
                    time.sleep(0.1)

            except Exception as e:
                set_status(f"Error running ffmpeg on {src_file}: {e}")
            finally:
                with proc_lock:
                    current_proc = None

        else:
            # Non-avi files => copy
            if not os.path.exists(dst_file):
                try:
                    shutil.copy2(src_file, dst_file)
                except Exception as e:
                    set_status(f"Error copying {src_file}: {e}")

        processed += 1
        set_status(f"Processed {processed}/{total_files} files...")
        time.sleep(0.01)  # small yield

    set_status("✅ Compression complete!")
    # re-enable Start button on main thread
    root_window.after(0, lambda: start_button.config(state="normal"))

# --- GUI setup ---
root_window = tk.Tk()
root_window.title("Offline FFV1 Batch Compressor (with pause/resume)")

input_path = tk.StringVar()
output_path = tk.StringVar()
status = tk.StringVar(value="Select folders and press Start")
paused = tk.BooleanVar(value=False)
skip_existing = tk.BooleanVar(value=True)
stop_requested = tk.BooleanVar(value=False)

# Input folder
tk.Label(root_window, text="Input folder:").grid(row=0, column=0, sticky="e")
tk.Entry(root_window, textvariable=input_path, width=50).grid(row=0, column=1)
tk.Button(root_window, text="Browse", command=select_input_folder).grid(row=0, column=2)

# Output folder
tk.Label(root_window, text="Output folder:").grid(row=1, column=0, sticky="e")
tk.Entry(root_window, textvariable=output_path, width=50).grid(row=1, column=1)
tk.Button(root_window, text="Browse", command=select_output_folder).grid(row=1, column=2)

# Skip checkbox
tk.Checkbutton(root_window, text="Skip already compressed (.mkv) files", variable=skip_existing).grid(
    row=2, column=0, columnspan=3, sticky="w", padx=5, pady=(5, 0)
)

# Buttons
start_button = tk.Button(root_window, text="Start Compression", command=lambda: (start_button.config(state="disabled"), start_compression_thread()), bg="#4CAF50", fg="white")
start_button.grid(row=3, column=0, columnspan=2, pady=10)

pause_button = tk.Button(root_window, text="⏸ Pause", command=toggle_pause, bg="#FFC107")
pause_button.grid(row=3, column=2, pady=10)

# Stop button (optional)
def request_stop():
    stop_requested.set(True)
    set_status("Stop requested. Will stop after current activity.")
tk.Button(root_window, text="Stop", command=request_stop, bg="#E53935", fg="white").grid(row=5, column=2, pady=4)

# Status label
tk.Label(root_window, textvariable=status, fg="blue").grid(row=4, column=0, columnspan=3, pady=5)

# Start polling the status queue
root_window.after(100, poll_status)

root_window.mainloop()
