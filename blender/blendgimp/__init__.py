# ============================================================
# BlendGimp
# Blender Extension Entry Point
# Version: 0.1.0
# ============================================================

from .ui import main_panel


def register():
    main_panel.register()

    print(
        "BLENDGIMP: Extension registered successfully"
    )


def unregister():
    main_panel.unregister()

    print(
        "BLENDGIMP: Extension unregistered"
    )