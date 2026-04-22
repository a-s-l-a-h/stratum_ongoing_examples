import sys
import traceback
import time
import threading
import stratum

try:
    import cv2
    import numpy as np
    OPENCV_OK = True
    print("[OPENCV] cv2 + numpy LOADED OK")
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
        self.cam_mgr         = None

        self.camera_ids      = []
        self.current_cam_idx = 0
        self.sensor_orient   = 90
        self.facing_front    = False

        # Zoom state — prioritises Camera2 SCALER_CROP_REGION (hardware zoom)
        self.zoom_level        = 1.0
        self.max_zoom          = 1.0
        self.active_array_size = None   # android.graphics.Rect

        # Resolution — never hardcoded, always queried from StreamConfigurationMap
        self.preview_width  = 1280
        self.preview_height = 720

        self.surface      = None
        self.running      = True
        self.is_switching = False
        self.frame_event  = threading.Event()
        self.worker_ready = True
        self.last_time    = time.time()

        self.processing_thread = threading.Thread(
            target=self._worker_loop, daemon=True)

        print("[INIT] Building UI ...")
        try:
            # ── Root: vertical linear layout ─────────────────────────────────
            self.root = stratum.create_android_widget_LinearLayout(activity)
            self.root.setOrientation(1)   # VERTICAL

            # ── Button row — placed at TOP above the camera feed ──────────────
            self.btn_row = stratum.create_android_widget_LinearLayout(activity)
            self.btn_row.setOrientation(0)   # HORIZONTAL
            self.btn_row.setGravity(17)      # CENTER_HORIZONTAL
            self.btn_row.setPadding(16, 24, 16, 24)

            self.btn_zoom_out = stratum.create_android_widget_Button(activity)
            self.btn_zoom_out.setText("  -  ")
            self.btn_zoom_out.setTextSize(22.0)
            self.btn_zoom_out.setOnClickListener(self._on_zoom_out) # FIX: Passed directly

            self.btn_switch = stratum.create_android_widget_Button(activity)
            self.btn_switch.setText("  Switch  ")
            self.btn_switch.setTextSize(18.0)
            self.btn_switch.setOnClickListener(self._on_switch) # FIX: Passed directly

            self.btn_zoom_in = stratum.create_android_widget_Button(activity)
            self.btn_zoom_in.setText("  +  ")
            self.btn_zoom_in.setTextSize(22.0)
            self.btn_zoom_in.setOnClickListener(self._on_zoom_in) # FIX: Passed directly

            self.btn_row.addView(self.btn_zoom_out)
            self.btn_row.addView(self.btn_switch)
            self.btn_row.addView(self.btn_zoom_in)

            # ── Camera area ───────────────────────────────────────────────────
            self.frame_layout = stratum.create_android_widget_FrameLayout(activity)

            self.texture_view = stratum.create_android_view_TextureView(activity)
            self.texture_view.setSurfaceTextureListener({
                "onSurfaceTextureAvailable":   self._on_surface_available,
                "onSurfaceTextureSizeChanged": self._on_surface_size_changed,
                "onSurfaceTextureDestroyed":   self._on_surface_destroyed,
                "onSurfaceTextureUpdated":     self._on_surface_updated,
            })

            self.image_view = stratum.create_android_widget_ImageView(activity)

            self.frame_layout.addView(self.texture_view)
            self.frame_layout.addView(self.image_view)

            # Layout params: button row wraps content, camera fills remaining space
            btn_lp = self._new("android_widget_LinearLayout_LayoutParams", -1, -2)
            cam_lp = self._new("android_widget_LinearLayout_LayoutParams", -1, 0, 1.0)

            if btn_lp is not None and cam_lp is not None:
                self.root.addView(self.btn_row, btn_lp)
                self.root.addView(self.frame_layout, cam_lp)
            else:
                self.root.addView(self.btn_row)
                self.root.addView(self.frame_layout)

            stratum.setContentView(activity, self.root)
            self.processing_thread.start()
            print("[INIT] OK")
        except Exception:
            traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _new(self, cls_name, *args):
        cls = getattr(stratum, cls_name)
        for i in range(12):
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

    def _clear_buffers(self):
        self.worker_ready = False
        for attr in ("in_bmp", "out_bmp", "bb_wrapper", "arr", "gray_buf", "bgr_buf"):
            if hasattr(self, attr):
                delattr(self, attr)
        self.w = self.h = 0

    def _get_display_rotation_degrees(self):
        try:
            display_raw = self.activity.getDisplay()
            if display_raw is None:
                return 0
            display = stratum.android_view_Display._stratum_cast(display_raw)
            return int(display.getRotation()) * 90
        except:
            return 0

    # ─────────────────────────────────────────────────────────────────────────
    # Hardware zoom via Camera2 SCALER_CROP_REGION
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_zoom(self):
        if self.capture_session is None or self.builder is None:
            return

        try:
            if self.active_array_size is not None:
                full_w = self.active_array_size.getWidth()
                full_h = self.active_array_size.getHeight()
                zoom   = max(1.0, min(float(self.zoom_level), float(self.max_zoom)))

                crop_w = int(full_w / zoom)
                crop_h = int(full_h / zoom)
                crop_x = (full_w - crop_w) // 2
                crop_y = (full_h - crop_h) // 2

                crop_rect = self._new("android_graphics_Rect", crop_x, crop_y, crop_x + crop_w, crop_y + crop_h)

                if crop_rect is not None:
                    CRCls    = stratum.android_hardware_camera2_CaptureRequest
                    crop_key = CRCls.sf_get_SCALER_CROP_REGION()
                    self.builder.set(crop_key, crop_rect)
                    print(f"[ZOOM] {zoom:.1f}x hardware crop=({crop_x},{crop_y}) {crop_w}x{crop_h}")
                else:
                    print("[ZOOM] Rect allocation failed. Using Software Zoom fallback.")
            else:
                print("[ZOOM] active_array_size is None. Using Software Zoom fallback.")
        except Exception:
            traceback.print_exc()

        # FIX: We ALWAYS start the RepeatingRequest, even if the hardware zoom fails!
        try:
            req = self.builder.build()
            self.capture_session.setRepeatingRequest(req, None, self.handler)
        except Exception as e:
            print(f"[CAMERA] Failed to start feed: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Button handlers
    # ─────────────────────────────────────────────────────────────────────────

    def _on_zoom_in(self, view):
        self.zoom_level = min(max(5.0, self.max_zoom), round(self.zoom_level + 0.5, 1))
        print(f"[ZOOM+] {self.zoom_level}x")
        self._apply_zoom()

    def _on_zoom_out(self, view):
        self.zoom_level = max(1.0, round(self.zoom_level - 0.5, 1))
        print(f"[ZOOM-] {self.zoom_level}x")
        self._apply_zoom()

    def _on_switch(self, view):
        print("[SWITCH] pressed")
        if len(self.camera_ids) < 2 or self.is_switching:
            return
        self.is_switching = True
        self.zoom_level   = 1.0
        self._clear_buffers()
        self._shutdown_camera()
        self.current_cam_idx = (self.current_cam_idx + 1) % len(self.camera_ids)
        self._open_camera()

    # ─────────────────────────────────────────────────────────────────────────
    # Transform — FLAWLESS MATRIX MATH FROM v1 (Fixes stretching)
    # ─────────────────────────────────────────────────────────────────────────

    def _configure_transform(self, view_w, view_h):
        if view_w <= 0 or view_h <= 0 or self.preview_width <= 0:
            return
        try:
            display_deg = self._get_display_rotation_degrees()
            matrix = self._new("android_graphics_Matrix")
            if matrix is None: matrix = stratum.android_graphics_Matrix.new_0()
            if matrix is None: return

            pw = float(self.preview_width)
            ph = float(self.preview_height)
            cx = view_w / 2.0
            cy = view_h / 2.0

            if display_deg == 0 or display_deg == 180:
                scale_x = view_w / ph
                scale_y = view_h / pw
                scale = max(scale_x, scale_y)

                matrix.setScale((ph / view_w) * scale, (pw / view_h) * scale, cx, cy)

                if display_deg == 180:
                    matrix.postRotate(180, cx, cy)

            elif display_deg == 90 or display_deg == 270:
                scale_x = view_w / pw
                scale_y = view_h / ph
                scale = max(scale_x, scale_y)

                matrix.setScale((pw / view_w) * scale, (ph / view_h) * scale, cx, cy)

                if display_deg == 90:
                    matrix.postRotate(270, cx, cy)
                elif display_deg == 270:
                    matrix.postRotate(90, cx, cy)

            self.texture_view.setTransform(matrix)
        except Exception as e:
            print(f"[TRANSFORM ERROR] {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Resolution picker — queries StreamConfigurationMap, up to 1080p max
    # ─────────────────────────────────────────────────────────────────────────

    def _choose_optimal_size(self, cam_id, view_w, view_h):
        try:
            chars      = self.cam_mgr.getCameraCharacteristics(cam_id)
            CharCls    = stratum.android_hardware_camera2_CameraCharacteristics
            stream_map = chars.get(CharCls.sf_get_SCALER_STREAM_CONFIGURATION_MAP())

            sizes_array = stream_map.getOutputSizes(34) # ImageFormat.PRIVATE
            if sizes_array is None:
                sizes_array = stream_map.getOutputSizes(256) # JPEG fallback

            if sizes_array is not None:
                best_w, best_h = 1280, 720
                min_diff = float('inf')
                target_area = 1920 * 1080 # Cap at 1080p

                screen_ratio = max(view_w, view_h) / max(1, min(view_w, view_h))

                for sz_obj in sizes_array:
                    if sz_obj is None: continue
                    sz = stratum.android_util_Size._stratum_cast(sz_obj)
                    w, h = sz.getWidth(), sz.getHeight()

                    if w * h <= target_area and w > h:
                        diff = abs((w / h) - screen_ratio)
                        if diff < min_diff:
                            min_diff = diff
                            best_w, best_h = w, h

                self.preview_width = best_w
                self.preview_height = best_h
                print(f"[RESOLUTION] {best_w}x{best_h} selected optimally.")
        except Exception as e:
            print(f"[RESOLUTION] Query failed: {e}. Fallback to 1280x720.")
            self.preview_width, self.preview_height = 1280, 720

    # ─────────────────────────────────────────────────────────────────────────
    # Camera characteristics
    # ─────────────────────────────────────────────────────────────────────────

    def _read_camera_info(self, cam_id):
        try:
            chars   = self.cam_mgr.getCameraCharacteristics(cam_id)
            CharCls = stratum.android_hardware_camera2_CameraCharacteristics

            orient = chars.get(CharCls.sf_get_SENSOR_ORIENTATION())
            facing = chars.get(CharCls.sf_get_LENS_FACING())
            mz     = chars.get(CharCls.sf_get_SCALER_AVAILABLE_MAX_DIGITAL_ZOOM())
            ar     = chars.get(CharCls.sf_get_SENSOR_INFO_ACTIVE_ARRAY_SIZE())

            self.sensor_orient = int(orient) if orient is not None else 90
            self.facing_front  = (int(facing) == 0) if facing is not None else False
            self.max_zoom      = float(mz) if mz is not None else 1.0

            if ar is not None:
                self.active_array_size = stratum.android_graphics_Rect._stratum_cast(ar)
            else:
                self.active_array_size = None

            print(f"[CAM INFO] id={cam_id} orient={self.sensor_orient} front={self.facing_front} max_zoom={self.max_zoom:.1f}x")
        except Exception:
            traceback.print_exc()

    def _filter_logical_cameras(self):
        try:
            raw_ids  = self.cam_mgr.getCameraIdList()
            CharCls  = stratum.android_hardware_camera2_CameraCharacteristics
            back_id, front_id = None, None

            for cid_obj in raw_ids:
                cid    = str(cid_obj)
                chars  = self.cam_mgr.getCameraCharacteristics(cid)
                facing = chars.get(CharCls.sf_get_LENS_FACING())
                if facing is None: continue

                f = int(facing)
                if f == 1 and back_id is None:
                    back_id = cid
                elif f == 0 and front_id is None:
                    front_id = cid

            self.camera_ids = []
            if back_id: self.camera_ids.append(back_id)
            if front_id: self.camera_ids.append(front_id)
            if not self.camera_ids: self.camera_ids = ["0"]

            print(f"[CAMERAS] Found logical cameras: {self.camera_ids}")
        except Exception:
            self.camera_ids = ["0"]

    # ─────────────────────────────────────────────────────────────────────────
    # Camera open / close
    # ─────────────────────────────────────────────────────────────────────────

    def _shutdown_camera(self):
        if self.capture_session:
            try: self.capture_session.close()
            except: pass
            self.capture_session = None
        if self.camera_device:
            try: self.camera_device.close()
            except: pass
            self.camera_device = None

    def _open_camera(self):
        try:
            cam_id = self.camera_ids[self.current_cam_idx]
            self._read_camera_info(cam_id)

            vw = self.texture_view.getWidth()
            vh = self.texture_view.getHeight()
            self._choose_optimal_size(cam_id, max(vw, 1), max(vh, 1))

            st_raw = self.texture_view.getSurfaceTexture()
            if st_raw:
                st = stratum.android_graphics_SurfaceTexture._stratum_cast(st_raw)
                st.setDefaultBufferSize(self.preview_width, self.preview_height)

            self._configure_transform(vw, vh)

            self.cam_mgr.openCamera(cam_id, {
                "onOpened":       self._on_camera_opened,
                "onDisconnected": self._on_camera_disconnected,
                "onError":        self._on_camera_error,
            }, self.handler)
        except Exception:
            traceback.print_exc()

    # ─────────────────────────────────────────────────────────────────────────
    # SurfaceTexture listener
    # ─────────────────────────────────────────────────────────────────────────

    def _on_surface_available(self, st, w, h):
        try:
            sys_svc = self.activity.getSystemService("camera")
            self.cam_mgr = stratum.android_hardware_camera2_CameraManager._stratum_cast(sys_svc)
            self._filter_logical_cameras()

            looper       = stratum.android_os_Looper.getMainLooper_static()
            self.handler = self._new("android_os_Handler", looper)

            self._open_camera()
        except Exception:
            traceback.print_exc()

    def _on_surface_size_changed(self, st, w, h):
        self.is_switching = True
        self._clear_buffers()
        if w > 0 and h > 0:
            self._configure_transform(w, h)
        self.is_switching = False
        self.worker_ready = True

    def _on_surface_destroyed(self, st):
        self.shutdown()
        return True

    def _on_surface_updated(self, st):
        if self.is_switching or not OPENCV_OK:
            return
        if self.worker_ready:
            if hasattr(self, "out_bmp"):
                self.image_view.setImageBitmap(self.out_bmp)
            self.worker_ready = False
            self.frame_event.set()

    # ─────────────────────────────────────────────────────────────────────────
    # Camera2 session
    # ─────────────────────────────────────────────────────────────────────────

    def _on_camera_opened(self, raw_device):
        try:
            self.camera_device = stratum.android_hardware_camera2_CameraDevice._stratum_cast(raw_device)

            st_raw = self.texture_view.getSurfaceTexture()
            st     = stratum.android_graphics_SurfaceTexture._stratum_cast(st_raw)
            self.surface = self._new("android_view_Surface", st)

            self.builder = self.camera_device.createCaptureRequest(1) # TEMPLATE_PREVIEW
            self.builder.addTarget(self.surface)

            lst    = self._new("java_util_ArrayList")
            lst.add(self.surface)
            lst_if = stratum.java_util_List._stratum_cast(lst)

            self.camera_device.createCaptureSession(lst_if, {
                "onConfigured":      self._on_session_configured,
                "onConfigureFailed": self._on_session_failed,
            }, self.handler)
        except Exception:
            traceback.print_exc()

    def _on_session_configured(self, raw_session):
        try:
            self.capture_session = stratum.android_hardware_camera2_CameraCaptureSession._stratum_cast(raw_session)

            # Apply zoom & start RepeatingRequest
            self._apply_zoom()

            cam_id = self.camera_ids[self.current_cam_idx]
            print(f">>> CAMERA {cam_id} LIVE | {self.preview_width}x{self.preview_height} | max_zoom={self.max_zoom:.1f}x <<<")

            self.is_switching = False
            self.worker_ready = True
        except Exception:
            traceback.print_exc()

    def _on_session_failed(self, raw_session):
        print("[SESSION] configure failed")
        self.is_switching = False

    def _on_camera_disconnected(self, raw): print("[CAMERA] disconnected")
    def _on_camera_error(self, raw, code): print(f"[CAMERA ERROR] code={code}"); self.is_switching = False

    # ─────────────────────────────────────────────────────────────────────────
    # OpenCV worker
    # ─────────────────────────────────────────────────────────────────────────

    def _worker_loop(self):
        print("[WORKER] thread started")
        orb = cv2.ORB_create(nfeatures=400)

        while self.running:
            self.frame_event.wait()
            self.frame_event.clear()

            if not self.running:
                break
            if self.is_switching:
                self.worker_ready = True
                continue

            try:
                if not hasattr(self, "in_bmp"):
                    frame = self.texture_view.getBitmap()
                    if frame is None:
                        self.worker_ready = True
                        continue

                    w, h   = frame.getWidth(), frame.getHeight()
                    self.w = w
                    self.h = h
                    size   = w * h * 4  # ARGB_8888

                    self.in_bmp  = frame
                    self.out_bmp = frame.copy(frame.getConfig(), True)

                    raw_buf         = stratum.allocate_direct_buffer(size)
                    self.bb_wrapper = stratum.java_nio_ByteBuffer._stratum_cast(raw_buf)
                    self.arr        = np.array(self.bb_wrapper.duplicate(), copy=False).reshape((h, w, 4))
                    self.gray_buf   = np.empty((h, w),    dtype=np.uint8)
                    self.bgr_buf    = np.empty((h, w, 3), dtype=np.uint8)
                else:
                    try:
                        if self.texture_view.getBitmap(self.in_bmp) is None:
                            self.worker_ready = True
                            continue
                    except Exception:
                        self._clear_buffers()
                        self.worker_ready = True
                        continue

                # Pull pixels into numpy
                self.bb_wrapper.rewind()
                self.in_bmp.copyPixelsToBuffer(self.bb_wrapper)

                # Mirror front camera
                if self.facing_front:
                    cv2.flip(self.arr, 1, dst=self.arr)

                # FALLBACK: If hardware zoom failed (Rect allocation issue), perform software zoom
                if self.active_array_size is None and self.zoom_level > 1.0:
                    h_sz, w_sz = self.arr.shape[:2]
                    crop_h, crop_w = int(h_sz / self.zoom_level), int(w_sz / self.zoom_level)
                    y, x = (h_sz - crop_h) // 2, (w_sz - crop_w) // 2
                    cropped = self.arr[y:y+crop_h, x:x+crop_w]
                    self.arr[:] = cv2.resize(cropped, (w_sz, h_sz))

                # ORB detection
                cv2.cvtColor(self.arr, cv2.COLOR_RGBA2GRAY, dst=self.gray_buf)
                keypoints = orb.detect(self.gray_buf, None)
                cv2.cvtColor(self.arr, cv2.COLOR_RGBA2BGR,  dst=self.bgr_buf)

                cv2.drawKeypoints(self.bgr_buf, keypoints, self.bgr_buf, color=(0, 255, 0), flags=0)

                t0  = time.time()
                fps = 1.0 / max(t0 - self.last_time, 1e-6)
                self.last_time = t0

                cv2.putText(
                    self.bgr_buf,
                    f"ORB:{len(keypoints)}  FPS:{fps:.1f}  Zoom:{self.zoom_level:.1f}x",
                    (20, 56), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 255), 3
                )

                # Write back
                cv2.cvtColor(self.bgr_buf, cv2.COLOR_BGR2RGBA, dst=self.arr)
                self.bb_wrapper.rewind()
                self.out_bmp.copyPixelsFromBuffer(self.bb_wrapper)

            except Exception as e:
                print(f"[WORKER ERROR] {e}")
                self._clear_buffers()
            finally:
                self.worker_ready = True

    def shutdown(self):
        self.running = False
        self.frame_event.set()
        self._shutdown_camera()

# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────────────────────
app = None

def onCreate():
    global app
    app = CameraApp(stratum.stratum_get_activity())

def onResume():
    global app
    if app and app.camera_device is None:
        st_raw = app.texture_view.getSurfaceTexture()
        if st_raw:
            vw = app.texture_view.getWidth()
            vh = app.texture_view.getHeight()
            if app.cam_mgr is None:
                app._on_surface_available(st_raw, vw, vh)
            else:
                app._open_camera()

def onPause(): pass
def onStop(): pass
def onDestroy():
    global app
    if app:
        app.shutdown()
        app = None