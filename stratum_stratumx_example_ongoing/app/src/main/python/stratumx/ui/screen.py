import stratum
from ..core import ctx
from .views import Button, TextView, EditText, TextureView, ImageView

class XMLScreen:
    def __init__(self, layout_name: str):
        self.layout_name = layout_name
        try:
            layout_id = ctx.res.getIdentifier(layout_name, "layout", ctx.package_name)
            self.root = ctx.inflater.inflate(layout_id, None, False)
        except Exception as e:
            print(f"[StratumX] Failed to load XML layout '{layout_name}': {e}")
            self.root = None

    def show(self):
        """Sets this screen as the active view."""
        if self.root:
            stratum.setContentView(ctx.activity, self.root)

    def _find(self, view_id: str):
        try:
            vid = ctx.res.getIdentifier(view_id, "id", ctx.package_name)
            return self.root.findViewById(vid)
        except Exception as e:
            print(f"[StratumX] Could not find view '{view_id}': {e}")
            return None

    def get_button(self, view_id: str) -> Button:
        return Button(stratum.android_widget_Button._stratum_cast(self._find(view_id)))

    def get_text_view(self, view_id: str) -> TextView:
        return TextView(stratum.android_widget_TextView._stratum_cast(self._find(view_id)))

    def get_edit_text(self, view_id: str) -> EditText:
        return EditText(stratum.android_widget_EditText._stratum_cast(self._find(view_id)))

    def get_texture_view(self, view_id: str) -> TextureView:
        return TextureView(stratum.android_view_TextureView._stratum_cast(self._find(view_id)))

    def get_image_view(self, view_id: str) -> ImageView:
        return ImageView(stratum.android_widget_ImageView._stratum_cast(self._find(view_id)))