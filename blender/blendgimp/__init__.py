# ============================================================
# BlendGimp
# Blender Extension Entry Point
# Version: 0.4.1 development
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

    # Phase 6.5.1 polishes the Texture Editor canvas/UV presentation while
    # preserving the frozen 6.4.2 pressure and 6.3.7 routing architecture.
    paint_tools.register()

    print("BLENDGIMP: Extension registered successfully")


def unregister():
    # Remove Phase 6.1 headers/timer before the existing Scene properties and
    # engine UI they observe are unregistered.
    paint_tools.unregister()
    texture_editor.unregister()
    main_panel.unregister()

    print("BLENDGIMP: Extension unregistered")
