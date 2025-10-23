import PySpin
import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import os
import cv2
import numpy as np
import imageio
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


        self.writer = None

        # State Variables
        self.acquiring = False
        self.recording = False
        self.roi_defined = False
        self.roi = None  # (x, y, w, h)
        self.rotation = tk.IntVar(value=270)  # 0, 90, 180, 270
        # self.save_path = tk.StringVar(value=os.getcwd())
        self.save_path = tk.StringVar(value=default_path)
        self.foldername = tk.StringVar(value=default_foldername)
        
        self.mode = tk.StringVar(value="Continuous")
        self.fps = tk.DoubleVar(value=30.0)
        self.brightness = tk.DoubleVar(value=1.0)
        self.compression = tk.StringVar(value="FFV1")
        self.exposure_time = tk.DoubleVar(value=5000.0)  # microseconds (example default 5 ms)
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


        tk.Label(root, text="Exposure time (µs):").pack()
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
        exposure_time = min(self.exposure_time.get(), max_exposure_us)
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

        while self.acquiring:
            current_preview_enabled = self.preview_enabled.get()
            image = self.cam.GetNextImage()
            frame_timestamp = image.GetTimeStamp()  # uint64, in microseconds
            if image.IsIncomplete():
                print('frame drop !')
                width = self.cam.Width.GetValue()
                height = self.cam.Height.GetValue()
                frame_rec = np.zeros((height, width), dtype=np.uint8)
            else : 
                frame_rec = image.GetNDArray()  # convert PySpin image to NumPy array   
            if self.roi_defined:
                x, y, w, h = self.roi
                frame_rec = frame_rec[y:y+h, x:x+w]

            image.Release()
            if self.last_preview_enabled and not current_preview_enabled:
                cv2.destroyWindow("FLIR Preview")
            if current_preview_enabled:
                rot_angle = self.rotation.get()
                frame_disp = np.copy(frame_rec)
                if rot_angle == 90:
                        frame_disp = cv2.rotate(frame_disp, cv2.ROTATE_90_COUNTERCLOCKWISE)
                elif rot_angle == 180:
                    frame_disp = cv2.rotate(frame_disp, cv2.ROTATE_180)
                elif rot_angle == 270:
                    frame_disp = cv2.rotate(frame_disp, cv2.ROTATE_90_CLOCKWISE)

                frame_disp = np.ascontiguousarray(frame_disp)
                frame_disp = frame_disp.astype(np.uint8)

                # frame_disp = cv2.cvtColor(frame, cv2.COLOR_BAYER_RG2BGR)  # convert if needed

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
                if self.recording and self.writer is None:
                    self.start_writer()
                elif not self.recording and self.writer:
                    self.stop_writer()

      # Append frame if recording
            if self.writer:
                if self.start_rec_time_hardware is None:
                    self.start_rec_time_hardware = frame_timestamp

                sync_line_state = self.get_line_status(sync_line_id)
                if sync_line_state and not last_sync_line_state:
                    timestamp = time.time() - self.start_rec_time
                    self.ttl_log.append(timestamp)
                timestamp_sec = (frame_timestamp - self.start_rec_time_hardware) / 1e6
                self.frames_times_log.append(timestamp_sec)
                self.writer.append_data(frame_rec)
                if current_preview_enabled:
                    cv2.putText(frame_disp, f"Rec. trial{self.trial_index}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                                1.0, (0, 0, 255), 2, cv2.LINE_AA)
            if current_preview_enabled:
                cv2.imshow("FLIR Preview", frame_disp)
                key = cv2.waitKey(1) & 0xFF
                if key == ord('c') and not self.recording:
                    if roi_start and roi_end:
                        x1, y1 = roi_start
                        x2, y2 = roi_end
                        w = (abs(x2-x1)//16)*16
                        h = (abs(y2-y1)//16)*16
                        x_raw, y_raw, w_raw, h_raw = map_roi_to_raw(
                            min(x1, x2), min(y1, y2), w, h, frame_rec.shape, self.rotation.get()
                        )

                        # Save the mapped coordinates for cropping raw frame
                        self.roi = (x_raw, y_raw, w_raw, h_raw)
                        self.roi_defined = True
                        print(f"ROI defined: {self.roi}")
            elif key == ord('f') and not self.recording:
                self.roi_defined = False
                self.roi = None
                print("Reset to full frame")
            # elif key == 27:
            #     self.acquiring = False
            last_sync_line_state = sync_line_state
            self.last_preview_enabled = current_preview_enabled
        # Cleanup
        self.stop_writer()
        self.cam.EndAcquisition()
        cv2.destroyAllWindows()


    def start_writer(self):
        self.date_now = datetime.datetime.now()
        self.start_rec_time = time.time()
        self.ttl_log = []  #make sure log is empty log
        self.frames_times_log = [] #make sure log is empty log

        ext = 'mkv' if self.compression.get() == "FFV1" else "avi"

        if not os.path.exists(self.save_path.get()):
            os.makedirs(self.save_path.get())
        filename = os.path.join(self.save_path.get(),
                                f"{self.date_now.strftime('%Y%m%d_%Hh%M')}_trial{self.trial_index}.{ext}")
        codec = "ffv1" if self.compression.get() == "FFV1" else "rawvideo"
        self.writer = imageio.get_writer(filename, fps=self.fps.get(), codec=codec)
        print(f"Recording started: {filename}")

    def stop_writer(self):
        if self.writer:
            filename = os.path.join(self.save_path.get(), f"{self.date_now.strftime('%Y%m%d_%Hh%M')}_trial{self.trial_index}_sync_ttl.csv")
            with open(filename, "w", newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp_seconds"])
                for t in self.ttl_log:
                    writer.writerow([t])
            self.ttl_log = []  #reset log

            filename = os.path.join(self.save_path.get(), f"{self.date_now.strftime('%Y%m%d_%Hh%M')}_trial{self.trial_index}_frame_timestamps.csv")
            with open(filename, "w", newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp_seconds"])
                for t in self.frames_times_log:
                    writer.writerow([t])
            self.frames_times_log = []  #reset log

            print(f"TTL timestamps saved: {filename}")
            self.start_rec_time = None
            self.start_rec_time_hardware =None
            self.writer.close()
            self.writer = None
            self.trial_index += 1
            print(f"Recording stopped for trial {self.trial_index}")


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
        self.recording = True
        self.check_save_path()
        print("Recording started")

    def stop_recording(self):
        self.recording = False

        if self.writer:
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

def map_roi_to_raw(x, y, w, h, raw_shape, rot_angle):
    """Map ROI from rotated preview to raw frame coordinates."""
    H, W = raw_shape[:2]

    if rot_angle == 90:
        x_raw = y
        y_raw = W - (x + w)
        w_raw = h
        h_raw = w
    elif rot_angle == 180:
        x_raw = W - (x + w)
        y_raw = H - (y + h)
        w_raw = w
        h_raw = h
    elif rot_angle == 270:
        x_raw = H - (y + h)
        y_raw = x
        w_raw = h
        h_raw = w
    else:  # 0 degrees
        x_raw, y_raw, w_raw, h_raw = x, y, w, h

    # Make sure coordinates are within bounds
    x_raw = max(0, x_raw)
    y_raw = max(0, y_raw)
    w_raw = min(W - x_raw, w_raw)
    h_raw = min(H - y_raw, h_raw)
    return x_raw, y_raw, w_raw, h_raw


if __name__ == "__main__":
    root = tk.Tk()
    app = FLIRApp(root)
    root.mainloop()
