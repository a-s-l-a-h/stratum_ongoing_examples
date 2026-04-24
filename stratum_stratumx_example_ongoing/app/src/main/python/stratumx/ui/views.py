import stratum
from ..core import safe_call

class View:
    """Base class holding a raw Stratum Object with zero overhead."""
    def __init__(self, raw_view):
        self.raw = raw_view

    def set_visible(self, visible: bool):
        try:
            self.raw.setVisibility(0 if visible else 8)
        except Exception as e:
            print(f"[StratumX] set_visible error: {e}")

class Button(View):
    def on_click(self, callback):
        @safe_call
        def click_handler(v):
            callback(self)
        self.raw.setOnClickListener(click_handler)

class TextView(View):
    def set_text(self, text: str):
        try:
            self.raw.setText(str(text))
        except Exception as e:
            print(f"[StratumX] TextView error: {e}")

    def get_text(self) -> str:
        return str(self.raw.getText())

class EditText(TextView):
    pass

class ImageView(View):
    def set_bitmap(self, bitmap):
        try:
            self.raw.setImageBitmap(bitmap)
        except Exception as e:
            print(f"[StratumX] ImageView error: {e}")

class TextureView(View):
    def __init__(self, raw_view):
        super().__init__(raw_view)
        self._on_available_cb = None
        self._on_updated_cb = None

        self.raw.setSurfaceTextureListener({
            "onSurfaceTextureAvailable": self._internal_available,
            "onSurfaceTextureSizeChanged": lambda st, w, h: None,
            "onSurfaceTextureDestroyed": lambda st: True,
            "onSurfaceTextureUpdated": self._internal_updated,
        })

    def on_available(self, callback):
        self._on_available_cb = callback

    def on_updated(self, callback):
        self._on_updated_cb = callback

    @safe_call
    def _internal_available(self, st, w, h):
        if self._on_available_cb:
            self._on_available_cb(st, w, h)

    @safe_call
    def _internal_updated(self, st):
        if self._on_updated_cb:
            self._on_updated_cb(self)

    def get_bitmap(self, recycle_bitmap=None):
        if recycle_bitmap:
            return self.raw.getBitmap(recycle_bitmap)
        return self.raw.getBitmap()