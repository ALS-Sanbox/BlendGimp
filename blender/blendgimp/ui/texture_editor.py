"""BlendGimp native Texture/Object editor integration.

BlendGimp uses Blender's existing Image Editor and 3D View as its artist-facing
texture and object painting surfaces.  GIMP remains the authoritative raster
engine while Blender owns the viewport, UVs, materials, and interaction shell.

Native Image Editors and 3D Views are enrolled automatically for BlendGimp
routing. Internal area markers exist only as private routing-ownership metadata
so the accepted Phase 6 automatic pointer/modal architecture remains intact.
"""

import bpy
import time
import math
import json

try:
    import gpu
    from gpu_extras.batch import batch_for_shader
except Exception:  # Blender-only runtime dependency
    gpu = None
    batch_for_shader = None

from ..ipc.connection import connection_manager, set_direct_paint_refresh_owner
from ..core.build_info import DISPLAY_NAME
from . import preferences as blendgimp_preferences


MODE_TEXTURE = "TEXTURE"
MODE_OBJECT = "OBJECT"

TEXTURE_DISPLAY_CHANNEL_ITEMS = (
    ("COLOR_ALPHA", "Checkerboard", "Display RGB with alpha transparency over Blender's checkerboard"),
    ("COLOR", "RGB", "Display RGB without alpha transparency"),
    ("ALPHA", "Alpha", "Display the alpha channel only"),
)

UV_EDGE_STYLE_ITEMS = (
    ("OUTLINE", "Outline", "White UV edges with a dark outline"),
    ("DASH", "Dash", "Dashed black/white UV edges"),
    ("BLACK", "Black", "Black UV edges"),
    ("WHITE", "White", "White UV edges"),
)

TEXTURE_EDITOR_POLL_INTERVAL = 0.35
AUTO_ROUTER_RETRY_INTERVAL = 2.0
AUTO_ROUTER_FAILURE_INTERVAL = 5.0
_AUTO_ROUTER_COOLDOWNS = {}
_RUNTIME_AREA_MODES = {}
_RUNTIME_AREA_ENABLED = set()
_AREA_RELEASE_SERIALS = {}
_ROUTING_GENERATIONS = {}
_SELECTION_DRAW_HANDLER = None
_SELECTION_SHADER = None
# Drag previews are transient UI state.  Keeping per-mousemove coordinates out
# of Scene RNA avoids triggering Blender-wide UI/property work at mouse polling
# rates and is the core of the 0.5.11 selection responsiveness fix.
_SELECTION_DRAG_PREVIEW = {
    "active": False,
    "area_ptr": 0,
    "shape": "RECTANGLE",
    "operation": "REPLACE",
    "x1": 0.0,
    "y1": 0.0,
    "x2": 0.0,
    "y2": 0.0,
}
_SELECTION_FREE_PREVIEW = {
    "active": False,
    "area_ptr": 0,
    "operation": "PENDING",
    "points": [],
    "hover": None,
}
# Exact/near-exact GIMP selection-mask boundary samples for fuzzy, by-color,
# combined and morphology selections.  Keep this runtime-only so a large
# selection contour never bloats the .blend or triggers Scene RNA updates.
_SELECTION_OUTLINE_CACHE = {}
_SELECTION_PREVIEW_REDRAW_INTERVAL = 1.0 / 60.0

SELECTION_SHAPE_ITEMS = (
    ("NONE", "None", "No active GIMP selection"),
    ("RECTANGLE", "Rectangle", "Rectangular GIMP selection"),
    ("ELLIPSE", "Ellipse", "Elliptical GIMP selection"),
    ("FREE", "Free Select", "Polygonal GIMP selection"),
    ("MASK", "Mask Contour", "Actual GIMP selection-mask contour"),
    ("ALL", "All", "Entire texture selected"),
    ("BOUNDS", "Selection", "Complex selection represented by synchronized bounds"),
)

SELECTION_OPERATION_ITEMS = (
    ("REPLACE", "Replace", "Replace the current selection"),
    ("ADD", "Add", "Add the new region to the current selection"),
    ("SUBTRACT", "Subtract", "Subtract the new region from the current selection"),
    ("INTERSECT", "Intersect", "Keep only the overlap with the current selection"),
)

_SCREEN_AREAS_KEY = "blendgimp_area_indices"
_SCREEN_MODE_PREFIX = "blendgimp_area_mode_"
_SCREEN_PREV_TYPE_PREFIX = "blendgimp_area_prev_type_"
_SCREEN_PREV_UI_TYPE_PREFIX = "blendgimp_area_prev_ui_type_"
_SCREEN_ROUTING_GENERATION_PREFIX = "blendgimp_area_route_generation_"


def _reset_texture_editor_runtime_state():
    """Reset transient routing/selection caches without touching saved data."""
    _AUTO_ROUTER_COOLDOWNS.clear()
    _RUNTIME_AREA_MODES.clear()
    _RUNTIME_AREA_ENABLED.clear()
    _AREA_RELEASE_SERIALS.clear()
    _ROUTING_GENERATIONS.clear()
    _SELECTION_OUTLINE_CACHE.clear()
    _SELECTION_FREE_PREVIEW.update(
        {"active": False, "area_ptr": 0, "operation": "PENDING", "points": [], "hover": None}
    )
    _SELECTION_DRAG_PREVIEW.update(
        {
            "active": False,
            "area_ptr": 0,
            "shape": "RECTANGLE",
            "operation": "REPLACE",
            "x1": 0.0,
            "y1": 0.0,
            "x2": 0.0,
            "y2": 0.0,
        }
    )


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



def _runtime_area_key(screen, area):
    if screen is None or area is None:
        return None
    try:
        screen_ptr = int(screen.as_pointer())
    except Exception:
        screen_ptr = id(screen)
    try:
        area_ptr = int(area.as_pointer())
    except Exception:
        area_ptr = id(area)
    return (screen_ptr, area_ptr)


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


def _area_from_slot(screen, index):
    if screen is None:
        return None
    try:
        index = int(index)
        if index < 0 or index >= len(screen.areas):
            return None
        return screen.areas[index]
    except Exception:
        return None


def _routing_generation(screen, area=None, index=None):
    if screen is None:
        return 0
    if index is None:
        index = _screen_area_index(screen, area)
    try:
        index = int(index)
    except Exception:
        return 0
    if index < 0:
        return 0
    try:
        value = int(screen.get(f"{_SCREEN_ROUTING_GENERATION_PREFIX}{index}", 0))
    except Exception:
        value = 0
    if value <= 0:
        value = int(_ROUTING_GENERATIONS.get((int(screen.as_pointer()) if hasattr(screen, "as_pointer") else id(screen), index), 0))
    return max(0, value)


def _bump_routing_generation(screen, area=None, index=None):
    if screen is None:
        return 0
    if index is None:
        index = _screen_area_index(screen, area)
    try:
        index = int(index)
    except Exception:
        return 0
    if index < 0:
        return 0
    value = _routing_generation(screen, index=index) + 1
    try:
        screen[f"{_SCREEN_ROUTING_GENERATION_PREFIX}{index}"] = int(value)
    except Exception:
        pass
    try:
        screen_ptr = int(screen.as_pointer())
    except Exception:
        screen_ptr = id(screen)
    _ROUTING_GENERATIONS[(screen_ptr, index)] = int(value)
    return int(value)


def _ensure_routing_generation(screen, area=None, index=None):
    value = _routing_generation(screen, area=area, index=index)
    if value > 0:
        return value
    return _bump_routing_generation(screen, area=area, index=index)


def _routing_token(screen, area, mode):
    index = _screen_area_index(screen, area)
    if index < 0:
        return (-1, 0, str(mode))
    return (index, _ensure_routing_generation(screen, index=index), str(mode))


def _routing_token_current(screen, slot_index, generation, mode):
    try:
        slot_index = int(slot_index)
        generation = int(generation)
    except Exception:
        return False
    area = _area_from_slot(screen, slot_index)
    if area is None:
        return False
    if slot_index not in _screen_area_indices(screen):
        return False
    if _routing_generation(screen, index=slot_index) != generation:
        return False
    current_mode = _area_mode(screen, area)
    if current_mode != mode:
        return False
    if mode == MODE_TEXTURE and getattr(area, "type", "") != "IMAGE_EDITOR":
        return False
    if mode == MODE_OBJECT and getattr(area, "type", "") != "VIEW_3D":
        return False
    return True


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
    # Fix4: the Screen slot is authoritative. Blender may replace the Area RNA
    # pointer when an editor changes type (IMAGE_EDITOR <-> VIEW_3D). A stale
    # modal can still hold the old Area wrapper, so never trust runtime pointer
    # metadata unless that exact Area is still present in the current Screen.
    index = _screen_area_index(screen, area)
    if index < 0:
        return ""

    try:
        mode = str(screen.get(f"{_SCREEN_MODE_PREFIX}{index}", ""))
    except Exception:
        mode = ""

    key = _runtime_area_key(screen, area)
    if mode in {MODE_TEXTURE, MODE_OBJECT} and key is not None:
        _RUNTIME_AREA_MODES[key] = mode
        _RUNTIME_AREA_ENABLED.add(key)
        return mode

    # Runtime state is only a cache for a live area, never an authority for a
    # detached/stale Area pointer.
    runtime_mode = _RUNTIME_AREA_MODES.get(key, "") if key is not None else ""
    return runtime_mode if runtime_mode in {MODE_TEXTURE, MODE_OBJECT} else ""


def _set_area_mode(screen, area, mode):
    index = _screen_area_index(screen, area)
    if index < 0 or mode not in {MODE_TEXTURE, MODE_OBJECT}:
        return
    key = _runtime_area_key(screen, area)
    if key is not None:
        _RUNTIME_AREA_MODES[key] = mode
        _RUNTIME_AREA_ENABLED.add(key)
    try:
        screen[f"{_SCREEN_MODE_PREFIX}{index}"] = mode
    except Exception:
        pass


def _is_blendgimp_area(screen, area):
    # Fix4: reject stale Area wrappers first. This prevents a retired 2D/3D
    # modal from remaining active after Blender swaps the editor Area pointer
    # during a Texture/Object mode change.
    index = _screen_area_index(screen, area)
    if index < 0:
        return False

    enabled = index in _screen_area_indices(screen)
    key = _runtime_area_key(screen, area)
    if enabled and key is not None:
        _RUNTIME_AREA_ENABLED.add(key)
    return enabled


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
    _ensure_routing_generation(screen, area=area)
    key = _runtime_area_key(screen, area)
    if key is not None:
        _RUNTIME_AREA_ENABLED.add(key)
        _RUNTIME_AREA_MODES[key] = mode
    indices = _screen_area_indices(screen)
    indices.add(index)
    _write_screen_area_indices(screen, indices)
    _set_area_mode(screen, area, mode)
    return True


def _disable_blendgimp_area(screen, area, restore=True):
    index = _screen_area_index(screen, area)
    if index < 0:
        return

    _bump_routing_generation(screen, area=area)
    key = _runtime_area_key(screen, area)
    if key is not None:
        _RUNTIME_AREA_ENABLED.discard(key)
        _RUNTIME_AREA_MODES.pop(key, None)
        _AREA_RELEASE_SERIALS.pop(key, None)

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




def _paint_cancel_serial(scene):
    try:
        return int(scene.get("blendgimp_paint_cancel_serial", 0))
    except Exception:
        return 0


def _area_release_serial(screen, area):
    key = _runtime_area_key(screen, area)
    if key is None:
        return 0
    try:
        return int(_AREA_RELEASE_SERIALS.get(key, 0))
    except Exception:
        return 0


def request_blendgimp_paint_release(scene, reason="mode change", screen=None, area=None):
    """Invalidate a BlendGimp modal owner.

    Phase 6.3.7 Fix4 keeps normal editor-mode changes area-scoped so switching
    one BlendGimp area does not tear down routers in other BlendGimp areas.
    Calls without an area remain global for shutdown/undo/emergency release.
    """
    key = _runtime_area_key(screen, area)
    if key is not None:
        _bump_routing_generation(screen, area=area)
        serial = int(_AREA_RELEASE_SERIALS.get(key, 0)) + 1
        _AREA_RELEASE_SERIALS[key] = serial
        print(
            "BLENDGIMP: Phase 6.3.7 area-scoped paint release "
            f"serial={serial} reason={reason}"
        )
        return serial

    serial = _paint_cancel_serial(scene) + 1
    try:
        scene["blendgimp_paint_cancel_serial"] = int(serial)
    except Exception:
        pass
    if hasattr(scene, "blendgimp_2d_paint_status"):
        scene.blendgimp_2d_paint_status = f"Released — {reason}"
    if hasattr(scene, "blendgimp_direct_paint_status"):
        scene.blendgimp_direct_paint_status = f"Released — {reason}"
    return serial

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
    """Atomically bind BlendGimp editor, layer-buffer and Auto Sync ownership."""
    try:
        image_id = int(image_id)
    except (TypeError, ValueError):
        image_id = -1

    image = _find_blendgimp_image(image_id)
    if image is None:
        image_id = -1

    previous_id = int(getattr(scene, "blendgimp_texture_editor_image_id", -1))
    sync_image_id = int(getattr(scene, "blendgimp_blender_paint_sync_image_id", -1))
    runtime_rebind_needed = image_id >= 0 and sync_image_id != image_id
    switching = previous_id != image_id

    if switching or runtime_rebind_needed:
        try:
            from . import main_panel
            main_panel._retire_active_image_runtime(  # noqa: SLF001
                scene,
                reason=(
                    f"before BlendGimp texture switch {previous_id}->{image_id}"
                    if switching
                    else f"repair active image ownership for image {image_id}"
                ),
                fail_if_unsent=True,
            )
        except Exception as exc:
            print(f"BLENDGIMP: Texture switch blocked by Unified Paint Sync: {exc}")
            return _find_blendgimp_image(previous_id)

    scene.blendgimp_texture_editor_image_id = image_id
    scene.blendgimp_texture_editor_image_name = image.name if image else ""
    if switching and hasattr(scene, "blendgimp_selection_image_id"):
        # Selection state is GIMP-image-specific. Do not let an old texture's
        # overlay survive a native Active Image switch; the Selection panel's
        # Refresh action can query an existing selection on the new document.
        _clear_selection_state(scene, image_id=-1)

    if image is None:
        return None

    # Rebind even when the editor already points at this image if the reusable
    # active-layer buffer still belongs to a different image. This repairs the
    # stale image/layer combination observed when rapidly selecting textures.
    if switching or runtime_rebind_needed:
        try:
            from . import main_panel
            target = main_panel.resolve_gimp_paint_target(
                scene, image_id, create_if_missing=True
            )
            layer_id = int(target["layer_id"])
            main_panel.load_active_layer_buffer_from_gimp(
                scene, image_id, layer_id,
                clear_first=True, activate_target=True
            )
            main_panel._retarget_auto_sync_to_image(scene, image_id)  # noqa: SLF001
            print(
                "BLENDGIMP: Active Image ownership rebound "
                f"{previous_id}->{image_id} layer ID {layer_id}"
            )
        except Exception as exc:
            # Leave the image selected, but keep the reusable paint buffer
            # disarmed rather than allowing stale layer ownership to survive.
            set_direct_paint_refresh_owner(False)
            try:
                scene.blendgimp_blender_paint_sync_enabled = False
                scene.blendgimp_blender_paint_sync_image_id = -1
                scene.blendgimp_blender_paint_sync_layer_id = -1
            except Exception:
                pass
            print(f"BLENDGIMP: Active Layer Buffer texture bind warning: {exc}")

    return image


# -----------------------------------------------------------------------------
# Managed editor routing configuration
# -----------------------------------------------------------------------------


def _apply_uv_overlay_to_space(space, scene):
    if space is None:
        return

    # Phase 6.5.1: presentation is explicit instead of hard-wired to
    # COLOR_ALPHA.  This keeps Blender's native checkerboard for transparent
    # pixels while also giving artists fast RGB-only and alpha-only inspection.
    display_channels = str(
        getattr(scene, "blendgimp_texture_editor_display_channels", "COLOR_ALPHA")
        or "COLOR_ALPHA"
    )
    if display_channels not in {"COLOR_ALPHA", "COLOR", "ALPHA"}:
        display_channels = "COLOR_ALPHA"
    _set_if_present(space, "display_channels", display_channels)
    _set_if_present(space, "use_realtime_update", True)

    overlay = getattr(space, "overlay", None)
    _set_if_present(overlay, "show_overlays", True)

    uv_editor = getattr(space, "uv_editor", None)
    if uv_editor is None:
        return

    overlay_enabled = bool(scene.blendgimp_texture_editor_show_uv)
    show_islands = bool(
        overlay_enabled and scene.blendgimp_texture_editor_show_islands
    )
    show_faces = bool(
        overlay_enabled and scene.blendgimp_texture_editor_active_face_highlight
    )
    opacity = float(scene.blendgimp_texture_editor_uv_opacity)
    edge_style = str(
        getattr(scene, "blendgimp_texture_editor_uv_edge_style", "OUTLINE")
        or "OUTLINE"
    )
    if edge_style not in {"OUTLINE", "DASH", "BLACK", "WHITE"}:
        edge_style = "OUTLINE"

    # Island edges and selected-face fill are intentionally independent.
    # This lets an artist hide the full UV wire while retaining selected-face
    # context over the texture.
    _set_if_present(uv_editor, "show_uv", show_islands)
    _set_if_present(uv_editor, "edge_display_type", edge_style)
    _set_if_present(uv_editor, "uv_opacity", opacity if show_islands else 0.0)
    _set_if_present(uv_editor, "uv_edge_opacity", opacity if show_islands else 0.0)
    _set_if_present(uv_editor, "show_faces", show_faces)
    _set_if_present(
        uv_editor,
        "uv_face_opacity",
        min(max(opacity * 0.35, 0.08), 1.0) if show_faces else 0.0,
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


def _ensure_default_native_areas(context):
    """Silently enroll normal Blender editors for BlendGimp routing.

    Enroll normal Blender editors while preserving the accepted area-scoped
    modal ownership implementation. At most one Image Editor and one 3D View
    are enrolled per screen by default. Existing saved routing markers are
    respected.
    """
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return

    current_screen = getattr(context, "screen", None)
    current_area = getattr(context, "area", None)

    for window in wm.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue

        existing_modes = set()
        for area in screen.areas:
            if _is_blendgimp_area(screen, area):
                mode = _area_mode(screen, area)
                if mode in {MODE_TEXTURE, MODE_OBJECT}:
                    existing_modes.add(mode)

        for mode, area_type in ((MODE_TEXTURE, "IMAGE_EDITOR"), (MODE_OBJECT, "VIEW_3D")):
            if mode in existing_modes:
                continue

            candidate = None
            if (
                screen is current_screen
                and current_area is not None
                and getattr(current_area, "type", "") == area_type
            ):
                candidate = current_area
            if candidate is None:
                candidate = next(
                    (area for area in screen.areas if getattr(area, "type", "") == area_type),
                    None,
                )
            if candidate is None:
                continue

            if _enable_blendgimp_area(screen, candidate, mode):
                print(
                    "BLENDGIMP: Native editor auto-enrolled "
                    f"mode={mode} area={area_type}"
                )


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

        for area in screen.areas:
            if not _is_blendgimp_area(screen, area):
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


def _iter_enabled_blendgimp_areas():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for window in wm.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if not _is_blendgimp_area(screen, area):
                continue
            mode = _area_mode(screen, area)
            if mode == MODE_TEXTURE and area.type == "IMAGE_EDITOR":
                yield window, screen, area, mode
            elif mode == MODE_OBJECT and area.type == "VIEW_3D":
                yield window, screen, area, mode


def _area_window_region(area):
    if area is None:
        return None
    return next((region for region in area.regions if region.type == "WINDOW"), None)


def _router_key(window, area, mode):
    try:
        window_ptr = int(window.as_pointer())
    except Exception:
        window_ptr = id(window)
    try:
        area_ptr = int(area.as_pointer())
    except Exception:
        area_ptr = id(area)
    return (window_ptr, area_ptr, str(mode))


def _auto_pointer_routing_update(_self, context):
    if context is None or getattr(context, "scene", None) is None:
        return
    scene = context.scene
    if not bool(getattr(scene, "blendgimp_auto_pointer_routing", True)):
        request_blendgimp_paint_release(scene, "Automatic pointer routing disabled")
        scene.blendgimp_texture_editor_status = "Automatic paint routing disabled"
    else:
        scene.blendgimp_texture_editor_status = "Automatic paint routing enabled"
        _AUTO_ROUTER_COOLDOWNS.clear()


def _ensure_auto_pointer_routing(context):
    scene = getattr(context, "scene", None)
    if scene is None or not blendgimp_preferences.auto_paint_enabled(context=context, scene=scene):
        return
    if bool(getattr(scene, "blendgimp_selection_interaction_active", False)):
        return
    if not connection_manager.is_connected():
        return

    image_id = int(getattr(scene, "blendgimp_texture_editor_image_id", -1))
    image = _find_blendgimp_image(image_id)
    if image is None or image_id < 0:
        return

    now = time.monotonic()
    for window, screen, area, mode in _iter_enabled_blendgimp_areas():
        key = _router_key(window, area, mode)
        if now < float(_AUTO_ROUTER_COOLDOWNS.get(key, 0.0)):
            continue
        region = _area_window_region(area)
        if region is None:
            _AUTO_ROUTER_COOLDOWNS[key] = now + AUTO_ROUTER_FAILURE_INTERVAL
            continue

        if mode == MODE_TEXTURE:
            if bool(getattr(scene, "blendgimp_2d_paint_active", False)):
                continue
            operator_id = "Texture Paint"
        else:
            if bool(getattr(scene, "blendgimp_direct_paint_active", False)):
                continue
            operator_id = "Object Paint"

            # 6.3.7 Fix2: normalize Blender's underlying object mode only when
            # no Object Paint modal is active, then wait one routing tick before
            # creating projection data.  Changing object mode under a live
            # projection owner invalidates evaluated mesh/UV data.
            try:
                with bpy.context.temp_override(
                    window=window,
                    screen=screen,
                    area=area,
                    region=region,
                ):
                    obj = getattr(bpy.context, "active_object", None)
                    if obj is not None and getattr(obj, "type", "") == "MESH":
                        previous_object_mode = str(getattr(obj, "mode", "OBJECT"))
                        if previous_object_mode != "OBJECT":
                            bpy.ops.object.mode_set(mode="OBJECT")
                            _AUTO_ROUTER_COOLDOWNS[key] = now + AUTO_ROUTER_RETRY_INTERVAL
                            print(
                                "BLENDGIMP: Phase 6.3.7 Object Paint native mode guard "
                                f"{previous_object_mode}->OBJECT; projection arm deferred one tick"
                            )
                            continue
            except Exception as exc:
                _AUTO_ROUTER_COOLDOWNS[key] = now + AUTO_ROUTER_FAILURE_INTERVAL
                print(
                    "BLENDGIMP: Object Paint native mode guard deferred: "
                    f"{exc}"
                )
                continue

        try:
            with bpy.context.temp_override(
                window=window,
                screen=screen,
                area=area,
                region=region,
            ):
                if mode == MODE_TEXTURE:
                    result = bpy.ops.blendgimp.gimp_2d_paint("INVOKE_DEFAULT")
                else:
                    result = bpy.ops.blendgimp.direct_gimp_brush_paint(
                        "INVOKE_DEFAULT",
                        image_id=image_id,
                        image_width=int(image.size[0]),
                        image_height=int(image.size[1]),
                    )
            if "RUNNING_MODAL" in result:
                _AUTO_ROUTER_COOLDOWNS[key] = now + AUTO_ROUTER_RETRY_INTERVAL
                print(
                    "BLENDGIMP: Phase 6.3.7 automatic pointer routing armed "
                    f"mode={mode} image ID {image_id}"
                )
            else:
                _AUTO_ROUTER_COOLDOWNS[key] = now + AUTO_ROUTER_FAILURE_INTERVAL
        except Exception as exc:
            _AUTO_ROUTER_COOLDOWNS[key] = now + AUTO_ROUTER_FAILURE_INTERVAL
            print(
                "BLENDGIMP: Phase 6.3.7 auto routing start deferred "
                f"mode={operator_id}: {exc}"
            )


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

        _ensure_default_native_areas(context)
        _sync_texture_areas(context)
        _ensure_auto_pointer_routing(context)
    except Exception as exc:
        print(f"BLENDGIMP: Phase 6.1 area timer recovered from error: {exc}")
    return TEXTURE_EDITOR_POLL_INTERVAL


# -----------------------------------------------------------------------------
# Phase 7.2 selection state + overlay
# -----------------------------------------------------------------------------


def _clear_selection_state(scene, image_id=-1):
    try:
        _SELECTION_OUTLINE_CACHE.pop(int(image_id), None)
    except Exception:
        pass
    scene.blendgimp_selection_active = False
    scene.blendgimp_selection_bounds_valid = True
    scene.blendgimp_selection_image_id = int(image_id)
    scene.blendgimp_selection_x1 = 0
    scene.blendgimp_selection_y1 = 0
    scene.blendgimp_selection_x2 = 0
    scene.blendgimp_selection_y2 = 0
    scene.blendgimp_selection_shape = "NONE"
    scene.blendgimp_selection_points_json = "[]"
    scene.blendgimp_selection_inverted = False


def _apply_selection_state(scene, response, fallback_shape="BOUNDS"):
    image_id = int(response.get("image_id", -1))
    active = bool(response.get("active", False))
    scene.blendgimp_selection_image_id = image_id
    scene.blendgimp_selection_active = active
    scene.blendgimp_selection_bounds_valid = bool(response.get("bounds_valid", True))
    scene.blendgimp_selection_x1 = int(response.get("x1", 0))
    scene.blendgimp_selection_y1 = int(response.get("y1", 0))
    scene.blendgimp_selection_x2 = int(response.get("x2", 0))
    scene.blendgimp_selection_y2 = int(response.get("y2", 0))
    shape = str(response.get("shape", fallback_shape) or fallback_shape).upper()
    valid_shapes = {item[0] for item in SELECTION_SHAPE_ITEMS}
    if not active:
        shape = "NONE"
    elif shape not in valid_shapes:
        shape = "BOUNDS"
    scene.blendgimp_selection_shape = shape
    outline_values = response.get("outline_points", None)
    if active and isinstance(outline_values, (list, tuple)) and len(outline_values) >= 4:
        try:
            values = [float(v) for v in outline_values]
            if len(values) % 2 == 0:
                _SELECTION_OUTLINE_CACHE[image_id] = [
                    (values[i], values[i + 1]) for i in range(0, len(values), 2)
                ]
        except Exception:
            _SELECTION_OUTLINE_CACHE.pop(image_id, None)
    elif not active or shape != "MASK":
        _SELECTION_OUTLINE_CACHE.pop(image_id, None)

    points = response.get("points", None)
    if active and shape == "FREE" and isinstance(points, (list, tuple)) and len(points) >= 6:
        try:
            scene.blendgimp_selection_points_json = json.dumps([float(v) for v in points])
        except Exception:
            scene.blendgimp_selection_points_json = "[]"
    elif shape != "FREE":
        scene.blendgimp_selection_points_json = "[]"
    return active


def _selection_points_from_scene(scene):
    try:
        values = json.loads(str(getattr(scene, "blendgimp_selection_points_json", "[]")))
        values = [float(v) for v in values]
        if len(values) >= 6 and len(values) % 2 == 0:
            return [(values[i], values[i + 1]) for i in range(0, len(values), 2)]
    except Exception:
        pass
    return []


def _selection_image_points_to_screen(region, width, height, image_points, close=False):
    if not image_points:
        return []
    pts = list(image_points)
    if close and len(pts) >= 3 and pts[0] != pts[-1]:
        pts.append(pts[0])
    out = []
    for x, y in pts:
        u = float(x) / float(width)
        v = 1.0 - (float(y) / float(height))
        try:
            rx, ry = region.view2d.view_to_region(u, v, clip=False)
        except Exception:
            return []
        out.append((float(rx), float(ry), 0.0))
    return out


def _selection_event_image_point(context, event):
    area = getattr(context, "area", None)
    region = _area_window_region(area)
    image = getattr(getattr(area, "spaces", None), "active", None)
    image = getattr(image, "image", None)
    if region is None or image is None:
        return None
    if not (
        region.x <= event.mouse_x < region.x + region.width
        and region.y <= event.mouse_y < region.y + region.height
    ):
        return None
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
    width = max(1, int(image.size[0]))
    height = max(1, int(image.size[1]))
    return (
        max(0.0, min(float(width), u * float(width))),
        max(0.0, min(float(height), (1.0 - v) * float(height))),
    )


def _selection_outline_screen_points(region, width, height, shape, x1, y1, x2, y2):
    if region is None or width <= 0 or height <= 0:
        return []

    left = min(float(x1), float(x2))
    right = max(float(x1), float(x2))
    top = min(float(y1), float(y2))
    bottom = max(float(y1), float(y2))
    if right <= left or bottom <= top:
        return []

    if str(shape).upper() == "ELLIPSE":
        cx = (left + right) * 0.5
        cy = (top + bottom) * 0.5
        rx = (right - left) * 0.5
        ry = (bottom - top) * 0.5
        image_points = [
            (
                cx + math.cos((i / 64.0) * math.tau) * rx,
                cy + math.sin((i / 64.0) * math.tau) * ry,
            )
            for i in range(65)
        ]
    else:
        image_points = [
            (left, top), (right, top), (right, bottom), (left, bottom), (left, top)
        ]

    points = []
    for x, y in image_points:
        u = float(x) / float(width)
        v = 1.0 - (float(y) / float(height))
        try:
            rx, ry = region.view2d.view_to_region(u, v, clip=False)
        except Exception:
            return []
        points.append((float(rx), float(ry), 0.0))
    return points


def _selection_dashed_segments(points, dash_px=7.0, gap_px=5.0):
    segments = []
    if len(points) < 2:
        return segments
    period = max(1.0, float(dash_px) + float(gap_px))
    phase = 0.0
    for a, b in zip(points[:-1], points[1:]):
        ax, ay = float(a[0]), float(a[1]); bx, by = float(b[0]), float(b[1])
        dx, dy = bx - ax, by - ay; length = math.hypot(dx, dy)
        if length <= 0.001:
            continue
        pos = 0.0
        while pos < length:
            cycle = (phase + pos) % period
            if cycle < dash_px:
                run = min(dash_px - cycle, length - pos)
                p0 = pos / length; p1 = (pos + run) / length
                segments.extend([(ax + dx*p0, ay + dy*p0, 0.0), (ax + dx*p1, ay + dy*p1, 0.0)])
                pos += run
            else:
                pos += min(period - cycle, length - pos)
        phase = (phase + length) % period
    return segments

def _draw_dashed_selection_polyline(shader, points):
    segments = _selection_dashed_segments(points)
    if len(segments) < 2:
        return
    batch = batch_for_shader(shader, "LINES", {"pos": segments})
    gpu.state.line_width_set(3.0)
    shader.uniform_float("color", (0.0, 0.0, 0.0, 0.95)); batch.draw(shader)
    gpu.state.line_width_set(1.2)
    shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0)); batch.draw(shader)


def _draw_selection_mask_points(shader, points):
    """Draw unordered GIMP mask-boundary samples as a dotted outline."""
    if len(points) < 2:
        return
    batch = batch_for_shader(shader, "POINTS", {"pos": points})
    try:
        gpu.state.point_size_set(4.0)
    except Exception:
        pass
    shader.uniform_float("color", (0.0, 0.0, 0.0, 0.95))
    batch.draw(shader)
    try:
        gpu.state.point_size_set(2.0)
    except Exception:
        pass
    shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
    batch.draw(shader)


def _committed_selection_screen_data(scene, region, width, height, current_image_id):
    """Return the synchronized committed GIMP selection for overlay drawing."""
    if (
        not bool(getattr(scene, "blendgimp_selection_active", False))
        or not bool(getattr(scene, "blendgimp_selection_bounds_valid", False))
        or int(getattr(scene, "blendgimp_selection_image_id", -1)) != int(current_image_id)
    ):
        return None, [], False
    shape = str(getattr(scene, "blendgimp_selection_shape", "BOUNDS"))
    if shape == "FREE":
        points = _selection_image_points_to_screen(
            region, width, height, _selection_points_from_scene(scene), close=True
        )
    elif shape == "MASK":
        points = _selection_image_points_to_screen(
            region, width, height, _SELECTION_OUTLINE_CACHE.get(int(current_image_id), []), close=False
        )
    else:
        x1 = float(getattr(scene, "blendgimp_selection_x1", 0))
        y1 = float(getattr(scene, "blendgimp_selection_y1", 0))
        x2 = float(getattr(scene, "blendgimp_selection_x2", 0))
        y2 = float(getattr(scene, "blendgimp_selection_y2", 0))
        points = _selection_outline_screen_points(region, width, height, shape, x1, y1, x2, y2)
    return shape, points, bool(getattr(scene, "blendgimp_selection_inverted", False))


def _draw_committed_selection(shader, scene, region, width, height, current_image_id):
    """Draw the current authoritative selection without changing interaction state."""
    shape, points, inverted = _committed_selection_screen_data(
        scene, region, width, height, current_image_id
    )
    if len(points) >= 2:
        if shape == "MASK":
            _draw_selection_mask_points(shader, points)
        else:
            _draw_dashed_selection_polyline(shader, points)
    if inverted:
        outer = _selection_outline_screen_points(
            region, width, height, "RECTANGLE", 0.0, 0.0, float(width), float(height)
        )
        _draw_dashed_selection_polyline(shader, outer)
    return bool(len(points) >= 2 or inverted)


def _draw_selection_overlay():
    if gpu is None or batch_for_shader is None:
        return
    context = bpy.context
    scene = getattr(context, "scene", None)
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if scene is None or area is None or region is None:
        return
    if region.type != "WINDOW" or not _context_is_blendgimp_area(context, MODE_TEXTURE):
        return
    if not bool(getattr(scene, "blendgimp_selection_overlay", True)):
        return

    space = getattr(area, "spaces", None)
    space = getattr(space, "active", None)
    image = getattr(space, "image", None)
    if image is None:
        return
    width = max(1, int(image.size[0]))
    height = max(1, int(image.size[1]))
    current_image_id = int(getattr(scene, "blendgimp_texture_editor_image_id", -1))

    try:
        area_ptr = int(area.as_pointer())
    except Exception:
        area_ptr = id(area)
    drag_preview = _SELECTION_DRAG_PREVIEW
    free_preview = _SELECTION_FREE_PREVIEW
    dragging = bool(drag_preview.get("active", False)) and int(drag_preview.get("area_ptr", 0)) == area_ptr
    free_drawing = bool(free_preview.get("active", False)) and int(free_preview.get("area_ptr", 0)) == area_ptr

    try:
        global _SELECTION_SHADER
        if _SELECTION_SHADER is None:
            _SELECTION_SHADER = gpu.shader.from_builtin("UNIFORM_COLOR")
        shader = _SELECTION_SHADER
        gpu.state.blend_set("ALPHA")
        shader.bind()

        # 0.5.15 composition preview: when a new selection is being added,
        # subtracted or intersected, keep the committed GIMP selection visible
        # while the user draws the candidate region.  Free Select also keeps
        # the old outline visible before its first point so the modifier can be
        # chosen at that first canvas click, matching GIMP behavior.
        preview_operation = "REPLACE"
        if dragging:
            preview_operation = str(drag_preview.get("operation", "REPLACE") or "REPLACE").upper()
        elif free_drawing:
            preview_operation = str(free_preview.get("operation", "PENDING") or "PENDING").upper()
        if (dragging or free_drawing) and preview_operation in {"PENDING", "ADD", "SUBTRACT", "INTERSECT"}:
            _draw_committed_selection(shader, scene, region, width, height, current_image_id)

        if free_drawing:
            image_points = list(free_preview.get("points", []))
            hover = free_preview.get("hover")
            if hover is not None:
                image_points.append(hover)
            points = _selection_image_points_to_screen(region, width, height, image_points, close=False)
            if len(points) >= 2:
                _draw_dashed_selection_polyline(shader, points)
            elif preview_operation == "REPLACE":
                # No polygon yet: do not erase the old selection just because
                # the Free tool has been armed.
                _draw_committed_selection(shader, scene, region, width, height, current_image_id)
        elif dragging:
            shape = str(drag_preview.get("shape", "RECTANGLE"))
            x1 = float(drag_preview.get("x1", 0.0))
            y1 = float(drag_preview.get("y1", 0.0))
            x2 = float(drag_preview.get("x2", 0.0))
            y2 = float(drag_preview.get("y2", 0.0))
            points = _selection_outline_screen_points(region, width, height, shape, x1, y1, x2, y2)
            if len(points) >= 2:
                _draw_dashed_selection_polyline(shader, points)
        else:
            _draw_committed_selection(shader, scene, region, width, height, current_image_id)
    except Exception:
        pass
    finally:
        try:
            gpu.state.line_width_set(1.0)
            try:
                gpu.state.point_size_set(1.0)
            except Exception:
                pass
            gpu.state.blend_set("NONE")
        except Exception:
            pass


def _tag_selection_preview_redraw(context):
    """Redraw only the texture canvas region during a live selection drag."""
    area = getattr(context, "area", None)
    if area is None:
        return
    region = _area_window_region(area)
    if region is not None:
        try:
            region.tag_redraw()
            return
        except Exception:
            pass
    try:
        area.tag_redraw()
    except Exception:
        pass


def _tag_texture_selection_redraw(context=None):
    ctx = context or bpy.context
    try:
        for _window, _screen, area, mode in _iter_enabled_blendgimp_areas():
            if mode == MODE_TEXTURE:
                area.tag_redraw()
    except Exception:
        area = getattr(ctx, "area", None)
        if area is not None:
            area.tag_redraw()


class BLENDGIMP_OT_selection_action(bpy.types.Operator):
    bl_idname = "blendgimp.selection_action"
    bl_label = "GIMP Selection Action"
    bl_description = "Apply a native GIMP selection operation to the active BlendGimp texture"

    action: bpy.props.EnumProperty(
        items=(
            ("ALL", "Select All", "Select the entire GIMP image"),
            ("NONE", "Deselect", "Clear the GIMP selection"),
            ("INVERT", "Invert", "Invert the current GIMP selection"),
            ("REFRESH", "Refresh", "Refresh selection state from GIMP"),
        ),
        default="REFRESH",
    )

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        return (
            scene is not None
            and connection_manager.is_connected()
            and int(getattr(scene, "blendgimp_texture_editor_image_id", -1)) >= 0
        )

    def execute(self, context):
        scene = context.scene
        image_id = int(scene.blendgimp_texture_editor_image_id)
        scene.blendgimp_selection_interaction_active = True
        request_blendgimp_paint_release(
            scene, "GIMP selection action", screen=context.screen, area=context.area
        )
        try:
            if self.action == "ALL":
                response = connection_manager.select_all(image_id)
            elif self.action == "NONE":
                response = connection_manager.select_none(image_id)
            elif self.action == "INVERT":
                old_shape = str(getattr(scene, "blendgimp_selection_shape", "BOUNDS"))
                old_bounds = (
                    int(getattr(scene, "blendgimp_selection_x1", 0)),
                    int(getattr(scene, "blendgimp_selection_y1", 0)),
                    int(getattr(scene, "blendgimp_selection_x2", 0)),
                    int(getattr(scene, "blendgimp_selection_y2", 0)),
                )
                old_points_json = str(getattr(scene, "blendgimp_selection_points_json", "[]"))
                old_inverted = bool(getattr(scene, "blendgimp_selection_inverted", False))
                response = connection_manager.select_invert(image_id)
                _apply_selection_state(scene, response)
                if bool(response.get("active", False)) and old_shape in {"RECTANGLE", "ELLIPSE", "FREE"}:
                    scene.blendgimp_selection_shape = old_shape
                    scene.blendgimp_selection_x1, scene.blendgimp_selection_y1, scene.blendgimp_selection_x2, scene.blendgimp_selection_y2 = old_bounds
                    scene.blendgimp_selection_bounds_valid = True
                    if old_shape == "FREE":
                        scene.blendgimp_selection_points_json = old_points_json
                    scene.blendgimp_selection_inverted = not old_inverted
                elif not bool(response.get("active", False)):
                    scene.blendgimp_selection_inverted = False
            else:
                response = connection_manager.get_selection_state(image_id)
                response.setdefault("shape", "BOUNDS")
                _apply_selection_state(scene, response)
            if self.action != "INVERT":
                _apply_selection_state(scene, response)
                scene.blendgimp_selection_inverted = False
            scene.blendgimp_texture_editor_status = (
                "GIMP selection active" if scene.blendgimp_selection_active else "GIMP selection cleared"
            )
            _tag_texture_selection_redraw(context)
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Selection action failed: {exc}")
            return {"CANCELLED"}
        finally:
            scene.blendgimp_selection_interaction_active = False


class BLENDGIMP_OT_selection_drag(bpy.types.Operator):
    bl_idname = "blendgimp.selection_drag"
    bl_label = "Draw GIMP Selection"
    bl_description = "Click-drag on the BlendGimp texture canvas to create a native GIMP selection"

    shape: bpy.props.EnumProperty(
        items=(
            ("RECTANGLE", "Rectangle", "Draw a rectangular selection"),
            ("ELLIPSE", "Ellipse", "Draw an elliptical selection"),
        ),
        default="RECTANGLE",
    )

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        return (
            _context_is_blendgimp_area(context, MODE_TEXTURE)
            and scene is not None
            and connection_manager.is_connected()
            and int(getattr(scene, "blendgimp_texture_editor_image_id", -1)) >= 0
        )

    def _finish_interaction(self, context):
        scene = context.scene
        scene.blendgimp_selection_interaction_active = False
        scene.blendgimp_selection_drag_active = False
        _SELECTION_DRAG_PREVIEW.update({
            "active": False,
            "area_ptr": 0,
            "shape": self.shape,
            "operation": "REPLACE",
            "x1": 0.0,
            "y1": 0.0,
            "x2": 0.0,
            "y2": 0.0,
        })
        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass
        _tag_texture_selection_redraw(context)

    def invoke(self, context, _event):
        scene = context.scene
        if bool(getattr(scene, "blendgimp_selection_interaction_active", False)):
            self.report({"INFO"}, "A BlendGimp selection tool is already active; Esc/RMB cancels it")
            return {"CANCELLED"}
        scene.blendgimp_selection_interaction_active = True
        scene.blendgimp_selection_drag_active = False
        scene.blendgimp_selection_drag_shape = self.shape
        request_blendgimp_paint_release(
            scene, "GIMP selection drag", screen=context.screen, area=context.area
        )
        self._started = False
        self._start = None
        self._last_preview_redraw_at = 0.0
        self._last_preview_point = None
        try:
            context.window.cursor_modal_set("CROSSHAIR")
        except Exception:
            pass
        scene.blendgimp_texture_editor_status = (
            f"{self.shape.title()} Select - click-drag on the texture canvas; Esc cancels"
        )
        print(
            "BLENDGIMP: Selection tool armed "
            f"shape={self.shape} operation=GIMP-HOTKEY "
            "preview=local-60hz scene_rna=commit-only"
        )
        # Redraw immediately so the chosen selection button visibly depresses.
        try:
            context.area.tag_redraw()
        except Exception:
            pass
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        scene = context.scene
        if event.type in {"ESC", "RIGHTMOUSE"}:
            self._finish_interaction(context)
            scene.blendgimp_texture_editor_status = "Selection cancelled"
            return {"CANCELLED"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS" and not self._started:
            point = _selection_event_image_point(context, event)
            if point is None:
                # Do not swallow clicks in Blender UI regions while the tool is armed.
                return {"PASS_THROUGH"}
            self._started = True
            self._start = point
            self._last_preview_point = point
            self._operation = _selection_operation_from_event(event)
            scene.blendgimp_selection_operation = self._operation
            scene.blendgimp_selection_drag_active = True
            scene.blendgimp_texture_editor_status = (
                f"{self.shape.title()} Select {_selection_operation_label(self._operation)} - drag to commit"
            )
            # Scene RNA is updated only at drag start/end.  Live movement stays
            # in module memory to avoid high-frequency property invalidation.
            scene.blendgimp_selection_drag_x1 = point[0]
            scene.blendgimp_selection_drag_y1 = point[1]
            scene.blendgimp_selection_drag_x2 = point[0]
            scene.blendgimp_selection_drag_y2 = point[1]
            try:
                area_ptr = int(context.area.as_pointer())
            except Exception:
                area_ptr = id(context.area)
            _SELECTION_DRAG_PREVIEW.update({
                "active": True,
                "area_ptr": area_ptr,
                "shape": self.shape,
                "operation": self._operation,
                "x1": point[0],
                "y1": point[1],
                "x2": point[0],
                "y2": point[1],
            })
            self._last_preview_redraw_at = time.monotonic()
            _tag_selection_preview_redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and self._started:
            point = _selection_event_image_point(context, event)
            if point is not None:
                last = self._last_preview_point
                if last is None or abs(point[0] - last[0]) >= 0.25 or abs(point[1] - last[1]) >= 0.25:
                    self._last_preview_point = point
                    _SELECTION_DRAG_PREVIEW["x2"] = point[0]
                    _SELECTION_DRAG_PREVIEW["y2"] = point[1]
                    now = time.monotonic()
                    if now - self._last_preview_redraw_at >= _SELECTION_PREVIEW_REDRAW_INTERVAL:
                        self._last_preview_redraw_at = now
                        _tag_selection_preview_redraw(context)
            return {"RUNNING_MODAL"}

        if not self._started:
            # Armed selection mode should not make the rest of Blender's UI
            # feel modal.  Let navigation/hover/sidebar events pass through.
            return {"PASS_THROUGH"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE" and self._started:
            point = _selection_event_image_point(context, event)
            if point is None:
                point = (
                    float(_SELECTION_DRAG_PREVIEW.get("x2", self._start[0])),
                    float(_SELECTION_DRAG_PREVIEW.get("y2", self._start[1])),
                )
            scene.blendgimp_selection_drag_x2 = point[0]
            scene.blendgimp_selection_drag_y2 = point[1]
            x0, y0 = self._start
            x1, y1 = point
            left = min(x0, x1)
            top = min(y0, y1)
            width = abs(x1 - x0)
            height = abs(y1 - y0)
            if width < 1.0 or height < 1.0:
                self._finish_interaction(context)
                self.report({"WARNING"}, "Selection is too small")
                return {"CANCELLED"}

            image_id = int(scene.blendgimp_texture_editor_image_id)
            operation = str(getattr(self, "_operation", "REPLACE") or "REPLACE").upper()
            try:
                if self.shape == "ELLIPSE":
                    response = connection_manager.select_ellipse(
                        image_id, left, top, width, height, operation
                    )
                else:
                    response = connection_manager.select_rectangle(
                        image_id, left, top, width, height, operation
                    )
                fallback = self.shape if operation == "REPLACE" else "BOUNDS"
                _apply_selection_state(scene, response, fallback_shape=fallback)
                scene.blendgimp_selection_inverted = False
                scene.blendgimp_texture_editor_status = (
                    f"{operation.title()} {self.shape.title()} GIMP selection"
                )
                print(
                    "BLENDGIMP: GIMP selection created "
                    f"shape={self.shape} operation={operation} image ID {image_id} "
                    f"bounds={scene.blendgimp_selection_x1},{scene.blendgimp_selection_y1}.."
                    f"{scene.blendgimp_selection_x2},{scene.blendgimp_selection_y2}"
                )
                self._finish_interaction(context)
                return {"FINISHED"}
            except Exception as exc:
                self._finish_interaction(context)
                self.report({"ERROR"}, f"Could not create selection: {exc}")
                return {"CANCELLED"}

        return {"RUNNING_MODAL"}


def _selection_active_layer_id(scene):
    """Return the current raster paint-layer id used by BlendGimp selection tools."""
    for name in (
        "blendgimp_blender_paint_sync_layer_id",
        "blendgimp_2d_paint_layer_id",
        "blendgimp_direct_paint_layer_id",
        "blendgimp_created_layer_id",
    ):
        try:
            value = int(getattr(scene, name, -1))
        except Exception:
            value = -1
        if value >= 0:
            return value

    # Fallback to the authoritative layer-tree snapshot. This covers a newly
    # selected raster layer before a paint owner has been armed.
    try:
        image_id = int(getattr(scene, "blendgimp_texture_editor_image_id", -1))
        store = json.loads(str(getattr(scene, "blendgimp_layers_json", "{}") or "{}"))
        response = store.get(str(image_id), {}) if isinstance(store, dict) else {}
        stack = list(response.get("layers", [])) if isinstance(response, dict) else []
        while stack:
            item = stack.pop(0)
            if not isinstance(item, dict):
                continue
            if bool(item.get("selected", False)) and not bool(item.get("is_group", False)):
                return int(item.get("id", -1))
            stack[0:0] = list(item.get("children", []) or [])
    except Exception:
        pass
    return -1


def _selection_operation(scene):
    """Compatibility accessor for the legacy persistent combine property.

    0.5.15 user-facing selection combine behavior is modifier driven to match
    GIMP: Shift=Add, Ctrl=Subtract, Shift+Ctrl=Intersect, no modifier=Replace.
    The RNA property remains registered for old .blend compatibility and
    scripted callers, but the texture-editor selection tools do not require
    the user to select a mode button anymore.
    """
    value = str(getattr(scene, "blendgimp_selection_operation", "REPLACE") or "REPLACE").upper()
    return value if value in {"REPLACE", "ADD", "SUBTRACT", "INTERSECT"} else "REPLACE"


def _selection_operation_from_event(event):
    """Return GIMP-style temporary selection combine mode from modifiers."""
    shift = bool(getattr(event, "shift", False))
    ctrl = bool(getattr(event, "ctrl", False))
    if shift and ctrl:
        return "INTERSECT"
    if shift:
        return "ADD"
    if ctrl:
        return "SUBTRACT"
    return "REPLACE"


def _selection_operation_label(operation):
    return {
        "REPLACE": "Replace",
        "ADD": "Add",
        "SUBTRACT": "Subtract",
        "INTERSECT": "Intersect",
    }.get(str(operation or "REPLACE").upper(), "Replace")


class BLENDGIMP_OT_selection_free(bpy.types.Operator):
    bl_idname = "blendgimp.selection_free"
    bl_label = "Free Select"
    bl_description = (
        "Create a native GIMP polygon selection: click vertices, Enter/double-click to close, "
        "Backspace removes the last point, Esc cancels"
    )

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        return (
            _context_is_blendgimp_area(context, MODE_TEXTURE)
            and scene is not None
            and connection_manager.is_connected()
            and int(getattr(scene, "blendgimp_texture_editor_image_id", -1)) >= 0
        )

    def _finish(self, context):
        scene = context.scene
        scene.blendgimp_selection_interaction_active = False
        scene.blendgimp_selection_drag_active = False
        _SELECTION_FREE_PREVIEW.update({
            "active": False,
            "area_ptr": 0,
            "operation": "PENDING",
            "points": [],
            "hover": None,
        })
        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass
        _tag_texture_selection_redraw(context)

    def _commit(self, context):
        scene = context.scene
        points = list(_SELECTION_FREE_PREVIEW.get("points", []))
        if len(points) < 3:
            self.report({"WARNING"}, "Free Select requires at least three points")
            return {"RUNNING_MODAL"}
        image_id = int(scene.blendgimp_texture_editor_image_id)
        operation = str(_SELECTION_FREE_PREVIEW.get("operation", "REPLACE") or "REPLACE").upper()
        if operation not in {"REPLACE", "ADD", "SUBTRACT", "INTERSECT"}:
            operation = "REPLACE"
        flat = [coord for point in points for coord in point]
        try:
            response = connection_manager.select_polygon(image_id, flat, operation)
            _apply_selection_state(
                scene,
                response,
                fallback_shape="FREE" if operation == "REPLACE" else "BOUNDS",
            )
            scene.blendgimp_selection_inverted = False
            scene.blendgimp_texture_editor_status = (
                f"{operation.title()} Free Select committed ({len(points)} points)"
            )
            print(
                "BLENDGIMP: GIMP free selection created "
                f"operation={operation} image ID {image_id} points={len(points)}"
            )
            self._finish(context)
            return {"FINISHED"}
        except Exception as exc:
            self._finish(context)
            self.report({"ERROR"}, f"Could not create Free Select: {exc}")
            return {"CANCELLED"}

    def invoke(self, context, _event):
        scene = context.scene
        if bool(getattr(scene, "blendgimp_selection_interaction_active", False)):
            self.report({"INFO"}, "A BlendGimp selection tool is already active; Esc/RMB cancels it")
            return {"CANCELLED"}
        scene.blendgimp_selection_interaction_active = True
        scene.blendgimp_selection_drag_active = False
        scene.blendgimp_selection_drag_shape = "FREE"
        request_blendgimp_paint_release(
            scene, "GIMP free selection", screen=context.screen, area=context.area
        )
        try:
            area_ptr = int(context.area.as_pointer())
        except Exception:
            area_ptr = id(context.area)
        _SELECTION_FREE_PREVIEW.update({
            "active": True,
            "area_ptr": area_ptr,
            "operation": "PENDING",
            "points": [],
            "hover": None,
        })
        self._last_preview_redraw_at = 0.0
        self._last_click_time = 0.0
        self._last_click_screen = None
        try:
            context.window.cursor_modal_set("CROSSHAIR")
        except Exception:
            pass
        scene.blendgimp_texture_editor_status = (
            "Free Select ACTIVE - first click sets combine hotkey; Enter/double-click closes"
        )
        print(
            "BLENDGIMP: Selection tool armed shape=FREE "
            "operation=GIMP-HOTKEY preview=local-60hz"
        )
        _tag_texture_selection_redraw(context)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        scene = context.scene
        if event.type in {"ESC", "RIGHTMOUSE"}:
            self._finish(context)
            scene.blendgimp_texture_editor_status = "Free Select cancelled"
            return {"CANCELLED"}

        if event.type in {"BACK_SPACE", "DEL"} and event.value == "PRESS":
            points = _SELECTION_FREE_PREVIEW.get("points", [])
            if points:
                points.pop()
                _SELECTION_FREE_PREVIEW["hover"] = points[-1] if points else None
                _tag_selection_preview_redraw(context)
            return {"RUNNING_MODAL"}

        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            return self._commit(context)

        if event.type == "MOUSEMOVE":
            point = _selection_event_image_point(context, event)
            if point is not None:
                _SELECTION_FREE_PREVIEW["hover"] = point
                now = time.monotonic()
                if now - self._last_preview_redraw_at >= _SELECTION_PREVIEW_REDRAW_INTERVAL:
                    self._last_preview_redraw_at = now
                    _tag_selection_preview_redraw(context)
            return {"RUNNING_MODAL"} if _SELECTION_FREE_PREVIEW.get("points") else {"PASS_THROUGH"}

        if event.type == "LEFTMOUSE" and event.value in {"PRESS", "DOUBLE_CLICK"}:
            point = _selection_event_image_point(context, event)
            if point is None:
                return {"PASS_THROUGH"}
            points = _SELECTION_FREE_PREVIEW.setdefault("points", [])
            if not points:
                operation = _selection_operation_from_event(event)
                _SELECTION_FREE_PREVIEW["operation"] = operation
                scene.blendgimp_selection_operation = operation
                scene.blendgimp_texture_editor_status = (
                    f"Free Select {_selection_operation_label(operation)} - add points; "
                    "Enter/double-click closes"
                )
                print(f"BLENDGIMP: Free Select combine hotkey operation={operation}")
            now = time.monotonic()
            screen_point = (float(event.mouse_region_x), float(event.mouse_region_y))
            manual_double = False
            if self._last_click_screen is not None and now - float(self._last_click_time) <= 0.40:
                manual_double = math.hypot(
                    screen_point[0] - self._last_click_screen[0],
                    screen_point[1] - self._last_click_screen[1],
                ) <= 10.0

            # Blender does not consistently expose LEFTMOUSE/DOUBLE_CLICK to a
            # modal operator in every Image Editor path.  Detect the second
            # PRESS ourselves as well.  The first click of the pair already
            # added the closing vertex, so the second click commits directly.
            if manual_double and len(points) >= 3:
                print("BLENDGIMP: Free Select manual double-click commit")
                return self._commit(context)

            if not points or math.hypot(point[0] - points[-1][0], point[1] - points[-1][1]) >= 0.25:
                points.append(point)
            self._last_click_time = now
            self._last_click_screen = screen_point
            _SELECTION_FREE_PREVIEW["hover"] = point
            _tag_selection_preview_redraw(context)
            if event.value == "DOUBLE_CLICK" and len(points) >= 3:
                print("BLENDGIMP: Free Select native double-click commit")
                return self._commit(context)
            return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"} if not _SELECTION_FREE_PREVIEW.get("points") else {"RUNNING_MODAL"}


class BLENDGIMP_OT_selection_point(bpy.types.Operator):
    bl_idname = "blendgimp.selection_point"
    bl_label = "Color Selection"
    bl_description = "Click the texture canvas to create a GIMP Fuzzy or Select by Color selection"

    mode: bpy.props.EnumProperty(
        items=(
            ("FUZZY", "Fuzzy Select", "Select a contiguous region around the clicked pixel"),
            ("COLOR", "Select by Color", "Select matching colors across the image/layer"),
        ),
        default="FUZZY",
    )

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        return (
            _context_is_blendgimp_area(context, MODE_TEXTURE)
            and scene is not None
            and connection_manager.is_connected()
            and int(getattr(scene, "blendgimp_texture_editor_image_id", -1)) >= 0
        )

    def _finish(self, context):
        scene = context.scene
        scene.blendgimp_selection_interaction_active = False
        scene.blendgimp_selection_drag_active = False
        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass
        _tag_texture_selection_redraw(context)

    def invoke(self, context, _event):
        scene = context.scene
        if bool(getattr(scene, "blendgimp_selection_interaction_active", False)):
            self.report({"INFO"}, "A BlendGimp selection tool is already active; Esc/RMB cancels it")
            return {"CANCELLED"}
        scene.blendgimp_selection_interaction_active = True
        scene.blendgimp_selection_drag_active = False
        scene.blendgimp_selection_drag_shape = self.mode
        request_blendgimp_paint_release(
            scene, f"GIMP {self.mode.lower()} selection", screen=context.screen, area=context.area
        )
        try:
            context.window.cursor_modal_set("EYEDROPPER")
        except Exception:
            try:
                context.window.cursor_modal_set("CROSSHAIR")
            except Exception:
                pass
        label = "Fuzzy Select" if self.mode == "FUZZY" else "Select by Color"
        scene.blendgimp_texture_editor_status = f"{label} ACTIVE - click texture canvas; Esc cancels"
        print(
            "BLENDGIMP: Selection tool armed "
            f"shape={self.mode} operation=GIMP-HOTKEY point-pick"
        )
        _tag_texture_selection_redraw(context)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        scene = context.scene
        if event.type in {"ESC", "RIGHTMOUSE"}:
            self._finish(context)
            scene.blendgimp_texture_editor_status = "Color selection cancelled"
            return {"CANCELLED"}
        if event.type != "LEFTMOUSE" or event.value != "PRESS":
            return {"PASS_THROUGH"}
        point = _selection_event_image_point(context, event)
        if point is None:
            return {"PASS_THROUGH"}
        image_id = int(scene.blendgimp_texture_editor_image_id)
        layer_id = _selection_active_layer_id(scene)
        if layer_id < 0:
            self._finish(context)
            self.report({"ERROR"}, "Select a raster GIMP layer before using Fuzzy/Color Select")
            return {"CANCELLED"}
        operation = _selection_operation_from_event(event)
        scene.blendgimp_selection_operation = operation
        threshold = float(getattr(scene, "blendgimp_selection_threshold", 0.15))
        sample_merged = bool(getattr(scene, "blendgimp_selection_sample_merged", False))
        sample_transparent = bool(getattr(scene, "blendgimp_selection_sample_transparent", True))
        try:
            if self.mode == "FUZZY":
                response = connection_manager.select_fuzzy(
                    image_id, layer_id, point[0], point[1], operation,
                    threshold, sample_merged, sample_transparent,
                )
                label = "Fuzzy Select"
            else:
                response = connection_manager.select_by_color(
                    image_id, layer_id, point[0], point[1], operation,
                    threshold, sample_merged, sample_transparent,
                )
                label = "Select by Color"
            _apply_selection_state(scene, response, fallback_shape="BOUNDS")
            scene.blendgimp_selection_inverted = False
            active = bool(response.get("active", False))
            outline_count = len(response.get("outline_points") or []) // 2
            sample_source = str(response.get("sample_source", "gimp-coordinate"))
            retry_used = bool(response.get("sample_retry", False))
            print(
                "BLENDGIMP: GIMP color selection created "
                f"mode={self.mode} operation={operation} image ID {image_id} "
                f"layer ID {layer_id} threshold={threshold:.3f} active={active} "
                f"bounds={response.get('x1', 0)},{response.get('y1', 0)}.."
                f"{response.get('x2', 0)},{response.get('y2', 0)} "
                f"outline_points={outline_count} sample={sample_source} retry={retry_used}"
            )
            if self.mode == "COLOR" and operation == "REPLACE" and not active:
                # A click can legitimately hit a transparent/out-of-drawable
                # point. Keep the tool armed instead of silently returning to
                # paint mode, which previously made Select by Color feel
                # intermittent to the user.
                scene.blendgimp_texture_editor_status = (
                    "Select by Color ACTIVE - no pixels matched; click another color or Esc"
                )
                _tag_texture_selection_redraw(context)
                return {"RUNNING_MODAL"}
            scene.blendgimp_texture_editor_status = f"{operation.title()} {label} committed"
            self._finish(context)
            return {"FINISHED"}
        except Exception as exc:
            self._finish(context)
            self.report({"ERROR"}, f"{self.mode.title()} selection failed: {exc}")
            return {"CANCELLED"}


class BLENDGIMP_OT_selection_modify(bpy.types.Operator):
    bl_idname = "blendgimp.selection_modify"
    bl_label = "Modify GIMP Selection"
    bl_description = "Grow, shrink, feather, or border the authoritative GIMP selection"

    action: bpy.props.EnumProperty(
        items=(
            ("GROW", "Grow", "Expand the selection boundary"),
            ("SHRINK", "Shrink", "Contract the selection boundary"),
            ("FEATHER", "Feather", "Blur/soften the selection boundary"),
            ("BORDER", "Border", "Replace the selection with a border around its boundary"),
        ),
        default="GROW",
    )

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        return (
            scene is not None
            and connection_manager.is_connected()
            and int(getattr(scene, "blendgimp_texture_editor_image_id", -1)) >= 0
            and bool(getattr(scene, "blendgimp_selection_active", False))
        )

    def execute(self, context):
        scene = context.scene
        image_id = int(scene.blendgimp_texture_editor_image_id)
        radius = float(getattr(scene, "blendgimp_selection_radius", 8.0))
        scene.blendgimp_selection_interaction_active = True
        request_blendgimp_paint_release(
            scene, f"GIMP selection {self.action.lower()}", screen=context.screen, area=context.area
        )
        try:
            response = connection_manager.modify_selection(image_id, self.action, radius)
            _apply_selection_state(scene, response, fallback_shape="BOUNDS")
            # Morphology can create topology that no longer matches a simple local
            # Rectangle/Ellipse/Free outline.  Bounds are deliberately used until
            # the exact selection-mask contour transport lands.
            scene.blendgimp_selection_inverted = False
            scene.blendgimp_texture_editor_status = (
                f"Selection {self.action.title()} {radius:g}px"
            )
            print(
                "BLENDGIMP: GIMP selection modified "
                f"action={self.action} radius={radius:g} image ID {image_id}"
            )
            _tag_texture_selection_redraw(context)
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Selection {self.action.title()} failed: {exc}")
            return {"CANCELLED"}
        finally:
            scene.blendgimp_selection_interaction_active = False



# -----------------------------------------------------------------------------
# Operators
# -----------------------------------------------------------------------------


class BLENDGIMP_OT_use_blender_brushes(bpy.types.Operator):
    bl_idname = "blendgimp.use_blender_brushes"
    bl_label = "Use Blender Brushes"
    bl_description = (
        "Release BlendGimp mouse ownership and use Blender's built-in Texture Paint "
        "on the active GIMP layer buffer while Unified Paint Sync remains active"
    )

    target: bpy.props.EnumProperty(
        items=(
            ("2D", "2D Image Paint", "Use Blender's built-in 2D image painting"),
            ("3D", "3D Texture Paint", "Use Blender's built-in Texture Paint on the mesh"),
        ),
        default="3D",
    )

    @classmethod
    def poll(cls, context):
        return context.area is not None and getattr(context, "scene", None) is not None

    def execute(self, context):
        scene = context.scene
        request_blendgimp_paint_release(scene, "Use Blender Brushes", screen=context.screen, area=context.area)

        try:
            from . import main_panel as _main_panel
            image_id = int(getattr(scene, "blendgimp_blender_paint_sync_image_id", -1))
            layer_id = int(getattr(scene, "blendgimp_blender_paint_sync_layer_id", -1))
            if image_id >= 0 and layer_id >= 0:
                buffer_image = _main_panel._active_layer_buffer_for_sync(scene, image_id)  # noqa: SLF001
                if buffer_image is None:
                    buffer_image = _main_panel.load_active_layer_buffer_from_gimp(
                        scene, image_id, layer_id, clear_first=True, activate_target=True
                    )
                else:
                    sync_result = _main_panel.get_texture_sync_result(scene, image_id) or {}
                    composite = _main_panel._find_blendgimp_image(  # noqa: SLF001
                        image_id, sync_result.get("sync_token", "")
                    )
                    if composite is not None:
                        _main_panel._ensure_active_layer_paint_node(  # noqa: SLF001
                            context, composite, buffer_image, image_id
                        )
            else:
                buffer_image = None
        except Exception as exc:
            self.report({"ERROR"}, f"Could not prepare Blender paint target: {exc}")
            return {"CANCELLED"}

        if _context_is_blendgimp_area(context):
            _disable_blendgimp_area(context.screen, context.area, restore=False)

        obj = getattr(context, "active_object", None)
        if obj is not None and obj.type == "MESH":
            try:
                if str(obj.mode) != "TEXTURE_PAINT":
                    bpy.ops.object.mode_set(mode="TEXTURE_PAINT")
            except Exception as exc:
                print(f"BLENDGIMP: Blender Texture Paint mode warning: {exc}")

        if self.target == "2D":
            try:
                context.area.type = "IMAGE_EDITOR"
                space = context.area.spaces.active
                if buffer_image is not None:
                    space.image = buffer_image
                _set_if_present(space, "mode", "PAINT")
                _set_if_present(space, "show_region_toolbar", True)
                _set_if_present(space, "show_region_asset_shelf", True)
            except Exception as exc:
                self.report({"ERROR"}, f"Could not enter Blender 2D Paint: {exc}")
                return {"CANCELLED"}
        else:
            try:
                context.area.type = "VIEW_3D"
                space = context.area.spaces.active
                _set_if_present(space, "show_region_toolbar", True)
                _set_if_present(space, "show_region_asset_shelf", True)
            except Exception:
                pass

        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass

        self.report({"INFO"}, "Blender Texture Paint ready — Unified Paint Sync remains active")
        print(
            "BLENDGIMP: Blender brush ownership active "
            f"target={self.target} unified_sync=True"
        )
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
    bl_label = "Fit Image"
    bl_description = "Fit the complete texture into this BlendGimp canvas"

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "IMAGE_EDITOR"

    def execute(self, context):
        region = _area_window_region(context.area)
        if region is None:
            self.report({"WARNING"}, "BlendGimp canvas window region is unavailable")
            return {"CANCELLED"}
        try:
            with context.temp_override(
                window=context.window,
                screen=context.screen,
                area=context.area,
                region=region,
            ):
                try:
                    bpy.ops.image.view_all(fit_view=True)
                except TypeError:
                    bpy.ops.image.view_all()
            context.area.tag_redraw()
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Could not fit texture view: {exc}")
            return {"CANCELLED"}


class BLENDGIMP_OT_texture_view_100(bpy.types.Operator):
    bl_idname = "blendgimp.texture_view_100"
    bl_label = "100%"
    bl_description = "Show one texture pixel per screen pixel"

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "IMAGE_EDITOR"

    def execute(self, context):
        region = _area_window_region(context.area)
        if region is None:
            self.report({"WARNING"}, "BlendGimp canvas window region is unavailable")
            return {"CANCELLED"}
        try:
            with context.temp_override(
                window=context.window,
                screen=context.screen,
                area=context.area,
                region=region,
            ):
                bpy.ops.image.view_zoom_ratio(ratio=1.0)
            context.area.tag_redraw()
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Could not set texture view to 100%: {exc}")
            return {"CANCELLED"}


# -----------------------------------------------------------------------------
# UI helpers
# -----------------------------------------------------------------------------


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
    col.prop(scene, "blendgimp_texture_editor_show_islands", text="UV Island Edges")
    col.prop(
        scene,
        "blendgimp_texture_editor_active_face_highlight",
        text="Selected Face Fill",
    )
    col.prop(scene, "blendgimp_texture_editor_uv_opacity", text="Overlay Opacity", slider=True)
    edge = col.row()
    edge.enabled = scene.blendgimp_texture_editor_show_islands
    edge.prop(scene, "blendgimp_texture_editor_uv_edge_style", text="Edge Style")

    obj = context.active_object
    if obj is None or obj.type != "MESH":
        box.label(text="Select a mesh to display UVs", icon="INFO")
    elif len(obj.data.uv_layers) == 0:
        box.label(text="Active mesh has no UV map", icon="ERROR")
    else:
        uv_layer = obj.data.uv_layers.active
        box.label(text=f"UV Map: {uv_layer.name if uv_layer else '[None]'}")
        if scene.blendgimp_texture_editor_active_face_highlight:
            box.label(text="Selected-face fill uses Blender's native UV overlay")



def _draw_selection_controls(layout, context):
    scene = context.scene
    box = layout.box()
    header = box.row(align=True)
    header.label(text="GIMP Selection", icon="RESTRICT_SELECT_OFF")
    refresh = header.operator("blendgimp.selection_action", text="", icon="FILE_REFRESH")
    refresh.action = "REFRESH"

    image_id = int(getattr(scene, "blendgimp_texture_editor_image_id", -1))
    connected = connection_manager.is_connected() and image_id >= 0
    interaction_active = bool(getattr(scene, "blendgimp_selection_interaction_active", False))
    armed_shape = str(getattr(scene, "blendgimp_selection_drag_shape", "RECTANGLE"))

    combine = box.column(align=True)
    combine.enabled = connected
    combine.label(text="Combine Hotkeys (GIMP-style)", icon="EVENT_SHIFT")
    combine.label(text="No modifier: Replace   |   Shift: Add")
    combine.label(text="Ctrl: Subtract   |   Shift+Ctrl: Intersect")

    shape_row = box.row(align=True)
    shape_row.enabled = connected
    rect = shape_row.operator(
        "blendgimp.selection_drag",
        text="Rectangle",
        icon="RESTRICT_SELECT_OFF",
        depress=bool(interaction_active and armed_shape == "RECTANGLE"),
    )
    rect.shape = "RECTANGLE"
    ellipse = shape_row.operator(
        "blendgimp.selection_drag",
        text="Ellipse",
        icon="MESH_CIRCLE",
        depress=bool(interaction_active and armed_shape == "ELLIPSE"),
    )
    ellipse.shape = "ELLIPSE"
    shape_row.operator(
        "blendgimp.selection_free",
        text="Free",
        icon="IPO_LINEAR",
        depress=bool(interaction_active and armed_shape == "FREE"),
    )

    color_row = box.row(align=True)
    color_row.enabled = connected
    fuzzy = color_row.operator(
        "blendgimp.selection_point",
        text="Fuzzy",
        icon="BRUSH_DATA",
        depress=bool(interaction_active and armed_shape == "FUZZY"),
    )
    fuzzy.mode = "FUZZY"
    by_color = color_row.operator(
        "blendgimp.selection_point",
        text="By Color",
        icon="EYEDROPPER",
        depress=bool(interaction_active and armed_shape == "COLOR"),
    )
    by_color.mode = "COLOR"

    color_settings = box.column(align=True)
    color_settings.enabled = connected and not interaction_active
    color_settings.prop(scene, "blendgimp_selection_threshold", text="Color Threshold")
    sample_row = color_settings.row(align=True)
    sample_row.prop(scene, "blendgimp_selection_sample_merged", text="Sample Merged")
    sample_row.prop(scene, "blendgimp_selection_sample_transparent", text="Transparent")

    action_row = box.row(align=True)
    action_row.enabled = connected and not interaction_active
    op = action_row.operator("blendgimp.selection_action", text="All")
    op.action = "ALL"
    op = action_row.operator("blendgimp.selection_action", text="Deselect")
    op.action = "NONE"
    op = action_row.operator("blendgimp.selection_action", text="Invert")
    op.action = "INVERT"

    modify = box.column(align=True)
    modify.enabled = connected and bool(getattr(scene, "blendgimp_selection_active", False)) and not interaction_active
    modify.prop(scene, "blendgimp_selection_radius", text="Modify Radius")
    row = modify.row(align=True)
    op = row.operator("blendgimp.selection_modify", text="Grow")
    op.action = "GROW"
    op = row.operator("blendgimp.selection_modify", text="Shrink")
    op.action = "SHRINK"
    row = modify.row(align=True)
    op = row.operator("blendgimp.selection_modify", text="Feather")
    op.action = "FEATHER"
    op = row.operator("blendgimp.selection_modify", text="Border")
    op.action = "BORDER"

    box.prop(scene, "blendgimp_selection_overlay", text="Selection Overlay")

    selection_matches = int(getattr(scene, "blendgimp_selection_image_id", -1)) == image_id
    active = bool(getattr(scene, "blendgimp_selection_active", False)) and selection_matches
    if active:
        shape = str(getattr(scene, "blendgimp_selection_shape", "BOUNDS")).title()
        if bool(getattr(scene, "blendgimp_selection_inverted", False)):
            shape = f"Inverse {shape}"
        x1 = int(getattr(scene, "blendgimp_selection_x1", 0))
        y1 = int(getattr(scene, "blendgimp_selection_y1", 0))
        x2 = int(getattr(scene, "blendgimp_selection_x2", 0))
        y2 = int(getattr(scene, "blendgimp_selection_y2", 0))
        box.label(text=f"Active: {shape}", icon="CHECKMARK")
        if bool(getattr(scene, "blendgimp_selection_bounds_valid", False)):
            box.label(text=f"Bounds: {x1}, {y1}  to  {x2}, {y2}")
        else:
            box.label(text="Selection active; exact bounds unavailable", icon="INFO")
    elif selection_matches:
        box.label(text="No active GIMP selection", icon="INFO")
    else:
        box.label(text="Press Refresh to synchronize selection state", icon="INFO")

    if interaction_active:
        label_map = {
            "RECTANGLE": "Rectangle Select",
            "ELLIPSE": "Ellipse Select",
            "FREE": "Free Select",
            "FUZZY": "Fuzzy Select",
            "COLOR": "Select by Color",
        }
        status = box.row(align=True)
        status.label(
            text=f"{label_map.get(armed_shape, armed_shape.title())} ACTIVE",
            icon="RADIOBUT_ON",
        )
        if armed_shape == "FREE":
            box.label(text="Click points; Enter/double-click closes; Backspace removes")
        elif armed_shape in {"FUZZY", "COLOR"}:
            box.label(text="Click the texture canvas to sample/select")
        else:
            box.label(text="Drag on the texture canvas")
        box.label(text="Esc/RMB cancels selection mode", icon="EVENT_ESC")
    else:
        box.label(text="Hold Shift/Ctrl before the first canvas click to combine selections")

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

        _draw_active_texture(layout, context)

        nav = layout.box()
        nav.label(text="Texture Canvas", icon="IMAGE_DATA")
        row = nav.row(align=True)
        row.operator("blendgimp.texture_view_fit", text="Fit Image")
        row.operator("blendgimp.texture_view_100", text="100%")
        nav.label(text="MMB Pan  •  Wheel Zoom")
        nav.prop(
            scene,
            "blendgimp_texture_editor_display_channels",
            text="Presentation",
        )
        display_mode = str(scene.blendgimp_texture_editor_display_channels)
        if display_mode == "COLOR_ALPHA":
            nav.label(text="Transparent pixels use Blender's checkerboard")
        elif display_mode == "ALPHA":
            nav.label(text="Alpha-only inspection")
        else:
            nav.label(text="RGB preview ignores alpha")

        _draw_uv_controls(layout, context)
        _draw_selection_controls(layout, context)

        info = layout.box()
        info.label(text="Automatic pointer routing", icon="MOUSE_LMB")
        info.label(text="Move over canvas to paint • move over UI to edit controls")
        if scene.blendgimp_texture_editor_status:
            info.label(text=scene.blendgimp_texture_editor_status)


# -----------------------------------------------------------------------------
# Panels — Object Paint mode
# -----------------------------------------------------------------------------


class BLENDGIMP_PT_object_area(bpy.types.Panel):
    bl_label = "BlendGimp"
    bl_idname = "BLENDGIMP_PT_object_area"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlendGimp"

    @classmethod
    def poll(cls, context):
        return _context_is_blendgimp_area(context, MODE_OBJECT)

    def draw(self, context):
        layout = self.layout
        _draw_active_texture(layout, context)

        info = layout.box()
        info.label(text="Automatic pointer routing", icon="MOUSE_LMB")
        info.label(text="Move over model to paint • move over UI to edit controls")
        info.label(text="Texture/Object Paint share the same GIMP tool state", icon="LINKED")


classes = (
    BLENDGIMP_OT_selection_action,
    BLENDGIMP_OT_selection_drag,
    BLENDGIMP_OT_selection_free,
    BLENDGIMP_OT_selection_point,
    BLENDGIMP_OT_selection_modify,
    BLENDGIMP_OT_use_blender_brushes,
    BLENDGIMP_OT_texture_editor_refresh,
    BLENDGIMP_OT_texture_editor_set_image,
    BLENDGIMP_OT_texture_view_fit,
    BLENDGIMP_OT_texture_view_100,
    BLENDGIMP_PT_texture_area,
    BLENDGIMP_PT_object_area,
)


# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------


def register():
    global _SELECTION_DRAW_HANDLER
    _reset_texture_editor_runtime_state()

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
    bpy.types.Scene.blendgimp_texture_editor_display_channels = bpy.props.EnumProperty(
        name="Texture Presentation",
        description="Choose how the BlendGimp texture is presented in the Image Editor",
        items=TEXTURE_DISPLAY_CHANNEL_ITEMS,
        default="COLOR_ALPHA",
        update=_texture_editor_property_update,
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
        description="Show or hide the active mesh UV island edges over the texture",
        default=True,
        update=_texture_editor_property_update,
    )
    bpy.types.Scene.blendgimp_texture_editor_uv_edge_style = bpy.props.EnumProperty(
        name="UV Edge Style",
        description="Blender-native display style for UV island edges",
        items=UV_EDGE_STYLE_ITEMS,
        default="OUTLINE",
        update=_texture_editor_property_update,
    )
    bpy.types.Scene.blendgimp_texture_editor_active_face_highlight = bpy.props.BoolProperty(
        name="Selected Face Highlight",
        description="Use Blender's native UV face fill for selected mesh faces",
        default=True,
        update=_texture_editor_property_update,
    )
    bpy.types.Scene.blendgimp_texture_editor_status = bpy.props.StringProperty(
        name="BlendGimp Texture Status",
        default="",
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_selection_overlay = bpy.props.BoolProperty(
        name="Selection Overlay",
        description="Show the synchronized GIMP selection boundary over the BlendGimp texture canvas",
        default=True,
        update=_texture_editor_property_update,
    )
    bpy.types.Scene.blendgimp_selection_operation = bpy.props.EnumProperty(
        name="Selection Combine Mode",
        description="How the next GIMP selection tool combines with the current selection",
        items=SELECTION_OPERATION_ITEMS,
        default="REPLACE",
    )
    bpy.types.Scene.blendgimp_selection_threshold = bpy.props.FloatProperty(
        name="Selection Color Threshold",
        description="Color similarity threshold used by Fuzzy Select and Select by Color",
        default=0.15,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    bpy.types.Scene.blendgimp_selection_sample_merged = bpy.props.BoolProperty(
        name="Sample Merged",
        description="Use the visible image composite instead of only the active raster layer for color sampling",
        default=False,
    )
    bpy.types.Scene.blendgimp_selection_sample_transparent = bpy.props.BoolProperty(
        name="Sample Transparent",
        description="Allow transparent pixels to participate in color-based selection",
        default=True,
    )
    bpy.types.Scene.blendgimp_selection_radius = bpy.props.FloatProperty(
        name="Selection Modify Radius",
        description="Pixel radius used by Grow, Shrink, Feather, and Border",
        default=8.0,
        min=0.1,
        max=512.0,
        soft_max=128.0,
    )
    bpy.types.Scene.blendgimp_selection_points_json = bpy.props.StringProperty(
        name="GIMP Free Selection Points",
        default="[]",
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.blendgimp_selection_active = bpy.props.BoolProperty(
        name="GIMP Selection Active", default=False, options={"SKIP_SAVE"}
    )
    bpy.types.Scene.blendgimp_selection_inverted = bpy.props.BoolProperty(
        name="GIMP Selection Inverted", default=False, options={"SKIP_SAVE"}
    )
    bpy.types.Scene.blendgimp_selection_bounds_valid = bpy.props.BoolProperty(
        name="GIMP Selection Bounds Valid", default=True, options={"SKIP_SAVE"}
    )
    bpy.types.Scene.blendgimp_selection_image_id = bpy.props.IntProperty(
        name="GIMP Selection Image ID", default=-1, options={"SKIP_SAVE"}
    )
    bpy.types.Scene.blendgimp_selection_x1 = bpy.props.IntProperty(default=0, options={"SKIP_SAVE"})
    bpy.types.Scene.blendgimp_selection_y1 = bpy.props.IntProperty(default=0, options={"SKIP_SAVE"})
    bpy.types.Scene.blendgimp_selection_x2 = bpy.props.IntProperty(default=0, options={"SKIP_SAVE"})
    bpy.types.Scene.blendgimp_selection_y2 = bpy.props.IntProperty(default=0, options={"SKIP_SAVE"})
    bpy.types.Scene.blendgimp_selection_shape = bpy.props.EnumProperty(
        name="GIMP Selection Shape", items=SELECTION_SHAPE_ITEMS, default="NONE", options={"SKIP_SAVE"}
    )
    bpy.types.Scene.blendgimp_selection_interaction_active = bpy.props.BoolProperty(
        name="GIMP Selection Interaction", default=False, options={"SKIP_SAVE"}
    )
    bpy.types.Scene.blendgimp_selection_drag_active = bpy.props.BoolProperty(
        name="GIMP Selection Drag", default=False, options={"SKIP_SAVE"}
    )
    bpy.types.Scene.blendgimp_selection_drag_shape = bpy.props.StringProperty(
        name="GIMP Selection Drag Shape", default="RECTANGLE", options={"SKIP_SAVE"}
    )
    bpy.types.Scene.blendgimp_selection_drag_x1 = bpy.props.FloatProperty(default=0.0, options={"SKIP_SAVE"})
    bpy.types.Scene.blendgimp_selection_drag_y1 = bpy.props.FloatProperty(default=0.0, options={"SKIP_SAVE"})
    bpy.types.Scene.blendgimp_selection_drag_x2 = bpy.props.FloatProperty(default=0.0, options={"SKIP_SAVE"})
    bpy.types.Scene.blendgimp_selection_drag_y2 = bpy.props.FloatProperty(default=0.0, options={"SKIP_SAVE"})
    bpy.types.Scene.blendgimp_auto_pointer_routing = bpy.props.BoolProperty(
        name="Automatic Paint Routing",
        description=(
            "Automatically arm BlendGimp Texture/Object Paint and route input by pointer region; "
            "Blender UI regions keep normal input"
        ),
        default=True,
        update=_auto_pointer_routing_update,
    )

    if _SELECTION_DRAW_HANDLER is None:
        try:
            _SELECTION_DRAW_HANDLER = bpy.types.SpaceImageEditor.draw_handler_add(
                _draw_selection_overlay, (), "WINDOW", "POST_PIXEL"
            )
        except Exception as exc:
            _SELECTION_DRAW_HANDLER = None
            print(f"BLENDGIMP: Selection overlay handler unavailable: {exc}")

    if not bpy.app.timers.is_registered(blendgimp_texture_editor_timer):
        bpy.app.timers.register(
            blendgimp_texture_editor_timer,
            first_interval=TEXTURE_EDITOR_POLL_INTERVAL,
            persistent=True,
        )

    print(f"BLENDGIMP: {DISPLAY_NAME} texture editor registered")


def unregister():
    global _SELECTION_DRAW_HANDLER
    _reset_texture_editor_runtime_state()

    if _SELECTION_DRAW_HANDLER is not None:
        try:
            bpy.types.SpaceImageEditor.draw_handler_remove(_SELECTION_DRAW_HANDLER, "WINDOW")
        except Exception:
            pass
        _SELECTION_DRAW_HANDLER = None

    try:
        if bpy.app.timers.is_registered(blendgimp_texture_editor_timer):
            bpy.app.timers.unregister(blendgimp_texture_editor_timer)
    except Exception:
        pass

    property_names = (
        "blendgimp_selection_points_json",
        "blendgimp_selection_radius",
        "blendgimp_selection_sample_transparent",
        "blendgimp_selection_sample_merged",
        "blendgimp_selection_threshold",
        "blendgimp_selection_operation",
        "blendgimp_selection_drag_y2",
        "blendgimp_selection_drag_x2",
        "blendgimp_selection_drag_y1",
        "blendgimp_selection_drag_x1",
        "blendgimp_selection_drag_shape",
        "blendgimp_selection_drag_active",
        "blendgimp_selection_interaction_active",
        "blendgimp_selection_shape",
        "blendgimp_selection_y2",
        "blendgimp_selection_x2",
        "blendgimp_selection_y1",
        "blendgimp_selection_x1",
        "blendgimp_selection_image_id",
        "blendgimp_selection_bounds_valid",
        "blendgimp_selection_active",
        "blendgimp_selection_overlay",
        "blendgimp_auto_pointer_routing",
        "blendgimp_texture_editor_status",
        "blendgimp_texture_editor_active_face_highlight",
        "blendgimp_texture_editor_uv_edge_style",
        "blendgimp_texture_editor_show_islands",
        "blendgimp_texture_editor_uv_opacity",
        "blendgimp_texture_editor_show_uv",
        "blendgimp_texture_editor_display_channels",
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

    print(f"BLENDGIMP: {DISPLAY_NAME} texture editor unregistered")
