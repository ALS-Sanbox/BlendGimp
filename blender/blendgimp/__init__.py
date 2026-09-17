"""BlendGimp Blender extension entry point."""

from .core.build_info import DISPLAY_NAME
from .ui import preferences
from .ui import main_panel
from .ui import texture_editor
from .ui import paint_tools


def register():
    """Register BlendGimp in dependency order."""
    preferences.register()
    main_panel.register()
    texture_editor.register()
    preferences.apply_preferences_to_all_scenes()
    paint_tools.register()
    print(f"BLENDGIMP: {DISPLAY_NAME} registered successfully")


def unregister():
    """Unregister BlendGimp in reverse dependency order."""
    paint_tools.unregister()
    texture_editor.unregister()
    main_panel.unregister()
    preferences.unregister()
    print("BLENDGIMP: Extension unregistered")
