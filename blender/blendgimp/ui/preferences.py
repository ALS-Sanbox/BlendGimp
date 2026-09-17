"""BlendGimp 0.5.18 — production preferences and diagnostics.

Phase 7.0 keeps production painting controls in the artist-facing panels and
moves configuration, setup, maintenance, and diagnostics into Blender
Preferences. Runtime Scene properties remain as compatibility/state mirrors so
the frozen Phase 6 engine and paint code do not need an architectural rewrite.
"""

import bpy

from ..core import gimp_manager
from ..ipc.connection import connection_manager

BLENDGIMP_VERSION = "0.5.18"
BUILD_ID = "7.2-real-brush-preview-hotkeys"

# AddonPreferences must use the root add-on package id. When installed as a
# Blender Extension this is normally something like
# ``bl_ext.user_default.blendgimp`` rather than this ``.ui`` subpackage.
ROOT_PACKAGE = __package__.rsplit(".ui", 1)[0]


def get_preferences(context=None):
    """Return BlendGimp's AddonPreferences, including extension namespaces."""
    context = context or bpy.context
    prefs_root = getattr(context, "preferences", None)
    addons = getattr(prefs_root, "addons", None)
    if addons is None:
        return None

    addon = addons.get(ROOT_PACKAGE)
    if addon is not None:
        return getattr(addon, "preferences", None)

    # Useful fallback for development installs where Blender changes the
    # extension repository namespace around the package id.
    for key, candidate in addons.items():
        if key == "blendgimp" or str(key).endswith(".blendgimp"):
            candidate_prefs = getattr(candidate, "preferences", None)
            if candidate_prefs is not None:
                return candidate_prefs
    return None


def _set_scene_property(scene, name, value):
    if scene is None or not hasattr(scene, name):
        return
    try:
        setattr(scene, name, value)
    except Exception:
        pass


def apply_preferences_to_scene(scene, prefs=None):
    """Mirror user preferences into protected Phase 6 runtime properties."""
    prefs = prefs or get_preferences()
    if prefs is None or scene is None:
        return

    _set_scene_property(scene, "blendgimp_engine_mode", prefs.engine_mode)
    _set_scene_property(
        scene,
        "blendgimp_engine_auto_reconnect",
        bool(prefs.automatic_recovery),
    )
    _set_scene_property(
        scene,
        "blendgimp_auto_pointer_routing",
        bool(prefs.auto_paint),
    )


def apply_preferences_to_all_scenes(context=None):
    prefs = get_preferences(context)
    if prefs is None:
        return
    for scene in getattr(bpy.data, "scenes", ()):  # pragma: no branch - Blender runtime
        apply_preferences_to_scene(scene, prefs)


def engine_mode(context=None, scene=None):
    prefs = get_preferences(context)
    if prefs is not None:
        return str(prefs.engine_mode)
    if scene is not None:
        return str(
            getattr(
                scene,
                "blendgimp_engine_mode",
                gimp_manager.ENGINE_MODE_HEADLESS,
            )
        )
    return gimp_manager.ENGINE_MODE_HEADLESS



def automatic_recovery_enabled(context=None, scene=None):
    prefs = get_preferences(context)
    if prefs is not None:
        return bool(prefs.automatic_recovery)
    if scene is not None:
        return bool(getattr(scene, "blendgimp_engine_auto_reconnect", True))
    return True

def auto_paint_enabled(context=None, scene=None):
    prefs = get_preferences(context)
    if prefs is not None:
        return bool(prefs.auto_paint)
    if scene is not None:
        return bool(getattr(scene, "blendgimp_auto_pointer_routing", True))
    return True


def _preferences_update(self, context):
    # Preference updates are global. Mirroring them into every open Scene keeps
    # current engine/paint code stable and also updates active modal routing.
    for scene in getattr(bpy.data, "scenes", ()):
        apply_preferences_to_scene(scene, self)


class BLENDGIMP_OT_open_preferences(bpy.types.Operator):
    bl_idname = "blendgimp.open_preferences"
    bl_label = "BlendGimp Preferences"
    bl_description = "Open Blender Preferences for BlendGimp setup and diagnostics"

    def execute(self, context):
        try:
            if hasattr(context.preferences, "active_section"):
                context.preferences.active_section = "ADDONS"
        except Exception:
            pass
        try:
            bpy.ops.screen.userpref_show("INVOKE_DEFAULT")
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Could not open Preferences: {exc}")
            return {"CANCELLED"}


class BLENDGIMP_Preferences(bpy.types.AddonPreferences):
    bl_idname = ROOT_PACKAGE

    engine_mode: bpy.props.EnumProperty(
        name="GIMP Engine Mode",
        description=(
            "Run GIMP invisibly for normal BlendGimp work or visibly for "
            "debugging and plug-ins that require the GIMP interface"
        ),
        items=(
            (
                gimp_manager.ENGINE_MODE_HEADLESS,
                "Headless",
                "Run the persistent GIMP engine without the normal GIMP interface",
            ),
            (
                gimp_manager.ENGINE_MODE_VISIBLE_DEBUG,
                "Visible / Debug",
                "Launch GIMP visibly for debugging, troubleshooting, or UI-dependent plug-ins",
            ),
        ),
        default=gimp_manager.ENGINE_MODE_HEADLESS,
        update=_preferences_update,
    )

    automatic_recovery: bpy.props.BoolProperty(
        name="Automatic Engine Recovery",
        description=(
            "Reconnect after socket loss and restart the managed GIMP engine "
            "after an unexpected process exit"
        ),
        default=True,
        update=_preferences_update,
    )

    auto_paint: bpy.props.BoolProperty(
        name="Auto Paint",
        description=(
            "Automatically arm BlendGimp Texture/Object Paint and route input "
            "by the pointer region while Blender UI controls keep normal input"
        ),
        default=True,
        update=_preferences_update,
    )

    show_advanced_diagnostics: bpy.props.BoolProperty(
        name="Advanced / Diagnostics",
        description="Show detailed engine, connection, and paint diagnostics",
        default=False,
    )

    def draw(self, context):
        layout = self.layout
        scene = getattr(context, "scene", None)
        if scene is None:
            scene = getattr(bpy.context, "scene", None)
        if scene is None and getattr(bpy.data, "scenes", None):
            try:
                scene = bpy.data.scenes[0]
            except Exception:
                scene = None
        snapshot = gimp_manager.get_engine_snapshot()

        setup = layout.box()
        setup.label(text="GIMP Installation", icon="FILE_FOLDER")
        if scene is not None and bool(getattr(scene, "blendgimp_gimp_detected", False)):
            row = setup.row(align=True)
            row.label(
                text=f"Detected: GIMP {getattr(scene, 'blendgimp_gimp_version', '')}",
                icon="CHECKMARK",
            )
            path = str(getattr(scene, "blendgimp_gimp_path", "") or "")
            if path:
                setup.label(text=path, icon="FILE")
        else:
            setup.label(text="GIMP has not been detected", icon="ERROR")
        setup.operator(
            "blendgimp.detect_gimp",
            text=(
                "Re-detect GIMP"
                if scene is not None and bool(getattr(scene, "blendgimp_gimp_detected", False))
                else "Detect GIMP"
            ),
            icon="VIEWZOOM",
        )
        setup.label(
            text="Detection also runs automatically when Start GIMP is pressed.",
            icon="INFO",
        )

        engine = layout.box()
        engine.label(text="Engine", icon="PREFERENCES")
        engine.prop(self, "engine_mode", text="Mode")
        engine.prop(self, "automatic_recovery")
        row = engine.row(align=True)
        row.operator("blendgimp.check_gimp", text="Check GIMP Process", icon="INFO")

        if scene is not None:
            running = bool(getattr(scene, "blendgimp_gimp_running", False))
            if running:
                active_mode = str(snapshot.get("mode", "") or "")
                if active_mode and active_mode != str(self.engine_mode):
                    engine.label(text="Restart GIMP to apply the selected mode", icon="INFO")

            actions = engine.row(align=True)

            start_cell = actions.row(align=True)
            start_cell.enabled = not running
            start_cell.operator(
                "blendgimp.launch_gimp",
                text="Start GIMP",
                icon="PLAY",
                depress=True,
            )

            restart_cell = actions.row(align=True)
            restart_cell.enabled = running
            restart_cell.operator(
                "blendgimp.restart_gimp",
                text="Restart",
                icon="FILE_REFRESH",
            )

            stop_cell = actions.row(align=True)
            stop_cell.alert = True
            stop_cell.enabled = running
            stop_cell.operator(
                "blendgimp.stop_gimp",
                text="Stop",
                icon="CANCEL",
            )

        behavior = layout.box()
        behavior.label(text="Painting Behavior", icon="BRUSH_DATA")
        behavior.prop(self, "auto_paint")
        behavior.label(
            text="Auto Paint is a global workflow preference; normal painting controls stay in the BlendGimp panels.",
            icon="INFO",
        )

        advanced = layout.box()
        header = advanced.row(align=True)
        header.prop(
            self,
            "show_advanced_diagnostics",
            text="Advanced / Diagnostics",
            emboss=False,
            icon="TRIA_DOWN" if self.show_advanced_diagnostics else "TRIA_RIGHT",
        )
        if not self.show_advanced_diagnostics:
            return

        advanced.label(text=f"BlendGimp {BLENDGIMP_VERSION} • Build {BUILD_ID}", icon="INFO")
        advanced.label(
            text=(
                "Connection: Connected"
                if connection_manager.is_connected()
                else "Connection: Disconnected"
            ),
            icon="CHECKMARK" if connection_manager.is_connected() else "ERROR",
        )
        advanced.label(
            text=f"Engine state: {str(snapshot.get('state', 'UNKNOWN')).replace('_', ' ').title()}"
        )
        if snapshot.get("pid"):
            advanced.label(text=f"Engine PID: {snapshot.get('pid')}")
        if snapshot.get("mode"):
            advanced.label(text=f"Running mode: {snapshot.get('mode')}")

        if scene is not None:
            runtime = str(getattr(scene, "blendgimp_runtime_gimp_version", "") or "")
            protocol = str(getattr(scene, "blendgimp_protocol_version", "") or "")
            component = str(getattr(scene, "blendgimp_remote_version", "") or "")
            if runtime:
                advanced.label(text=f"Runtime GIMP: {runtime}")
            if protocol:
                advanced.label(text=f"Protocol: {protocol}")
            if component:
                advanced.label(text=f"GIMP component: {component}")
            restarts = int(getattr(scene, "blendgimp_engine_restart_count", 0) or 0)
            advanced.label(text=f"Automatic restarts: {restarts}")

            for label, prop in (
                ("Brush", "blendgimp_brush_status"),
                ("Texture Paint", "blendgimp_2d_paint_status"),
                ("Object Paint", "blendgimp_direct_paint_status"),
            ):
                value = str(getattr(scene, prop, "") or "")
                if value:
                    advanced.label(text=f"{label}: {value}")

            last_error = str(getattr(scene, "blendgimp_engine_last_error", "") or "")
            if last_error:
                error = advanced.box()
                error.alert = True
                error.label(text=last_error, icon="ERROR")

        diag_actions = advanced.row(align=True)
        diag_actions.operator("blendgimp.ping", text="Ping GIMP", icon="FILE_REFRESH")
        diag_actions.operator("blendgimp.check_gimp", text="Check Process", icon="INFO")
        if scene is not None and bool(getattr(scene, "blendgimp_gimp_running", False)):
            force = advanced.row()
            force.alert = True
            force.operator(
                "blendgimp.force_stop_gimp",
                text="Force Stop (Discard Unsaved)",
                icon="ERROR",
            )


classes = (
    BLENDGIMP_OT_open_preferences,
    BLENDGIMP_Preferences,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
