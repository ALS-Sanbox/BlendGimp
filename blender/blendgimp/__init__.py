# ============================================================
# BlendGimp
# Blender Extension Entry Point
# Version: 0.2.0 development
# ============================================================

from .ui import main_panel
from .ui import texture_editor
from .ui import paint_tools


def register():
    # Existing engine / IPC / sync / 3D painting registration remains the owner
    # of Phase 1-5 runtime state.
    main_panel.register()

    # Phase 6.1 is a Blender-side artist UI layer over that existing state.
    texture_editor.register()

    # Phase 6.2 real GIMP-backed 2D tools + shared 2D/3D brush state.
    paint_tools.register()

    print("BLENDGIMP: Extension registered successfully")


def unregister():
    # Remove Phase 6.1 headers/timer before the existing Scene properties and
    # engine UI they observe are unregistered.
    paint_tools.unregister()
    texture_editor.unregister()
    main_panel.unregister()

    print("BLENDGIMP: Extension unregistered")
