import stratum
from ..core import ctx, safe_call, construct

class Intents:
    @staticmethod
    @safe_call
    def share_text(text: str, title: str = "Share via"):
        intent_cls = stratum.android_content_Intent

        # SMART CONSTRUCTOR USAGE HERE
        intent = construct(intent_cls, intent_cls.sf_get_ACTION_SEND())

        intent.setType("text/plain")
        intent.putExtra(intent_cls.sf_get_EXTRA_TEXT(), text)

        chooser = intent_cls.createChooser_static(intent, title)
        ctx.activity.startActivity(chooser)

    @staticmethod
    @safe_call
    def open_url(url: str):
        intent_cls = stratum.android_content_Intent
        uri = stratum.android_net_Uri.parse_static(url)

        # SMART CONSTRUCTOR USAGE HERE
        intent = construct(intent_cls, intent_cls.sf_get_ACTION_VIEW(), uri)

        ctx.activity.startActivity(intent)