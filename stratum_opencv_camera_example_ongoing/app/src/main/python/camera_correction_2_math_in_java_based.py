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
        self.sensor_orient   = 90
        self.preview_width   = 1280
        self.preview_height  = 720

        self.running      = True
        self.frame_event  = threading.Event()
        self.worker_ready = True
        self.last_time    = time.time()

        self.processing_thread = threading.Thread(
            target=self._worker_loop, daemon=True)

        print("[INIT] Building UI...")
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

    # ── Safe constructor helper ───────────────────────────────────────────────

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

    # ── Display rotation ──────────────────────────────────────────────────────

    def _get_display_rotation_degrees(self):
        """
        Gets display rotation using activity.getDisplay().getRotation()
        which is the modern API (not deprecated unlike WindowManager).

        Returns 0, 90, 180, or 270.
        """
        try:
            # activity.getDisplay() → Display object directly
            # No WindowManager needed — Activity has getDisplay() since API 30
            display = self.activity.getDisplay()
            if display is None:
                print("[ROTATION] getDisplay() returned null, using 0")
                return 0

            display_obj = stratum.android_view_Display._stratum_cast(display)
            # getRotation() returns Surface.ROTATION_0/90/180/270 = 0/1/2/3
            rot_const = display_obj.getRotation()
            degrees   = int(rot_const) * 90
            print(f"[ROTATION] display rotation = {degrees}°")
            return degrees
        except Exception as e:
            print(f"[ROTATION] getDisplay failed: {e}, falling back to 0")
            return 0

    # ── The correct transform using Java Matrix properly ──────────────────────

    def _configure_transform(self, view_w, view_h):
        """
        Correct Camera2 TextureView transform — handles ALL rotations.

        Java reference (from Google Camera2Basic sample):

            int displayRotation = activity.getWindowManager()
                                      .getDefaultDisplay().getRotation();
            int sensorOrientation = characteristics.get(SENSOR_ORIENTATION);

            boolean swappedDimensions = false;
            switch (displayRotation) {
                case Surface.ROTATION_0:
                case Surface.ROTATION_180:
                    if (sensorOrientation == 90 || sensorOrientation == 270)
                        swappedDimensions = true;
                    break;
                case Surface.ROTATION_90:
                case Surface.ROTATION_270:
                    if (sensorOrientation == 0 || sensorOrientation == 180)
                        swappedDimensions = true;
                    break;
            }

            // Then for the Matrix:
            int rotation = activity.getWindowManager()
                               .getDefaultDisplay().getRotation();
            Matrix matrix = new Matrix();
            RectF viewRect   = new RectF(0, 0, viewWidth, viewHeight);
            RectF bufferRect = new RectF(0, 0, previewH, previewW);
            float cx = viewRect.centerX();
            float cy = viewRect.centerY();
            if (ROTATION_90 == rotation || ROTATION_270 == rotation) {
                bufferRect.offset(cx - bufferRect.centerX(),
                                  cy - bufferRect.centerY());
                matrix.setRectToRect(viewRect, bufferRect, Matrix.ScaleToFit.FILL);
                float scale = Math.max(
                    (float) viewHeight / previewH,
                    (float) viewWidth  / previewW);
                matrix.postScale(scale, scale, cx, cy);
                matrix.postRotate(90 * (rotation - 2), cx, cy);
            } else if (Surface.ROTATION_180 == rotation) {
                matrix.postRotate(180, cx, cy);
            }
            textureView.setTransform(matrix);

        We replicate this exactly using Stratum Matrix bindings.
        """
        try:
            display_deg = self._get_display_rotation_degrees()
            # Surface.ROTATION_* constants: 0=0°,1=90°,2=180°,3=270°
            rotation    = display_deg // 90

            pw = self.preview_width   # camera buffer width
            ph = self.preview_height  # camera buffer height

            cx = view_w / 2.0
            cy = view_h / 2.0

            matrix = self._new("android_graphics_Matrix")
            if matrix is None:
                print("[TRANSFORM] Matrix creation failed — trying new_0 directly")
                matrix = stratum.android_graphics_Matrix.new_0()

            if rotation == 1 or rotation == 3:
                # ROTATION_90 or ROTATION_270
                # bufferRect has SWAPPED dimensions (ph x pw)
                # centered on the view center.
                # matrix.setRectToRect equivalent:
                #   maps viewRect(0,0,vw,vh) → bufferRect(cx-ph/2, cy-pw/2, cx+ph/2, cy+pw/2)
                buf_left   = cx - ph / 2.0
                buf_top    = cy - pw / 2.0
                buf_right  = cx + ph / 2.0
                buf_bottom = cy + pw / 2.0

                # setRectToRect(viewRect, bufferRect, FILL) computes:
                #   scaleX = bufferRect.width()  / viewRect.width()  = ph / vw
                #   scaleY = bufferRect.height() / viewRect.height() = pw / vh
                #   translateX = buf_left - 0 * scaleX = buf_left
                #   translateY = buf_top  - 0 * scaleY = buf_top
                # In Matrix terms: scale then translate
                scale_x = float(ph) / float(view_w)
                scale_y = float(pw) / float(view_h)

                # Use matrix.setScale + postTranslate to replicate setRectToRect
                matrix.setScale(scale_x, scale_y)
                matrix.postTranslate(buf_left, buf_top)

                # postScale(scale, scale, cx, cy) — uniform fill scale
                scale = max(float(view_h) / float(ph),
                            float(view_w) / float(pw))
                matrix.postScale(scale, scale, cx, cy)

                # postRotate(90 * (rotation - 2), cx, cy)
                # rotation=1 → 90*(1-2) = -90°
                # rotation=3 → 90*(3-2) = +90°
                post_rot = 90.0 * (rotation - 2)
                matrix.postRotate(post_rot, cx, cy)

            elif rotation == 2:
                # ROTATION_180 — just flip 180°
                matrix.postRotate(180.0, cx, cy)

            # rotation == 0: ROTATION_0 — identity, no transform

            self.texture_view.setTransform(matrix)
            print(f"[TRANSFORM] OK display={display_deg}° "
                  f"sensor={self.sensor_orient}° "
                  f"view={view_w}x{view_h} "
                  f"preview={pw}x{ph} "
                  f"post_rot={90*(rotation-2) if rotation in (1,3) else 0}°")

        except Exception as e:
            print(f"[TRANSFORM ERROR] {e}")
            traceback.print_exc()

    # ── Sensor orientation ────────────────────────────────────────────────────

    def _read_sensor_orientation(self, cam_mgr, camera_id="0"):
        try:
            characteristics = cam_mgr.getCameraCharacteristics(camera_id)
            key    = stratum.android_hardware_camera2_CameraCharacteristics\
                         .sf_get_SENSOR_ORIENTATION()
            orient = characteristics.get(key)
            print(f"[CAMERA] sensor_orientation = {orient}°")
            return int(orient) if orient is not None else 90
        except Exception as e:
            print(f"[CAMERA] sensor_orientation read failed: {e}")
            return 90

    # ── Camera lifecycle ──────────────────────────────────────────────────────

    def on_surface_available(self, st, w, h):
        try:
            sys_svc = self.activity.getSystemService("camera")
            cam_mgr = stratum.android_hardware_camera2_CameraManager\
                          ._stratum_cast(sys_svc)

            self.sensor_orient = self._read_sensor_orientation(cam_mgr)

            # Cast SurfaceTexture and set buffer size
            surface_texture = stratum.android_graphics_SurfaceTexture\
                                  ._stratum_cast(st)
            surface_texture.setDefaultBufferSize(
                self.preview_width, self.preview_height)
            print(f"[SURFACE] buffer={self.preview_width}x{self.preview_height}")

            # Use actual TextureView dimensions if w/h are 0
            vw = w if w > 0 else self.texture_view.getWidth()
            vh = h if h > 0 else self.texture_view.getHeight()
            if vw > 0 and vh > 0:
                self._configure_transform(vw, vh)

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
        # Called on every rotation — critical to re-apply transform
        if w > 0 and h > 0:
            self._configure_transform(w, h)

    def on_camera_opened(self, raw_device):
        try:
            self.camera_device = stratum\
                .android_hardware_camera2_CameraDevice\
                ._stratum_cast(raw_device)

            st_raw  = self.texture_view.getSurfaceTexture()
            st      = stratum.android_graphics_SurfaceTexture\
                          ._stratum_cast(st_raw)
            surface = self._new("android_view_Surface", st)

            self.builder = self.camera_device.createCaptureRequest(1)
            self.builder.addTarget(surface)

            lst    = self._new("java_util_ArrayList")
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

    def on_camera_disconnected(self, raw): pass
    def on_camera_error(self, raw, code):
        print(f"[CAMERA ERROR] code={code}")

    def on_surface_destroyed(self, st):
        self.shutdown()
        return True

    # ── Ping-Pong ─────────────────────────────────────────────────────────────

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

                cv2.cvtColor(self.arr, cv2.COLOR_RGBA2GRAY, dst=self.gray_buf)
                keypoints = orb.detect(self.gray_buf, None)
                cv2.cvtColor(self.arr, cv2.COLOR_RGBA2BGR,  dst=self.bgr_buf)
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

                cv2.cvtColor(self.bgr_buf, cv2.COLOR_BGR2RGBA, dst=self.arr)

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
            try: self.capture_session.close()
            except: pass
        if self.camera_device:
            try: self.camera_device.close()
            except: pass


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