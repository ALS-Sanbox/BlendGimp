"""BlendGimp Phase 6.3.5A — unified Blender/GIMP paint sync + shared GIMP paint tools for Texture/Object Paint.

This module adds the first real artist-facing GIMP tools to the Phase 6.1
BlendGimp Area. Raster edits still happen in the persistent GIMP process; the
Image Editor only captures pointer coordinates and displays synchronized pixels.
"""

import base64
import json
import math
import queue
import threading
import time
import uuid

import bpy

try:
    import numpy as np
except Exception:
    np = None

try:
    import gpu
    from gpu_extras.batch import batch_for_shader
except Exception:
    gpu = None
    batch_for_shader = None

from ..ipc.connection import connection_manager, set_direct_paint_refresh_owner
from . import main_panel
from . import texture_editor

BUILD_ID = "6.5.1-canvas-uv-polish"


LIVE_REFRESH_INTERVAL = 0.125  # Throttled authoritative GIMP updates while LMB is down.
STROKE_STREAM_INTERVAL = 0.050
STROKE_MIN_BATCH_POINTS = 32
STROKE_MAX_BATCH_POINTS = 96
COMMIT_ROWS_PER_TICK = 48
COMMIT_IMMEDIATE_MAX_BYTES = 512 * 1024
BRUSH_FALLBACK_LAYER_NAME = "BlendGimp Paint"

_STATE_SYNCING = False
_BRUSH_NAMES = []
_GRADIENT_NAMES = []
_DYNAMICS_NAMES = []
_BRUSH_SYNC_SCENE_NAME = ""
_BRUSH_SYNC_DUE_AT = 0.0
_SYNCED_BRUSH_PAYLOADS = {}
BRUSH_SYNC_DEBOUNCE = 0.075


def _tablet_input_from_event(event):
    """Return compact tablet state for one Blender event.

    Blender reports pressure=1.0 and tilt=(0, 0) for normal mouse input.
    ``is_tablet`` is preserved separately so full-pressure pen samples are not
    mistaken for mouse samples.  The compact [pressure, tilt_x, tilt_y, flag]
    shape keeps localhost JSON overhead predictable.
    """
    try:
        pressure = max(0.0, min(1.0, float(getattr(event, "pressure", 1.0))))
    except Exception:
        pressure = 1.0
    try:
        tilt = getattr(event, "tilt", (0.0, 0.0))
        tilt_x = float(tilt[0])
        tilt_y = float(tilt[1])
    except Exception:
        tilt_x = 0.0
        tilt_y = 0.0
    try:
        is_tablet = bool(getattr(event, "is_tablet", False))
    except Exception:
        is_tablet = False
    return [pressure, tilt_x, tilt_y, 1 if is_tablet else 0]


def _tablet_stats_add(owner, sample):
    """Accumulate diagnostics without changing paint behavior."""
    if not isinstance(sample, (list, tuple)) or len(sample) < 4:
        return
    pressure = max(0.0, min(1.0, float(sample[0])))
    is_tablet = bool(sample[3])
    owner._input_sample_count = int(getattr(owner, "_input_sample_count", 0)) + 1
    if is_tablet:
        owner._tablet_sample_count = int(getattr(owner, "_tablet_sample_count", 0)) + 1
    owner._pressure_sum = float(getattr(owner, "_pressure_sum", 0.0)) + pressure
    owner._pressure_min = min(float(getattr(owner, "_pressure_min", 1.0)), pressure)
    owner._pressure_max = max(float(getattr(owner, "_pressure_max", 0.0)), pressure)
    tilt_mag = math.hypot(float(sample[1]), float(sample[2]))
    owner._tilt_max = max(float(getattr(owner, "_tilt_max", 0.0)), tilt_mag)


def _active_image(scene):
    return texture_editor._find_blendgimp_image(  # noqa: SLF001
        getattr(scene, "blendgimp_texture_editor_image_id", -1)
    )


def _paint_area_poll(context, mode=None):
    return texture_editor._context_is_blendgimp_area(context, mode)  # noqa: SLF001


def _normalized_color(values, fallback):
    try:
        values = list(values)
        if len(values) < 3:
            raise ValueError
        rgba = [max(0.0, min(1.0, float(v))) for v in values[:4]]
        if len(rgba) == 3:
            rgba.append(1.0)
        return rgba
    except Exception:
        return list(fallback)


def _set_scene_from_gimp(scene, state):
    global _STATE_SYNCING
    _STATE_SYNCING = True
    try:
        if state.get("brush_name") is not None:
            scene.blendgimp_brush_name = str(state.get("brush_name") or "")
        if state.get("brush_size") is not None:
            scene.blendgimp_brush_size = max(1.0, float(state["brush_size"]))
        if state.get("brush_opacity") is not None:
            scene.blendgimp_brush_opacity = max(0.0, min(100.0, float(state["brush_opacity"])))
        if state.get("brush_spacing") is not None:
            scene.blendgimp_brush_spacing_percent = max(
                1.0,
                min(200.0, float(state["brush_spacing"]) * 100.0),
            )
        if state.get("brush_hardness") is not None:
            scene.blendgimp_brush_hardness = max(0.0, min(1.0, float(state["brush_hardness"])))
        if state.get("brush_angle") is not None:
            scene.blendgimp_brush_angle = max(-180.0, min(180.0, float(state["brush_angle"])))
        if state.get("brush_aspect_ratio") is not None:
            scene.blendgimp_brush_aspect_ratio = max(-20.0, min(20.0, float(state["brush_aspect_ratio"])))

        fg = state.get("foreground_color")
        if fg is not None:
            incoming = _normalized_color(fg, (0.0, 0.0, 0.0, 1.0))
            try:
                local_alpha = float(scene.blendgimp_foreground_color[3])
            except Exception:
                local_alpha = 1.0
            incoming[3] = max(0.0, min(1.0, local_alpha))
            scene.blendgimp_foreground_color = incoming
        bg = state.get("background_color")
        if bg is not None:
            incoming = _normalized_color(bg, (1.0, 1.0, 1.0, 1.0))
            try:
                local_alpha = float(scene.blendgimp_background_color[3])
            except Exception:
                local_alpha = 1.0
            incoming[3] = max(0.0, min(1.0, local_alpha))
            scene.blendgimp_background_color = incoming

        scene.blendgimp_brush_dynamics_name = str(state.get("dynamics_name", "") or "")
        if state.get("dynamics_enabled") is not None and hasattr(scene, "blendgimp_brush_dynamics_enabled"):
            scene.blendgimp_brush_dynamics_enabled = bool(state.get("dynamics_enabled"))
        if hasattr(scene, "blendgimp_brush_state_initialized"):
            scene.blendgimp_brush_state_initialized = True
        scene.blendgimp_brush_status = (
            f"GIMP: {scene.blendgimp_brush_name or 'Active Brush'} • "
            f"{scene.blendgimp_brush_size:.1f}px • "
            f"{scene.blendgimp_brush_opacity:.0f}%"
        )
        _remember_synced_brush_state(scene)
    finally:
        _STATE_SYNCING = False


def _scene_brush_payload(scene):
    return {
        "brush_name": str(scene.blendgimp_brush_name or "") or None,
        "brush_size": float(scene.blendgimp_brush_size),
        "brush_opacity": float(scene.blendgimp_brush_opacity),
        "brush_spacing": float(scene.blendgimp_brush_spacing_percent) / 100.0,
        "brush_hardness": float(scene.blendgimp_brush_hardness),
        "brush_angle": float(scene.blendgimp_brush_angle),
        "brush_aspect_ratio": float(scene.blendgimp_brush_aspect_ratio),
        "foreground_color": list(scene.blendgimp_foreground_color),
        "background_color": list(scene.blendgimp_background_color),
    }


def _brush_payload_key(scene):
    payload = _scene_brush_payload(scene)
    # Normalize list values so payload equality is stable and cheap.
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _remember_synced_brush_state(scene):
    try:
        _SYNCED_BRUSH_PAYLOADS[scene.name_full] = _brush_payload_key(scene)
    except Exception:
        pass


def _push_scene_brush_state(scene, quiet=False, force=False):
    if _STATE_SYNCING or not connection_manager.is_connected():
        return False
    try:
        payload_key = _brush_payload_key(scene)
        if not force and _SYNCED_BRUSH_PAYLOADS.get(scene.name_full) == payload_key:
            return True
        response = connection_manager.set_brush_state(**_scene_brush_payload(scene))
        _set_scene_from_gimp(scene, response)
        _remember_synced_brush_state(scene)
        return True
    except Exception as exc:
        scene.blendgimp_brush_status = f"Brush sync failed: {exc}"
        if not quiet:
            print(f"BLENDGIMP: Phase 6.2 brush sync failed: {exc}")
        return False


def _flush_pending_brush_sync():
    global _BRUSH_SYNC_SCENE_NAME, _BRUSH_SYNC_DUE_AT
    if not _BRUSH_SYNC_SCENE_NAME:
        return None

    remaining = _BRUSH_SYNC_DUE_AT - time.monotonic()
    if remaining > 0.0:
        return max(0.01, remaining)

    scene = bpy.data.scenes.get(_BRUSH_SYNC_SCENE_NAME)
    _BRUSH_SYNC_SCENE_NAME = ""
    _BRUSH_SYNC_DUE_AT = 0.0
    if scene is not None:
        _push_scene_brush_state(scene, quiet=True)
    return None


def _brush_property_update(_self, context):
    global _BRUSH_SYNC_SCENE_NAME, _BRUSH_SYNC_DUE_AT
    if _STATE_SYNCING or context is None or getattr(context, "scene", None) is None:
        return
    if not connection_manager.is_connected():
        return

    scene = context.scene
    _BRUSH_SYNC_SCENE_NAME = scene.name_full
    _BRUSH_SYNC_DUE_AT = time.monotonic() + BRUSH_SYNC_DEBOUNCE
    if not bpy.app.timers.is_registered(_flush_pending_brush_sync):
        bpy.app.timers.register(
            _flush_pending_brush_sync,
            first_interval=BRUSH_SYNC_DEBOUNCE,
        )


def _dynamics_enabled_update(_self, context):
    if _STATE_SYNCING or context is None or getattr(context, "scene", None) is None:
        return
    if not connection_manager.is_connected():
        return

    scene = context.scene
    try:
        response = connection_manager.set_dynamics_enabled(
            bool(scene.blendgimp_brush_dynamics_enabled)
        )
        _set_scene_from_gimp(scene, response)
        print(
            "BLENDGIMP: GIMP dynamics "
            + ("enabled" if scene.blendgimp_brush_dynamics_enabled else "disabled")
            + f" active={scene.blendgimp_brush_dynamics_name or '<none>'}"
        )
    except Exception as exc:
        scene.blendgimp_brush_status = f"Dynamics sync failed: {exc}"
        print(f"BLENDGIMP: Phase 6.3.6 dynamics enable sync failed: {exc}")


def _tool_label(tool):
    return {
        "PAINTBRUSH": "Paintbrush",
        "PENCIL": "Pencil",
        "ERASER": "Eraser",
        "AIRBRUSH": "Airbrush",
        "FILL": "Fill",
        "GRADIENT": "Gradient",
        "SMUDGE": "Smudge",
        "CLONE": "Clone",
        "HEAL": "Heal",
    }.get(str(tool), str(tool).title())


def _refresh_brush_cache(scene):
    global _BRUSH_NAMES
    response = connection_manager.get_brushes()
    names = [str(name) for name in response.get("brushes", []) if str(name)]
    _BRUSH_NAMES = sorted(set(names), key=str.casefold)
    scene.blendgimp_brush_names_json = json.dumps(_BRUSH_NAMES)
    print(f"BLENDGIMP: Loaded {len(_BRUSH_NAMES)} GIMP brushes")
    return _BRUSH_NAMES


def _load_saved_brush_cache(scene):
    global _BRUSH_NAMES
    if _BRUSH_NAMES:
        return
    try:
        raw = json.loads(getattr(scene, "blendgimp_brush_names_json", "[]") or "[]")
        _BRUSH_NAMES = [str(name) for name in raw if str(name)]
    except Exception:
        _BRUSH_NAMES = []


def _refresh_dynamics_cache(scene):
    global _DYNAMICS_NAMES
    response = connection_manager.get_dynamics()
    names = [str(name) for name in response.get("dynamics", []) if str(name)]
    _DYNAMICS_NAMES = sorted(set(names), key=str.casefold)
    scene.blendgimp_dynamics_names_json = json.dumps(_DYNAMICS_NAMES)

    active = str(response.get("active_dynamics", "") or "")
    enabled = response.get("enabled")
    global _STATE_SYNCING
    previous_syncing = _STATE_SYNCING
    _STATE_SYNCING = True
    try:
        if active:
            scene.blendgimp_brush_dynamics_name = active
        elif not str(getattr(scene, "blendgimp_brush_dynamics_name", "") or "") and _DYNAMICS_NAMES:
            scene.blendgimp_brush_dynamics_name = _DYNAMICS_NAMES[0]
        if enabled is not None:
            scene.blendgimp_brush_dynamics_enabled = bool(enabled)
    finally:
        _STATE_SYNCING = previous_syncing

    _remember_synced_brush_state(scene)
    print(
        "BLENDGIMP: Loaded "
        f"{len(_DYNAMICS_NAMES)} GIMP dynamics; "
        f"active={scene.blendgimp_brush_dynamics_name or '<none>'} "
        f"enabled={bool(scene.blendgimp_brush_dynamics_enabled)}"
    )
    return _DYNAMICS_NAMES


def _load_saved_dynamics_cache(scene):
    global _DYNAMICS_NAMES
    if _DYNAMICS_NAMES:
        return
    try:
        raw = json.loads(getattr(scene, "blendgimp_dynamics_names_json", "[]") or "[]")
        _DYNAMICS_NAMES = [str(name) for name in raw if str(name)]
    except Exception:
        _DYNAMICS_NAMES = []


class BLENDGIMP_OT_refresh_dynamics(bpy.types.Operator):
    bl_idname = "blendgimp.refresh_dynamics"
    bl_label = "Refresh GIMP Dynamics"
    bl_description = "Read installed GIMP paint dynamics and the active dynamics state"

    def execute(self, context):
        if not connection_manager.is_connected():
            self.report({"ERROR"}, "GIMP is not connected")
            return {"CANCELLED"}
        try:
            names = _refresh_dynamics_cache(context.scene)
            self.report({"INFO"}, f"Loaded {len(names)} GIMP dynamics")
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Could not read GIMP dynamics: {exc}")
            return {"CANCELLED"}


class BLENDGIMP_OT_select_dynamics(bpy.types.Operator):
    bl_idname = "blendgimp.select_dynamics"
    bl_label = "Select GIMP Dynamics"

    dynamics_name: bpy.props.StringProperty(default="")

    def execute(self, context):
        name = str(self.dynamics_name or "").strip()
        if not name:
            return {"CANCELLED"}
        try:
            response = connection_manager.set_dynamics(name)
            _set_scene_from_gimp(context.scene, response)
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Could not activate GIMP dynamics: {exc}")
            return {"CANCELLED"}


def _dynamics_enum_items(_self, context):
    if context is not None and getattr(context, "scene", None) is not None:
        _load_saved_dynamics_cache(context.scene)
    if not _DYNAMICS_NAMES:
        return [("", "No dynamics loaded", "Use Refresh GIMP Dynamics first")]
    return [(name, name, f"Use GIMP dynamics {name}") for name in _DYNAMICS_NAMES]


class BLENDGIMP_OT_choose_dynamics(bpy.types.Operator):
    bl_idname = "blendgimp.choose_dynamics"
    bl_label = "Choose GIMP Dynamics"
    bl_description = "Search installed GIMP paint dynamics"
    bl_property = "dynamics_name"

    dynamics_name: bpy.props.EnumProperty(
        name="GIMP Dynamics",
        items=_dynamics_enum_items,
    )

    def invoke(self, context, _event):
        _load_saved_dynamics_cache(context.scene)
        if not _DYNAMICS_NAMES and connection_manager.is_connected():
            try:
                _refresh_dynamics_cache(context.scene)
            except Exception:
                pass
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        name = str(self.dynamics_name or "").strip()
        if not name:
            return {"CANCELLED"}
        try:
            response = connection_manager.set_dynamics(name)
            _set_scene_from_gimp(context.scene, response)
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Could not activate GIMP dynamics: {exc}")
            return {"CANCELLED"}


class BLENDGIMP_MT_dynamics(bpy.types.Menu):
    bl_idname = "BLENDGIMP_MT_dynamics"
    bl_label = "GIMP Dynamics"

    def draw(self, context):
        layout = self.layout
        _load_saved_dynamics_cache(context.scene)
        if not _DYNAMICS_NAMES:
            layout.label(text="No dynamics list loaded", icon="INFO")
            layout.operator("blendgimp.refresh_dynamics", text="Load Dynamics", icon="FILE_REFRESH")
            return

        active = str(context.scene.blendgimp_brush_dynamics_name or "")
        for name in _DYNAMICS_NAMES:
            op = layout.operator(
                "blendgimp.select_dynamics",
                text=name,
                icon="CHECKMARK" if name == active else "MOD_DYNAMICPAINT",
            )
            op.dynamics_name = name


def _refresh_gradient_cache(scene):
    global _GRADIENT_NAMES
    response = connection_manager.get_gradients()
    names = [str(name) for name in response.get("gradients", []) if str(name)]
    _GRADIENT_NAMES = sorted(set(names), key=str.casefold)
    scene.blendgimp_gradient_names_json = json.dumps(_GRADIENT_NAMES)
    if not str(getattr(scene, "blendgimp_gradient_name", "") or ""):
        active = str(response.get("active_gradient", "") or "")
        if active:
            scene.blendgimp_gradient_name = active
        elif _GRADIENT_NAMES:
            scene.blendgimp_gradient_name = _GRADIENT_NAMES[0]
    print(f"BLENDGIMP: Loaded {len(_GRADIENT_NAMES)} GIMP gradients")
    return _GRADIENT_NAMES


def _load_saved_gradient_cache(scene):
    global _GRADIENT_NAMES
    if _GRADIENT_NAMES:
        return
    try:
        raw = json.loads(getattr(scene, "blendgimp_gradient_names_json", "[]") or "[]")
        _GRADIENT_NAMES = [str(name) for name in raw if str(name)]
    except Exception:
        _GRADIENT_NAMES = []


class BLENDGIMP_OT_refresh_gradients(bpy.types.Operator):
    bl_idname = "blendgimp.refresh_gradients"
    bl_label = "Refresh GIMP Gradients"
    bl_description = "Read installed GIMP gradient resources"

    def execute(self, context):
        if not connection_manager.is_connected():
            self.report({"ERROR"}, "GIMP is not connected")
            return {"CANCELLED"}
        try:
            names = _refresh_gradient_cache(context.scene)
            self.report({"INFO"}, f"Loaded {len(names)} GIMP gradients")
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Could not read GIMP gradients: {exc}")
            return {"CANCELLED"}


class BLENDGIMP_OT_select_gradient(bpy.types.Operator):
    bl_idname = "blendgimp.select_gradient"
    bl_label = "Select GIMP Gradient"

    gradient_name: bpy.props.StringProperty(default="")

    def execute(self, context):
        name = str(self.gradient_name or "").strip()
        if not name:
            return {"CANCELLED"}
        context.scene.blendgimp_gradient_name = name
        context.scene.blendgimp_gradient_source = "RESOURCE"
        return {"FINISHED"}


class BLENDGIMP_MT_gradients(bpy.types.Menu):
    bl_idname = "BLENDGIMP_MT_gradients"
    bl_label = "GIMP Gradients"

    def draw(self, context):
        layout = self.layout
        _load_saved_gradient_cache(context.scene)
        if not _GRADIENT_NAMES:
            layout.label(text="No gradient list loaded", icon="INFO")
            layout.operator("blendgimp.refresh_gradients", text="Load Gradients", icon="FILE_REFRESH")
            return

        active = str(context.scene.blendgimp_gradient_name or "")
        for name in _GRADIENT_NAMES:
            op = layout.operator(
                "blendgimp.select_gradient",
                text=name,
                icon="CHECKMARK" if name == active else "COLORSET_03_VEC",
            )
            op.gradient_name = name


class BLENDGIMP_OT_refresh_brush_state(bpy.types.Operator):
    bl_idname = "blendgimp.refresh_brush_state"
    bl_label = "Refresh GIMP Brushes"
    bl_description = "Read the current GIMP brush state and installed brush list"

    def execute(self, context):
        if not connection_manager.is_connected():
            self.report({"ERROR"}, "GIMP is not connected")
            return {"CANCELLED"}
        try:
            state = connection_manager.get_brush_state()
            _set_scene_from_gimp(context.scene, state)
            names = _refresh_brush_cache(context.scene)
            dynamics = _refresh_dynamics_cache(context.scene)
            self.report({"INFO"}, f"Loaded {len(names)} GIMP brushes and {len(dynamics)} dynamics")
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Could not read GIMP brush state: {exc}")
            return {"CANCELLED"}


class BLENDGIMP_OT_select_brush(bpy.types.Operator):
    bl_idname = "blendgimp.select_brush"
    bl_label = "Select GIMP Brush"

    brush_name: bpy.props.StringProperty(default="")

    def execute(self, context):
        name = str(self.brush_name or "").strip()
        if not name:
            return {"CANCELLED"}
        try:
            response = connection_manager.set_brush_state(brush_name=name)
            _set_scene_from_gimp(context.scene, response)
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Could not activate GIMP brush: {exc}")
            return {"CANCELLED"}


def _brush_enum_items(_self, context):
    if context is not None and getattr(context, "scene", None) is not None:
        _load_saved_brush_cache(context.scene)
    if not _BRUSH_NAMES:
        return [("", "No brushes loaded", "Use Refresh GIMP Brushes first")]
    return [(name, name, f"Use GIMP brush {name}") for name in _BRUSH_NAMES]


class BLENDGIMP_OT_choose_brush(bpy.types.Operator):
    bl_idname = "blendgimp.choose_brush"
    bl_label = "Choose GIMP Brush"
    bl_description = "Search installed GIMP brushes"
    bl_property = "brush_name"

    brush_name: bpy.props.EnumProperty(
        name="GIMP Brush",
        items=_brush_enum_items,
    )

    def invoke(self, context, _event):
        _load_saved_brush_cache(context.scene)
        if not _BRUSH_NAMES and connection_manager.is_connected():
            try:
                _refresh_brush_cache(context.scene)
            except Exception:
                pass
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        name = str(self.brush_name or "").strip()
        if not name:
            return {"CANCELLED"}
        try:
            response = connection_manager.set_brush_state(brush_name=name)
            _set_scene_from_gimp(context.scene, response)
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Could not activate GIMP brush: {exc}")
            return {"CANCELLED"}


class BLENDGIMP_MT_brushes(bpy.types.Menu):
    bl_idname = "BLENDGIMP_MT_brushes"
    bl_label = "GIMP Brushes"

    def draw(self, context):
        layout = self.layout
        _load_saved_brush_cache(context.scene)
        if not _BRUSH_NAMES:
            layout.label(text="No brush list loaded", icon="INFO")
            layout.operator("blendgimp.refresh_brush_state", text="Load Brushes", icon="FILE_REFRESH")
            return

        active = str(context.scene.blendgimp_brush_name or "")
        for name in _BRUSH_NAMES:
            op = layout.operator(
                "blendgimp.select_brush",
                text=name,
                icon="CHECKMARK" if name == active else "BRUSH_DATA",
            )
            op.brush_name = name


class BLENDGIMP_OT_set_paint_tool(bpy.types.Operator):
    bl_idname = "blendgimp.set_paint_tool"
    bl_label = "Set BlendGimp Paint Tool"

    tool: bpy.props.EnumProperty(
        items=(
            ("PAINTBRUSH", "Paintbrush", "GIMP Paintbrush"),
            ("PENCIL", "Pencil", "GIMP Pencil"),
            ("ERASER", "Eraser", "GIMP Eraser"),
            ("AIRBRUSH", "Airbrush", "GIMP Airbrush"),
            ("FILL", "Fill", "GIMP Bucket Fill"),
            ("GRADIENT", "Gradient", "GIMP Gradient"),
            ("SMUDGE", "Smudge", "GIMP Smudge"),
            ("CLONE", "Clone", "GIMP Clone"),
            ("HEAL", "Heal", "GIMP Heal"),
        ),
        default="PAINTBRUSH",
    )

    def execute(self, context):
        context.scene.blendgimp_paint_tool = self.tool
        context.scene.blendgimp_brush_status = f"Tool: {_tool_label(self.tool)}"
        return {"FINISHED"}


class BLENDGIMP_OT_clear_clone_source(bpy.types.Operator):
    bl_idname = "blendgimp.clear_clone_source"
    bl_label = "Clear Clone Source"
    bl_description = "Clear the persistent BlendGimp Clone source point"

    def execute(self, context):
        scene = context.scene
        scene.blendgimp_clone_source_set = False
        scene.blendgimp_clone_source_image_id = -1
        scene.blendgimp_clone_source_layer_id = -1
        scene.blendgimp_clone_source_x = 0.0
        scene.blendgimp_clone_source_y = 0.0
        scene.blendgimp_brush_status = "Clone source cleared"
        return {"FINISHED"}


class BLENDGIMP_OT_clear_heal_source(bpy.types.Operator):
    bl_idname = "blendgimp.clear_heal_source"
    bl_label = "Clear Heal Source"
    bl_description = "Clear the persistent BlendGimp Heal source point"

    def execute(self, context):
        scene = context.scene
        scene.blendgimp_heal_source_set = False
        scene.blendgimp_heal_source_image_id = -1
        scene.blendgimp_heal_source_layer_id = -1
        scene.blendgimp_heal_source_x = 0.0
        scene.blendgimp_heal_source_y = 0.0
        scene.blendgimp_brush_status = "Heal source cleared"
        return {"FINISHED"}


class _Gimp2DPaintWorker:
    """Single background IPC worker for one active Texture Paint operator.

    The worker performs only socket/GIMP work. It never touches bpy data.
    Results are returned to Blender's main thread through ``results`` where
    dirty pixels can be safely applied to Blender Image datablocks.
    """

    def __init__(self):
        self.jobs = queue.Queue()
        self.results = queue.Queue()
        self.thread = threading.Thread(
            target=self._run,
            name="BlendGimp-2D-Paint",
            daemon=True,
        )
        self._stopping = False

    def start(self):
        self.thread.start()

    def submit(self, kind, **payload):
        if not self._stopping:
            self.jobs.put((str(kind), dict(payload)))

    def stop(self):
        if self._stopping:
            return
        self._stopping = True
        self.jobs.put(("STOP", {}))

    def _dirty(self, image_id, *, full_width_rows=False):
        return connection_manager.get_image_dirty_pixels_binary(
            int(image_id),
            full_width_rows=bool(full_width_rows),
        )

    def _layer_dirty(self, image_id, layer_id, dirty):
        if not dirty or not bool(dirty.get("changed", False)):
            return None
        x = int(dirty.get("x", 0))
        y = int(dirty.get("y", 0))
        width = int(dirty.get("region_width", 0))
        height = int(dirty.get("region_height", 0))
        if width <= 0 or height <= 0:
            return None
        return connection_manager.get_layer_pixels_binary(
            int(image_id), int(layer_id), x, y, width, height
        )

    def _run(self):
        while True:
            kind, payload = self.jobs.get()
            try:
                if kind == "STOP":
                    return

                stroke_id = str(payload.get("stroke_id", ""))

                if kind == "BEGIN":
                    connection_manager.begin_paint_stroke(
                        payload["image_id"],
                        payload["layer_id"],
                        stroke_id,
                        tool=payload.get("tool", "PAINTBRUSH"),
                        source_image_id=payload.get("source_image_id"),
                        source_layer_id=payload.get("source_layer_id"),
                        source_x=payload.get("source_x"),
                        source_y=payload.get("source_y"),
                    )
                    self.results.put({"type": "BEGIN_DONE", "stroke_id": stroke_id})
                    continue

                if kind == "CHUNK":
                    started = time.perf_counter()
                    response = connection_manager.paint_stroke_chunk(
                        payload["image_id"],
                        payload["layer_id"],
                        stroke_id,
                        payload.get("coordinates", ()),
                        input_samples=payload.get("input_samples"),
                    )
                    dirty = None
                    layer_dirty = None
                    refreshed = bool(payload.get("refresh", False))
                    if refreshed:
                        dirty = self._dirty(payload["image_id"])
                        layer_dirty = self._layer_dirty(
                            payload["image_id"], payload["layer_id"], dirty
                        )
                    self.results.put(
                        {
                            "type": "CHUNK_DONE",
                            "stroke_id": stroke_id,
                            "point_count": int(response.get("point_count", 0)),
                            "pressure_application": str(response.get("pressure_application", "metadata-only")),
                            "pressure_subsegments": int(response.get("pressure_subsegments", 0)),
                            "tablet_sample_count": int(response.get("tablet_sample_count", 0)),
                            "preview_count": int(payload.get("preview_count", 0)),
                            "refreshed": refreshed,
                            "dirty": dirty,
                            "layer_dirty": layer_dirty,
                            "worker_ms": (time.perf_counter() - started) * 1000.0,
                        }
                    )
                    continue

                if kind == "FILL":
                    started = time.perf_counter()
                    response = connection_manager.bucket_fill(
                        payload["image_id"],
                        payload["layer_id"],
                        payload["x"],
                        payload["y"],
                        fill_type=payload.get("fill_type", "FOREGROUND"),
                    )
                    dirty = self._dirty(payload["image_id"])
                    layer_dirty = self._layer_dirty(
                        payload["image_id"], payload["layer_id"], dirty
                    )
                    self.results.put(
                        {
                            "type": "FILL_DONE",
                            "operation_id": str(payload.get("operation_id", "")),
                            "dirty": dirty,
                            "layer_dirty": layer_dirty,
                            "response": response,
                            "worker_ms": (time.perf_counter() - started) * 1000.0,
                        }
                    )
                    continue

                if kind == "GRADIENT":
                    started = time.perf_counter()
                    response = connection_manager.gradient_fill(
                        payload["image_id"],
                        payload["layer_id"],
                        payload["x1"],
                        payload["y1"],
                        payload["x2"],
                        payload["y2"],
                        gradient_source=payload.get("gradient_source", "FG_BG"),
                        gradient_name=payload.get("gradient_name", ""),
                        gradient_type=payload.get("gradient_type", "LINEAR"),
                        reverse=payload.get("reverse", False),
                        repeat_mode=payload.get("repeat_mode", "NONE"),
                    )
                    dirty = self._dirty(payload["image_id"])
                    layer_dirty = self._layer_dirty(
                        payload["image_id"], payload["layer_id"], dirty
                    )
                    self.results.put(
                        {
                            "type": "GRADIENT_DONE",
                            "operation_id": str(payload.get("operation_id", "")),
                            "dirty": dirty,
                            "layer_dirty": layer_dirty,
                            "response": response,
                            "worker_ms": (time.perf_counter() - started) * 1000.0,
                        }
                    )
                    continue

                if kind == "END":
                    started = time.perf_counter()
                    connection_manager.end_paint_stroke(stroke_id)
                    dirty = None
                    layer_dirty = None
                    if payload.get("refresh", True):
                        dirty = self._dirty(
                            payload["image_id"],
                            full_width_rows=payload.get("full_width_rows", False),
                        )
                        if payload.get("layer_id") is not None:
                            layer_dirty = self._layer_dirty(
                                payload["image_id"], payload["layer_id"], dirty
                            )
                    self.results.put(
                        {
                            "type": "END_DONE",
                            "stroke_id": stroke_id,
                            "dirty": dirty,
                            "layer_dirty": layer_dirty,
                            "worker_ms": (time.perf_counter() - started) * 1000.0,
                        }
                    )
                    continue

            except Exception as exc:
                self.results.put(
                    {
                        "type": "ERROR",
                        "stroke_id": str(payload.get("stroke_id", "")),
                        "error": str(exc),
                    }
                )
            finally:
                self.jobs.task_done()


class BLENDGIMP_OT_gimp_2d_paint(bpy.types.Operator):
    bl_idname = "blendgimp.gimp_2d_paint"
    bl_label = "Start Texture Paint"
    bl_description = "Paint directly on the BlendGimp texture using the selected real GIMP tool"
    bl_options = {"REGISTER"}

    def _set_status(self, context, text):
        context.scene.blendgimp_2d_paint_status = str(text)
        try:
            self._area.tag_redraw()
        except Exception:
            pass

    def _preview_tag_redraw(self):
        try:
            self._area.tag_redraw()
        except Exception:
            pass

    def _add_preview_handler(self):
        if gpu is None or batch_for_shader is None:
            self._preview_handler = None
            return
        try:
            self._preview_handler = bpy.types.SpaceImageEditor.draw_handler_add(
                self._draw_preview,
                (),
                "WINDOW",
                "POST_PIXEL",
            )
        except Exception as exc:
            self._preview_handler = None
            print(f"BLENDGIMP: 2D preview handler unavailable: {exc}")

    def _remove_preview_handler(self):
        handler = getattr(self, "_preview_handler", None)
        if handler is not None:
            try:
                bpy.types.SpaceImageEditor.draw_handler_remove(handler, "WINDOW")
            except Exception:
                pass
            self._preview_handler = None
        self._preview_tag_redraw()

    def _clone_source_preview_point(self):
        source = getattr(self, "_clone_source_image", None)
        scene = getattr(bpy.context, "scene", None)
        if scene is not None and str(getattr(scene, "blendgimp_paint_tool", "")) != "CLONE":
            return None
        if scene is not None and not bool(getattr(scene, "blendgimp_clone_source_set", False)):
            return None
        if source is None:
            return None
        source_image_id = int(getattr(self, "_clone_source_image_id", -1))
        if source_image_id != int(getattr(self, "_image_id", -1)):
            return None
        region = getattr(self, "_window_region", None)
        if region is None:
            return None
        try:
            x, y = source
            u = float(x) / max(1, self._width - 1)
            v = 1.0 - (float(y) / max(1, self._height - 1))
            rx, ry = region.view2d.view_to_region(u, v, clip=False)
            return (float(rx), float(ry))
        except Exception:
            return None

    def _heal_source_preview_point(self):
        source = getattr(self, "_heal_source_image", None)
        scene = getattr(bpy.context, "scene", None)
        if scene is not None and str(getattr(scene, "blendgimp_paint_tool", "")) != "HEAL":
            return None
        if scene is not None and not bool(getattr(scene, "blendgimp_heal_source_set", False)):
            return None
        if source is None:
            return None
        source_image_id = int(getattr(self, "_heal_source_image_id", -1))
        if source_image_id != int(getattr(self, "_image_id", -1)):
            return None
        region = getattr(self, "_window_region", None)
        if region is None:
            return None
        try:
            x, y = source
            u = float(x) / max(1, self._width - 1)
            v = 1.0 - (float(y) / max(1, self._height - 1))
            rx, ry = region.view2d.view_to_region(u, v, clip=False)
            return (float(rx), float(ry))
        except Exception:
            return None

    def _draw_preview(self):
        if gpu is None or batch_for_shader is None:
            return

        current_region = getattr(bpy.context, "region", None)
        target_region = getattr(self, "_window_region", None)
        if current_region is not None and target_region is not None:
            try:
                if current_region.as_pointer() != target_region.as_pointer():
                    return
            except Exception:
                pass

        points = list(getattr(self, "_preview_points", ()) or ())
        clone_marker = self._clone_source_preview_point()
        heal_marker = self._heal_source_preview_point()
        source_marker = clone_marker if clone_marker is not None else heal_marker
        marker_color = (1.0, 0.2, 1.0, 0.95) if clone_marker is not None else (0.25, 1.0, 0.45, 0.95)
        if len(points) < 2 and source_marker is None:
            return

        try:
            gpu.state.blend_set("ALPHA")

            if len(points) >= 2:
                shader = gpu.shader.from_builtin("UNIFORM_COLOR")
                batch = batch_for_shader(
                    shader,
                    "LINE_STRIP",
                    {"pos": [(float(x), float(y), 0.0) for x, y in points]},
                )

                color = tuple(getattr(self, "_preview_color", (1.0, 0.5, 0.0, 0.95)))
                luminance = 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
                outline = (0.05, 0.05, 0.05, 0.95) if luminance > 0.45 else (1.0, 1.0, 1.0, 0.95)

                shader.bind()
                gpu.state.line_width_set(7.0)
                shader.uniform_float("color", outline)
                batch.draw(shader)

                gpu.state.line_width_set(3.0)
                shader.uniform_float("color", color)
                batch.draw(shader)

                last = points[-1]
                point_shader = gpu.shader.from_builtin("POINT_UNIFORM_COLOR")
                point_batch = batch_for_shader(
                    point_shader,
                    "POINTS",
                    {"pos": [(float(last[0]), float(last[1]), 0.0)]},
                )
                point_shader.bind()
                point_shader.uniform_float("color", color)
                point_shader.uniform_float("size", 9.0)
                gpu.state.program_point_size_set(True)
                point_batch.draw(point_shader)

                if not getattr(self, "_preview_draw_confirmed", False):
                    self._preview_draw_confirmed = True
                    print(
                        "BLENDGIMP: 2D live preview first frame drawn "
                        f"points={len(points)} region={int(target_region.width) if target_region else -1}x"
                        f"{int(target_region.height) if target_region else -1}"
                    )

            if source_marker is not None:
                mx, my = source_marker
                marker_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
                marker_batch = batch_for_shader(
                    marker_shader,
                    "LINES",
                    {
                        "pos": [
                            (mx - 9.0, my, 0.0), (mx + 9.0, my, 0.0),
                            (mx, my - 9.0, 0.0), (mx, my + 9.0, 0.0),
                        ]
                    },
                )
                marker_shader.bind()
                gpu.state.line_width_set(3.0)
                marker_shader.uniform_float("color", marker_color)
                marker_batch.draw(marker_shader)

                marker_point_shader = gpu.shader.from_builtin("POINT_UNIFORM_COLOR")
                marker_point_batch = batch_for_shader(
                    marker_point_shader,
                    "POINTS",
                    {"pos": [(mx, my, 0.0)]},
                )
                marker_point_shader.bind()
                marker_point_shader.uniform_float("color", marker_color)
                marker_point_shader.uniform_float("size", 7.0)
                gpu.state.program_point_size_set(True)
                marker_point_batch.draw(marker_point_shader)

        except Exception as exc:
            if not getattr(self, "_preview_draw_error_logged", False):
                self._preview_draw_error_logged = True
                print(f"BLENDGIMP: 2D live preview draw failed: {exc}")
        finally:
            try:
                gpu.state.line_width_set(1.0)
                gpu.state.program_point_size_set(False)
                gpu.state.blend_set("NONE")
            except Exception:
                pass

    def _event_preview_point(self, event):
        region = self._window_region
        if not self._inside_window_region(event):
            return None
        return (
            float(event.mouse_x - region.x),
            float(event.mouse_y - region.y),
        )

    def _append_preview_point(self, event):
        point = self._event_preview_point(event)
        if point is None:
            return
        points = self._preview_points
        if points and math.hypot(point[0] - points[-1][0], point[1] - points[-1][1]) < 0.5:
            return
        points.append(point)
        # Keep the live overlay bounded even during very long strokes.
        if len(points) > 8192:
            del points[: len(points) - 8192]
        self._preview_tag_redraw()

    def _clear_preview(self):
        if getattr(self, "_preview_points", None):
            self._preview_points.clear()
            self._preview_tag_redraw()

    def _trim_preview_committed(self, count):
        """Drop the oldest preview samples once real GIMP pixels are visible."""
        count = max(0, int(count or 0))
        points = getattr(self, "_preview_points", None)
        if not points or count <= 0:
            return 0
        removed = min(count, len(points))
        del points[:removed]
        self._preview_tag_redraw()
        return removed

    def _remove_timer(self, context):
        timer = getattr(self, "_stream_timer", None)
        if timer is not None:
            try:
                context.window_manager.event_timer_remove(timer)
            except Exception:
                pass
            self._stream_timer = None

    def _acquire_refresh_owner(self):
        if not getattr(self, "_owns_refresh", False):
            set_direct_paint_refresh_owner(True, self._image_id, owner=self._refresh_owner_token)
            self._owns_refresh = True
            print(
                "BLENDGIMP: 2D Texture Paint acquired Auto Sync refresh ownership "
                f"for image ID {self._image_id}"
            )

    def _release_refresh_owner(self):
        if getattr(self, "_owns_refresh", False):
            set_direct_paint_refresh_owner(False, owner=self._refresh_owner_token)
            self._owns_refresh = False
            print("BLENDGIMP: Normal GIMP Auto Sync resumed after 2D Texture Paint")

    def _finalize(self, context, text="Texture Paint off"):
        if getattr(self, "_commit_state", None):
            try:
                self._process_progressive_commit(context, flush=True)
            except Exception as exc:
                print(f"BLENDGIMP: progressive commit flush recovered from error: {exc}")
        worker = getattr(self, "_worker", None)
        if worker is not None:
            # Preserve GIMP undo/stroke integrity when the user exits while a
            # stroke is still in flight. Queue any locally buffered points and
            # END without consuming the dirty baseline; Auto Sync will perform
            # the final refresh after the worker has drained.
            if getattr(self, "_stroke_id", "") and not getattr(self, "_end_inflight", False):
                if getattr(self, "_pending_points", None):
                    points = list(self._pending_points)
                    inputs = list(getattr(self, "_pending_inputs", ()))
                    self._pending_points.clear()
                    self._pending_inputs.clear()
                    if self._last_sent_point is not None and points[0] != self._last_sent_point:
                        points.insert(0, self._last_sent_point)
                        inputs.insert(0, self._last_sent_input or [1.0, 0.0, 0.0, 0])
                    coordinates = []
                    for x, y in points:
                        coordinates.extend((x, y))
                    worker.submit(
                        "CHUNK",
                        image_id=self._image_id,
                        layer_id=self._layer_id,
                        stroke_id=self._stroke_id,
                        coordinates=coordinates,
                        input_samples=inputs,
                        refresh=False,
                    )
                worker.submit(
                    "END",
                    image_id=self._image_id,
                    layer_id=self._layer_id,
                    stroke_id=self._stroke_id,
                    refresh=False,
                )
                self._end_inflight = True
            worker.stop()
            try:
                worker.thread.join(timeout=1.0)
            except Exception:
                pass
            self._worker = None
        self._remove_timer(context)
        self._remove_preview_handler()
        self._release_refresh_owner()
        scene = context.scene
        scene.blendgimp_2d_paint_active = False
        scene.blendgimp_2d_paint_layer_id = -1
        self._set_status(context, text)
        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass

    def _queue_point(self, point, event=None, input_state=None, force_duplicate=False):
        if point is None:
            return
        point = (float(point[0]), float(point[1]))
        sample = list(input_state) if input_state is not None else _tablet_input_from_event(event)
        if self._pending_points:
            previous = self._pending_points[-1]
        else:
            previous = self._last_sent_point
        if previous is not None and not force_duplicate:
            if math.hypot(point[0] - previous[0], point[1] - previous[1]) < 0.5:
                return
        self._pending_points.append(point)
        self._pending_inputs.append(sample)
        _tablet_stats_add(self, sample)
        self._stroke_sample_count = int(getattr(self, "_stroke_sample_count", 0)) + 1
        if force_duplicate:
            self._pending_points.append(point)
            self._pending_inputs.append(list(sample))


    def _decode_dirty_raw(self, dirty_response):
        raw = dirty_response.get("pixels_raw")
        if raw is not None:
            return raw if isinstance(raw, bytes) else bytes(raw)
        encoded = str(dirty_response.get("pixels_b64", "") or "")
        if not encoded:
            return None
        try:
            return base64.b64decode(encoded, validate=True)
        except Exception:
            return None

    def _apply_dirty_result(self, context, dirty_response):
        """Apply the final GIMP dirty rectangle using Blender's fast full-array API.

        Blender 5.2 exposes Image.pixels as bpy_prop_array, whose foreach_get/
        foreach_set methods avoid the very expensive repeated Python/RNA slice
        assignments used by the 6.2.8 experiment.  We keep a full float32 CPU
        mirror for the active Texture Paint image when practical, patch only the
        tight dirty rectangle into that mirror, then publish it with one
        foreach_set call.
        """
        if not dirty_response:
            return False
        started = time.perf_counter()
        try:
            image_id = int(dirty_response.get("image_id", self._image_id))
            sync_token = str(dirty_response.get("sync_token", "") or "").strip()
            blender_image = main_panel._find_blendgimp_image(image_id, sync_token)  # noqa: SLF001
            if blender_image is None:
                raise RuntimeError("No existing Blender Image is available for a dirty update")

            if not dirty_response.get("changed", False):
                main_panel.tag_texture_views_for_redraw(context)
                None  # active-layer baseline maintained from raw GIMP layer pixels
                context.scene.blendgimp_connected = True
                return True

            image_width = int(dirty_response["width"])
            image_height = int(dirty_response["height"])
            x = int(dirty_response["x"])
            y = int(dirty_response["y"])
            region_width = int(dirty_response["region_width"])
            region_height = int(dirty_response["region_height"])
            raw = self._decode_dirty_raw(dirty_response)
            expected = region_width * region_height * 4
            if raw is None or len(raw) != expected:
                raise RuntimeError(f"Dirty RGBA byte count mismatch. Expected {expected}, got {0 if raw is None else len(raw)}")

            if np is None or not hasattr(blender_image.pixels, "foreach_get") or not hasattr(blender_image.pixels, "foreach_set"):
                # Compatibility fallback. This is intentionally not the normal 6.2.9 path.
                blender_image = main_panel.apply_blender_image_dirty_pixels(dirty_response)
                main_panel.tag_texture_views_for_redraw(context)
                None  # active-layer baseline maintained from raw GIMP layer pixels
                context.scene.blendgimp_connected = True
                print(
                    "BLENDGIMP: 2D bulk-buffer fallback dirty apply "
                    f"total_ms={(time.perf_counter() - started) * 1000.0:.1f}"
                )
                return True

            total_values = image_width * image_height * 4
            cache_key = (image_id, sync_token, image_width, image_height)
            cache = getattr(self, "_bulk_pixel_cache", None)
            cache_key_current = getattr(self, "_bulk_pixel_cache_key", None)

            read_started = time.perf_counter()
            if cache is None or cache_key_current != cache_key or int(getattr(cache, "size", 0)) != total_values:
                cache = np.empty(total_values, dtype=np.float32)
                blender_image.pixels.foreach_get(cache)
                self._bulk_pixel_cache = cache
                self._bulk_pixel_cache_key = cache_key
                cache_hit = False
            else:
                cache_hit = True
            read_ms = (time.perf_counter() - read_started) * 1000.0

            patch_started = time.perf_counter()
            full = cache.reshape((image_height, image_width, 4))
            region = np.frombuffer(raw, dtype=np.uint8).reshape((region_height, region_width, 4))
            region_float = region.astype(np.float32) * (1.0 / 255.0)
            blender_row_start = image_height - (y + region_height)
            blender_row_end = image_height - y
            full[blender_row_start:blender_row_end, x:x + region_width, :] = region_float[::-1]
            patch_ms = (time.perf_counter() - patch_started) * 1000.0

            write_started = time.perf_counter()
            blender_image.pixels.foreach_set(cache)
            # Keep the shared Object Paint/Auto Sync bulk mirror coherent so
            # switching from Texture Paint to Object Paint cannot republish a
            # stale pre-2D image on the first 3D dirty update.
            try:
                main_panel._drop_dirty_pixel_cache_for_image(image_id)  # noqa: SLF001
                shared_key = main_panel._dirty_cache_key(  # noqa: SLF001
                    blender_image, image_id, sync_token, image_width, image_height
                )
                main_panel._DIRTY_PIXEL_CACHE[shared_key] = cache.copy()  # noqa: SLF001
            except Exception as cache_exc:
                print(f"BLENDGIMP: Shared dirty cache refresh skipped: {cache_exc}")
            write_ms = (time.perf_counter() - write_started) * 1000.0

            update_started = time.perf_counter()
            blender_image["blendgimp_transport"] = str(dirty_response.get("transport", "dirty-rgba-binary"))
            blender_image["blendgimp_last_dirty_x"] = x
            blender_image["blendgimp_last_dirty_y"] = y
            blender_image["blendgimp_last_dirty_width"] = region_width
            blender_image["blendgimp_last_dirty_height"] = region_height
            blender_image.update()
            main_panel.tag_texture_views_for_redraw(context)
            None  # active-layer baseline maintained from raw GIMP layer pixels
            context.scene.blendgimp_connected = True
            update_ms = (time.perf_counter() - update_started) * 1000.0
            total_ms = (time.perf_counter() - started) * 1000.0
            print(
                "BLENDGIMP: 2D bulk-buffer dirty RGBA applied: "
                f"x={x} y={y} {region_width}x{region_height} {expected} bytes "
                f"cache_hit={cache_hit} read_ms={read_ms:.1f} patch_ms={patch_ms:.1f} "
                f"foreach_set_ms={write_ms:.1f} image_update_ms={update_ms:.1f} total_ms={total_ms:.1f}"
            )
            return True
        except Exception as exc:
            print(f"BLENDGIMP: 2D bulk-buffer dirty apply recovered from error: {exc}")
            return False

    def _finish_committed_stroke(self, context, *, worker_ms=0.0, apply_ms=0.0, progressive=False):
        self._clear_preview()
        print(
            "BLENDGIMP: 2D hybrid-progressive stroke committed "
            f"samples_captured={int(getattr(self, '_stroke_sample_count', 0))} "
            f"tablet_samples={int(getattr(self, '_tablet_sample_count', 0))} "
            f"pressure_min={float(getattr(self, '_pressure_min', 1.0)):.3f} "
            f"pressure_max={float(getattr(self, '_pressure_max', 1.0)):.3f} "
            f"pressure_avg={(float(getattr(self, '_pressure_sum', 0.0)) / max(1, int(getattr(self, '_input_sample_count', 0)))):.3f} "
            f"tilt_max={float(getattr(self, '_tilt_max', 0.0)):.3f} "
            f"gimp_chunks_sent={int(getattr(self, '_gimp_chunks_sent', 0))} "
            f"pressure_application={str(getattr(self, '_pressure_application', 'metadata-only'))} "
            f"pressure_subsegments={int(getattr(self, '_pressure_subsegments', 0))} "
            f"progressive_dirty_updates={int(getattr(self, '_progressive_dirty_updates', 0))} "
            f"progressive_publish_ms={float(getattr(self, '_progressive_publish_ms', 0.0)):.1f} "
            f"gimp_end_dirty_ms={float(worker_ms):.1f} "
            f"final_apply_ms={float(apply_ms):.1f}"
        )
        self._stroke_id = ""
        self._ending = False
        self._pending_points.clear()
        self._pending_inputs.clear()
        self._last_sent_point = None
        self._last_sent_input = None
        self._commit_state = None
        self._set_status(
            context,
            (
                "Auto Texture Paint — Clone • Ctrl+LMB source • LMB clone"
                if str(context.scene.blendgimp_paint_tool) == "CLONE"
                else "Auto Texture Paint — Heal • Ctrl+LMB source • LMB heal"
                if str(context.scene.blendgimp_paint_tool) == "HEAL"
                else f"Auto Texture Paint — {_tool_label(context.scene.blendgimp_paint_tool)} • LMB paint"
            ),
        )

        self._release_refresh_owner()

    def _begin_commit(self, context, dirty_response, worker_ms):
        # END performs one final authoritative dirty pull. Progressive updates
        # may already have replaced most of the preview during the stroke; the
        # remaining tail stays visible until this final bulk publish completes.
        started = time.perf_counter()
        applied = self._apply_dirty_result(context, dirty_response)
        apply_ms = (time.perf_counter() - started) * 1000.0
        if not applied:
            self._set_status(context, "Texture commit failed — see console")
        self._finish_committed_stroke(
            context,
            worker_ms=worker_ms,
            apply_ms=apply_ms,
            progressive=False,
        )

    def _process_progressive_commit(self, context, *, flush=False):
        # Retained as a no-op for lifecycle compatibility with 6.2.8 operator state.
        self._commit_state = None
        return False

    def _drain_worker_results(self, context):
        worker = getattr(self, "_worker", None)
        if worker is None:
            return None

        error = None
        while True:
            try:
                result = worker.results.get_nowait()
            except queue.Empty:
                break

            kind = str(result.get("type", ""))
            stroke_id = str(result.get("stroke_id", ""))
            layer_dirty = result.get("layer_dirty")
            if layer_dirty:
                try:
                    main_panel.apply_active_layer_buffer_response(
                        context.scene, self._image_id, self._layer_id, layer_dirty,
                        clear_first=False, activate_target=False
                    )
                except Exception as layer_exc:
                    print(f"BLENDGIMP: 2D Active Layer Buffer patch warning: {layer_exc}")

            if kind == "ERROR":
                error = str(result.get("error", "Unknown GIMP worker error"))
                break

            if kind == "BEGIN_DONE" and stroke_id == self._stroke_id:
                self._begin_inflight = False

            elif kind == "CHUNK_DONE" and stroke_id == self._stroke_id:
                self._chunk_inflight = False
                self._gimp_chunks_sent = int(getattr(self, "_gimp_chunks_sent", 0)) + 1
                application = str(result.get("pressure_application", "metadata-only"))
                if application not in {"metadata-only", "mouse-fast-path"}:
                    self._pressure_application = application
                self._pressure_subsegments = int(getattr(self, "_pressure_subsegments", 0)) + int(
                    result.get("pressure_subsegments", 0)
                )
                self._preview_committed_pending = int(getattr(self, "_preview_committed_pending", 0)) + int(
                    result.get("preview_count", 0)
                )

                dirty = result.get("dirty")
                if bool(result.get("refreshed", False)) and dirty is not None:
                    apply_started = time.perf_counter()
                    applied = self._apply_dirty_result(context, dirty)
                    apply_ms = (time.perf_counter() - apply_started) * 1000.0
                    if applied:
                        pending = int(getattr(self, "_preview_committed_pending", 0))
                        removed = self._trim_preview_committed(pending)
                        self._preview_committed_pending = max(0, pending - removed)
                        self._progressive_dirty_updates = int(
                            getattr(self, "_progressive_dirty_updates", 0)
                        ) + 1
                        self._progressive_publish_ms = float(
                            getattr(self, "_progressive_publish_ms", 0.0)
                        ) + apply_ms
                        print(
                            "BLENDGIMP: 2D progressive GIMP update applied "
                            f"update={self._progressive_dirty_updates} "
                            f"worker_ms={float(result.get('worker_ms', 0.0)):.1f} "
                            f"publish_ms={apply_ms:.1f} "
                            f"preview_tail_points={len(getattr(self, '_preview_points', ())) }"
                        )

            elif kind == "FILL_DONE":
                operation_id = str(result.get("operation_id", ""))
                if operation_id == str(getattr(self, "_fill_operation_id", "")):
                    self._fill_inflight = False
                    apply_started = time.perf_counter()
                    applied = self._apply_dirty_result(context, result.get("dirty"))
                    apply_ms = (time.perf_counter() - apply_started) * 1000.0
                    response = result.get("response") or {}
                    fill_type = str(response.get("fill_type", "FOREGROUND")).upper()
                    if applied:
                        self._set_status(
                            context,
                            f"Auto Texture Paint — Fill ({fill_type.title()}) • LMB fill",
                        )
                        print(
                            "BLENDGIMP: 2D GIMP fill committed "
                            f"x={float(response.get('x', 0.0)):.1f} "
                            f"y={float(response.get('y', 0.0)):.1f} "
                            f"fill={fill_type} "
                            f"worker_ms={float(result.get('worker_ms', 0.0)):.1f} "
                            f"publish_ms={apply_ms:.1f}"
                        )
                    else:
                        self._set_status(context, "Fill completed in GIMP but Blender refresh failed — see console")
                    self._release_refresh_owner()

            elif kind == "GRADIENT_DONE":
                operation_id = str(result.get("operation_id", ""))
                if operation_id == str(getattr(self, "_gradient_operation_id", "")):
                    self._gradient_inflight = False
                    apply_started = time.perf_counter()
                    applied = self._apply_dirty_result(context, result.get("dirty"))
                    apply_ms = (time.perf_counter() - apply_started) * 1000.0
                    response = result.get("response") or {}
                    self._gradient_start_image = None
                    self._gradient_start_preview = None
                    self._clear_preview()
                    if applied:
                        self._set_status(
                            context,
                            "Auto Texture Paint — Gradient • LMB drag gradient",
                        )
                        print(
                            "BLENDGIMP: 2D GIMP gradient committed "
                            f"start=({float(response.get('x1', 0.0)):.1f},{float(response.get('y1', 0.0)):.1f}) "
                            f"end=({float(response.get('x2', 0.0)):.1f},{float(response.get('y2', 0.0)):.1f}) "
                            f"type={response.get('gradient_type', 'LINEAR')} "
                            f"source={response.get('gradient_source', 'FG_BG')} "
                            f"gradient={response.get('gradient_name', '')} "
                            f"reverse={bool(response.get('reverse', False))} "
                            f"repeat={response.get('repeat_mode', 'NONE')} "
                            f"worker_ms={float(result.get('worker_ms', 0.0)):.1f} "
                            f"publish_ms={apply_ms:.1f}"
                        )
                    else:
                        self._set_status(context, "Gradient completed in GIMP but Blender refresh failed — see console")
                    self._release_refresh_owner()

            elif kind == "END_DONE" and stroke_id == self._stroke_id:
                self._end_inflight = False
                self._begin_commit(
                    context,
                    result.get("dirty"),
                    float(result.get("worker_ms", 0.0)),
                )

            worker.results.task_done()

        return error

    def _dispatch_chunk(self, force=False):
        if not self._stroke_id or self._begin_inflight or self._chunk_inflight:
            return False
        if not self._pending_points:
            return False

        now = time.monotonic()
        if not force and now - self._last_dispatch < STROKE_STREAM_INTERVAL:
            return False

        if not force and len(self._pending_points) < STROKE_MIN_BATCH_POINTS:
            return False

        take = min(len(self._pending_points), STROKE_MAX_BATCH_POINTS)
        points = list(self._pending_points[:take])
        inputs = list(self._pending_inputs[:take])
        del self._pending_points[:take]
        del self._pending_inputs[:take]
        preview_count = take
        if self._last_sent_point is not None and points[0] != self._last_sent_point:
            points.insert(0, self._last_sent_point)
            inputs.insert(0, self._last_sent_input or [1.0, 0.0, 0.0, 0])

        coordinates = []
        for x, y in points:
            coordinates.extend((x, y))

        # Hybrid progressive feedback: pointer capture never waits for GIMP,
        # but roughly every LIVE_REFRESH_INTERVAL a background CHUNK also pulls
        # the accumulated real GIMP dirty pixels.  The main thread publishes
        # those pixels through the proven bulk-buffer path, then trims the
        # already-authoritative portion of the local preview.  Final release
        # chunks skip the progressive pull because END performs the final sync.
        refresh = (
            not self._ending
            and LIVE_REFRESH_INTERVAL > 0.0
            and now - float(getattr(self, "_last_refresh_request", 0.0)) >= LIVE_REFRESH_INTERVAL
        )
        self._worker.submit(
            "CHUNK",
            image_id=self._image_id,
            layer_id=self._layer_id,
            stroke_id=self._stroke_id,
            coordinates=coordinates,
            input_samples=inputs,
            preview_count=preview_count,
            refresh=refresh,
        )
        if refresh:
            self._last_refresh_request = now
        self._last_sent_point = points[-1]
        self._last_sent_input = list(inputs[-1]) if inputs else [1.0, 0.0, 0.0, 0]
        self._chunk_inflight = True
        self._last_dispatch = now
        return True

    def _schedule_end_if_ready(self):
        if getattr(self, "_commit_state", None):
            return False
        if not self._ending or not self._stroke_id:
            return False
        if self._begin_inflight or self._chunk_inflight or self._end_inflight:
            return False
        if self._pending_points:
            return self._dispatch_chunk(force=True)

        self._worker.submit(
            "END",
            image_id=self._image_id,
            layer_id=self._layer_id,
            stroke_id=self._stroke_id,
            refresh=True,
            full_width_rows=False,
        )
        self._end_inflight = True
        return True

    def _inside_window_region(self, event):
        region = self._window_region
        return (
            region.x <= event.mouse_x < region.x + region.width
            and region.y <= event.mouse_y < region.y + region.height
        )

    def _event_image_point(self, event):
        if not self._inside_window_region(event):
            return None

        region = self._window_region
        local_x = float(event.mouse_x - region.x)
        local_y = float(event.mouse_y - region.y)

        try:
            u, v = region.view2d.region_to_view(local_x, local_y)
        except Exception:
            return None

        u = float(u)
        v = float(v)
        if u < 0.0 or u > 1.0 or v < 0.0 or v > 1.0:
            return None

        x = u * max(1, self._width - 1)
        y = (1.0 - v) * max(1, self._height - 1)
        return (float(x), float(y))

    def _queue_fill(self, context, point):
        if point is None or bool(getattr(self, "_fill_inflight", False)):
            return False
        if self._stroke_id or self._ending or getattr(self, "_commit_state", None):
            return False

        self._acquire_refresh_owner()
        self._fill_operation_id = uuid.uuid4().hex
        self._fill_inflight = True
        fill_type = str(getattr(context.scene, "blendgimp_fill_source", "FOREGROUND")).upper()
        self._worker.submit(
            "FILL",
            operation_id=self._fill_operation_id,
            image_id=self._image_id,
            layer_id=self._layer_id,
            x=float(point[0]),
            y=float(point[1]),
            fill_type=fill_type,
        )
        self._set_status(context, f"Filling with GIMP {fill_type.title()}...")
        print(
            "BLENDGIMP: 2D GIMP fill queued "
            f"x={float(point[0]):.1f} y={float(point[1]):.1f} "
            f"fill={fill_type} image ID {self._image_id} layer ID {self._layer_id}"
        )
        return True

    def _begin_gradient(self, context, event, point):
        if point is None or bool(getattr(self, "_gradient_inflight", False)):
            return False
        if self._stroke_id or self._ending or getattr(self, "_commit_state", None):
            return False

        preview = self._event_preview_point(event)
        if preview is None:
            return False
        self._gradient_start_image = (float(point[0]), float(point[1]))
        self._gradient_start_preview = (float(preview[0]), float(preview[1]))
        self._preview_points[:] = [self._gradient_start_preview, self._gradient_start_preview]
        self._preview_color = tuple(list(context.scene.blendgimp_foreground_color[:3]) + [0.95])
        self._preview_tag_redraw()
        self._set_status(context, "Gradient drag active — release LMB to apply in GIMP")
        return True

    def _update_gradient_preview(self, event):
        start = getattr(self, "_gradient_start_preview", None)
        if start is None:
            return False
        current = self._event_preview_point(event)
        if current is None:
            return False
        self._preview_points[:] = [start, (float(current[0]), float(current[1]))]
        self._preview_tag_redraw()
        return True

    def _queue_gradient(self, context, point):
        start = getattr(self, "_gradient_start_image", None)
        if start is None or point is None or bool(getattr(self, "_gradient_inflight", False)):
            return False

        end = (float(point[0]), float(point[1]))
        if math.hypot(end[0] - start[0], end[1] - start[1]) < 1.0:
            self._gradient_start_image = None
            self._gradient_start_preview = None
            self._clear_preview()
            self._set_status(context, "Gradient cancelled — drag farther before release")
            return False

        scene = context.scene
        self._acquire_refresh_owner()
        self._gradient_operation_id = uuid.uuid4().hex
        self._gradient_inflight = True
        self._worker.submit(
            "GRADIENT",
            operation_id=self._gradient_operation_id,
            image_id=self._image_id,
            layer_id=self._layer_id,
            x1=float(start[0]),
            y1=float(start[1]),
            x2=float(end[0]),
            y2=float(end[1]),
            gradient_source=str(scene.blendgimp_gradient_source),
            gradient_name=str(scene.blendgimp_gradient_name or ""),
            gradient_type=str(scene.blendgimp_gradient_type),
            reverse=bool(scene.blendgimp_gradient_reverse),
            repeat_mode=str(scene.blendgimp_gradient_repeat_mode),
        )
        self._set_status(context, "Applying authoritative GIMP Gradient...")
        print(
            "BLENDGIMP: 2D GIMP gradient queued "
            f"start=({start[0]:.1f},{start[1]:.1f}) end=({end[0]:.1f},{end[1]:.1f}) "
            f"type={scene.blendgimp_gradient_type} source={scene.blendgimp_gradient_source} "
            f"gradient={scene.blendgimp_gradient_name if scene.blendgimp_gradient_source == 'RESOURCE' else 'FG/BG RGB'} "
            f"reverse={bool(scene.blendgimp_gradient_reverse)} repeat={scene.blendgimp_gradient_repeat_mode} "
            f"image ID {self._image_id} layer ID {self._layer_id}"
        )
        return True

    def _set_clone_source(self, context, point):
        if point is None:
            return False
        if self._stroke_id or self._ending or getattr(self, "_commit_state", None):
            return False
        scene = context.scene
        x, y = float(point[0]), float(point[1])
        scene.blendgimp_clone_source_set = True
        scene.blendgimp_clone_source_image_id = int(self._image_id)
        scene.blendgimp_clone_source_layer_id = int(self._layer_id)
        scene.blendgimp_clone_source_x = x
        scene.blendgimp_clone_source_y = y
        self._clone_source_image_id = int(self._image_id)
        self._clone_source_image = (x, y)
        self._preview_tag_redraw()
        self._set_status(
            context,
            f"Clone source set at ({x:.0f}, {y:.0f}) — LMB clone • Ctrl+LMB reset source",
        )
        print(
            "BLENDGIMP: 2D Clone source set "
            f"image ID {self._image_id} layer ID {self._layer_id} x={x:.1f} y={y:.1f}"
        )
        return True

    def _clone_source_payload(self, context):
        scene = context.scene
        if not bool(getattr(scene, "blendgimp_clone_source_set", False)):
            return None
        source_image_id = int(getattr(scene, "blendgimp_clone_source_image_id", -1))
        source_layer_id = int(getattr(scene, "blendgimp_clone_source_layer_id", -1))
        if source_image_id < 0 or source_layer_id < 0:
            return None
        return {
            "source_image_id": source_image_id,
            "source_layer_id": source_layer_id,
            "source_x": float(getattr(scene, "blendgimp_clone_source_x", 0.0)),
            "source_y": float(getattr(scene, "blendgimp_clone_source_y", 0.0)),
        }

    def _set_heal_source(self, context, point):
        if point is None:
            return False
        if self._stroke_id or self._ending or getattr(self, "_commit_state", None):
            return False
        scene = context.scene
        x, y = float(point[0]), float(point[1])
        scene.blendgimp_heal_source_set = True
        scene.blendgimp_heal_source_image_id = int(self._image_id)
        scene.blendgimp_heal_source_layer_id = int(self._layer_id)
        scene.blendgimp_heal_source_x = x
        scene.blendgimp_heal_source_y = y
        self._heal_source_image_id = int(self._image_id)
        self._heal_source_image = (x, y)
        self._preview_tag_redraw()
        self._set_status(
            context,
            f"Heal source set at ({x:.0f}, {y:.0f}) — LMB heal • Ctrl+LMB reset source",
        )
        print(
            "BLENDGIMP: 2D Heal source set "
            f"image ID {self._image_id} layer ID {self._layer_id} x={x:.1f} y={y:.1f}"
        )
        return True

    def _heal_source_payload(self, context):
        scene = context.scene
        if not bool(getattr(scene, "blendgimp_heal_source_set", False)):
            return None
        source_image_id = int(getattr(scene, "blendgimp_heal_source_image_id", -1))
        source_layer_id = int(getattr(scene, "blendgimp_heal_source_layer_id", -1))
        if source_image_id < 0 or source_layer_id < 0:
            return None
        return {
            "source_image_id": source_image_id,
            "source_layer_id": source_layer_id,
            "source_x": float(getattr(scene, "blendgimp_heal_source_x", 0.0)),
            "source_y": float(getattr(scene, "blendgimp_heal_source_y", 0.0)),
        }

    def _begin_stroke(self, context, point, event=None):
        # Keep one logical GIMP stroke in flight at a time. A previous stroke
        # normally finishes within one localhost round trip; input remains
        # responsive because none of that work happens on Blender's UI thread.
        if self._stroke_id or self._ending or getattr(self, "_commit_state", None):
            return False

        tool = str(context.scene.blendgimp_paint_tool)
        source_payload = None
        if tool == "CLONE":
            source_payload = self._clone_source_payload(context)
            if source_payload is None:
                self._set_status(context, "Clone needs a source — Ctrl+LMB on the texture to set one")
                return False
        elif tool == "HEAL":
            source_payload = self._heal_source_payload(context)
            if source_payload is None:
                self._set_status(context, "Heal needs a source — Ctrl+LMB on the texture to set one")
                return False

        self._acquire_refresh_owner()
        self._stroke_id = uuid.uuid4().hex
        self._painting = True
        self._ending = False
        self._pending_points.clear()
        self._pending_inputs.clear()
        self._last_sent_point = None
        self._last_sent_input = None
        self._input_sample_count = 0
        self._tablet_sample_count = 0
        self._pressure_sum = 0.0
        self._pressure_min = 1.0
        self._pressure_max = 0.0
        self._tilt_max = 0.0
        self._begin_inflight = True
        self._chunk_inflight = False
        self._end_inflight = False
        self._last_dispatch = 0.0
        self._last_refresh_request = 0.0
        self._stroke_sample_count = 0
        self._preview_committed_pending = 0
        self._progressive_dirty_updates = 0
        self._progressive_publish_ms = 0.0
        self._gimp_chunks_sent = 0
        self._pressure_application = "metadata-only"
        self._pressure_subsegments = 0
        self._preview_points.clear()
        fg = list(context.scene.blendgimp_foreground_color)
        alpha = 0.90
        if tool == "ERASER":
            self._preview_color = (1.0, 0.35, 0.15, alpha)
        elif tool == "SMUDGE":
            # Smudge does not deposit the foreground color. Keep the local
            # non-authoritative path guide neutral until real GIMP pixels
            # progressively replace it.
            self._preview_color = (0.65, 0.65, 0.65, alpha)
        elif tool == "CLONE":
            # Clone copies source pixels rather than the foreground color.
            self._preview_color = (0.25, 0.85, 1.0, alpha)
        elif tool == "HEAL":
            # Heal blends sampled source structure into the destination.
            self._preview_color = (0.25, 1.0, 0.45, alpha)
        else:
            self._preview_color = (float(fg[0]), float(fg[1]), float(fg[2]), alpha)

        begin_payload = dict(
            image_id=self._image_id,
            layer_id=self._layer_id,
            stroke_id=self._stroke_id,
            tool=tool,
        )
        if source_payload is not None:
            begin_payload.update(source_payload)
        self._worker.submit("BEGIN", **begin_payload)
        print(
            "BLENDGIMP: 2D GIMP async stroke queued "
            f"tool={tool} image ID {self._image_id} layer ID {self._layer_id}"
        )
        self._queue_point(point, event=event, force_duplicate=True)
        self._set_status(
            context,
            "Cloning with GIMP — Ctrl+LMB resets source" if tool == "CLONE"
            else "Healing with GIMP — Ctrl+LMB resets source" if tool == "HEAL"
            else f"Painting with GIMP {_tool_label(tool)}",
        )
        return True

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == "IMAGE_EDITOR"
            and _paint_area_poll(context, texture_editor.MODE_TEXTURE)
        )

    def invoke(self, context, _event):
        scene = context.scene
        image = _active_image(scene)
        if image is None:
            self.report({"ERROR"}, "No active synchronized BlendGimp texture")
            return {"CANCELLED"}
        if not connection_manager.is_connected():
            self.report({"ERROR"}, "GIMP is not connected")
            return {"CANCELLED"}

        window_region = next((region for region in context.area.regions if region.type == "WINDOW"), None)
        if window_region is None:
            self.report({"ERROR"}, "Image Editor window region is unavailable")
            return {"CANCELLED"}

        try:
            # Unified paint ownership barrier: Blender built-in Texture Paint
            # must reach the active GIMP layer before any GIMP tool can start.
            main_panel.flush_blender_paint_changes(
                scene,
                reason="before 2D GIMP tool",
                fail_if_unsent=True,
            )
            layer_response = connection_manager.resolve_active_raster_layer(
                scene.blendgimp_texture_editor_image_id,
                BRUSH_FALLBACK_LAYER_NAME,
                create_if_missing=True,
            )
            if bool(layer_response.get("created", False)):
                connection_manager.consume_dirty_baseline(scene.blendgimp_texture_editor_image_id)

            resolved_layer_id = int(layer_response["layer_id"])
            if (
                int(getattr(scene, "blendgimp_blender_paint_sync_image_id", -1))
                == int(scene.blendgimp_texture_editor_image_id)
                and int(getattr(scene, "blendgimp_blender_paint_sync_layer_id", -1))
                != resolved_layer_id
            ):
                main_panel.load_active_layer_buffer_from_gimp(
                    scene, scene.blendgimp_texture_editor_image_id, resolved_layer_id,
                    clear_first=True, activate_target=False
                )

            if scene.blendgimp_brush_state_initialized:
                _push_scene_brush_state(scene)
            state = connection_manager.get_brush_state()
            _set_scene_from_gimp(scene, state)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not start GIMP Texture Paint: {exc}")
            return {"CANCELLED"}

        self._blendgimp_cancel_serial = texture_editor._paint_cancel_serial(scene)  # noqa: SLF001
        self._blendgimp_area_release_serial = texture_editor._area_release_serial(  # noqa: SLF001
            context.screen, context.area
        )
        self._blendgimp_route_slot, self._blendgimp_route_generation, _route_mode = (
            texture_editor._routing_token(context.screen, context.area, texture_editor.MODE_TEXTURE)  # noqa: SLF001
        )
        self._area = context.area
        self._window_region = window_region
        self._image_id = int(scene.blendgimp_texture_editor_image_id)
        self._refresh_owner_token = f"2d:{id(self)}"
        self._layer_id = int(layer_response["layer_id"])
        self._width = int(image.size[0])
        self._height = int(image.size[1])
        self._stroke_id = ""
        self._painting = False
        self._ending = False
        self._pending_points = []
        self._pending_inputs = []
        self._last_sent_point = None
        self._last_sent_input = None
        self._input_sample_count = 0
        self._tablet_sample_count = 0
        self._pressure_sum = 0.0
        self._pressure_min = 1.0
        self._pressure_max = 0.0
        self._tilt_max = 0.0
        self._begin_inflight = False
        self._chunk_inflight = False
        self._end_inflight = False
        self._last_dispatch = 0.0
        self._last_refresh_request = 0.0
        self._stroke_sample_count = 0
        self._preview_points = []
        self._preview_color = (1.0, 0.5, 0.0, 0.9)
        self._preview_handler = None
        self._preview_draw_error_logged = False
        self._preview_draw_confirmed = False
        self._commit_state = None
        self._fill_inflight = False
        self._fill_operation_id = ""
        self._gradient_inflight = False
        self._gradient_operation_id = ""
        self._gradient_start_image = None
        self._gradient_start_preview = None
        self._clone_source_image_id = int(getattr(scene, "blendgimp_clone_source_image_id", -1))
        if bool(getattr(scene, "blendgimp_clone_source_set", False)) and self._clone_source_image_id == self._image_id:
            self._clone_source_image = (
                float(getattr(scene, "blendgimp_clone_source_x", 0.0)),
                float(getattr(scene, "blendgimp_clone_source_y", 0.0)),
            )
        else:
            self._clone_source_image = None
        self._heal_source_image_id = int(getattr(scene, "blendgimp_heal_source_image_id", -1))
        if bool(getattr(scene, "blendgimp_heal_source_set", False)) and self._heal_source_image_id == self._image_id:
            self._heal_source_image = (
                float(getattr(scene, "blendgimp_heal_source_x", 0.0)),
                float(getattr(scene, "blendgimp_heal_source_y", 0.0)),
            )
        else:
            self._heal_source_image = None
        self._worker = _Gimp2DPaintWorker()
        self._worker.start()
        self._add_preview_handler()

        # 6.3.7 keeps this modal armed while idle. Auto Sync refresh ownership
        # is acquired only for an actual raster operation/stroke and released
        # after its authoritative dirty-pixel publication.
        self._owns_refresh = False

        self._stream_timer = context.window_manager.event_timer_add(
            STROKE_STREAM_INTERVAL,
            window=context.window,
        )

        scene.blendgimp_2d_paint_active = True
        scene.blendgimp_2d_paint_layer_id = self._layer_id
        active_tool = str(scene.blendgimp_paint_tool)
        action_hint = (
            "LMB fill" if active_tool == "FILL"
            else "LMB drag gradient" if active_tool == "GRADIENT"
            else "Ctrl+LMB source • LMB clone" if active_tool == "CLONE"
            else "LMB paint"
        )
        self._set_status(
            context,
            f"Auto Texture Paint — {_tool_label(active_tool)} • {action_hint}",
        )

        # 6.3.7 pointer routing keeps the normal Blender pointer over panels/UI
        # and only switches to a paint cursor while the pointer is over the
        # texture canvas (or while a stroke is locked to this owner).
        try:
            context.window.cursor_modal_set("DEFAULT")
        except Exception:
            pass

        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        # Phase 6.3.7 Fix5: generation ownership is authoritative. Blender may
        # retain a retired modal after an editor type/mode switch; it must
        # become inert before inspecting mouse/tool events.
        if not texture_editor._routing_token_current(  # noqa: SLF001
            context.screen,
            getattr(self, "_blendgimp_route_slot", -1),
            getattr(self, "_blendgimp_route_generation", -1),
            texture_editor.MODE_TEXTURE,
        ):
            self._finalize(context, "Texture Paint ended — routing generation retired")
            print("BLENDGIMP: Phase 6.3.7 stale Texture Paint owner retired before event")
            return {"FINISHED"}

        live_area = texture_editor._area_from_slot(  # noqa: SLF001
            context.screen, getattr(self, "_blendgimp_route_slot", -1)
        )
        if live_area is not None:
            self._area = live_area

        global_released = texture_editor._paint_cancel_serial(context.scene) != int(  # noqa: SLF001
            getattr(self, "_blendgimp_cancel_serial", -1)
        )
        area_released = texture_editor._area_release_serial(  # noqa: SLF001
            context.screen, getattr(self, "_area", None)
        ) != int(getattr(self, "_blendgimp_area_release_serial", -1))
        if global_released or area_released:
            self._finalize(context, "Texture Paint released to Blender")
            print("BLENDGIMP: 2D GIMP Texture Paint ownership released")
            return {"FINISHED"}

        if (
            self._area.type != "IMAGE_EDITOR"
            or not texture_editor._is_blendgimp_area(context.screen, self._area)  # noqa: SLF001
            or texture_editor._area_mode(context.screen, self._area) != texture_editor.MODE_TEXTURE  # noqa: SLF001
        ):
            self._finalize(context, "Texture Paint ended — area mode changed")
            return {"FINISHED"}

        if int(getattr(context.scene, "blendgimp_texture_editor_image_id", -1)) != self._image_id:
            self._finalize(context, "Texture Paint ended — active texture changed")
            return {"FINISHED"}

        # 6.3.7: ESC cancels the current interaction but no longer exits the
        # paint owner. RMB is only consumed while a stroke/gradient is active;
        # otherwise it remains available to normal Blender UI/navigation.
        if event.type == "ESC" and event.value == "PRESS":
            if getattr(self, "_gradient_start_image", None) is not None:
                self._gradient_start_image = None
                self._gradient_start_preview = None
                self._clear_preview()
                self._set_status(context, "Gradient cancelled — auto Texture Paint ready")
                return {"RUNNING_MODAL"}
            if self._painting or self._ending or getattr(self, "_stroke_id", ""):
                self._painting = False
                self._ending = True
                if not self._begin_inflight and not self._chunk_inflight:
                    if not self._dispatch_chunk(force=True):
                        self._schedule_end_if_ready()
                self._set_status(context, "Stroke ended — auto Texture Paint ready")
                return {"RUNNING_MODAL"}
            self._set_status(context, "Auto Texture Paint ready")
            return {"RUNNING_MODAL"}

        if event.type == "RIGHTMOUSE" and event.value == "PRESS":
            if self._painting or self._ending or getattr(self, "_gradient_start_image", None) is not None:
                if getattr(self, "_gradient_start_image", None) is not None:
                    self._gradient_start_image = None
                    self._gradient_start_preview = None
                    self._clear_preview()
                if self._painting or self._ending:
                    self._painting = False
                    self._ending = True
                    if not self._begin_inflight and not self._chunk_inflight:
                        if not self._dispatch_chunk(force=True):
                            self._schedule_end_if_ready()
                self._set_status(context, "Interaction ended — auto Texture Paint ready")
                return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            try:
                over_canvas = self._inside_window_region(event)
                area = getattr(self, "_area", None)
                over_area = bool(
                    area is not None
                    and area.x <= event.mouse_x < area.x + area.width
                    and area.y <= event.mouse_y < area.y + area.height
                )
                # Do not fight another BlendGimp area's cursor while idle.
                # Only this area's canvas/UI (or a locked stroke) may alter it.
                if over_area or self._painting:
                    context.window.cursor_modal_set(
                        "PAINT_BRUSH" if (over_canvas or self._painting) else "DEFAULT"
                    )
            except Exception:
                pass

        if event.type == "TIMER":
            error = self._drain_worker_results(context)
            if error:
                self._finalize(context, f"Texture Paint error: {error}")
                self.report({"ERROR"}, error)
                return {"CANCELLED"}

            if getattr(self, "_commit_state", None):
                self._process_progressive_commit(context)

            if self._painting and not self._ending:
                self._dispatch_chunk(force=False)
            elif self._ending:
                self._schedule_end_if_ready()

            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            active_tool = str(context.scene.blendgimp_paint_tool)
            if event.value == "PRESS":
                point = self._event_image_point(event)
                if point is None:
                    return {"PASS_THROUGH"}
                if active_tool == "FILL":
                    self._queue_fill(context, point)
                    return {"RUNNING_MODAL"}
                if active_tool == "GRADIENT":
                    self._begin_gradient(context, event, point)
                    return {"RUNNING_MODAL"}
                if active_tool == "CLONE" and bool(getattr(event, "ctrl", False)):
                    self._set_clone_source(context, point)
                    return {"RUNNING_MODAL"}
                if active_tool == "HEAL" and bool(getattr(event, "ctrl", False)):
                    self._set_heal_source(context, point)
                    return {"RUNNING_MODAL"}
                if self._begin_stroke(context, point, event=event):
                    self._append_preview_point(event)
                return {"RUNNING_MODAL"}

            if event.value == "RELEASE" and active_tool == "GRADIENT":
                if getattr(self, "_gradient_start_image", None) is not None:
                    point = self._event_image_point(event)
                    if point is not None:
                        self._update_gradient_preview(event)
                        self._queue_gradient(context, point)
                    else:
                        self._gradient_start_image = None
                        self._gradient_start_preview = None
                        self._clear_preview()
                return {"RUNNING_MODAL"}

            if event.value == "RELEASE" and self._painting:
                point = self._event_image_point(event)
                if point is not None:
                    self._queue_point(point, event=event)
                    self._append_preview_point(event)
                self._painting = False
                self._ending = True
                # Dispatch immediately if the worker is free; otherwise the
                # next timer tick will send all accumulated samples at once.
                if not self._begin_inflight and not self._chunk_inflight:
                    if not self._dispatch_chunk(force=True):
                        self._schedule_end_if_ready()
                return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE":
            if str(context.scene.blendgimp_paint_tool) == "GRADIENT" and getattr(self, "_gradient_start_image", None) is not None:
                self._update_gradient_preview(event)
                return {"RUNNING_MODAL"}
            if self._painting:
                point = self._event_image_point(event)
                if point is not None:
                    self._queue_point(point, event=event)
                    self._append_preview_point(event)
                return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}


def _draw_tool_buttons(layout, scene):
    """Compact unified toolbar shared by Texture Paint and Object Paint."""
    rows = (
        (
            ("PAINTBRUSH", "Brush", "BRUSH_DATA"),
            ("PENCIL", "Pencil", "GREASEPENCIL"),
            ("ERASER", "Eraser", "BRUSH_DATA"),
            ("AIRBRUSH", "Airbrush", "BRUSH_DATA"),
        ),
        (
            ("SMUDGE", "Smudge", "BRUSH_DATA"),
            ("CLONE", "Clone", "DUPLICATE"),
            ("HEAL", "Heal", "MOD_SMOOTH"),
        ),
        (
            ("FILL", "Fill", "SHADING_SOLID"),
            ("GRADIENT", "Gradient", "COLORSET_03_VEC"),
        ),
    )
    for tools in rows:
        row = layout.row(align=True)
        for tool, label, icon in tools:
            op = row.operator(
                "blendgimp.set_paint_tool",
                text=label,
                icon=icon,
                depress=(scene.blendgimp_paint_tool == tool),
            )
            op.tool = tool


def _find_selected_layer(layers):
    for layer in layers or ():
        if bool(layer.get("selected", False)):
            return layer
        selected = _find_selected_layer(layer.get("children", []))
        if selected is not None:
            return selected
    return None


def _active_layer_summary(scene, image_id):
    try:
        result = main_panel.get_stored_layer_result(scene, int(image_id)) or {}
        layer = _find_selected_layer(result.get("layers", []))
        return layer, int(result.get("layer_count", 0) or 0)
    except Exception:
        return None, 0


def _draw_session_summary(layout, context):
    scene = context.scene
    image_id = int(getattr(scene, "blendgimp_texture_editor_image_id", -1))
    layer, layer_count = _active_layer_summary(scene, image_id)

    box = layout.box()
    row = box.row(align=True)
    row.label(
        text="GIMP Connected" if connection_manager.is_connected() else "GIMP Disconnected",
        icon="CHECKMARK" if connection_manager.is_connected() else "ERROR",
    )
    if hasattr(scene, "blendgimp_auto_pointer_routing"):
        row.prop(
            scene,
            "blendgimp_auto_pointer_routing",
            text="Auto Paint",
            toggle=True,
            icon="MOUSE_LMB",
        )

    texture_name = str(getattr(scene, "blendgimp_texture_editor_image_name", "") or "")
    box.label(
        text=(f"Texture: {texture_name}  •  GIMP ID {image_id}" if image_id >= 0 else "Texture: None"),
        icon="IMAGE_DATA",
    )
    if layer is not None:
        box.label(
            text=f"Layer: {layer.get('name', '[Unnamed]')}  •  {layer_count} total",
            icon="RENDERLAYERS",
        )
    elif image_id >= 0:
        box.label(text="Layer: refresh layer list to show active layer", icon="INFO")

    tool = str(getattr(scene, "blendgimp_paint_tool", "PAINTBRUSH"))
    brush = str(getattr(scene, "blendgimp_brush_name", "") or "Active GIMP Brush")
    dyn = str(getattr(scene, "blendgimp_brush_dynamics_name", "") or "None")
    dyn_suffix = "on" if bool(getattr(scene, "blendgimp_brush_dynamics_enabled", False)) else "off"
    box.label(text=f"Tool: {_tool_label(tool)}  •  Brush: {brush}", icon="BRUSH_DATA")
    box.label(text=f"Dynamics: {dyn} ({dyn_suffix})", icon="MOD_DYNAMICPAINT")


def _draw_layer_controls(layout, context):
    scene = context.scene
    image_id = int(getattr(scene, "blendgimp_texture_editor_image_id", -1))
    box = layout.box()
    header = box.row(align=True)
    expanded = bool(getattr(scene, "blendgimp_ui_show_layers", True))
    header.prop(
        scene,
        "blendgimp_ui_show_layers",
        text="Layers",
        emboss=False,
        icon="TRIA_DOWN" if expanded else "TRIA_RIGHT",
    )
    if image_id >= 0:
        op = header.operator("blendgimp.get_image_layers", text="", icon="FILE_REFRESH")
        op.image_id = image_id
        add = header.operator("blendgimp.add_layer", text="", icon="ADD")
        add.image_id = image_id
        group = header.operator("blendgimp.create_group", text="", icon="NEWFOLDER")
        group.image_id = image_id

    if not expanded:
        return
    if image_id < 0:
        box.label(text="Create or synchronize a texture first", icon="INFO")
        return

    result = main_panel.get_stored_layer_result(scene, image_id)
    if not result:
        box.label(text="Press refresh to load the GIMP layer stack", icon="INFO")
        return
    layers = result.get("layers", [])
    if not layers:
        box.label(text="No layers returned by GIMP", icon="INFO")
        return
    main_panel.draw_layer_tree(box, layers, image_id)


def _draw_tool_specific_options(box, context):
    scene = context.scene
    tool = str(scene.blendgimp_paint_tool)

    if tool == "FILL":
        tool_box = box.box()
        tool_box.label(text="Fill Options", icon="SHADING_SOLID")
        tool_box.prop(scene, "blendgimp_fill_source", text="Source")
        tool_box.label(text="LMB on canvas/model fills from that seed point", icon="MOUSE_LMB")
        return

    if tool == "GRADIENT":
        tool_box = box.box()
        tool_box.label(text="Gradient Options", icon="COLORSET_03_VEC")
        tool_box.prop(scene, "blendgimp_gradient_source", text="Source")
        if scene.blendgimp_gradient_source == "RESOURCE":
            row = tool_box.row(align=True)
            row.menu(
                "BLENDGIMP_MT_gradients",
                text=scene.blendgimp_gradient_name or "Choose GIMP Gradient",
                icon="COLORSET_03_VEC",
            )
            row.operator("blendgimp.refresh_gradients", text="", icon="FILE_REFRESH")
        tool_box.prop(scene, "blendgimp_gradient_type", text="Type")
        row = tool_box.row(align=True)
        row.prop(scene, "blendgimp_gradient_reverse", text="Reverse", toggle=True)
        row.prop(scene, "blendgimp_gradient_repeat_mode", text="Repeat")
        tool_box.label(text="LMB drag sets start/end • release applies", icon="MOUSE_LMB")
        return

    if tool == "CLONE":
        tool_box = box.box()
        tool_box.label(text="Clone Source", icon="DUPLICATE")
        if bool(getattr(scene, "blendgimp_clone_source_set", False)):
            tool_box.label(
                text=(
                    f"Image {int(scene.blendgimp_clone_source_image_id)} / "
                    f"Layer {int(scene.blendgimp_clone_source_layer_id)} • "
                    f"({float(scene.blendgimp_clone_source_x):.0f}, "
                    f"{float(scene.blendgimp_clone_source_y):.0f})"
                ),
                icon="EYEDROPPER",
            )
            tool_box.operator("blendgimp.clear_clone_source", text="Clear Source", icon="X")
        else:
            tool_box.label(text="Ctrl+LMB sets the source", icon="EYEDROPPER")
        tool_box.label(text="LMB paints while source alignment stays continuous")
        return

    if tool == "HEAL":
        tool_box = box.box()
        tool_box.label(text="Heal Source", icon="MOD_SMOOTH")
        if bool(getattr(scene, "blendgimp_heal_source_set", False)):
            tool_box.label(
                text=(
                    f"Image {int(scene.blendgimp_heal_source_image_id)} / "
                    f"Layer {int(scene.blendgimp_heal_source_layer_id)} • "
                    f"({float(scene.blendgimp_heal_source_x):.0f}, "
                    f"{float(scene.blendgimp_heal_source_y):.0f})"
                ),
                icon="EYEDROPPER",
            )
            tool_box.operator("blendgimp.clear_heal_source", text="Clear Source", icon="X")
        else:
            tool_box.label(text="Ctrl+LMB sets the source", icon="EYEDROPPER")
        tool_box.label(text="LMB heals while source alignment stays continuous")
        return

    if tool == "SMUDGE":
        tool_box = box.box()
        tool_box.label(text="Smudge", icon="BRUSH_DATA")
        tool_box.label(text="Uses the active GIMP brush and current dynamics")


def _draw_brush_controls(box, context):
    scene = context.scene
    tool = str(scene.blendgimp_paint_tool)
    brush_tools = {"PAINTBRUSH", "PENCIL", "ERASER", "AIRBRUSH", "SMUDGE", "CLONE", "HEAL"}
    color_tools = {"PAINTBRUSH", "PENCIL", "AIRBRUSH", "FILL", "GRADIENT"}

    if tool in brush_tools:
        brush_box = box.box()
        brush_box.label(text="Brush", icon="BRUSH_DATA")
        row = brush_box.row(align=True)
        row.operator(
            "blendgimp.choose_brush",
            text=scene.blendgimp_brush_name or "Choose GIMP Brush",
            icon="BRUSH_DATA",
        )
        row.menu("BLENDGIMP_MT_brushes", text="", icon="DOWNARROW_HLT")
        row.operator("blendgimp.refresh_brush_state", text="", icon="FILE_REFRESH")

        brush_box.prop(scene, "blendgimp_brush_size", text="Size")
        brush_box.prop(scene, "blendgimp_brush_opacity", text="Opacity", slider=True)
        brush_box.prop(scene, "blendgimp_brush_hardness", text="Hardness", slider=True)
        brush_box.prop(scene, "blendgimp_brush_spacing_percent", text="Spacing %")

        advanced = bool(getattr(scene, "blendgimp_ui_show_brush_advanced", False))
        row = brush_box.row(align=True)
        row.prop(
            scene,
            "blendgimp_ui_show_brush_advanced",
            text="Advanced Brush Shape",
            emboss=False,
            icon="TRIA_DOWN" if advanced else "TRIA_RIGHT",
        )
        if advanced:
            col = brush_box.column(align=True)
            col.prop(scene, "blendgimp_brush_angle", text="Angle")
            col.prop(scene, "blendgimp_brush_aspect_ratio", text="Aspect")

    if tool in color_tools:
        color_box = box.box()
        color_box.label(text="Colors", icon="COLORSET_03_VEC")
        colors = color_box.row(align=True)
        colors.prop(scene, "blendgimp_foreground_color", text="FG")
        colors.prop(scene, "blendgimp_background_color", text="BG")
        if tool in {"FILL", "GRADIENT"}:
            color_box.prop(scene, "blendgimp_brush_opacity", text="Opacity", slider=True)

    if tool in brush_tools:
        dynamics_box = box.box()
        dynamics_box.label(text="Dynamics", icon="MOD_DYNAMICPAINT")
        row = dynamics_box.row(align=True)
        row.operator(
            "blendgimp.choose_dynamics",
            text=scene.blendgimp_brush_dynamics_name or "Choose GIMP Dynamics",
            icon="MOD_DYNAMICPAINT",
        )
        row.menu("BLENDGIMP_MT_dynamics", text="", icon="DOWNARROW_HLT")
        row.operator("blendgimp.refresh_dynamics", text="", icon="FILE_REFRESH")
        dynamics_box.prop(scene, "blendgimp_brush_dynamics_enabled", text="Enable Dynamics")


def _draw_projection_controls(layout, context):
    scene = context.scene
    if context.area is None or context.area.type != "VIEW_3D":
        return
    expanded = bool(getattr(scene, "blendgimp_ui_show_projection", False))
    box = layout.box()
    row = box.row(align=True)
    row.prop(
        scene,
        "blendgimp_ui_show_projection",
        text="Surface Projection",
        emboss=False,
        icon="TRIA_DOWN" if expanded else "TRIA_RIGHT",
    )
    if not expanded:
        return
    if hasattr(scene, "blendgimp_direct_paint_projection_mesh"):
        box.prop(scene, "blendgimp_direct_paint_projection_mesh", text="Projection Mesh")
    if hasattr(scene, "blendgimp_direct_paint_occlusion_mode"):
        box.prop(scene, "blendgimp_direct_paint_occlusion_mode", text="Surface Mode")
    if hasattr(scene, "blendgimp_direct_paint_footprint_protection"):
        box.prop(scene, "blendgimp_direct_paint_footprint_protection", text="Protect Brush Footprint")
    if hasattr(scene, "blendgimp_direct_paint_front_faces_only"):
        box.prop(scene, "blendgimp_direct_paint_front_faces_only", text="Front Faces Only")
    box.label(text="Modifier-aware, seam-safe projection remains active", icon="CHECKMARK")


def _draw_diagnostics(layout, context):
    scene = context.scene
    expanded = bool(getattr(scene, "blendgimp_ui_show_diagnostics", False))
    box = layout.box()
    row = box.row(align=True)
    row.prop(
        scene,
        "blendgimp_ui_show_diagnostics",
        text="Advanced / Diagnostics",
        emboss=False,
        icon="TRIA_DOWN" if expanded else "TRIA_RIGHT",
    )
    if not expanded:
        return
    if scene.blendgimp_brush_status:
        box.label(text=scene.blendgimp_brush_status)
    if getattr(scene, "blendgimp_2d_paint_status", ""):
        box.label(text=scene.blendgimp_2d_paint_status)
    if getattr(scene, "blendgimp_direct_paint_status", ""):
        box.label(text=scene.blendgimp_direct_paint_status)
    _load_saved_brush_cache(scene)
    _load_saved_dynamics_cache(scene)
    box.label(text=f"Brush resources: {len(_BRUSH_NAMES)}")
    box.label(text=f"Dynamics resources: {len(_DYNAMICS_NAMES)}")
    box.label(text=f"Build: {BUILD_ID}", icon="INFO")


def _draw_shared_brush(layout, context, include_start=False):
    """Unified artist UI for both BlendGimp Texture Paint and Object Paint."""
    scene = context.scene

    _draw_session_summary(layout, context)
    _draw_layer_controls(layout, context)

    box = layout.box()
    header = box.row(align=True)
    header.label(text="Paint Tools", icon="BRUSH_DATA")
    header.operator("blendgimp.refresh_brush_state", text="", icon="FILE_REFRESH")
    _draw_tool_buttons(box, scene)
    _draw_tool_specific_options(box, context)
    _draw_brush_controls(box, context)

    # 6.3.7 automatic pointer routing owns the modal operators. The old manual
    # Start/ESC workflow is intentionally not exposed in the artist UI.
    routing = layout.box()
    mode = texture_editor._area_mode(context.screen, context.area)  # noqa: SLF001
    if mode == texture_editor.MODE_TEXTURE:
        active = bool(getattr(scene, "blendgimp_2d_paint_active", False))
        routing.label(
            text="Texture Canvas: paint on hover" if active else "Texture Canvas: auto paint owner starting…",
            icon="CHECKMARK" if active else "TIME",
        )
        routing.label(text="LMB paints • Ctrl+LMB sets Clone/Heal source • ESC ends current operation")
    else:
        active = bool(getattr(scene, "blendgimp_direct_paint_active", False))
        routing.label(
            text="3D View: paint on hover" if active else "3D View: auto paint owner starting…",
            icon="CHECKMARK" if active else "TIME",
        )
        routing.label(text="LMB paints projected UVs • UI/panels keep normal Blender input")

    _draw_projection_controls(layout, context)
    _draw_diagnostics(layout, context)


class BLENDGIMP_PT_gimp_texture_paint(bpy.types.Panel):
    bl_label = "BlendGimp Paint"
    bl_idname = "BLENDGIMP_PT_gimp_texture_paint"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "BlendGimp"

    @classmethod
    def poll(cls, context):
        return _paint_area_poll(context, texture_editor.MODE_TEXTURE)

    def draw(self, context):
        _draw_shared_brush(self.layout, context, include_start=False)


class BLENDGIMP_PT_gimp_object_brush(bpy.types.Panel):
    bl_label = "BlendGimp Paint"
    bl_idname = "BLENDGIMP_PT_gimp_object_brush"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlendGimp"

    @classmethod
    def poll(cls, context):
        return _paint_area_poll(context, texture_editor.MODE_OBJECT)

    def draw(self, context):
        _draw_shared_brush(self.layout, context, include_start=False)


classes = (
    BLENDGIMP_OT_refresh_dynamics,
    BLENDGIMP_OT_select_dynamics,
    BLENDGIMP_OT_choose_dynamics,
    BLENDGIMP_MT_dynamics,
    BLENDGIMP_OT_refresh_gradients,
    BLENDGIMP_OT_select_gradient,
    BLENDGIMP_MT_gradients,
    BLENDGIMP_OT_refresh_brush_state,
    BLENDGIMP_OT_select_brush,
    BLENDGIMP_OT_choose_brush,
    BLENDGIMP_MT_brushes,
    BLENDGIMP_OT_set_paint_tool,
    BLENDGIMP_OT_clear_clone_source,
    BLENDGIMP_OT_clear_heal_source,
    BLENDGIMP_OT_gimp_2d_paint,
    BLENDGIMP_PT_gimp_texture_paint,
    BLENDGIMP_PT_gimp_object_brush,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.blendgimp_paint_tool = bpy.props.EnumProperty(
        name="Paint Tool",
        items=(
            ("PAINTBRUSH", "Paintbrush", "GIMP Paintbrush"),
            ("PENCIL", "Pencil", "GIMP Pencil"),
            ("ERASER", "Eraser", "GIMP Eraser"),
            ("AIRBRUSH", "Airbrush", "GIMP Airbrush"),
            ("FILL", "Fill", "GIMP Bucket Fill"),
            ("GRADIENT", "Gradient", "GIMP Gradient"),
            ("SMUDGE", "Smudge", "GIMP Smudge"),
            ("CLONE", "Clone", "GIMP Clone"),
            ("HEAL", "Heal", "GIMP Heal"),
        ),
        default="PAINTBRUSH",
    )
    bpy.types.Scene.blendgimp_clone_source_set = bpy.props.BoolProperty(
        name="Clone Source Set",
        default=False,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_clone_source_image_id = bpy.props.IntProperty(
        name="Clone Source Image ID",
        default=-1,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_clone_source_layer_id = bpy.props.IntProperty(
        name="Clone Source Layer ID",
        default=-1,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_clone_source_x = bpy.props.FloatProperty(
        name="Clone Source X",
        default=0.0,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_clone_source_y = bpy.props.FloatProperty(
        name="Clone Source Y",
        default=0.0,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_heal_source_set = bpy.props.BoolProperty(
        name="Heal Source Set",
        default=False,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_heal_source_image_id = bpy.props.IntProperty(
        name="Heal Source Image ID",
        default=-1,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_heal_source_layer_id = bpy.props.IntProperty(
        name="Heal Source Layer ID",
        default=-1,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_heal_source_x = bpy.props.FloatProperty(
        name="Heal Source X",
        default=0.0,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_heal_source_y = bpy.props.FloatProperty(
        name="Heal Source Y",
        default=0.0,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_fill_source = bpy.props.EnumProperty(
        name="Fill Source",
        items=(
            ("FOREGROUND", "Foreground", "Fill using the shared GIMP foreground color"),
            ("BACKGROUND", "Background", "Fill using the shared GIMP background color"),
        ),
        default="FOREGROUND",
    )
    bpy.types.Scene.blendgimp_gradient_source = bpy.props.EnumProperty(
        name="Gradient Source",
        items=(
            ("FG_BG", "Foreground / Background", "Use GIMP's built-in FG to BG RGB gradient"),
            ("RESOURCE", "GIMP Resource", "Use an installed GIMP gradient resource"),
        ),
        default="FG_BG",
    )
    bpy.types.Scene.blendgimp_gradient_name = bpy.props.StringProperty(
        name="Gradient Resource",
        default="",
    )
    bpy.types.Scene.blendgimp_gradient_type = bpy.props.EnumProperty(
        name="Gradient Type",
        items=(
            ("LINEAR", "Linear", "Linear gradient"),
            ("BILINEAR", "Bi-linear", "Bi-linear gradient"),
            ("RADIAL", "Radial", "Radial gradient"),
            ("SQUARE", "Square", "Square gradient"),
            ("CONICAL_SYMMETRIC", "Conical Symmetric", "Symmetric conical gradient"),
            ("CONICAL_ASYMMETRIC", "Conical Asymmetric", "Asymmetric conical gradient"),
            ("SHAPEBURST_ANGULAR", "Shapeburst Angular", "Angular shapeburst gradient"),
            ("SHAPEBURST_SPHERICAL", "Shapeburst Spherical", "Spherical shapeburst gradient"),
            ("SHAPEBURST_DIMPLED", "Shapeburst Dimpled", "Dimpled shapeburst gradient"),
            ("SPIRAL_CLOCKWISE", "Spiral Clockwise", "Clockwise spiral gradient"),
            ("SPIRAL_ANTICLOCKWISE", "Spiral Counter-clockwise", "Counter-clockwise spiral gradient"),
        ),
        default="LINEAR",
    )
    bpy.types.Scene.blendgimp_gradient_reverse = bpy.props.BoolProperty(
        name="Reverse Gradient",
        default=False,
    )
    bpy.types.Scene.blendgimp_gradient_repeat_mode = bpy.props.EnumProperty(
        name="Gradient Repeat",
        items=(
            ("NONE", "None (Extend)", "Extend end colors beyond the gradient line"),
            ("TRUNCATE", "None (Truncate)", "Truncate outside the gradient line"),
            ("SAWTOOTH", "Sawtooth", "Repeat as a sawtooth wave"),
            ("TRIANGULAR", "Triangular", "Repeat as a triangular wave"),
        ),
        default="NONE",
    )
    bpy.types.Scene.blendgimp_gradient_names_json = bpy.props.StringProperty(
        name="GIMP Gradient Cache",
        default="[]",
        options={"HIDDEN"},
    )
    bpy.types.Scene.blendgimp_brush_name = bpy.props.StringProperty(
        name="GIMP Brush",
        default="",
    )
    bpy.types.Scene.blendgimp_brush_size = bpy.props.FloatProperty(
        name="Brush Size",
        default=50.0,
        min=1.0,
        max=2000.0,
        soft_max=500.0,
        subtype="PIXEL",
        update=_brush_property_update,
    )
    bpy.types.Scene.blendgimp_brush_opacity = bpy.props.FloatProperty(
        name="Opacity",
        default=100.0,
        min=0.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_brush_property_update,
    )
    bpy.types.Scene.blendgimp_brush_hardness = bpy.props.FloatProperty(
        name="Hardness",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_brush_property_update,
    )
    bpy.types.Scene.blendgimp_brush_spacing_percent = bpy.props.FloatProperty(
        name="Spacing",
        default=10.0,
        min=1.0,
        max=200.0,
        subtype="PERCENTAGE",
        update=_brush_property_update,
    )
    bpy.types.Scene.blendgimp_brush_angle = bpy.props.FloatProperty(
        name="Angle",
        default=0.0,
        min=-180.0,
        max=180.0,
        update=_brush_property_update,
    )
    bpy.types.Scene.blendgimp_brush_aspect_ratio = bpy.props.FloatProperty(
        name="Aspect Ratio",
        default=0.0,
        min=-20.0,
        max=20.0,
        update=_brush_property_update,
    )
    bpy.types.Scene.blendgimp_foreground_color = bpy.props.FloatVectorProperty(
        name="Foreground",
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        size=4,
        subtype="COLOR",
        update=_brush_property_update,
    )
    bpy.types.Scene.blendgimp_background_color = bpy.props.FloatVectorProperty(
        name="Background",
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        size=4,
        subtype="COLOR",
        update=_brush_property_update,
    )
    bpy.types.Scene.blendgimp_brush_dynamics_name = bpy.props.StringProperty(
        name="Dynamics",
        default="",
    )
    bpy.types.Scene.blendgimp_brush_dynamics_enabled = bpy.props.BoolProperty(
        name="Enable Dynamics",
        default=False,
        update=_dynamics_enabled_update,
    )
    bpy.types.Scene.blendgimp_dynamics_names_json = bpy.props.StringProperty(
        name="GIMP Dynamics Cache",
        default="[]",
        options={"HIDDEN"},
    )
    bpy.types.Scene.blendgimp_brush_state_initialized = bpy.props.BoolProperty(
        name="Brush State Initialized",
        default=False,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_brush_status = bpy.props.StringProperty(
        name="Brush Status",
        default="",
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_brush_names_json = bpy.props.StringProperty(
        name="GIMP Brush Cache",
        default="[]",
        options={"HIDDEN"},
    )
    bpy.types.Scene.blendgimp_2d_paint_active = bpy.props.BoolProperty(
        name="2D Paint Active",
        default=False,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_2d_paint_layer_id = bpy.props.IntProperty(
        name="2D Paint Layer ID",
        default=-1,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_2d_paint_status = bpy.props.StringProperty(
        name="2D Paint Status",
        default="",
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_ui_show_layers = bpy.props.BoolProperty(
        name="Show Layers",
        default=True,
    )
    bpy.types.Scene.blendgimp_ui_show_brush_advanced = bpy.props.BoolProperty(
        name="Show Advanced Brush Shape",
        default=False,
    )
    bpy.types.Scene.blendgimp_ui_show_projection = bpy.props.BoolProperty(
        name="Show Surface Projection",
        default=False,
    )
    bpy.types.Scene.blendgimp_ui_show_diagnostics = bpy.props.BoolProperty(
        name="Show Advanced Diagnostics",
        default=False,
    )

    print("BLENDGIMP: Phase 6.5.1 Canvas & UV Polish registered — build 6.5.1-canvas-uv-polish")


def unregister():
    global _BRUSH_SYNC_SCENE_NAME, _BRUSH_SYNC_DUE_AT
    if bpy.app.timers.is_registered(_flush_pending_brush_sync):
        try:
            bpy.app.timers.unregister(_flush_pending_brush_sync)
        except Exception:
            pass
    _BRUSH_SYNC_SCENE_NAME = ""
    _BRUSH_SYNC_DUE_AT = 0.0
    _SYNCED_BRUSH_PAYLOADS.clear()

    property_names = (
        "blendgimp_ui_show_diagnostics",
        "blendgimp_ui_show_projection",
        "blendgimp_ui_show_brush_advanced",
        "blendgimp_ui_show_layers",
        "blendgimp_2d_paint_status",
        "blendgimp_2d_paint_layer_id",
        "blendgimp_2d_paint_active",
        "blendgimp_brush_names_json",
        "blendgimp_brush_status",
        "blendgimp_brush_state_initialized",
        "blendgimp_dynamics_names_json",
        "blendgimp_brush_dynamics_enabled",
        "blendgimp_brush_dynamics_name",
        "blendgimp_background_color",
        "blendgimp_foreground_color",
        "blendgimp_brush_aspect_ratio",
        "blendgimp_brush_angle",
        "blendgimp_brush_spacing_percent",
        "blendgimp_brush_hardness",
        "blendgimp_brush_opacity",
        "blendgimp_brush_size",
        "blendgimp_brush_name",
        "blendgimp_gradient_names_json",
        "blendgimp_gradient_repeat_mode",
        "blendgimp_gradient_reverse",
        "blendgimp_gradient_type",
        "blendgimp_gradient_name",
        "blendgimp_gradient_source",
        "blendgimp_fill_source",
        "blendgimp_heal_source_y",
        "blendgimp_heal_source_x",
        "blendgimp_heal_source_layer_id",
        "blendgimp_heal_source_image_id",
        "blendgimp_heal_source_set",
        "blendgimp_clone_source_y",
        "blendgimp_clone_source_x",
        "blendgimp_clone_source_layer_id",
        "blendgimp_clone_source_image_id",
        "blendgimp_clone_source_set",
        "blendgimp_paint_tool",
    )
    for name in property_names:
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass

    print("BLENDGIMP: Phase 6.5.1 Canvas & UV Polish unregistered — build 6.5.1-canvas-uv-polish")
