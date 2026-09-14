# ============================================================
# BlendGimp
# Blender Extension Entry Point
# Version: 0.5.3 — Phase 7.0 Usability / Production UI
# ============================================================

from .ui import preferences
from .ui import main_panel
from .ui import texture_editor
from .ui import paint_tools


def register():
    # Phase 7.0 introduces real extension preferences. Register them before the
    # runtime UI so configuration can be mirrored into the protected Phase 6
    # Scene/runtime properties without changing the accepted engine pipeline.
    preferences.register()

    # Existing engine / IPC / sync / 3D painting registration remains the owner
    # of Phase 1-6 runtime state.
    main_panel.register()

    # BlendGimp Area / texture editor.
    texture_editor.register()

    # Apply persistent user preferences only after all mirrored Scene
    # properties exist (engine mode, automatic recovery, and Auto Paint).
    preferences.apply_preferences_to_all_scenes()

    # Frozen Phase 6 artist-workflow baseline. Phase 7.0 only reorganizes
    # presentation/settings ownership; painting behavior stays protected.
    paint_tools.register()

    print(
        "BLENDGIMP: BlendGimp 0.5.3 — Phase 7.0 Usability / Production UI "
        "registered successfully"
    )


def unregister():
    paint_tools.unregister()
    texture_editor.unregister()
    main_panel.unregister()
    preferences.unregister()

    print("BLENDGIMP: Extension unregistered")
