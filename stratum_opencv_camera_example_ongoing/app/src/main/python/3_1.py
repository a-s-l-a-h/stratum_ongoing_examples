#v1
import sys
import traceback
import time
import threading
import stratum

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
        self.cam_mgr         = None

        self.camera_ids      = []
        self.current_cam_idx = 0
        self.sensor_orient   = 90
        self.facing_front    = False

        # Zoom state (For future hardware lens-shifting)
        self.zoom_level      = 1.0

        # No longer hardcoded! These will be overwritten dynamically.
        self.preview_width   = 1280
        self.preview_height  = 720

        self.running      = True
        self.is_switching = False
        self.frame_event  = threading.Event()
        self.worker_ready = True
        self.last_time    = time.time()

        self.processing_thread = threading.Thread(target=self._worker_loop, daemon=True)

        print("[INIT] Building UI...")
        try:
            self.root = stratum.create_android_widget_LinearLayout(activity)
            self.root.setOrientation(1) # VERTICAL

            # Top Button Row for Zoom and Switch
            self.btn_row = stratum.create_android_widget_LinearLayout(activity)
            self.btn_row.setOrientation(0) # HORIZONTAL
            self.btn_row.setGravity(17)    # CENTER
            self.btn_row.setPadding(0, 30, 0, 30)

            self.btn_zoom_out = stratum.create_android_widget_Button(activity)
            self.btn_zoom_out.setText(" - ")
            self.btn_zoom_out.setTextSize(20.0)
            self.btn_zoom_out.setOnClickListener(self.on_zoom_out_clicked)

            self.btn_switch = stratum.create_android_widget_Button(activity)
            self.btn_switch.setText(" Switch ")
            self.btn_switch.setTextSize(18.0)
            self.btn_switch.setOnClickListener(self.on_switch_camera_clicked)

            self.btn_zoom_in = stratum.create_android_widget_Button(activity)
            self.btn_zoom_in.setText(" + ")
            self.btn_zoom_in.setTextSize(20.0)
            self.btn_zoom_in.setOnClickListener(self.on_zoom_in_clicked)

            self.btn_row.addView(self.btn_zoom_out)
            self.btn_row.addView(self.btn_switch)
            self.btn_row.addView(self.btn_zoom_in)

            # Camera View Area
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

            self.root.addView(self.btn_row)
            self.root.addView(self.frame_layout)

            stratum.setContentView(activity, self.root)
            self.processing_thread.start()
            print("[INIT] OK")
        except Exception as e:
            traceback.print_exc()

    def _new(self, cls_name, *args):
        cls = getattr(stratum, cls_name)
        for i in range(10):
            m = getattr(cls, f"new_{i}", None)
            if m is None: continue
            try:
                r = m(*args)
                if r is not None: return r
            except: pass
        return None

    def _clear_buffers(self):
        self.worker_ready = False
        if hasattr(self, 'in_bmp'): del self.in_bmp
        if hasattr(self, 'out_bmp'): del self.out_bmp
        if hasattr(self, 'bb_wrapper'): del self.bb_wrapper
        self.w, self.h = 0, 0

    def _get_display_rotation_degrees(self):
        try:
            display = self.activity.getDisplay()
            if display is None: return 0
            display_obj = stratum.android_view_Display._stratum_cast(display)
            return int(display_obj.getRotation()) * 90
        except:
            return 0

    # ── FLAWLESS MATRIX MATH (Fixes the Landscape / Portrait Squashing) ───────
    def _configure_transform(self, view_w, view_h):
        try:
            display_deg = self._get_display_rotation_degrees()
            matrix = self._new("android_graphics_Matrix")
            if matrix is None: matrix = stratum.android_graphics_Matrix.new_0()

            pw = float(self.preview_width)
            ph = float(self.preview_height)
            cx = view_w / 2.0
            cy = view_h / 2.0

            if display_deg == 0 or display_deg == 180:
                # PORTRAIT: TextureView naturally squashes 16:9 into 9:16.
                # We calculate the scale required to "Center Crop" the natural aspect ratio.
                scale_x = view_w / ph
                scale_y = view_h / pw
                scale = max(scale_x, scale_y)

                matrix.setScale((ph / view_w) * scale, (pw / view_h) * scale, cx, cy)

                if display_deg == 180:
                    matrix.postRotate(180, cx, cy)

            elif display_deg == 90 or display_deg == 270:
                # LANDSCAPE
                scale_x = view_w / pw
                scale_y = view_h / ph
                scale = max(scale_x, scale_y)

                matrix.setScale((pw / view_w) * scale, (ph / view_h) * scale, cx, cy)

                # If rotated sideways, physically counter-rotate the hardware buffer so it stands upright
                if display_deg == 90:
                    matrix.postRotate(270, cx, cy)
                elif display_deg == 270:
                    matrix.postRotate(90, cx, cy)

            self.texture_view.setTransform(matrix)
        except Exception as e:
            print(f"[TRANSFORM ERROR] {e}")

    # ── DYNAMIC RESOLUTION PICKER (Removes Hardcoding!) ───────────────────────
    def _choose_optimal_size(self, cam_id, view_w, view_h):
        """Finds the absolute best resolution up to 1080p for the current screen ratio"""
        try:
            chars = self.cam_mgr.getCameraCharacteristics(cam_id)
            CharCls = stratum.android_hardware_camera2_CameraCharacteristics
            stream_map = chars.get(CharCls.sf_get_SCALER_STREAM_CONFIGURATION_MAP())

            # ImageFormat.PRIVATE = 34 (Used for SurfaceTexture)
            sizes_array = stream_map.getOutputSizes(34)
            if sizes_array is None:
                sizes_array = stream_map.getOutputSizes(256) # Fallback to JPEG sizes

            if sizes_array is not None:
                best_w, best_h = 1280, 720
                min_diff = float('inf')
                target_area = 1920 * 1080 # Cap at 1080p so OpenCV doesn't lag

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
                print(f"[CAMERA] DYNAMIC RESOLUTION CHOSEN: {best_w}x{best_h}")
                return
        except Exception as e:
            print(f"[CAMERA] Dynamic size failed, fallback to 1280x720: {e}")

    # ── CAMERA FILTERING (Finds Logical Back & Front Cameras) ─────────────────
    def _filter_logical_cameras(self):
        try:
            raw_ids = self.cam_mgr.getCameraIdList()
            back_id = None
            front_id = None

            CharCls = stratum.android_hardware_camera2_CameraCharacteristics
            for cid_obj in raw_ids:
                cid = str(cid_obj)
                chars = self.cam_mgr.getCameraCharacteristics(cid)
                facing = chars.get(CharCls.sf_get_LENS_FACING())

                if facing is not None:
                    # LENS_FACING_BACK = 1, LENS_FACING_FRONT = 0
                    if int(facing) == 1 and back_id is None:
                        back_id = cid
                    elif int(facing) == 0 and front_id is None:
                        front_id = cid

            self.camera_ids = []
            if back_id: self.camera_ids.append(back_id)
            if front_id: self.camera_ids.append(front_id)
            if not self.camera_ids: self.camera_ids = ["0"]

            print(f"[CAMERA] Filtered Logical Cameras: {self.camera_ids}")
        except Exception as e:
            print(f"[CAMERA] Filter failed: {e}")
            self.camera_ids = ["0"]

    def _read_camera_info(self, cam_id):
        try:
            chars = self.cam_mgr.getCameraCharacteristics(cam_id)
            CharCls = stratum.android_hardware_camera2_CameraCharacteristics
            orient = chars.get(CharCls.sf_get_SENSOR_ORIENTATION())
            facing = chars.get(CharCls.sf_get_LENS_FACING())

            self.sensor_orient = int(orient) if orient is not None else 90
            self.facing_front  = (int(facing) == 0) if facing is not None else False
        except Exception as e:
            print(f"[CAMERA] read_camera_info failed: {e}")

    # ── UI Button Clicks ──────────────────────────────────────────────────────
    def on_switch_camera_clicked(self, view):
        if not self.camera_ids or len(self.camera_ids) < 2 or self.is_switching: return
        self.is_switching = True
        self._clear_buffers()
        self.shutdown_camera_only()

        self.current_cam_idx = (self.current_cam_idx + 1) % len(self.camera_ids)
        self.zoom_level = 1.0 # Reset zoom on switch
        self.open_current_camera()

    def on_zoom_in_clicked(self, view):
        self.zoom_level = min(5.0, self.zoom_level + 0.5)
        print(f"[ZOOM] Level: {self.zoom_level}x")
        # Hardware Zoom requires android.graphics.Rect in targets.json.
        # Add it to targets.json, run Stratum again, then you can apply SCALER_CROP_REGION here!

    def on_zoom_out_clicked(self, view):
        self.zoom_level = max(1.0, self.zoom_level - 0.5)
        print(f"[ZOOM] Level: {self.zoom_level}x")

    # ── Camera Teardown & Setup ───────────────────────────────────────────────
    def shutdown_camera_only(self):
        if self.capture_session:
            try: self.capture_session.close()
            except: pass
            self.capture_session = None
        if self.camera_device:
            try: self.camera_device.close()
            except: pass
            self.camera_device = None

    def open_current_camera(self):
        try:
            cam_id = self.camera_ids[self.current_cam_idx]
            self._read_camera_info(cam_id)

            vw = self.texture_view.getWidth()
            vh = self.texture_view.getHeight()
            self._choose_optimal_size(cam_id, max(1, vw), max(1, vh))

            st_raw = self.texture_view.getSurfaceTexture()
            if st_raw:
                surface_texture = stratum.android_graphics_SurfaceTexture._stratum_cast(st_raw)
                surface_texture.setDefaultBufferSize(self.preview_width, self.preview_height)
                self._configure_transform(vw, vh)

            self.cam_mgr.openCamera(cam_id, {
                "onOpened":       self.on_camera_opened,
                "onDisconnected": self.on_camera_disconnected,
                "onError":        self.on_camera_error,
            }, self.handler)
        except Exception as e:
            traceback.print_exc()

    # ── Camera Lifecycle ──────────────────────────────────────────────────────
    def on_surface_available(self, st, w, h):
        try:
            sys_svc = self.activity.getSystemService("camera")
            self.cam_mgr = stratum.android_hardware_camera2_CameraManager._stratum_cast(sys_svc)

            self._filter_logical_cameras()

            looper = stratum.android_os_Looper.getMainLooper_static()
            self.handler = self._new("android_os_Handler", looper)

            self.open_current_camera()
        except Exception as e:
            traceback.print_exc()

    def on_surface_size_changed(self, st, w, h):
        self.is_switching = True
        self._clear_buffers()
        if w > 0 and h > 0:
            self._configure_transform(w, h)
        self.is_switching = False
        self.worker_ready = True

    def on_camera_opened(self, raw_device):
        try:
            self.camera_device = stratum.android_hardware_camera2_CameraDevice._stratum_cast(raw_device)
            st_raw  = self.texture_view.getSurfaceTexture()
            st      = stratum.android_graphics_SurfaceTexture._stratum_cast(st_raw)
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
            self.capture_session = stratum.android_hardware_camera2_CameraCaptureSession._stratum_cast(raw_session)
            req = self.builder.build()
            self.capture_session.setRepeatingRequest(req, None, self.handler)
            print(f">>> CAMERA {self.camera_ids[self.current_cam_idx]} LIVE <<<")

            self.is_switching = False
            self.worker_ready = True
        except Exception as e:
            traceback.print_exc()

    def on_camera_disconnected(self, raw): pass
    def on_camera_error(self, raw, code): print(f"[CAMERA ERROR] code={code}")

    def on_surface_destroyed(self, st):
        self.shutdown()
        return True

    def on_surface_updated(self, st):
        if not OPENCV_OK or self.is_switching:
            return
        if self.worker_ready:
            if hasattr(self, 'out_bmp'):
                self.image_view.setImageBitmap(self.out_bmp)
            self.worker_ready = False
            self.frame_event.set()

    # ── Background Thread ─────────────────────────────────────────────────────
    def _worker_loop(self):
        print("[WORKER] Background OpenCV Thread Started")
        orb = cv2.ORB_create(nfeatures=500)

        while self.running:
            self.frame_event.wait()
            self.frame_event.clear()

            if not self.running: break
            if self.is_switching:
                self.worker_ready = True
                continue

            try:
                if not hasattr(self, "in_bmp"):
                    frame = self.texture_view.getBitmap()
                    if frame is None:
                        self.worker_ready = True
                        continue

                    w, h = frame.getWidth(), frame.getHeight()
                    self.w, self.h = w, h
                    size = w * h * 4

                    self.in_bmp  = frame
                    self.out_bmp = frame.copy(frame.getConfig(), True)

                    raw_buf         = stratum.allocate_direct_buffer(size)
                    self.bb_wrapper = stratum.java_nio_ByteBuffer._stratum_cast(raw_buf)
                    self.mem_view   = self.bb_wrapper.duplicate()
                    self.arr        = np.array(self.mem_view, copy=False).reshape((h, w, 4))
                    self.gray_buf   = np.empty((h, w), dtype=np.uint8)
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

                self.bb_wrapper.rewind()
                self.in_bmp.copyPixelsToBuffer(self.bb_wrapper)

                if self.facing_front:
                    cv2.flip(self.arr, 1, dst=self.arr)

                # ── Optional: Software Crop (Visual Zoom) ──
                # If you want OpenCV to do the zoom visually until hardware zoom is implemented:
                # if self.zoom_level > 1.0:
                #     h, w = self.arr.shape[:2]
                #     crop_h, crop_w = int(h / self.zoom_level), int(w / self.zoom_level)
                #     y, x = (h - crop_h) // 2, (w - crop_w) // 2
                #     cropped = self.arr[y:y+crop_h, x:x+crop_w]
                #     self.arr[:] = cv2.resize(cropped, (w, h))

                cv2.cvtColor(self.arr, cv2.COLOR_RGBA2GRAY, dst=self.gray_buf)
                keypoints = orb.detect(self.gray_buf, None)
                cv2.cvtColor(self.arr, cv2.COLOR_RGBA2BGR,  dst=self.bgr_buf)

                cv2.drawKeypoints(self.bgr_buf, keypoints, self.bgr_buf, color=(0, 255, 0), flags=0)

                t0  = time.time()
                fps = 1.0 / (t0 - self.last_time) if self.last_time else 0
                self.last_time = t0

                cv2.putText(self.bgr_buf, f"ORB: {len(keypoints)} | FPS: {fps:.1f} | Zoom: {self.zoom_level}x",
                            (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 4)

                cv2.cvtColor(self.bgr_buf, cv2.COLOR_BGR2RGBA, dst=self.arr)
                self.bb_wrapper.rewind()
                self.out_bmp.copyPixelsFromBuffer(self.bb_wrapper)

            except Exception as e:
                print(f"[WORKER ERROR] recovering from error: {e}")
                self._clear_buffers()
            finally:
                self.worker_ready = True

    def shutdown(self):
        self.running = False
        self.frame_event.set()
        self.shutdown_camera_only()

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