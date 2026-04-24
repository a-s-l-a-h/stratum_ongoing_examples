import stratum
from ..core import ctx, safe_call, construct

class Camera2:
    def __init__(self, texture_view, facing="back", target_res=None):
        self.texture_view = texture_view
        self.facing_front = (facing.lower() == "front")
        self.target_res = target_res

        self.cam_mgr = None
        self.camera_device = None
        self.capture_session = None
        self.surface = None

        self.texture_view.on_available(self._init_camera)

    @safe_call
    def _init_camera(self, st_raw, w, h):
        sys_svc = ctx.activity.getSystemService("camera")
        self.cam_mgr = stratum.android_hardware_camera2_CameraManager._stratum_cast(sys_svc)

        looper = stratum.android_os_Looper.getMainLooper_static()

        # SMART CONSTRUCTOR USAGE HERE
        self.handler = construct("android_os_Handler", looper)

        cam_id = self._find_camera_id()
        if not cam_id:
            print("[StratumX] No suitable camera found.")
            return

        opt_w, opt_h = self._choose_optimal_size(cam_id, max(w, 1), max(h, 1))
        st = stratum.android_graphics_SurfaceTexture._stratum_cast(st_raw)
        st.setDefaultBufferSize(opt_w, opt_h)

        self.cam_mgr.openCamera(cam_id, {
            "onOpened": self._on_camera_opened,
            "onDisconnected": lambda dev: print("[StratumX] Camera Disconnected"),
            "onError": lambda dev, err: print(f"[StratumX] Camera Error {err}"),
        }, self.handler)

    def _find_camera_id(self):
        try:
            cam_ids = self.cam_mgr.getCameraIdList()
            CharCls = stratum.android_hardware_camera2_CameraCharacteristics
            for cid in cam_ids:
                chars = self.cam_mgr.getCameraCharacteristics(cid)
                facing_obj = chars.get(CharCls.sf_get_LENS_FACING())
                is_front = (int(facing_obj.to_string()) == 0) if facing_obj else False
                if is_front == self.facing_front:
                    return str(cid)
            return str(cam_ids[0]) if cam_ids else None
        except Exception as e:
            print(f"[StratumX] Enumeration failed: {e}")
            return "0"

    def _choose_optimal_size(self, cam_id, view_w, view_h):
        if self.target_res:
            return self.target_res
        try:
            chars = self.cam_mgr.getCameraCharacteristics(cam_id)
            CharCls = stratum.android_hardware_camera2_CameraCharacteristics
            map_obj = chars.get(CharCls.sf_get_SCALER_STREAM_CONFIGURATION_MAP())
            stream_map = stratum.stratum_cast_to(map_obj, "android.hardware.camera2.params.StreamConfigurationMap")

            sizes_array = stream_map.getOutputSizes(34)
            if sizes_array:
                best_w, best_h = 1280, 720
                min_diff = float('inf')
                screen_ratio = max(view_w, view_h) / max(1, min(view_w, view_h))

                for sz_obj in sizes_array:
                    sz = stratum.android_util_Size._stratum_cast(sz_obj)
                    w, h = sz.getWidth(), sz.getHeight()
                    if w * h <= 1920 * 1080 and w > h:
                        diff = abs((w / h) - screen_ratio)
                        if diff < min_diff:
                            min_diff, best_w, best_h = diff, w, h
                return best_w, best_h
        except Exception:
            pass
        return 1280, 720

    @safe_call
    def _on_camera_opened(self, raw_device):
        self.camera_device = stratum.android_hardware_camera2_CameraDevice._stratum_cast(raw_device)
        st_raw = self.texture_view.raw.getSurfaceTexture()
        st = stratum.android_graphics_SurfaceTexture._stratum_cast(st_raw)

        # SMART CONSTRUCTOR USAGE HERE
        self.surface = construct("android_view_Surface", st)

        self.builder = self.camera_device.createCaptureRequest(1)
        self.builder.addTarget(self.surface)

        # SMART CONSTRUCTOR USAGE HERE
        lst = construct("java_util_ArrayList")
        lst.add(self.surface)
        lst_if = stratum.java_util_List._stratum_cast(lst)

        self.camera_device.createCaptureSession(lst_if, {
            "onConfigured": self._on_session_configured,
            "onConfigureFailed": lambda ses: print("[StratumX] Session Failed"),
        }, self.handler)

    @safe_call
    def _on_session_configured(self, raw_session):
        self.capture_session = stratum.android_hardware_camera2_CameraCaptureSession._stratum_cast(raw_session)
        req = self.builder.build()
        self.capture_session.setRepeatingRequest(req, None, self.handler)

    def close(self):
        try:
            if self.capture_session: self.capture_session.close()
            if self.camera_device: self.camera_device.close()
        except:
            pass