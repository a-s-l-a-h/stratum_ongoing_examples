import threading
import time
import stratum
import stratumx
from stratumx import StratumApp
from stratumx.core import ctx, run_on_ui
from stratumx.ui.views import TextureView, ImageView
from stratumx.sensor import Camera2

try:
    import cv2
    import numpy as np
    OPENCV_OK = True
except ImportError:
    OPENCV_OK = False

stratum.set_log_enabled(False)


# Inherit from StratumApp!
class VisionApp(StratumApp):
    def __init__(self):
        super().__init__() # This binds the Android lifecycle to this class!

        self.camera = None
        self.tv_camera = None
        self.iv_overlay = None

        self.is_processing = False
        self.frame_event = threading.Event()

        self.bmp_in = None
        self.bmp_out = None
        self.bb_wrapper = None
        self.arr = None

    # This is now a real class method!
    def onCreate(self):
        stratumx.init()

        # Build UI
        raw_root = stratum.create_android_widget_FrameLayout(ctx.activity)
        raw_tv   = stratum.create_android_view_TextureView(ctx.activity)
        raw_iv   = stratum.create_android_widget_ImageView(ctx.activity)

        raw_root.addView(raw_tv)
        raw_root.addView(raw_iv)
        stratum.setContentView(ctx.activity, raw_root)

        # Wrap in StratumX
        self.tv_camera  = TextureView(raw_tv)
        self.iv_overlay = ImageView(raw_iv)

        # Initialize Camera
        self.camera = Camera2(texture_view=self.tv_camera, facing="back", target_res=(1280, 720))
        self.tv_camera.on_updated(self.on_frame_ready)

        # Start Worker
        if OPENCV_OK:
            threading.Thread(target=self.opencv_worker_loop, daemon=True).start()

    def on_frame_ready(self, tv):
        if not OPENCV_OK or self.is_processing:
            return

        self.bmp_in = tv.get_bitmap(self.bmp_in)
        if self.bmp_in:
            self.is_processing = True
            self.frame_event.set()

    def opencv_worker_loop(self):
        while True:
            self.frame_event.wait()
            self.frame_event.clear()

            try:
                w, h = self.bmp_in.getWidth(), self.bmp_in.getHeight()

                if self.bb_wrapper is None:
                    raw_buf = stratum.allocate_direct_buffer(w * h * 4)
                    self.bb_wrapper = stratum.java_nio_ByteBuffer._stratum_cast(raw_buf)
                    self.arr = np.array(self.bb_wrapper.duplicate(), copy=False).reshape((h, w, 4))
                    self.bmp_out = self.bmp_in.copy(self.bmp_in.getConfig(), True)

                self.bb_wrapper.rewind()
                self.bmp_in.copyPixelsToBuffer(self.bb_wrapper)

                # --- OpenCV Processing ---
                gray = cv2.cvtColor(self.arr, cv2.COLOR_RGBA2GRAY)
                edges = cv2.Canny(gray, 50, 150)
                cv2.cvtColor(edges, cv2.COLOR_GRAY2RGBA, dst=self.arr)

                self.bb_wrapper.rewind()
                self.bmp_out.copyPixelsFromBuffer(self.bb_wrapper)

                run_on_ui(lambda: self.iv_overlay.set_bitmap(self.bmp_out))

            except Exception as e:
                print(f"[OpenCV Worker Error] {e}")
            finally:
                self.is_processing = False

    # This is now a real class method!
    def onDestroy(self):
        if self.camera:
            self.camera.close()

    def onResume(self):
        if self.camera and self.tv_camera:
            st_raw = self.tv_camera.raw.getSurfaceTexture()
            if st_raw and (not hasattr(self.camera, 'camera_device') or self.camera.camera_device is None):
                self.camera._init_camera(st_raw,
                    self.tv_camera.raw.getWidth(),
                    self.tv_camera.raw.getHeight())


# Instantiate the app once.
# It will intercept all Android lifecycles automatically!
app = VisionApp()