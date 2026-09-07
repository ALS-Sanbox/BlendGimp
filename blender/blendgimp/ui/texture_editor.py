"""BlendGimp Phase 6.1 — Blender-native BlendGimp Area foundation.

Phase 6.1 deliberately stays Blender-side.  It creates a Blender-native artist
surface using existing editor spaces rather than creating/duplicating a
workspace or pretending Python can register a new Blender SpaceType.

A BlendGimp Area has two artist-facing modes:

* Texture Paint — hosted by Blender's Image/UV Editor and showing the active
  synchronized GIMP-backed image, checkerboard alpha, native navigation, and
  UV overlays.
* Object Paint — hosted by Blender's 3D Viewport and exposing the existing
  direct GIMP 3D paint system.

The area marker is stored on the current Screen by area index, so custom
workspaces can contain independent BlendGimp areas and the marker survives
saving/reopening the .blend.  No GIMP protocol, lifecycle, Auto Sync, dirty
region, projection, or stroke-streaming ownership lives in this module.
"""

import bpy


MODE_TEXTURE = "TEXTURE"
MODE_OBJECT = "OBJECT"
TEXTURE_EDITOR_POLL_INTERVAL = 0.35

_SCREEN_AREAS_KEY = "blendgimp_area_indices"
_SCREEN_MODE_PREFIX = "blendgimp_area_mode_"
_SCREEN_PREV_TYPE_PREFIX = "blendgimp_area_prev_type_"
_SCREEN_PREV_UI_TYPE_PREFIX = "blendgimp_area_prev_ui_type_"


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------


def _set_if_present(target, property_name, value):
    if target is None or not hasattr(target, property_name):
        return False
    try:
        setattr(target, property_name, value)
        return True
    except (AttributeError, TypeError, ValueError):
        return False


def _screen_area_index(screen, area):
    if screen is None or area is None:
        return -1

    try:
        target_ptr = area.as_pointer()
    except Exception:
        target_ptr = None

    for index, candidate in enumerate(screen.areas):
        if candidate is area:
            return index
        if target_ptr is not None:
            try:
                if candidate.as_pointer() == target_ptr:
                    return index
            except Exception:
                pass
    return -1


def _screen_area_indices(screen):
    if screen is None:
        return set()
    try:
        raw = screen.get(_SCREEN_AREAS_KEY, [])
        return {int(value) for value in raw}
    except Exception:
        return set()


def _write_screen_area_indices(screen, indices):
    if screen is None:
        return
    clean = sorted({int(value) for value in indices if int(value) >= 0})
    try:
        if clean:
            screen[_SCREEN_AREAS_KEY] = clean
        elif _SCREEN_AREAS_KEY in screen:
            del screen[_SCREEN_AREAS_KEY]
    except Exception:
        pass


def _area_mode(screen, area):
    index = _screen_area_index(screen, area)
    if index < 0:
        return ""
    try:
        mode = str(screen.get(f"{_SCREEN_MODE_PREFIX}{index}", ""))
    except Exception:
        mode = ""
    return mode if mode in {MODE_TEXTURE, MODE_OBJECT} else ""


def _set_area_mode(screen, area, mode):
    index = _screen_area_index(screen, area)
    if index < 0 or mode not in {MODE_TEXTURE, MODE_OBJECT}:
        return
    try:
        screen[f"{_SCREEN_MODE_PREFIX}{index}"] = mode
    except Exception:
        pass


def _is_blendgimp_area(screen, area):
    index = _screen_area_index(screen, area)
    return index >= 0 and index in _screen_area_indices(screen)


def _remember_area_host(screen, area):
    index = _screen_area_index(screen, area)
    if index < 0:
        return
    try:
        previous_key = f"{_SCREEN_PREV_TYPE_PREFIX}{index}"
        if previous_key not in screen:
            screen[previous_key] = str(area.type)
        ui_key = f"{_SCREEN_PREV_UI_TYPE_PREFIX}{index}"
        if ui_key not in screen:
            screen[ui_key] = str(getattr(area, "ui_type", "") or "")
    except Exception:
        pass


def _enable_blendgimp_area(screen, area, mode):
    index = _screen_area_index(screen, area)
    if index < 0:
        return False

    _remember_area_host(screen, area)
    indices = _screen_area_indices(screen)
    indices.add(index)
    _write_screen_area_indices(screen, indices)
    _set_area_mode(screen, area, mode)
    return True


def _disable_blendgimp_area(screen, area, restore=True):
    index = _screen_area_index(screen, area)
    if index < 0:
        return

    indices = _screen_area_indices(screen)
    indices.discard(index)
    _write_screen_area_indices(screen, indices)

    mode_key = f"{_SCREEN_MODE_PREFIX}{index}"
    prev_key = f"{_SCREEN_PREV_TYPE_PREFIX}{index}"
    prev_ui_key = f"{_SCREEN_PREV_UI_TYPE_PREFIX}{index}"

    previous_type = ""
    previous_ui_type = ""
    try:
        previous_type = str(screen.get(prev_key, "") or "")
        previous_ui_type = str(screen.get(prev_ui_key, "") or "")
    except Exception:
        pass

    for key in (mode_key, prev_key, prev_ui_key):
        try:
            if key in screen:
                del screen[key]
        except Exception:
            pass

    if not restore:
        return

    if previous_type and previous_type not in {"TOPBAR", "STATUSBAR"}:
        try:
            area.type = previous_type
            if previous_ui_type:
                try:
                    area.ui_type = previous_ui_type
                except Exception:
                    pass
        except (AttributeError, TypeError, ValueError):
            pass


def _context_is_blendgimp_area(context, mode=None):
    screen = getattr(context, "screen", None)
    area = getattr(context, "area", None)
    if not _is_blendgimp_area(screen, area):
        return False
    if mode is None:
        return True
    return _area_mode(screen, area) == mode


# -----------------------------------------------------------------------------
# BlendGimp image/session helpers
# -----------------------------------------------------------------------------


def _image_gimp_id(image):
    if image is None:
        return -1
    try:
        return int(image.get("blendgimp_gimp_image_id", -1))
    except (TypeError, ValueError):
        return -1


def _find_blendgimp_image(image_id):
    try:
        image_id = int(image_id)
    except (TypeError, ValueError):
        return None
    if image_id < 0:
        return None

    for image in bpy.data.images:
        if _image_gimp_id(image) == image_id:
            return image
    return None


def _blendgimp_images():
    images = []
    for image in bpy.data.images:
        image_id = _image_gimp_id(image)
        if image_id >= 0:
            images.append((image_id, image))
    images.sort(key=lambda item: (item[0], item[1].name.lower()))
    return images


def _active_material_image_id(context):
    obj = getattr(context, "active_object", None)
    if obj is None:
        return -1

    material = getattr(obj, "active_material", None)
    if material is None:
        return -1

    try:
        material_image_id = int(material.get("blendgimp_gimp_image_id", -1))
    except (TypeError, ValueError):
        material_image_id = -1

    if material_image_id >= 0 and _find_blendgimp_image(material_image_id):
        return material_image_id

    node_tree = getattr(material, "node_tree", None)
    nodes = getattr(node_tree, "nodes", None)
    if nodes is None:
        return -1

    active_node = getattr(nodes, "active", None)
    candidates = []
    if active_node is not None:
        candidates.append(active_node)
    candidates.extend(node for node in nodes if node is not active_node)

    for node in candidates:
        image_id = _image_gimp_id(getattr(node, "image", None))
        if image_id >= 0:
            return image_id
    return -1


def _existing_scene_image_id(scene, property_name):
    if not hasattr(scene, property_name):
        return -1
    try:
        image_id = int(getattr(scene, property_name))
    except (TypeError, ValueError):
        return -1
    if image_id >= 0 and _find_blendgimp_image(image_id) is not None:
        return image_id
    return -1


def _resolve_follow_image_id(context):
    """Resolve the shared artist-facing BlendGimp texture without taking ownership."""
    scene = context.scene

    for property_name in (
        "blendgimp_auto_sync_image_id",
        "blendgimp_blender_paint_sync_image_id",
        "blendgimp_direct_paint_image_id",
    ):
        image_id = _existing_scene_image_id(scene, property_name)
        if image_id >= 0:
            return image_id

    image_id = _active_material_image_id(context)
    if image_id >= 0:
        return image_id

    image_id = _existing_scene_image_id(scene, "blendgimp_texture_editor_image_id")
    if image_id >= 0:
        return image_id

    images = _blendgimp_images()
    return images[0][0] if images else -1


def _set_active_texture(scene, image_id):
    try:
        image_id = int(image_id)
    except (TypeError, ValueError):
        image_id = -1

    image = _find_blendgimp_image(image_id)
    if image is None:
        image_id = -1

    scene.blendgimp_texture_editor_image_id = image_id
    scene.blendgimp_texture_editor_image_name = image.name if image else ""
    return image


# -----------------------------------------------------------------------------
# BlendGimp Area host configuration
# -----------------------------------------------------------------------------


def _apply_uv_overlay_to_space(space, scene):
    if space is None:
        return

    _set_if_present(space, "display_channels", "COLOR_ALPHA")
    _set_if_present(space, "use_realtime_update", True)

    overlay = getattr(space, "overlay", None)
    _set_if_present(overlay, "show_overlays", True)

    uv_editor = getattr(space, "uv_editor", None)
    if uv_editor is None:
        return

    show_uv = bool(
        scene.blendgimp_texture_editor_show_uv
        and scene.blendgimp_texture_editor_show_islands
    )
    opacity = float(scene.blendgimp_texture_editor_uv_opacity)

    _set_if_present(uv_editor, "show_uv", show_uv)
    _set_if_present(uv_editor, "edge_display_type", "OUTLINE")
    _set_if_present(uv_editor, "uv_opacity", opacity)
    _set_if_present(uv_editor, "uv_edge_opacity", opacity)
    _set_if_present(uv_editor, "uv_face_opacity", min(opacity * 0.30, 1.0))
    _set_if_present(
        uv_editor,
        "show_faces",
        bool(scene.blendgimp_texture_editor_active_face_highlight and show_uv),
    )


def _configure_texture_area(area, scene, image=None):
    if area is None:
        return
    if area.type != "IMAGE_EDITOR":
        area.type = "IMAGE_EDITOR"

    space = area.spaces.active
    if image is not None and getattr(space, "image", None) is not image:
        try:
            space.image = image
        except (AttributeError, TypeError):
            pass

    # UV mode is used as the Phase 6.1 host because it supplies Blender's native
    # mesh UV overlay and edit-mode face highlighting. GIMP painting itself is
    # intentionally not routed through Blender's native image-paint engine.
    _set_if_present(space, "mode", "UV")
    _set_if_present(space, "show_region_ui", True)
    _set_if_present(space, "show_region_toolbar", False)
    _apply_uv_overlay_to_space(space, scene)
    area.tag_redraw()


def _configure_object_area(area, _scene):
    if area is None:
        return
    if area.type != "VIEW_3D":
        area.type = "VIEW_3D"

    space = area.spaces.active
    _set_if_present(space, "show_region_ui", True)
    area.tag_redraw()


def _switch_area_mode(context, mode):
    area = context.area
    screen = context.screen
    scene = context.scene

    if area is None or screen is None:
        return False

    if not _is_blendgimp_area(screen, area):
        if not _enable_blendgimp_area(screen, area, mode):
            return False
    else:
        _set_area_mode(screen, area, mode)

    image = _find_blendgimp_image(scene.blendgimp_texture_editor_image_id)
    if scene.blendgimp_texture_editor_follow_active:
        image = _set_active_texture(scene, _resolve_follow_image_id(context))

    if mode == MODE_TEXTURE:
        _configure_texture_area(area, scene, image=image)
        scene.blendgimp_texture_editor_status = (
            f"Texture Paint — {image.name}" if image else "Texture Paint — no active texture"
        )
    else:
        _configure_object_area(area, scene)
        scene.blendgimp_texture_editor_status = (
            f"Object Paint — {image.name}" if image else "Object Paint — no active texture"
        )

    scene.blendgimp_area_last_mode = mode
    return True


def _iter_enabled_texture_areas():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return

    seen_screens = set()
    for window in wm.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        try:
            screen_ptr = screen.as_pointer()
        except Exception:
            screen_ptr = id(screen)
        if screen_ptr in seen_screens:
            continue
        seen_screens.add(screen_ptr)

        enabled = _screen_area_indices(screen)
        for index, area in enumerate(screen.areas):
            if index not in enabled:
                continue
            if _area_mode(screen, area) != MODE_TEXTURE:
                continue
            if area.type != "IMAGE_EDITOR":
                continue
            yield area


def _sync_texture_areas(context):
    scene = context.scene
    image = _find_blendgimp_image(scene.blendgimp_texture_editor_image_id)
    for area in _iter_enabled_texture_areas():
        _configure_texture_area(area, scene, image=image)
    return image


def _texture_editor_property_update(_self, context):
    if context is None or getattr(context, "scene", None) is None:
        return
    _sync_texture_areas(context)


def _follow_property_update(_self, context):
    if context is None or getattr(context, "scene", None) is None:
        return
    if context.scene.blendgimp_texture_editor_follow_active:
        _set_active_texture(context.scene, _resolve_follow_image_id(context))
    _sync_texture_areas(context)


def blendgimp_texture_editor_timer():
    """Follow Blender-side active texture state without touching GIMP/IPC."""
    try:
        context = bpy.context
        scene = getattr(context, "scene", None)
        if scene is None or not hasattr(scene, "blendgimp_texture_editor_follow_active"):
            return TEXTURE_EDITOR_POLL_INTERVAL

        if scene.blendgimp_texture_editor_follow_active:
            image_id = _resolve_follow_image_id(context)
            if image_id != scene.blendgimp_texture_editor_image_id:
                _set_active_texture(scene, image_id)

        _sync_texture_areas(context)
    except Exception as exc:
        print(f"BLENDGIMP: Phase 6.1 area timer recovered from error: {exc}")
    return TEXTURE_EDITOR_POLL_INTERVAL


# -----------------------------------------------------------------------------
# Operators
# -----------------------------------------------------------------------------


class BLENDGIMP_OT_use_area(bpy.types.Operator):
    bl_idname = "blendgimp.use_area"
    bl_label = "Use Area as BlendGimp"
    bl_description = "Turn the current Blender area into a BlendGimp painting area"
    bl_options = {"REGISTER"}

    mode: bpy.props.EnumProperty(
        name="BlendGimp Mode",
        items=(
            (MODE_TEXTURE, "Texture Paint", "2D GIMP-backed texture canvas", "IMAGE_DATA", 0),
            (MODE_OBJECT, "Object Paint", "Paint on the 3D object through GIMP", "OBJECT_DATA", 1),
        ),
        default=MODE_TEXTURE,
    )

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type not in {"TOPBAR", "STATUSBAR"}

    def execute(self, context):
        if not _switch_area_mode(context, self.mode):
            self.report({"ERROR"}, "Could not initialize this area for BlendGimp")
            return {"CANCELLED"}

        label = "Texture Paint" if self.mode == MODE_TEXTURE else "Object Paint"
        self.report({"INFO"}, f"BlendGimp Area: {label}")
        return {"FINISHED"}


class BLENDGIMP_OT_switch_area_mode(bpy.types.Operator):
    bl_idname = "blendgimp.switch_area_mode"
    bl_label = "Switch BlendGimp Mode"
    bl_description = "Switch this BlendGimp area between Texture Paint and Object Paint"

    mode: bpy.props.EnumProperty(
        items=(
            (MODE_TEXTURE, "Texture Paint", "2D GIMP-backed texture painting"),
            (MODE_OBJECT, "Object Paint", "3D object surface painting"),
        ),
        default=MODE_TEXTURE,
    )

    @classmethod
    def poll(cls, context):
        return _context_is_blendgimp_area(context)

    def execute(self, context):
        if not _switch_area_mode(context, self.mode):
            return {"CANCELLED"}
        return {"FINISHED"}


class BLENDGIMP_OT_disable_area(bpy.types.Operator):
    bl_idname = "blendgimp.disable_area"
    bl_label = "Disable BlendGimp Area"
    bl_description = "Return this area to the editor type it used before BlendGimp"

    @classmethod
    def poll(cls, context):
        return _context_is_blendgimp_area(context)

    def execute(self, context):
        _disable_blendgimp_area(context.screen, context.area, restore=True)
        self.report({"INFO"}, "BlendGimp Area disabled")
        return {"FINISHED"}


class BLENDGIMP_OT_texture_editor_refresh(bpy.types.Operator):
    bl_idname = "blendgimp.texture_editor_refresh"
    bl_label = "Refresh Active Texture"
    bl_description = "Refresh which synchronized BlendGimp image the area follows"

    def execute(self, context):
        scene = context.scene
        if scene.blendgimp_texture_editor_follow_active:
            image = _set_active_texture(scene, _resolve_follow_image_id(context))
        else:
            image = _find_blendgimp_image(scene.blendgimp_texture_editor_image_id)
        _sync_texture_areas(context)

        if image is None:
            scene.blendgimp_texture_editor_status = "No synchronized BlendGimp texture found"
            self.report({"WARNING"}, "No synchronized BlendGimp texture found")
        else:
            scene.blendgimp_texture_editor_status = f"Active Texture — {image.name}"
            self.report({"INFO"}, f"BlendGimp Texture: {image.name}")
        return {"FINISHED"}


class BLENDGIMP_OT_texture_editor_set_image(bpy.types.Operator):
    bl_idname = "blendgimp.texture_editor_set_image"
    bl_label = "Use BlendGimp Texture"

    image_id: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        image = _set_active_texture(context.scene, self.image_id)
        if image is None:
            self.report({"ERROR"}, "BlendGimp texture is no longer available")
            return {"CANCELLED"}

        context.scene.blendgimp_texture_editor_follow_active = False
        context.scene.blendgimp_texture_editor_status = f"Pinned Texture — {image.name}"
        _sync_texture_areas(context)
        return {"FINISHED"}


class BLENDGIMP_OT_texture_view_fit(bpy.types.Operator):
    bl_idname = "blendgimp.texture_view_fit"
    bl_label = "Fit"
    bl_description = "Fit the complete texture into this BlendGimp canvas"

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "IMAGE_EDITOR"

    def execute(self, _context):
        try:
            bpy.ops.image.view_all(fit_view=True)
        except TypeError:
            bpy.ops.image.view_all()
        return {"FINISHED"}


class BLENDGIMP_OT_texture_view_100(bpy.types.Operator):
    bl_idname = "blendgimp.texture_view_100"
    bl_label = "100%"
    bl_description = "Show one texture pixel per screen pixel"

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "IMAGE_EDITOR"

    def execute(self, _context):
        bpy.ops.image.view_zoom_ratio(ratio=1.0)
        return {"FINISHED"}


# -----------------------------------------------------------------------------
# UI helpers
# -----------------------------------------------------------------------------


def _draw_mode_switch(layout, context, compact=False):
    screen = context.screen
    area = context.area
    current = _area_mode(screen, area)

    row = layout.row(align=True)
    row.operator_context = "INVOKE_DEFAULT"

    op = row.operator(
        "blendgimp.switch_area_mode",
        text="Texture Paint",
        icon="IMAGE_DATA",
        depress=(current == MODE_TEXTURE),
    )
    op.mode = MODE_TEXTURE

    op = row.operator(
        "blendgimp.switch_area_mode",
        text="Object Paint",
        icon="OBJECT_DATA",
        depress=(current == MODE_OBJECT),
    )
    op.mode = MODE_OBJECT

    if compact:
        row.operator("blendgimp.disable_area", text="", icon="X")


def _draw_active_texture(layout, context, show_manual=True):
    scene = context.scene
    image = _find_blendgimp_image(scene.blendgimp_texture_editor_image_id)

    box = layout.box()
    header = box.row(align=True)
    header.label(text="Active Texture", icon="IMAGE_DATA")
    header.operator("blendgimp.texture_editor_refresh", text="", icon="FILE_REFRESH")

    if image is None:
        box.label(text="No synchronized BlendGimp texture", icon="INFO")
    else:
        box.label(text=image.name)
        box.label(
            text=(
                f"{image.size[0]} × {image.size[1]}  •  "
                f"GIMP ID {scene.blendgimp_texture_editor_image_id}"
            )
        )

    box.prop(scene, "blendgimp_texture_editor_follow_active", text="Follow Active Texture")

    if show_manual and not scene.blendgimp_texture_editor_follow_active:
        images = _blendgimp_images()
        if images:
            manual = box.box()
            manual.label(text="BlendGimp Textures")
            for image_id, candidate in images:
                op = manual.operator(
                    "blendgimp.texture_editor_set_image",
                    text=candidate.name,
                    icon="IMAGE_DATA",
                    depress=(image_id == scene.blendgimp_texture_editor_image_id),
                )
                op.image_id = image_id

    return image


def _draw_uv_controls(layout, context):
    scene = context.scene
    box = layout.box()
    box.label(text="UV Overlay", icon="UV")
    box.prop(scene, "blendgimp_texture_editor_show_uv", text="Show UV Overlay")

    col = box.column()
    col.enabled = scene.blendgimp_texture_editor_show_uv
    col.prop(scene, "blendgimp_texture_editor_uv_opacity", text="Opacity", slider=True)
    col.prop(scene, "blendgimp_texture_editor_show_islands", text="Show UV Islands")
    col.prop(
        scene,
        "blendgimp_texture_editor_active_face_highlight",
        text="Selected Face Highlight",
    )

    obj = context.active_object
    if obj is None or obj.type != "MESH":
        box.label(text="Select a mesh to display UVs", icon="INFO")
    elif len(obj.data.uv_layers) == 0:
        box.label(text="Active mesh has no UV map", icon="ERROR")
    else:
        uv_layer = obj.data.uv_layers.active
        box.label(text=f"UV Map: {uv_layer.name if uv_layer else '[None]'}")
        if scene.blendgimp_texture_editor_active_face_highlight:
            box.label(text="Face highlight follows Edit Mode selection")


def _draw_object_paint_controls(layout, context, image):
    scene = context.scene

    paint = layout.box()
    paint.label(text="Object Paint", icon="BRUSH_DATA")

    if image is None:
        paint.label(text="Create or synchronize a texture first", icon="INFO")
        return

    image_id = scene.blendgimp_texture_editor_image_id
    direct_active = bool(
        hasattr(scene, "blendgimp_direct_paint_active")
        and scene.blendgimp_direct_paint_active
        and int(getattr(scene, "blendgimp_direct_paint_image_id", -1)) == image_id
    )

    if direct_active:
        paint.label(text="Painting ACTIVE — LMB paint • Esc/RMB exit", icon="CHECKMARK")
    elif hasattr(bpy.types, "BLENDGIMP_OT_direct_gimp_brush_paint"):
        op = paint.operator(
            "blendgimp.direct_gimp_brush_paint",
            text="Start Object Paint",
            icon="BRUSH_DATA",
        )
        op.image_id = int(image_id)
        op.image_width = int(image.size[0])
        op.image_height = int(image.size[1])
    else:
        paint.label(text="3D paint operator unavailable — restart Blender", icon="ERROR")

    brush_name = str(getattr(scene, "blendgimp_direct_paint_brush", "") or "")
    if brush_name:
        paint.label(text=f"GIMP Brush: {brush_name}")

    protection = layout.box()
    protection.label(text="Surface Projection", icon="MODIFIER")

    if hasattr(scene, "blendgimp_direct_paint_projection_mesh"):
        protection.prop(scene, "blendgimp_direct_paint_projection_mesh", text="Projection Mesh")
    if hasattr(scene, "blendgimp_direct_paint_occlusion_mode"):
        protection.prop(scene, "blendgimp_direct_paint_occlusion_mode", text="Surface Mode")
    if hasattr(scene, "blendgimp_direct_paint_footprint_protection"):
        protection.prop(
            scene,
            "blendgimp_direct_paint_footprint_protection",
            text="Protect Brush Footprint",
        )
    if hasattr(scene, "blendgimp_direct_paint_front_faces_only"):
        protection.prop(scene, "blendgimp_direct_paint_front_faces_only", text="Front Faces Only")

    protection.label(text="Uses existing modifier-aware/seam-safe Phase 4 projection")


# -----------------------------------------------------------------------------
# Header additions
# -----------------------------------------------------------------------------


def _draw_blendgimp_header(self, context):
    area = getattr(context, "area", None)
    screen = getattr(context, "screen", None)
    if area is None or screen is None:
        return

    layout = self.layout

    if not _is_blendgimp_area(screen, area):
        if getattr(context.scene, "blendgimp_show_area_header_launcher", True):
            layout.separator()
            op = layout.operator(
                "blendgimp.use_area",
                text="BlendGimp",
                icon="BRUSH_DATA",
            )
            op.mode = MODE_TEXTURE if area.type == "IMAGE_EDITOR" else MODE_OBJECT
        return

    layout.separator()
    layout.label(text="BlendGimp", icon="BRUSH_DATA")
    _draw_mode_switch(layout, context, compact=True)


# -----------------------------------------------------------------------------
# Panels — launchers
# -----------------------------------------------------------------------------


class _BLENDGIMP_PT_area_launcher_base:
    bl_label = "BlendGimp Area"
    bl_region_type = "UI"
    bl_category = "BlendGimp"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return not _context_is_blendgimp_area(context)

    def draw(self, _context):
        layout = self.layout
        layout.label(text="Use this editor area for BlendGimp.")
        row = layout.row(align=True)
        op = row.operator("blendgimp.use_area", text="Texture Paint", icon="IMAGE_DATA")
        op.mode = MODE_TEXTURE
        op = row.operator("blendgimp.use_area", text="Object Paint", icon="OBJECT_DATA")
        op.mode = MODE_OBJECT
        layout.label(text="Tip: F3 → 'Use Area as BlendGimp' works from other editors.")


class BLENDGIMP_PT_area_launcher_image(_BLENDGIMP_PT_area_launcher_base, bpy.types.Panel):
    bl_idname = "BLENDGIMP_PT_area_launcher_image"
    bl_space_type = "IMAGE_EDITOR"


class BLENDGIMP_PT_area_launcher_view3d(_BLENDGIMP_PT_area_launcher_base, bpy.types.Panel):
    bl_idname = "BLENDGIMP_PT_area_launcher_view3d"
    bl_space_type = "VIEW_3D"


# -----------------------------------------------------------------------------
# Panels — Texture Paint mode
# -----------------------------------------------------------------------------


class BLENDGIMP_PT_texture_area(bpy.types.Panel):
    bl_label = "BlendGimp"
    bl_idname = "BLENDGIMP_PT_texture_area"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "BlendGimp"

    @classmethod
    def poll(cls, context):
        return _context_is_blendgimp_area(context, MODE_TEXTURE)

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        _draw_mode_switch(layout, context)
        layout.separator()
        _draw_active_texture(layout, context)

        nav = layout.box()
        nav.label(text="Texture Canvas", icon="IMAGE_DATA")
        row = nav.row(align=True)
        row.operator("blendgimp.texture_view_fit", text="Fit")
        row.operator("blendgimp.texture_view_100", text="100%")
        nav.label(text="MMB Pan  •  Wheel Zoom")
        nav.label(text="RGBA checkerboard transparency")

        _draw_uv_controls(layout, context)

        info = layout.box()
        info.label(text="Phase 6.1 Canvas Foundation", icon="CHECKMARK")
        info.label(text="GIMP paint tools are available in the panel below")
        if scene.blendgimp_texture_editor_status:
            info.label(text=scene.blendgimp_texture_editor_status)

        layout.operator("blendgimp.disable_area", text="Return Area to Previous Editor", icon="BACK")


# -----------------------------------------------------------------------------
# Panels — Object Paint mode
# -----------------------------------------------------------------------------


class BLENDGIMP_PT_object_area(bpy.types.Panel):
    bl_label = "BlendGimp Area"
    bl_idname = "BLENDGIMP_PT_object_area"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlendGimp"

    @classmethod
    def poll(cls, context):
        return _context_is_blendgimp_area(context, MODE_OBJECT)

    def draw(self, context):
        layout = self.layout
        _draw_mode_switch(layout, context)
        layout.separator()
        image = _draw_active_texture(layout, context)
        _draw_object_paint_controls(layout, context, image)

        info = layout.box()
        info.label(text="Shared BlendGimp Area", icon="LINKED")
        info.label(text="Switch to Texture Paint without changing workspace")
        info.label(text="Active texture is shared between both modes")

        layout.operator("blendgimp.disable_area", text="Return Area to Previous Editor", icon="BACK")


classes = (
    BLENDGIMP_OT_use_area,
    BLENDGIMP_OT_switch_area_mode,
    BLENDGIMP_OT_disable_area,
    BLENDGIMP_OT_texture_editor_refresh,
    BLENDGIMP_OT_texture_editor_set_image,
    BLENDGIMP_OT_texture_view_fit,
    BLENDGIMP_OT_texture_view_100,
    BLENDGIMP_PT_area_launcher_image,
    BLENDGIMP_PT_area_launcher_view3d,
    BLENDGIMP_PT_texture_area,
    BLENDGIMP_PT_object_area,
)


# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.blendgimp_texture_editor_image_id = bpy.props.IntProperty(
        name="Active BlendGimp Texture ID",
        default=-1,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_texture_editor_image_name = bpy.props.StringProperty(
        name="Active BlendGimp Texture",
        default="",
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_texture_editor_follow_active = bpy.props.BoolProperty(
        name="Follow Active Texture",
        description=(
            "Follow the image owned by BlendGimp Auto Sync, 3D Paint Sync, "
            "direct paint, or the active material"
        ),
        default=True,
        update=_follow_property_update,
    )
    bpy.types.Scene.blendgimp_texture_editor_show_uv = bpy.props.BoolProperty(
        name="Show UV Overlay",
        default=True,
        update=_texture_editor_property_update,
    )
    bpy.types.Scene.blendgimp_texture_editor_uv_opacity = bpy.props.FloatProperty(
        name="UV Overlay Opacity",
        default=0.85,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_texture_editor_property_update,
    )
    bpy.types.Scene.blendgimp_texture_editor_show_islands = bpy.props.BoolProperty(
        name="Show UV Islands",
        description="Show or hide the active mesh UV islands over the texture",
        default=True,
        update=_texture_editor_property_update,
    )
    bpy.types.Scene.blendgimp_texture_editor_active_face_highlight = bpy.props.BoolProperty(
        name="Selected Face Highlight",
        description="Use Blender's native UV face fill for selected mesh faces",
        default=True,
        update=_texture_editor_property_update,
    )
    bpy.types.Scene.blendgimp_texture_editor_status = bpy.props.StringProperty(
        name="BlendGimp Area Status",
        default="",
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_area_last_mode = bpy.props.EnumProperty(
        name="BlendGimp Area Mode",
        items=(
            (MODE_TEXTURE, "Texture Paint", "2D GIMP-backed texture canvas"),
            (MODE_OBJECT, "Object Paint", "3D surface painting"),
        ),
        default=MODE_TEXTURE,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_show_area_header_launcher = bpy.props.BoolProperty(
        name="Show BlendGimp Header Launcher",
        description="Show a compact BlendGimp activation button in Image Editor and 3D View headers",
        default=True,
    )

    try:
        bpy.types.IMAGE_HT_header.append(_draw_blendgimp_header)
    except Exception as exc:
        print(f"BLENDGIMP: Could not extend Image Editor header: {exc}")
    try:
        bpy.types.VIEW3D_HT_header.append(_draw_blendgimp_header)
    except Exception as exc:
        print(f"BLENDGIMP: Could not extend 3D View header: {exc}")

    if not bpy.app.timers.is_registered(blendgimp_texture_editor_timer):
        bpy.app.timers.register(
            blendgimp_texture_editor_timer,
            first_interval=TEXTURE_EDITOR_POLL_INTERVAL,
            persistent=True,
        )

    print("BLENDGIMP: Phase 6.1 BlendGimp Area registered")


def unregister():
    try:
        bpy.types.IMAGE_HT_header.remove(_draw_blendgimp_header)
    except Exception:
        pass
    try:
        bpy.types.VIEW3D_HT_header.remove(_draw_blendgimp_header)
    except Exception:
        pass

    try:
        if bpy.app.timers.is_registered(blendgimp_texture_editor_timer):
            bpy.app.timers.unregister(blendgimp_texture_editor_timer)
    except Exception:
        pass

    property_names = (
        "blendgimp_show_area_header_launcher",
        "blendgimp_area_last_mode",
        "blendgimp_texture_editor_status",
        "blendgimp_texture_editor_active_face_highlight",
        "blendgimp_texture_editor_show_islands",
        "blendgimp_texture_editor_uv_opacity",
        "blendgimp_texture_editor_show_uv",
        "blendgimp_texture_editor_follow_active",
        "blendgimp_texture_editor_image_name",
        "blendgimp_texture_editor_image_id",
    )
    for property_name in property_names:
        if hasattr(bpy.types.Scene, property_name):
            delattr(bpy.types.Scene, property_name)

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass

    print("BLENDGIMP: Phase 6.1 BlendGimp Area unregistered")
