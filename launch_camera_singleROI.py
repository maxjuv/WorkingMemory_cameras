import PySpin
import tkinter as tk
from tkinter import filedialog, messagebox
from collections import deque
import threading
import os
import cv2
import numpy as np
# import imageio
import datetime
import time
import csv

default_path = 'C:/Users/alan/Desktop/'
default_foldername = None

sync_line_id = 2
trigger_line_id = 0

class FLIRApp:
    def __init__(self, root):
        self.root = root
        self.root.title("FLIR Acquisition Control")

        # Camera Setup
        self.system = PySpin.System.GetInstance()
        cam_list = self.system.GetCameras()
        if cam_list.GetSize() == 0:
            messagebox.showerror("Error", "No FLIR camera detected.")
            self.system.ReleaseInstance()
            root.destroy()
            return
        self.cam = cam_list[0]
        self.cam.Init()
        
        nodemap = self.cam.GetNodeMap()

        line_selector = PySpin.CEnumerationPtr(nodemap.GetNode("LineSelector"))
        line_mode     = PySpin.CEnumerationPtr(nodemap.GetNode("LineMode"))
        line_mode_in  = line_mode.GetEntryByName("Input")
        line_inv      = PySpin.CBooleanPtr(nodemap.GetNode("LineInverter"))


        print('######################################################### \n'
        'To crop : select with mouse then press "c"  \n'
        'To go back full size, press "f" \n'
        '#########################################################')

        for id in [sync_line_id, trigger_line_id]:
            # Line0 (opto) as Input
            line_selector.SetIntValue(line_selector.GetEntryByName(f"Line{id}").GetValue())
            line_mode.SetIntValue(line_mode_in.GetValue())
            line_inv.SetValue(False)  # set True if polarity looks flipped


        self.frame_queue = deque()  # frames go from acquisition to writer
        self.queue_lock = threading.Lock()  # optional, for safety
        self.current_thread_writer = None
        self.next_thread_writer = None
        self.next_thread_writer_filename = None

        self.frame_lock = threading.Lock()


        self.update_writer=False
        with self.frame_lock:
            self.frame_width = None
            self.frame_height = None

        # State Variables
        self.acquiring = False
        self.recording = False
        self.roi_defined = False
        self.roi = None  # (x, y, w, h)
        self.rotation = tk.IntVar(value=270)  # 0, 90, 180, 270
        self.save_path = tk.StringVar(value=default_path)
        self.foldername = tk.StringVar(value=default_foldername)
        
        self.mode = tk.StringVar(value="Continuous")
        self.fps = tk.DoubleVar(value=30.0)
        self.brightness = tk.DoubleVar(value=1.0)
        self.compression = tk.StringVar(value="RAW")
        self.last_compression = self.compression.get()
        self.exposure_time = tk.DoubleVar(value=15.0)  # microseconds (example default 5 ms)
        self.trial_index = 0
        self.ttl_log = []  # store timestamps for current trial
        self.frames_times_log = []  # store timestamps for current trial
        self.start_rec_time_hardware =None
        self.preview_enabled = tk.BooleanVar(value=True)
        self.last_preview_enabled = tk.BooleanVar(value=True)
        # GUI Layout
        tk.Button(root, text="Select Save path", command=self.select_folder).pack()
        tk.Label(root, textvariable=self.save_path).pack()

        tk.Label(root, text="Folder name:").pack()
        tk.Entry(root, textvariable=self.foldername).pack()


        tk.Label(root, text="Acquisition Mode:").pack()
        tk.OptionMenu(root, self.mode, "Continuous", "Trigger").pack()

        tk.Label(root, text="Compression:").pack()
        tk.OptionMenu(root, self.compression, "RAW", "FFV1").pack()

        tk.Label(root, text="FPS:").pack()
        tk.Entry(root, textvariable=self.fps).pack()

        tk.Label(root, text="Rotate image:").pack()
        tk.OptionMenu(root, self.rotation, 0, 90, 180, 270).pack()

        tk.Label(root, text="Brightness:").pack()
        tk.Entry(root, textvariable=self.brightness).pack()


        tk.Label(root, text="Exposure time (ms):").pack()
        tk.Entry(root, textvariable=self.exposure_time).pack()

        tk.Button(root, text="Start Acquisition", command=self.start_acquisition).pack(side="left", padx=5)
        tk.Button(root, text="Stop Acquisition", command=self.stop_acquisition).pack(side="left", padx=5)
        tk.Button(root, text="Start Recording", command=self.start_recording).pack(side="left", padx=5)
        tk.Button(root, text="Stop Recording", command=self.stop_recording).pack(side="left", padx=5)

        tk.Checkbutton(root, text="Enable Preview", variable=self.preview_enabled).pack()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def check_save_path(self):

        base_path = self.save_path.get()
        folder_name = self.foldername.get().strip()
        if folder_name:
            if not base_path.endswith(folder_name):
                base_path = os.path.join(base_path, folder_name)
            if not os.path.exists(base_path):
                os.makedirs(base_path)
        self.save_path.set(base_path)


    def select_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.save_path.set(folder)

    def start_acquisition(self):
        if self.acquiring:
            return
        self.acquiring = True

        # Camera settings
        self.cam.AcquisitionMode.SetValue(PySpin.AcquisitionMode_Continuous)
        self.cam.AcquisitionFrameRateEnable.SetValue(True)
        self.cam.AcquisitionFrameRate.SetValue(self.fps.get())
        self.cam.GainAuto.SetValue(PySpin.GainAuto_Off)
        self.cam.Gain.SetValue(self.brightness.get())
        self.cam.ExposureAuto.SetValue(PySpin.ExposureAuto_Off)
        max_exposure_us = 1_000_000 / self.fps.get()
        exposure_time = min(self.exposure_time.get()*1000, max_exposure_us)
        self.cam.ExposureTime.SetValue(exposure_time)

        self.check_save_path()
    

        # Start acquisition thread
        self.thread = threading.Thread(target=self.acquire_loop, daemon=True)
        self.thread.start()



    def acquire_loop(self):
        self.cam.BeginAcquisition()
        cv2.namedWindow("FLIR Preview", cv2.WINDOW_NORMAL)
        roi_start = None
        roi_end = None
        drawing = False
        last_trigger_line_state = False  
        sync_line_state = self.get_line_status(sync_line_id)
        last_sync_line_state = False
        
        def mouse_callback(event, x, y, flags, param):
            nonlocal roi_start, roi_end, drawing
            if self.recording:
                return
            if event == cv2.EVENT_LBUTTONDOWN:
                drawing = True
                roi_start = (x, y)
                roi_end = roi_start
            elif event == cv2.EVENT_MOUSEMOVE and drawing:
                roi_end = (x, y)
            elif event == cv2.EVENT_LBUTTONUP:
                drawing = False
                roi_end = (x, y)
        cv2.setMouseCallback("FLIR Preview", mouse_callback)
        rot_angle = self.rotation.get()

        while self.acquiring:

            current_compression = self.compression.get()
            if current_compression != self.last_compression:
                print(f"Compression changed: {self.last_compression} → {current_compression}")
                self.last_compression = current_compression
                self.update_writer = True  # trigger next writer preparation

            current_preview_enabled = self.preview_enabled.get()
            image = self.cam.GetNextImage()
            frame_timestamp = image.GetTimeStamp()  # uint64, in microseconds
            if image.IsIncomplete():
                print('frame drop !')
                width_drop = self.cam.Width.GetValue()
                height_drop = self.cam.Height.GetValue()
                frame_rec = np.zeros((height_drop, width_drop), dtype=np.uint8)
            else : 
                frame_rec = image.GetNDArray()  # convert PySpin image to NumPy array   
            if rot_angle == 90:
                frame_rec = cv2.rotate(frame_rec, cv2.ROTATE_90_COUNTERCLOCKWISE)
            elif rot_angle == 180:
                frame_rec = cv2.rotate(frame_rec, cv2.ROTATE_180)
            elif rot_angle == 270:
                frame_rec = cv2.rotate(frame_rec, cv2.ROTATE_90_CLOCKWISE)

            if self.roi_defined:
                x, y, w, h = self.roi
                frame_rec = frame_rec[y:y+h, x:x+w]
            h, w = frame_rec.shape
            with self.frame_lock:
                self.frame_width = w
                self.frame_height = h
            if self.update_writer :
                self.next_thread_writer = self.prepare_next_writer(self.trial_index)
            self.update_writer = False

            image.Release()
            if self.last_preview_enabled and not current_preview_enabled:
                cv2.destroyWindow("FLIR Preview")
            if current_preview_enabled:
                rot_angle = self.rotation.get()
                frame_disp = np.copy(frame_rec)
                frame_disp = np.ascontiguousarray(frame_disp)
                frame_disp = frame_disp.astype(np.uint8)
                # Draw ROI during selection
                if roi_start and roi_end and (not self.recording or self.roi_defined):
                    x1, y1 = roi_start
                    x2, y2 = roi_end
                    cv2.rectangle(frame_disp, (x1, y1), (x2, y2), (0, 255, 0), 2)

            
            # Recording logic
            if self.mode.get() == "Trigger" and self.recording:
                trigger_line_state = self.get_line_status(trigger_line_id)
                if trigger_line_state and not last_trigger_line_state:
                    # TTL rising edge → start recording
                    self.start_writer()
                elif not trigger_line_state and last_trigger_line_state:
                    # TTL falling edge → stop recording
                    self.stop_writer()
                last_trigger_line_state = trigger_line_state
            else:  # Continuous mode
                if self.recording and self.current_thread_writer is None and self.next_thread_writer is not None:
                    self.start_writer()
                elif not self.recording and self.current_thread_writer is not None:
                    self.stop_writer()


      # Append frame if recording
            if self.current_thread_writer:
                if self.start_rec_time_hardware is None:
                    self.start_rec_time_hardware = frame_timestamp

                sync_line_state = self.get_line_status(sync_line_id)
                if sync_line_state and not last_sync_line_state:
                    timestamp = time.time() - self.start_rec_time
                    self.ttl_log.append(timestamp)
                timestamp_sec = (frame_timestamp - self.start_rec_time_hardware) / 1e6
                self.frames_times_log.append(timestamp_sec)
                frame_to_push = frame_rec.copy()
                with self.queue_lock:
                    self.frame_queue.append(frame_to_push)
    

                if current_preview_enabled:
                    cv2.putText(frame_disp, f"Rec. trial{self.trial_index}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                                1.0, (0, 0, 255), 2, cv2.LINE_AA)
            if current_preview_enabled:
                cv2.imshow("FLIR Preview", frame_disp)
                key = cv2.waitKey(1) & 0xFF
                if key == ord('c') and not self.recording:
                    if self.roi_defined:
                        print("ROI already defined, ignoring 'c'")
                    elif roi_start and roi_end:
                        x1, y1 = roi_start
                        x2, y2 = roi_end
                        w = (abs(x2-x1)//16)*16
                        h = (abs(y2-y1)//16)*16
                        self.roi = (min(x1, x2), min(y1, y2), w, h)
                        self.roi_defined = True
                        print(f"ROI defined: {self.roi}")
                        self.update_writer = True

                elif key == ord('f') and not self.recording:
                    self.roi_defined = False
                    self.roi = None
                    print("Reset to full frame")
                    self.update_writer = True


            last_sync_line_state = sync_line_state
            self.last_preview_enabled = current_preview_enabled
        # Cleanup
        self.stop_writer()
        self.cam.EndAcquisition()
        cv2.destroyAllWindows()

    def start_writer(self):
        with self.queue_lock:
            self.frame_queue.clear()


        self.date_now = datetime.datetime.now()
        self.start_rec_time = time.time()
        self.ttl_log = []
        self.frames_times_log = []

        self.current_thread_writer = self.next_thread_writer
        self.current_thread_writer.active = True
        self.next_thread_writer = None


    def prepare_next_writer(self, trial_index):
        """Create the next writer thread but leave it inactive."""
        ext = "mkv" if self.compression.get() == "FFV1" else "avi"

        filename = os.path.join(
            self.save_path.get(),
            f"{datetime.datetime.now():%Y%m%d_%Hh%M}_trial{trial_index}.{ext}"  ### issue extension
        )

        already_prep_thread = self.next_thread_writer
        if already_prep_thread is not None :
            already_prepared_filename = self.next_thread_writer_filename
            already_prep_thread.active = False
            already_prep_thread.stop_flag = True
            already_prep_thread.join(timeout=1)
            os.remove(already_prepared_filename)
            print('[Writer] stop unused prepared thread and erase 0-frame video')
        self.next_thread_writer_filename = filename
        t = threading.Thread(target=self.writer_thread, daemon=True)
        t.active = False
        t.stop_flag = False
        t.filename = filename
        # t.codec = "ffv1" if self.compression.get() == "FFV1" else "rawvideo"  ## imageio style
        t.codec = "FFV1" if self.compression.get() == "FFV1" else "Y800"   ###opencv
        t.start()
        print(f"[Writer] Prewarmed writer for trial {trial_index}")
        return t

    def writer_thread(self):
        """Writer thread using OpenCV (pre-sized, no lazy init)."""
        t = threading.current_thread()
        fourcc = cv2.VideoWriter_fourcc(*t.codec)
        fps = self.fps.get()
        with self.frame_lock:
            width = self.frame_width
            height = self.frame_height
        # Directly use dimensions from the main class
        writer = cv2.VideoWriter(t.filename, fourcc, fps, (width, height), isColor=False)
        if not writer.isOpened():
            print(f"[Writer] ERROR: could not open {t.filename} with codec {t.codec}")
            return

        print(f"[Writer] Started {t.filename} ({t.codec}, {width}x{height})")
        while True:
            if getattr(t, "active", False):
                frame = None
                with self.queue_lock:
                    if self.frame_queue:
                        frame = self.frame_queue.popleft()
                if frame is not None:
                    writer.write(frame)
                else:
                    time.sleep(0.001)
            elif getattr(t, "stop_flag", False):
                break
            else:
                time.sleep(0.1)

        writer.release()
        print(f"[Writer] Finished writing {t.filename}")

    def stop_writer(self):
        t = self.current_thread_writer
        if t is not None :
            t.active = False
            t.stop_flag = True
            t.join()
            self.current_thread_writer = None
            # Save TTL timestamps
            filename_ttl = os.path.join(
                self.save_path.get(),
                f"{self.date_now.strftime('%Y%m%d_%Hh%M')}_trial{self.trial_index}_sync_ttl.csv"
            )
            with open(filename_ttl, "w", newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp_seconds"])
                for t in self.ttl_log:
                    writer.writerow([t])
            self.ttl_log = []

            # Save frame timestamps
            filename_frames = os.path.join(
                self.save_path.get(),
                f"{self.date_now.strftime('%Y%m%d_%Hh%M')}_trial{self.trial_index}_frame_timestamps.csv"
            )
            with open(filename_frames, "w", newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp_seconds"])
                for t in self.frames_times_log:
                    writer.writerow([t])
            self.frames_times_log = []

            print(f"TTL timestamps saved: {filename_ttl}")
            self.start_rec_time = None
            self.start_rec_time_hardware = None
            self.trial_index += 1
            print(f"Recording stopped for trial {self.trial_index}")

            self.next_thread_writer = self.prepare_next_writer(self.trial_index)

    
    def get_line_status(self, line_id):
        nodemap = self.cam.GetNodeMap()
        line_selector = PySpin.CEnumerationPtr(nodemap.GetNode("LineSelector"))
        line_selector_line = line_selector.GetEntryByName(f"Line{line_id}")
        line_selector.SetIntValue(line_selector_line.GetValue())
        line_status = PySpin.CBooleanPtr(nodemap.GetNode("LineStatus"))
        return line_status.GetValue()  # True = HIGH, False = LOW
    
    def stop_acquisition(self):
        self.acquiring = False

    def start_recording(self):
        if not self.acquiring or self.recording:
            return
        ### prepare first writer
        if self.trial_index == 0:
            self.next_thread_writer = self.prepare_next_writer(self.trial_index)
        self.recording = True
        self.check_save_path()
        print("Recording started")

    def stop_recording(self):
        self.recording = False

        if self.current_thread_writer and self.current_thread_writer.is_alive():
            self.stop_writer()


        print("Recording stopped")

    def on_close(self):
        self.stop_recording()
        self.stop_acquisition()
        
        # Wait for acquisition thread to fully exit
        if hasattr(self, 'thread') and self.thread.is_alive():
            self.thread.join(timeout=2)  # wait up to 2 sec
        
        if self.cam:
            try:
                self.cam.EndAcquisition()  # make sure acquisition ended
                self.cam.DeInit()
            except PySpin.SpinnakerException as e:
                print(f"Warning: camera deinit failed: {e}")
        
        try:
            self.system.ReleaseInstance()
        except PySpin.SpinnakerException as e:
            print(f"Warning: system release failed: {e}")

        self.root.destroy() 

if __name__ == "__main__":
    root = tk.Tk()
    app = FLIRApp(root)
    root.mainloop()



