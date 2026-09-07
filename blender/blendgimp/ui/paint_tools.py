"""BlendGimp Phase 6.2 — shared GIMP paint tools for Texture/Object Paint.

This module adds the first real artist-facing GIMP tools to the Phase 6.1
BlendGimp Area. Raster edits still happen in the persistent GIMP process; the
Image Editor only captures pointer coordinates and displays synchronized pixels.
"""

import json
import math
import time
import uuid

import bpy

from ..ipc.connection import connection_manager
from . import main_panel
from . import texture_editor


LIVE_REFRESH_INTERVAL = 0.085
STROKE_STREAM_INTERVAL = 0.025
BRUSH_FALLBACK_LAYER_NAME = "BlendGimp Paint"

_STATE_SYNCING = False
_BRUSH_NAMES = []
_BRUSH_SYNC_SCENE_NAME = ""
_BRUSH_SYNC_DUE_AT = 0.0
_SYNCED_BRUSH_PAYLOADS = {}
BRUSH_SYNC_DEBOUNCE = 0.075


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
            scene.blendgimp_foreground_color = _normalized_color(fg, (0.0, 0.0, 0.0, 1.0))
        bg = state.get("background_color")
        if bg is not None:
            scene.blendgimp_background_color = _normalized_color(bg, (1.0, 1.0, 1.0, 1.0))

        scene.blendgimp_brush_dynamics_name = str(state.get("dynamics_name", "") or "")
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


def _tool_label(tool):
    return {
        "PAINTBRUSH": "Paintbrush",
        "PENCIL": "Pencil",
        "ERASER": "Eraser",
        "AIRBRUSH": "Airbrush",
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
            self.report({"INFO"}, f"Loaded {len(names)} GIMP brushes")
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
        ),
        default="PAINTBRUSH",
    )

    def execute(self, context):
        context.scene.blendgimp_paint_tool = self.tool
        context.scene.blendgimp_brush_status = f"Tool: {_tool_label(self.tool)}"
        return {"FINISHED"}


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

    def _remove_timer(self, context):
        timer = getattr(self, "_stream_timer", None)
        if timer is not None:
            try:
                context.window_manager.event_timer_remove(timer)
            except Exception:
                pass
            self._stream_timer = None

    def _queue_point(self, point, force_duplicate=False):
        if point is None:
            return
        point = (float(point[0]), float(point[1]))
        if self._pending_points:
            previous = self._pending_points[-1]
        else:
            previous = self._last_sent_point
        if previous is not None and not force_duplicate:
            if math.hypot(point[0] - previous[0], point[1] - previous[1]) < 0.5:
                return
        self._pending_points.append(point)
        if force_duplicate:
            self._pending_points.append(point)

    def _flush_pending_points(self, context, force=False):
        if not self._painting or not self._stroke_id:
            return False
        if not self._pending_points:
            return False

        now = time.monotonic()
        if not force and now - self._last_stream < STROKE_STREAM_INTERVAL:
            return False

        points = list(self._pending_points)
        self._pending_points.clear()

        # Continue every packet from the last coordinate already accepted by
        # GIMP. This keeps the stroke continuous while allowing all mouse
        # samples collected between timer ticks to travel in one request.
        if self._last_sent_point is not None and points[0] != self._last_sent_point:
            points.insert(0, self._last_sent_point)

        coordinates = []
        for x, y in points:
            coordinates.extend((x, y))

        connection_manager.paint_stroke_chunk(
            self._image_id,
            self._layer_id,
            self._stroke_id,
            coordinates,
        )
        self._last_sent_point = points[-1]
        self._last_stream = time.monotonic()
        self._dirty_since_refresh = True
        return True

    def _end_open_stroke(self, context):
        if not getattr(self, "_stroke_id", ""):
            return
        stroke_id = self._stroke_id
        try:
            self._flush_pending_points(context, force=True)
        except Exception as exc:
            print(f"BLENDGIMP: 2D final stroke chunk recovered from error: {exc}")
        self._stroke_id = ""
        self._painting = False
        self._pending_points.clear()
        try:
            connection_manager.end_paint_stroke(stroke_id)
        except Exception as exc:
            print(f"BLENDGIMP: 2D stroke end recovered from error: {exc}")
        self._refresh_live(context, force=True)

    def _finish(self, context, text="Texture Paint off"):
        self._end_open_stroke(context)
        self._remove_timer(context)
        scene = context.scene
        scene.blendgimp_2d_paint_active = False
        scene.blendgimp_2d_paint_layer_id = -1
        self._set_status(context, text)
        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass

    def _refresh_live(self, context, force=False):
        now = time.monotonic()
        if not force and now - self._last_refresh < LIVE_REFRESH_INTERVAL:
            return False
        self._last_refresh = now
        try:
            main_panel.synchronize_gimp_composite(
                context,
                self._image_id,
                assign_material=False,
                dirty_only=True,
            )
            self._dirty_since_refresh = False
            return True
        except Exception as exc:
            print(f"BLENDGIMP: 2D live texture refresh recovered from error: {exc}")
            return False

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

    def _begin_stroke(self, context, point):
        self._stroke_id = uuid.uuid4().hex
        tool = str(context.scene.blendgimp_paint_tool)
        connection_manager.begin_paint_stroke(
            self._image_id,
            self._layer_id,
            self._stroke_id,
            tool=tool,
        )
        print(
            "BLENDGIMP: 2D GIMP stroke started "
            f"tool={tool} image ID {self._image_id} layer ID {self._layer_id}"
        )
        self._painting = True
        self._pending_points.clear()
        self._last_sent_point = None
        self._last_stream = 0.0
        # Keep mouse events lightweight. The timer sends this click/dab and
        # subsequent motion samples as one buffered packet.
        self._queue_point(point, force_duplicate=True)
        self._set_status(context, f"Painting with GIMP {_tool_label(tool)}")

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
            layer_response = connection_manager.resolve_active_raster_layer(
                scene.blendgimp_texture_editor_image_id,
                BRUSH_FALLBACK_LAYER_NAME,
                create_if_missing=True,
            )
            if bool(layer_response.get("created", False)):
                connection_manager.consume_dirty_baseline(scene.blendgimp_texture_editor_image_id)

            if scene.blendgimp_brush_state_initialized:
                _push_scene_brush_state(scene)
                state = connection_manager.get_brush_state()
            else:
                state = connection_manager.get_brush_state()
            _set_scene_from_gimp(scene, state)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not start GIMP Texture Paint: {exc}")
            return {"CANCELLED"}

        self._area = context.area
        self._window_region = window_region
        self._image_id = int(scene.blendgimp_texture_editor_image_id)
        self._layer_id = int(layer_response["layer_id"])
        self._width = int(image.size[0])
        self._height = int(image.size[1])
        self._stroke_id = ""
        self._painting = False
        self._pending_points = []
        self._last_sent_point = None
        self._last_stream = 0.0
        self._last_refresh = 0.0
        self._dirty_since_refresh = False
        self._stream_timer = context.window_manager.event_timer_add(
            STROKE_STREAM_INTERVAL,
            window=context.window,
        )

        scene.blendgimp_2d_paint_active = True
        scene.blendgimp_2d_paint_layer_id = self._layer_id
        self._set_status(
            context,
            f"Texture Paint ACTIVE — {_tool_label(scene.blendgimp_paint_tool)} • LMB paint • Esc/RMB exit",
        )

        try:
            context.window.cursor_modal_set("CROSSHAIR")
        except Exception:
            pass

        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if (
            self._area.type != "IMAGE_EDITOR"
            or not texture_editor._is_blendgimp_area(context.screen, self._area)  # noqa: SLF001
            or texture_editor._area_mode(context.screen, self._area) != texture_editor.MODE_TEXTURE  # noqa: SLF001
        ):
            self._finish(context, "Texture Paint ended — area mode changed")
            return {"FINISHED"}

        if int(getattr(context.scene, "blendgimp_texture_editor_image_id", -1)) != self._image_id:
            self._finish(context, "Texture Paint ended — active texture changed")
            return {"FINISHED"}

        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._finish(context)
            return {"FINISHED"}

        if event.type == "TIMER":
            if self._painting:
                try:
                    now = time.monotonic()

                    # Blender 5.2 TIMER events do not expose ``event.timer``.
                    # This operator owns a WindowManager timer while active, and
                    # the monotonic interval guards below make unrelated TIMER
                    # events harmless.
                    #
                    # Do at most ONE blocking GIMP request per timer event.
                    # A dirty read used to run immediately after a stroke chunk,
                    # doubling the time Blender's event loop was blocked. Alternate
                    # feedback reads with stroke sends instead; points continue to
                    # accumulate locally during a refresh tick.
                    refresh_due = (
                        self._dirty_since_refresh
                        and now - self._last_refresh >= LIVE_REFRESH_INTERVAL
                    )

                    if refresh_due:
                        self._refresh_live(context)
                    else:
                        self._flush_pending_points(context)
                except Exception as exc:
                    self._finish(context, f"Texture Paint error: {exc}")
                    self.report({"ERROR"}, str(exc))
                    return {"CANCELLED"}
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                point = self._event_image_point(event)
                if point is None:
                    return {"PASS_THROUGH"}
                try:
                    self._begin_stroke(context, point)
                except Exception as exc:
                    self._finish(context, f"Texture Paint error: {exc}")
                    self.report({"ERROR"}, str(exc))
                    return {"CANCELLED"}
                return {"RUNNING_MODAL"}

            if event.value == "RELEASE" and self._painting:
                try:
                    point = self._event_image_point(event)
                    if point is not None:
                        self._queue_point(point)
                    self._end_open_stroke(context)
                    self._set_status(
                        context,
                        f"Texture Paint ACTIVE — {_tool_label(context.scene.blendgimp_paint_tool)} • LMB paint • Esc/RMB exit",
                    )
                except Exception as exc:
                    self._finish(context, f"Texture Paint error: {exc}")
                    self.report({"ERROR"}, str(exc))
                    return {"CANCELLED"}
                return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and self._painting:
            point = self._event_image_point(event)
            if point is not None:
                self._queue_point(point)
            return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}



def _draw_tool_buttons(layout, scene):
    row = layout.row(align=True)
    for tool, label in (
        ("PAINTBRUSH", "Brush"),
        ("PENCIL", "Pencil"),
        ("ERASER", "Eraser"),
        ("AIRBRUSH", "Airbrush"),
    ):
        op = row.operator(
            "blendgimp.set_paint_tool",
            text=label,
            depress=(scene.blendgimp_paint_tool == tool),
        )
        op.tool = tool


def _draw_shared_brush(layout, context, include_start=False):
    scene = context.scene
    box = layout.box()
    header = box.row(align=True)
    header.label(text="GIMP Paint", icon="BRUSH_DATA")
    header.operator("blendgimp.refresh_brush_state", text="", icon="FILE_REFRESH")

    _draw_tool_buttons(box, scene)

    brush_row = box.row(align=True)
    brush_row.operator(
        "blendgimp.choose_brush",
        text=scene.blendgimp_brush_name or "Choose GIMP Brush",
        icon="BRUSH_DATA",
    )
    brush_row.menu("BLENDGIMP_MT_brushes", text="", icon="DOWNARROW_HLT")
    _load_saved_brush_cache(scene)
    if _BRUSH_NAMES:
        box.label(text=f"{len(_BRUSH_NAMES)} GIMP brushes loaded")

    box.prop(scene, "blendgimp_brush_size", text="Size")
    box.prop(scene, "blendgimp_brush_opacity", text="Opacity", slider=True)
    box.prop(scene, "blendgimp_brush_hardness", text="Hardness", slider=True)
    box.prop(scene, "blendgimp_brush_spacing_percent", text="Spacing %")

    advanced = box.column(align=True)
    advanced.prop(scene, "blendgimp_brush_angle", text="Angle")
    advanced.prop(scene, "blendgimp_brush_aspect_ratio", text="Aspect")

    colors = box.row(align=True)
    colors.prop(scene, "blendgimp_foreground_color", text="FG")
    colors.prop(scene, "blendgimp_background_color", text="BG")

    if scene.blendgimp_brush_dynamics_name:
        box.label(text=f"Dynamics: {scene.blendgimp_brush_dynamics_name}")
    if scene.blendgimp_brush_status:
        box.label(text=scene.blendgimp_brush_status)

    if include_start:
        box.separator()
        if scene.blendgimp_2d_paint_active:
            box.label(text="Texture Paint ACTIVE — LMB paint • Esc/RMB exit", icon="CHECKMARK")
        else:
            box.operator("blendgimp.gimp_2d_paint", text="Start GIMP Texture Paint", icon="BRUSH_DATA")
        if scene.blendgimp_2d_paint_status:
            box.label(text=scene.blendgimp_2d_paint_status)


class BLENDGIMP_PT_gimp_texture_paint(bpy.types.Panel):
    bl_label = "GIMP Paint Tools"
    bl_idname = "BLENDGIMP_PT_gimp_texture_paint"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "BlendGimp"

    @classmethod
    def poll(cls, context):
        return _paint_area_poll(context, texture_editor.MODE_TEXTURE)

    def draw(self, context):
        _draw_shared_brush(self.layout, context, include_start=True)


class BLENDGIMP_PT_gimp_object_brush(bpy.types.Panel):
    bl_label = "Shared GIMP Brush"
    bl_idname = "BLENDGIMP_PT_gimp_object_brush"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlendGimp"

    @classmethod
    def poll(cls, context):
        return _paint_area_poll(context, texture_editor.MODE_OBJECT)

    def draw(self, context):
        _draw_shared_brush(self.layout, context, include_start=False)
        self.layout.label(text="The same tool/brush state is used by Object Paint.", icon="LINKED")


classes = (
    BLENDGIMP_OT_refresh_brush_state,
    BLENDGIMP_OT_select_brush,
    BLENDGIMP_OT_choose_brush,
    BLENDGIMP_MT_brushes,
    BLENDGIMP_OT_set_paint_tool,
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
        ),
        default="PAINTBRUSH",
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

    print("BLENDGIMP: Phase 6.2.3 GIMP paint tools registered")


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
        "blendgimp_2d_paint_status",
        "blendgimp_2d_paint_layer_id",
        "blendgimp_2d_paint_active",
        "blendgimp_brush_names_json",
        "blendgimp_brush_status",
        "blendgimp_brush_state_initialized",
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

    print("BLENDGIMP: Phase 6.2.3 GIMP paint tools unregistered")
