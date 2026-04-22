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
        self.activity        = activity
        self.camera_device   = None
        self.capture_session = None
        self.builder         = None
        self.handler         = None
        self.sensor_orient   = 90   # default, updated in on_surface_available
        self.preview_width   = 1280
        self.preview_height  = 720

        # Ping-Pong
        self.running      = True
        self.frame_event  = threading.Event()
        self.worker_ready = True
        self.last_time    = time.time()

        self.processing_thread = threading.Thread(
            target=self._worker_loop, daemon=True)

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
            print("[INIT] OK")
        except Exception as e:
            traceback.print_exc()

    # ── Safe constructors ─────────────────────────────────────────────────────

    def _new(self, cls_name, *args):
        cls = getattr(stratum, cls_name)
        for i in range(10):
            m = getattr(cls, f"new_{i}", None)
            if m is None:
                continue
            try:
                r = m(*args)
                if r is not None:
                    return r
            except:
                pass
        return None

    # ── Transform — pure math, no Matrix JNI needed ───────────────────────────

    def _configure_transform(self, view_w, view_h):
        """
        Apply TextureView transform using android.graphics.Matrix via Stratum.

        For a portrait device (display rotation = 0) with a 90° sensor:
          - We need to rotate 90° and fix the aspect ratio.

        Java equivalent (simplified for portrait-only):
            matrix.setRotate(90, cx, cy)
            float scaleX = (float)viewH / previewH
            float scaleY = (float)viewW / previewW
            matrix.postScale(scaleX, scaleY, cx, cy)
            textureView.setTransform(matrix)
        """
        try:
            orient = self.sensor_orient   # 90 or 270 typically
            pw     = self.preview_width
            ph     = self.preview_height
            cx     = view_w / 2.0
            cy     = view_h / 2.0

            matrix = self._new("android_graphics_Matrix")
            if matrix is None:
                # Fallback: try directly
                matrix = stratum.android_graphics_Matrix.new_0()

            if orient == 90 or orient == 270:
                # Step 1: rotate
                matrix.setRotate(float(orient), cx, cy)

                # Step 2: fix aspect ratio stretch introduced by rotation
                # After rotating 90°, view_w maps to preview height axis
                # and view_h maps to preview width axis
                scale_x = float(view_h) / float(pw)
                scale_y = float(view_w) / float(ph)
                matrix.postScale(scale_x, scale_y, cx, cy)

            elif orient == 180:
                matrix.setRotate(180.0, cx, cy)
            # orient == 0: identity, no transform needed

            self.texture_view.setTransform(matrix)
            print(f"[TRANSFORM] OK orient={orient} "
                  f"view={view_w}x{view_h} preview={pw}x{ph}")

        except Exception as e:
            print(f"[TRANSFORM ERROR] {e}")
            traceback.print_exc()

    # ── Sensor orientation from CameraCharacteristics ─────────────────────────

    def _read_sensor_orientation(self, cam_mgr, camera_id="0"):
        try:
            characteristics = cam_mgr.getCameraCharacteristics(camera_id)
            # SENSOR_ORIENTATION static field
            key = stratum.android_hardware_camera2_CameraCharacteristics\
                      .sf_get_SENSOR_ORIENTATION()
            orient = characteristics.get(key)
            print(f"[CAMERA] sensor_orientation={orient}")
            return int(orient) if orient is not None else 90
        except Exception as e:
            print(f"[CAMERA] Could not read sensor orientation: {e}")
            return 90

    # ── Camera lifecycle ──────────────────────────────────────────────────────

    def on_surface_available(self, st, w, h):
        try:
            sys_svc = self.activity.getSystemService("camera")
            cam_mgr = stratum.android_hardware_camera2_CameraManager\
                          ._stratum_cast(sys_svc)

            # Read sensor orientation
            self.sensor_orient = self._read_sensor_orientation(cam_mgr)

            # Set buffer size on SurfaceTexture BEFORE opening camera
            # st arrives as raw StratumObject — cast it
            surface_texture = stratum.android_graphics_SurfaceTexture\
                                  ._stratum_cast(st)
            surface_texture.setDefaultBufferSize(
                self.preview_width, self.preview_height)
            print(f"[SURFACE] buffer size set to "
                  f"{self.preview_width}x{self.preview_height}")

            # Apply transform
            if w > 0 and h > 0:
                self._configure_transform(w, h)
            else:
                # w/h are 0 on first call sometimes; get from TextureView
                tw = self.texture_view.getWidth()
                th = self.texture_view.getHeight()
                if tw > 0 and th > 0:
                    self._configure_transform(tw, th)

            looper       = stratum.android_os_Looper.getMainLooper_static()
            self.handler = self._new("android_os_Handler", looper)

            cam_mgr.openCamera("0", {
                "onOpened":       self.on_camera_opened,
                "onDisconnected": self.on_camera_disconnected,
                "onError":        self.on_camera_error,
            }, self.handler)

        except Exception as e:
            traceback.print_exc()

    def on_surface_size_changed(self, st, w, h):
        if w > 0 and h > 0:
            self._configure_transform(w, h)

    def on_camera_opened(self, raw_device):
        try:
            self.camera_device = stratum\
                .android_hardware_camera2_CameraDevice\
                ._stratum_cast(raw_device)

            # Get SurfaceTexture and create Surface from it
            st_raw  = self.texture_view.getSurfaceTexture()
            st      = stratum.android_graphics_SurfaceTexture\
                          ._stratum_cast(st_raw)
            surface = self._new("android_view_Surface", st)

            self.builder = self.camera_device.createCaptureRequest(1)
            self.builder.addTarget(surface)

            lst = self._new("java_util_ArrayList")
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
            self.capture_session = stratum\
                .android_hardware_camera2_CameraCaptureSession\
                ._stratum_cast(raw_session)
            req = self.builder.build()
            self.capture_session.setRepeatingRequest(req, None, self.handler)
            print(">>> CAMERA PREVIEW LIVE <<<")
        except Exception as e:
            traceback.print_exc()

    def on_camera_disconnected(self, raw):
        pass

    def on_camera_error(self, raw, code):
        print(f"[CAMERA ERROR] code={code}")

    def on_surface_destroyed(self, st):
        self.shutdown()
        return True

    # ── Ping-Pong threading ───────────────────────────────────────────────────

    def on_surface_updated(self, st):
        if not OPENCV_OK:
            return
        if self.worker_ready:
            if hasattr(self, 'out_bmp'):
                self.image_view.setImageBitmap(self.out_bmp)
            self.worker_ready = False
            self.frame_event.set()

    def _worker_loop(self):
        print("[WORKER] Background OpenCV Thread Started")
        orb = cv2.ORB_create(nfeatures=500)

        while self.running:
            self.frame_event.wait()
            self.frame_event.clear()
            if not self.running:
                break

            try:
                if not hasattr(self, "bb_wrapper"):
                    first_frame = self.texture_view.getBitmap()
                    if first_frame is None:
                        self.worker_ready = True
                        continue

                    self.w = first_frame.getWidth()
                    self.h = first_frame.getHeight()
                    size   = self.w * self.h * 4

                    self.in_bmp  = first_frame
                    self.out_bmp = first_frame.copy(
                        first_frame.getConfig(), True)

                    raw_buf         = stratum.allocate_direct_buffer(size)
                    self.bb_wrapper = stratum.java_nio_ByteBuffer\
                                          ._stratum_cast(raw_buf)
                    self.mem_view   = self.bb_wrapper.duplicate()
                    self.arr        = np.array(self.mem_view, copy=False)\
                                        .reshape((self.h, self.w, 4))
                    self.gray_buf   = np.empty(
                        (self.h, self.w), dtype=np.uint8)
                    self.bgr_buf    = np.empty(
                        (self.h, self.w, 3), dtype=np.uint8)
                else:
                    if self.texture_view.getBitmap(self.in_bmp) is None:
                        self.worker_ready = True
                        continue

                self.bb_wrapper.rewind()
                self.in_bmp.copyPixelsToBuffer(self.bb_wrapper)

                cv2.cvtColor(self.arr, cv2.COLOR_RGBA2GRAY,
                             dst=self.gray_buf)
                keypoints = orb.detect(self.gray_buf, None)
                cv2.cvtColor(self.arr, cv2.COLOR_RGBA2BGR,
                             dst=self.bgr_buf)
                cv2.drawKeypoints(self.bgr_buf, keypoints, self.bgr_buf,
                                  color=(0, 255, 0), flags=0)

                t0  = time.time()
                fps = 1.0 / (t0 - self.last_time) if self.last_time else 0
                self.last_time = t0

                cv2.putText(
                    self.bgr_buf,
                    f"ORB: {len(keypoints)} | FPS: {fps:.1f}",
                    (40, 80), cv2.FONT_HERSHEY_SIMPLEX,
                    1.5, (0, 0, 255), 4)

                cv2.cvtColor(self.bgr_buf, cv2.COLOR_BGR2RGBA,
                             dst=self.arr)

                self.bb_wrapper.rewind()
                self.out_bmp.copyPixelsFromBuffer(self.bb_wrapper)

            except Exception as e:
                print(f"[WORKER ERROR] {e}")
                traceback.print_exc()
            finally:
                self.worker_ready = True

    def shutdown(self):
        self.running = False
        self.frame_event.set()
        if self.capture_session:
            try:
                self.capture_session.close()
            except:
                pass
        if self.camera_device:
            try:
                self.camera_device.close()
            except:
                pass


# ─── Lifecycle ────────────────────────────────────────────────────────────────
app = None

def onCreate():
    global app
    app = CameraApp(stratum.stratum_get_activity())

def onResume():
    global app
    if app and app.camera_device is None:
        st_raw = app.texture_view.getSurfaceTexture()
        if st_raw:
            app.on_surface_available(st_raw, 0, 0)

def onPause():  pass
def onStop():   pass

def onDestroy():
    global app
    if app:
        app.shutdown()
        app = None