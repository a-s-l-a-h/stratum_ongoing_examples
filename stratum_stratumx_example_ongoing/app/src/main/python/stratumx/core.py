import traceback
import stratum

class AppContext:
    def __init__(self):
        self.activity = None
        self.res = None
        self.inflater = None
        self.package_name = None

ctx = AppContext()

def init():
    """Initializes the StratumX context. Call this first in onCreate."""
    try:
        ctx.activity = stratum.getActivity()
        ctx.res = ctx.activity.getResources()
        ctx.inflater = ctx.activity.getLayoutInflater()
        ctx.package_name = str(ctx.activity.getPackageName())
        stratum.log_msg("[StratumX] Framework Initialized.")
    except Exception as e:
        print(f"[StratumX] Initialization Error: {e}")
        traceback.print_exc()

def safe_call(func):
    """Decorator to prevent callbacks from crashing the entire app."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(f"[StratumX] Error in {func.__name__}: {e}")
            traceback.print_exc()
    return wrapper

def run_on_ui(func):
    """Safely runs a function on the Android Main UI Thread."""
    if ctx.activity:
        ctx.activity.runOnUiThread(safe_call(func))

def construct(cls_or_name, *args):
    """Smart helper to automatically find and call the correct new_X constructor."""
    if isinstance(cls_or_name, str):
        cls = getattr(stratum, cls_or_name.replace(".", "_"), None)
    else:
        cls = cls_or_name

    if not cls:
        print(f"[StratumX] Cannot find class: {cls_or_name}")
        return None

    for i in range(20):
        m = getattr(cls, f"new_{i}", None)
        if m is None:
            continue
        try:
            res = m(*args)
            if res is not None:
                return res
        except TypeError:
            # Wrong arguments for this overload, try the next one
            continue

    print(f"[StratumX] Failed to find a matching constructor for {cls_or_name}")
    return None

class StratumApp:
    """
    Base class for Stratum apps. Inherit from this to automatically route
    Android lifecycle events into your class methods.
    """
    def __init__(self):
        import stratum._stratum as _core

        # Tell the C++ engine to route events directly to THIS instance
        _core.set_lifecycle_callback("onCreate", self.onCreate)
        _core.set_lifecycle_callback("onResume", self.onResume)
        _core.set_lifecycle_callback("onPause", self.onPause)
        _core.set_lifecycle_callback("onStop", self.onStop)
        _core.set_lifecycle_callback("onDestroy", self.onDestroy)

    # Default empty implementations (override these in your subclass)
    def onCreate(self): pass
    def onResume(self): pass
    def onPause(self): pass
    def onStop(self): pass
    def onDestroy(self): pass