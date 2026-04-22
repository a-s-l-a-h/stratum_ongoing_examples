import stratum
import sys
import traceback
import time
import threading

try:
    import cv2
    import numpy as np
    OPENCV_OK = True
    print("[OPENCV] cv2 + numpy SUCCESSFULLY LOADED")
except Exception as e:
    OPENCV_OK = False
    print(f"[OPENCV] FAIL: {e}")

class CameraApp:
    def __init__(self, activity):
        self.activity = activity
        self.camera_device = None
        self.capture_session = None
        self.builder = None
        self.handler = None

        # --- Multi-Threading Ping-Pong Architecture ---
        self.running = True
        self.frame_event = threading.Event()
        self.worker_ready = True  # Used for Ping-Pong sync
        self.last_time = time.time()

        self.processing_thread = threading.Thread(target=self._worker_loop, daemon=True)

        print("[INIT] Building UI Layers...")
        try:
            self.frame_layout = stratum.create_android_widget_FrameLayout(activity)
            self.texture_view = stratum.create_android_view_TextureView(activity)
            self.texture_view.setSurfaceTextureListener({
                "onSurfaceTextureAvailable":   self.on_surface_available,
                "onSurfaceTextureSizeChanged": self.on_surface_size_changed,
                "onSurfaceTextureDestroyed":   self.on_surface_destroyed,
                "onSurfaceTextureUpdated":     self.on_surface_updated,
            })

            self.image_view = stratum.create_android_widget_ImageView(activity)
            self.frame_layout.addView(self.texture_view)
            self.frame_layout.addView(self.image_view)

            stratum.setContentView(activity, self.frame_layout)
            self.processing_thread.start()
            print("[INIT] Dual-Layer UI & Background Thread OK")
        except Exception as e:
            traceback.print_exc()

    # ── Safe Constructors ─────────────────────────────────────────────────────
    def _create_handler(self, looper):
        cls = stratum.android_os_Handler
        for i in range(10):
            if hasattr(cls, f"new_{i}"):
                try: return getattr(cls, f"new_{i}")(looper)
                except: pass

    def _create_surface(self, st):
        cls = stratum.android_view_Surface
        for i in range(10):
            if hasattr(cls, f"new_{i}"):
                try: return getattr(cls, f"new_{i}")(st)
                except: pass

    def _create_array_list(self):
        cls = stratum.java_util_ArrayList
        for i in range(10):
            if hasattr(cls, f"new_{i}"):
                try:
                    res = getattr(cls, f"new_{i}")()
                    if res is not None: return res
                except: pass

    # ── Camera Setup ──────────────────────────────────────────────────────────
    def on_surface_available(self, st, w, h):
        try:
            sys_svc = self.activity.getSystemService("camera")
            cam_mgr = stratum.android_hardware_camera2_CameraManager._stratum_cast(sys_svc)
            looper = stratum.android_os_Looper.getMainLooper_static()
            self.handler = self._create_handler(looper)

            cam_mgr.openCamera("0", {
                "onOpened":       self.on_camera_opened,
                "onDisconnected": self.on_camera_disconnected,
                "onError":        self.on_camera_error,
            }, self.handler)
        except Exception as e:
            traceback.print_exc()

    def on_camera_opened(self, raw_device):
        try:
            self.camera_device = stratum.android_hardware_camera2_CameraDevice._stratum_cast(raw_device)
            st = self.texture_view.getSurfaceTexture()
            surface = self._create_surface(st)
            self.builder = self.camera_device.createCaptureRequest(1)
            self.builder.addTarget(surface)

            lst = self._create_array_list()
            lst.add(surface)
            lst_if = stratum.java_util_List._stratum_cast(lst)

            self.camera_device.createCaptureSession(lst_if, {
                "onConfigured":      self.on_session_configured,
                "onConfigureFailed": lambda r: print("[CB] sessionFailed"),
            }, self.handler)
        except Exception as e:
            traceback.print_exc()

    def on_session_configured(self, raw_session):
        try:
            self.capture_session = stratum.android_hardware_camera2_CameraCaptureSession._stratum_cast(raw_session)
            req = self.builder.build()
            self.capture_session.setRepeatingRequest(req, None, self.handler)
            print(">>> CAMERA PREVIEW LIVE <<<")
        except Exception as e:
            traceback.print_exc()

    def on_camera_disconnected(self, raw): pass
    def on_camera_error(self, raw, code): pass
    def on_surface_size_changed(self, st, w, h): pass

    def on_surface_destroyed(self, st):
        self.shutdown()
        return True

    # ── Ping-Pong Threading Architecture ──────────────────────────────────────

    def on_surface_updated(self, st):
        """ Runs on the UI Thread ~30-60 times a second """
        if not OPENCV_OK: return

        # Only trigger a new frame if the background thread has finished the last one!
        if self.worker_ready:
            # 1. Update the UI with the *completed* ORB frame
            if hasattr(self, 'out_bmp'):
                self.image_view.setImageBitmap(self.out_bmp)

            # 2. Tell the background thread to wake up and process the *current* frame
            self.worker_ready = False
            self.frame_event.set()

    def _worker_loop(self):
        """ Runs entirely in the background. Does not freeze the Android UI. """
        print("[WORKER] Background OpenCV Thread Started")

        # Initialize ORB Detector once (500 features is optimal for mobile CPU)
        orb = cv2.ORB_create(nfeatures=500)

        while self.running:
            # Sleep until the UI thread tells us a frame is ready
            self.frame_event.wait()
            self.frame_event.clear()

            if not self.running: break

            try:
                # 1. ONE-TIME MEMORY ALLOCATION
                if not hasattr(self, "bb_wrapper"):
                    first_frame = self.texture_view.getBitmap()
                    if first_frame is None:
                        self.worker_ready = True
                        continue

                    self.w, self.h = first_frame.getWidth(), first_frame.getHeight()
                    size = self.w * self.h * 4

                    self.in_bmp = first_frame
                    self.out_bmp = first_frame.copy(first_frame.getConfig(), True)

                    raw_buffer = stratum.allocate_direct_buffer(size)
                    self.bb_wrapper = stratum.java_nio_ByteBuffer._stratum_cast(raw_buffer)
                    self.mem_view = self.bb_wrapper.duplicate()

                    # NumPy zero-copy wrapper
                    self.arr = np.array(self.mem_view, copy=False).reshape((self.h, self.w, 4))

                    # Pre-allocate OpenCV buffers
                    self.gray_buf = np.empty((self.h, self.w), dtype=np.uint8)
                    self.bgr_buf = np.empty((self.h, self.w, 3), dtype=np.uint8)
                else:
                    # Pull frame from camera into our cached Bitmap
                    if self.texture_view.getBitmap(self.in_bmp) is None:
                        self.worker_ready = True
                        continue

                # 2. ZERO-COPY TO C++
                self.bb_wrapper.rewind()
                self.in_bmp.copyPixelsToBuffer(self.bb_wrapper)

                # 3. FAST ORB PROCESSING (100% In-Place)
                # RGBA -> Grayscale
                cv2.cvtColor(self.arr, cv2.COLOR_RGBA2GRAY, dst=self.gray_buf)

                # Detect Keypoints
                keypoints = orb.detect(self.gray_buf, None)

                # RGBA -> BGR (so we can draw green dots over the original image)
                cv2.cvtColor(self.arr, cv2.COLOR_RGBA2BGR, dst=self.bgr_buf)

                # Draw Keypoints In-Place
                cv2.drawKeypoints(self.bgr_buf, keypoints, self.bgr_buf, color=(0, 255, 0), flags=0)

                # Calculate FPS
                t0 = time.time()
                fps = 1.0 / (t0 - self.last_time) if self.last_time else 0
                self.last_time = t0

                cv2.putText(self.bgr_buf, f"ORB FEATURES: {len(keypoints)} | FPS: {fps:.1f}",
                            (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 4)

                # Convert back to RGBA directly into the Java DirectBuffer mapped memory
                cv2.cvtColor(self.bgr_buf, cv2.COLOR_BGR2RGBA, dst=self.arr)

                # 4. SEND TO ANDROID
                self.bb_wrapper.rewind()
                self.out_bmp.copyPixelsFromBuffer(self.bb_wrapper)

            except Exception as e:
                print(f"[WORKER ERROR] {e}")
                traceback.print_exc()
            finally:
                # 5. Tell the UI thread we are finished, so it updates the screen on its next tick!
                self.worker_ready = True

    def shutdown(self):
        self.running = False
        self.frame_event.set() # Unblock thread so it can safely exit
        if self.capture_session:
            try: self.capture_session.close()
            except: pass
        if self.camera_device:
            try: self.camera_device.close()
            except: pass


# ─── Android App Lifecycle ───────────────────────────────────────────────────
app = None

def onCreate():
    global app
    app = CameraApp(stratum.getActivity())

def onResume():
    global app
    if app and app.camera_device is None:
        st = app.texture_view.getSurfaceTexture()
        if st: app.on_surface_available(st, 0, 0)

def onPause(): pass
def onStop(): pass
def onDestroy():
    global app
    if app:
        app.shutdown()
        app = None