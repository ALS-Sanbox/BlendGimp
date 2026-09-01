#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import json
import socket
import threading
import time
import traceback
import os
import tempfile
import uuid
import hashlib
import base64

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gimp, GLib, Gio, Gegl


# -----------------------------------------------------------------------------
# BlendGimp IPC configuration
# -----------------------------------------------------------------------------

PLUGIN_PROC = "extension-blendgimp"
HOST = "127.0.0.1"
PORT = 8765
PROTOCOL_VERSION = 1

# One generated token per open GIMP image ID for the lifetime of this
# persistent BlendGimp plug-in session. This keeps refresh paths stable while
# avoiding dependence on user document names such as "Demo.xcf".
BLENDGIMP_COMPOSITE_TOKENS = {}

# Per-image visual state maintained only for the lifetime of the persistent
# BlendGimp GIMP plug-in process. `revision` increases whenever the visible
# composite fingerprint changes. It does not alter GIMP's own dirty/save state.
BLENDGIMP_IMAGE_VISUAL_STATE = {}

# Last full-resolution visible composite per GIMP image. This is BlendGimp's
# Stage-1 dirty-rectangle baseline. It is in-memory only and never modifies the
# user's GIMP document.
BLENDGIMP_PIXEL_SNAPSHOTS = {}

# Native GEGL damage trackers, one per open GIMP image. These listen to the
# drawable buffers' "changed" signal and accumulate the exact image-space
# rectangle touched by painting/edit operations.
BLENDGIMP_DAMAGE_TRACKERS = {}

# Blender-originated writes are temporarily tracked by the final composited
# pixel hash. IMAGE_STATE uses this to distinguish a delayed thumbnail/cache
# change caused by our own write from a real subsequent GIMP edit.
BLENDGIMP_ECHO_SUPPRESSION = {}
BLENDGIMP_ECHO_SUPPRESSION_SECONDS = 2.0

# GIMP supports thumbnails up to 1024px. This is now a compatibility fallback
# only when GEGL Buffer::changed cannot be connected in the Python runtime.
# Stage-1 fallback detector that runs inside GIMP and sends only a hash.
BLENDGIMP_STATE_THUMBNAIL_SIZE = 1024

BLENDGIMP_PAINT_LAYER_NAME = "BlendGimp Paint"

# Live direct 3D strokes keep one GIMP undo group open while Blender streams
# several paintbrush chunks. The server closes any remaining groups if the
# Blender socket disconnects unexpectedly.
BLENDGIMP_ACTIVE_DIRECT_STROKES = {}


def _blendgimp_composite_token(image_id):
    image_id = int(image_id)

    token = BLENDGIMP_COMPOSITE_TOKENS.get(
        image_id
    )

    if token is None:
        token = uuid.uuid4().hex
        BLENDGIMP_COMPOSITE_TOKENS[
            image_id
        ] = token

    return token


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

def log(message):
    print(f"BLENDGIMP-GIMP: {message}", flush=True)


# -----------------------------------------------------------------------------
# GIMP main-thread dispatcher
# -----------------------------------------------------------------------------

class GimpMainThreadDispatcher:
    """Marshal GIMP API work from worker threads onto GIMP's main thread."""

    def __init__(self):
        # Constructed from the persistent procedure's run function, which is
        # executing on GIMP's plug-in main thread.
        self._main_thread_id = threading.get_ident()

    def call(self, function, *args, timeout=5.0, **kwargs):
        """Run *function* on GIMP's main thread and return its result."""

        # Avoid scheduling through GLib when already on the GIMP main thread.
        if threading.get_ident() == self._main_thread_id:
            return function(*args, **kwargs)

        finished = threading.Event()
        result = {}

        def run_on_main_thread():
            try:
                result["value"] = function(*args, **kwargs)
            except Exception as exc:
                result["error"] = exc
                result["traceback"] = traceback.format_exc()
            finally:
                finished.set()

            # GLib.idle_add() should invoke this callback once only.
            return GLib.SOURCE_REMOVE

        GLib.idle_add(run_on_main_thread)

        if not finished.wait(timeout):
            raise TimeoutError(
                f"Timed out after {timeout:.1f}s waiting for GIMP main-thread command"
            )

        if "error" in result:
            log("Main-thread command failed:\n" + result.get("traceback", ""))
            raise result["error"]

        return result.get("value")


# -----------------------------------------------------------------------------
# Read-only GIMP commands
# -----------------------------------------------------------------------------

def gimp_get_images_snapshot():
    """
    MAIN THREAD ONLY.

    Return metadata for every image currently open in GIMP.
    This does not read pixel buffers and does not modify image state.
    """

    images_snapshot = []

    for image in Gimp.get_images():
        name = image.get_name()

        images_snapshot.append(
            {
                "id": int(image.get_id()),
                "name": "" if name is None else str(name),
                "width": int(image.get_width()),
                "height": int(image.get_height()),
            }
        )

    return images_snapshot



# GIMP 3.2.4 layer-mode names supported by BlendGimp.
BLENDGIMP_LAYER_MODE_NAMES = ['NORMAL_LEGACY', 'DISSOLVE', 'BEHIND_LEGACY', 'MULTIPLY_LEGACY', 'SCREEN_LEGACY', 'OVERLAY_LEGACY', 'DIFFERENCE_LEGACY', 'ADDITION_LEGACY', 'SUBTRACT_LEGACY', 'DARKEN_ONLY_LEGACY', 'LIGHTEN_ONLY_LEGACY', 'HSV_HUE_LEGACY', 'HSV_SATURATION_LEGACY', 'HSL_COLOR_LEGACY', 'HSV_VALUE_LEGACY', 'DIVIDE_LEGACY', 'DODGE_LEGACY', 'BURN_LEGACY', 'HARDLIGHT_LEGACY', 'SOFTLIGHT_LEGACY', 'GRAIN_EXTRACT_LEGACY', 'GRAIN_MERGE_LEGACY', 'COLOR_ERASE_LEGACY', 'OVERLAY', 'LCH_HUE', 'LCH_CHROMA', 'LCH_COLOR', 'LCH_LIGHTNESS', 'NORMAL', 'BEHIND', 'MULTIPLY', 'SCREEN', 'DIFFERENCE', 'ADDITION', 'SUBTRACT', 'DARKEN_ONLY', 'LIGHTEN_ONLY', 'HSV_HUE', 'HSV_SATURATION', 'HSL_COLOR', 'HSV_VALUE', 'DIVIDE', 'DODGE', 'BURN', 'HARDLIGHT', 'SOFTLIGHT', 'GRAIN_EXTRACT', 'GRAIN_MERGE', 'VIVID_LIGHT', 'PIN_LIGHT', 'LINEAR_LIGHT', 'HARD_MIX', 'EXCLUSION', 'LINEAR_BURN', 'LUMA_DARKEN_ONLY', 'LUMA_LIGHTEN_ONLY', 'LUMINANCE', 'COLOR_ERASE', 'ERASE', 'MERGE', 'SPLIT', 'PASS_THROUGH', 'REPLACE', 'OVERWRITE']


def _gimp_layer_mode_name(mode):
    """
    MAIN THREAD ONLY.

    Convert a Gimp.LayerMode enum value to its stable Python enum name.
    """

    mode_value = int(mode)

    for mode_name in BLENDGIMP_LAYER_MODE_NAMES:

        enum_value = getattr(
            Gimp.LayerMode,
            mode_name,
            None
        )

        if (
            enum_value is not None
            and int(enum_value) == mode_value
        ):
            return mode_name

    return f"UNKNOWN_{mode_value}"


def _gimp_layer_snapshot(layer, selected_ids):
    """
    MAIN THREAD ONLY.

    Convert a GIMP layer (including group layers) into JSON-safe metadata.
    Group children are collected recursively from topmost to bottommost.
    """

    layer_id = int(layer.get_id())
    name = layer.get_name()
    is_group = bool(layer.is_group())

    image = layer.get_image()

    parent_item = layer.get_parent()

    parent_id = (
        None
        if parent_item is None
        else int(parent_item.get_id())
    )

    position = (
        int(image.get_item_position(layer))
        if image is not None and image.is_valid()
        else -1
    )

    children = []

    if is_group:
        for child_item in layer.get_children():
            child_id = int(child_item.get_id())

            # get_children() is typed as Gimp.Item. Resolve it back to a
            # Gimp.Layer so layer-specific methods such as get_opacity()
            # are always available through the Python binding.
            child_layer = Gimp.Layer.get_by_id(child_id)

            if child_layer is None:
                continue

            children.append(
                _gimp_layer_snapshot(
                    child_layer,
                    selected_ids,
                )
            )

    return {
        "id": layer_id,
        "name": "" if name is None else str(name),
        "visible": bool(layer.get_visible()),
        "opacity": float(layer.get_opacity()),
        "is_group": is_group,
        "selected": layer_id in selected_ids,
        "parent_id": parent_id,
        "position": position,
        "lock_content": bool(layer.get_lock_content()),
        "lock_position": bool(layer.get_lock_position()),
        "lock_alpha": bool(layer.get_lock_alpha()),
        "mode": _gimp_layer_mode_name(
            layer.get_mode()
        ),
        "mode_value": int(
            layer.get_mode()
        ),
        "children": children,
    }


def gimp_get_image_layers_snapshot(image_id):
    """
    MAIN THREAD ONLY.

    Return the complete layer tree for one currently open GIMP image.

    This is read-only:
        - no layer state is changed
        - no pixels are read
        - no selections are changed
        - no image is saved
    """

    image_id = int(image_id)

    image = Gimp.Image.get_by_id(image_id)

    if image is None or not image.is_valid():
        raise ValueError(
            f"Image ID {image_id} is not a valid open GIMP image"
        )

    selected_ids = set()

    for selected_layer in image.get_selected_layers():
        selected_ids.add(
            int(selected_layer.get_id())
        )

    layers = []

    for layer in image.get_layers():
        layers.append(
            _gimp_layer_snapshot(
                layer,
                selected_ids,
            )
        )

    def count_layers(items):
        total = 0

        for item in items:
            total += 1
            total += count_layers(
                item.get("children", [])
            )

        return total

    image_name = image.get_name()

    return {
        "image_id": image_id,
        "image_name": (
            ""
            if image_name is None
            else str(image_name)
        ),
        "root_count": len(layers),
        "layer_count": count_layers(layers),
        "layers": layers,
    }


# -----------------------------------------------------------------------------
# Layer mutation helpers
# -----------------------------------------------------------------------------

def _gimp_resolve_image_layer(image_id, layer_id):
    """
    MAIN THREAD ONLY.

    Resolve and validate an image/layer pair.  The layer must currently
    belong to the requested image.
    """

    image_id = int(image_id)
    layer_id = int(layer_id)

    image = Gimp.Image.get_by_id(image_id)

    if image is None or not image.is_valid():
        raise ValueError(
            f"Image ID {image_id} is not a valid open GIMP image"
        )

    layer = Gimp.Layer.get_by_id(layer_id)

    if layer is None or not layer.is_valid():
        raise ValueError(
            f"Layer ID {layer_id} is not a valid GIMP layer"
        )

    owner_image = layer.get_image()

    if (
        owner_image is None
        or not owner_image.is_valid()
        or int(owner_image.get_id()) != image_id
    ):
        raise ValueError(
            f"Layer ID {layer_id} does not belong to image ID {image_id}"
        )

    return image, layer


def gimp_set_active_layer(image_id, layer_id):
    """
    MAIN THREAD ONLY.

    Select exactly one layer in the requested image.
    """

    image, layer = _gimp_resolve_image_layer(
        image_id,
        layer_id,
    )

    success = image.set_selected_layers(
        [layer]
    )

    if not success:
        raise RuntimeError(
            f"GIMP could not select layer ID {layer_id}"
        )

    Gimp.displays_flush()

    return {
        "image_id": int(image.get_id()),
        "layer_id": int(layer.get_id()),
        "selected": True,
    }


def gimp_set_layer_visibility(
    image_id,
    layer_id,
    visible,
):
    """
    MAIN THREAD ONLY.

    Set one layer's visibility.
    """

    image, layer = _gimp_resolve_image_layer(
        image_id,
        layer_id,
    )

    visible = bool(visible)

    success = layer.set_visible(
        visible
    )

    if not success:
        raise RuntimeError(
            f"GIMP could not change visibility for layer ID {layer_id}"
        )

    Gimp.displays_flush()

    return {
        "image_id": int(image.get_id()),
        "layer_id": int(layer.get_id()),
        "visible": bool(layer.get_visible()),
    }


def gimp_set_layer_opacity(
    image_id,
    layer_id,
    opacity,
):
    """
    MAIN THREAD ONLY.

    Set one layer's opacity in GIMP's native 0..100 range.
    """

    image, layer = _gimp_resolve_image_layer(
        image_id,
        layer_id,
    )

    opacity = float(opacity)

    if opacity < 0.0:
        opacity = 0.0
    elif opacity > 100.0:
        opacity = 100.0

    success = layer.set_opacity(
        opacity
    )

    if not success:
        raise RuntimeError(
            f"GIMP could not change opacity for layer ID {layer_id}"
        )

    Gimp.displays_flush()

    return {
        "image_id": int(image.get_id()),
        "layer_id": int(layer.get_id()),
        "opacity": float(layer.get_opacity()),
    }



# -----------------------------------------------------------------------------
# Full layer-management helpers
# -----------------------------------------------------------------------------

def _gimp_resolve_group_parent(
    image,
    parent_id,
):
    """
    MAIN THREAD ONLY.

    Resolve an optional target group. None / negative means top level.
    """

    if parent_id is None:
        return None

    parent_id = int(parent_id)

    if parent_id < 0:
        return None

    parent = Gimp.Layer.get_by_id(
        parent_id
    )

    if parent is None or not parent.is_valid():
        raise ValueError(
            f"Parent layer ID {parent_id} is not valid"
        )

    if not parent.is_group():
        raise ValueError(
            f"Layer ID {parent_id} is not a group layer"
        )

    parent_image = parent.get_image()

    if (
        parent_image is None
        or not parent_image.is_valid()
        or int(parent_image.get_id()) != int(image.get_id())
    ):
        raise ValueError(
            f"Group layer ID {parent_id} does not belong to image ID {image.get_id()}"
        )

    return parent


def _gimp_get_layer_parent_as_layer(
    layer
):
    """
    MAIN THREAD ONLY.

    Return the layer's parent as Gimp.Layer, or None for top-level layers.
    """

    parent_item = layer.get_parent()

    if parent_item is None:
        return None

    parent_id = int(
        parent_item.get_id()
    )

    parent = Gimp.Layer.get_by_id(
        parent_id
    )

    if parent is None or not parent.is_valid():
        raise RuntimeError(
            f"Could not resolve parent layer ID {parent_id}"
        )

    return parent


def _gimp_new_layer_type_for_image(
    image
):
    """
    MAIN THREAD ONLY.

    Create new editable layers with alpha matching the image base type.
    """

    base_type = image.get_base_type()

    if base_type == Gimp.ImageBaseType.RGB:
        return Gimp.ImageType.RGBA_IMAGE

    if base_type == Gimp.ImageBaseType.GRAY:
        return Gimp.ImageType.GRAYA_IMAGE

    if base_type == Gimp.ImageBaseType.INDEXED:
        return Gimp.ImageType.INDEXEDA_IMAGE

    raise RuntimeError(
        f"Unsupported GIMP image base type: {base_type}"
    )


def gimp_add_layer(
    image_id,
    name,
):
    """
    MAIN THREAD ONLY.

    Create a full-image transparent layer above the selected layer at the
    same hierarchy level. If no layer is selected, create it at the top level.
    """

    image_id = int(
        image_id
    )

    image = Gimp.Image.get_by_id(
        image_id
    )

    if image is None or not image.is_valid():
        raise ValueError(
            f"Image ID {image_id} is not a valid open GIMP image"
        )

    name = str(
        name or "New Layer"
    ).strip()

    if not name:
        name = "New Layer"

    parent = None
    position = 0

    selected_layers = list(
        image.get_selected_layers()
    )

    if selected_layers:

        selected = selected_layers[0]

        parent = (
            _gimp_get_layer_parent_as_layer(
                selected
            )
        )

        position = int(
            image.get_item_position(
                selected
            )
        )

    layer = Gimp.Layer.new(
        image,
        name,
        int(image.get_width()),
        int(image.get_height()),
        _gimp_new_layer_type_for_image(
            image
        ),
        100.0,
        image.get_default_new_layer_mode(),
    )

    if layer is None or not layer.is_valid():
        raise RuntimeError(
            "GIMP could not create the new layer"
        )

    success = image.insert_layer(
        layer,
        parent,
        position,
    )

    if not success:
        raise RuntimeError(
            "GIMP could not insert the new layer"
        )

    image.set_selected_layers(
        [layer]
    )

    Gimp.displays_flush()

    parent_id = (
        None
        if parent is None
        else int(parent.get_id())
    )

    return {
        "image_id": image_id,
        "layer_id": int(layer.get_id()),
        "name": str(layer.get_name() or ""),
        "parent_id": parent_id,
        "position": int(
            image.get_item_position(
                layer
            )
        ),
    }


def gimp_delete_layer(
    image_id,
    layer_id,
):
    """
    MAIN THREAD ONLY.

    Remove a layer or group layer from the image.
    """

    image, layer = (
        _gimp_resolve_image_layer(
            image_id,
            layer_id,
        )
    )

    name = str(
        layer.get_name() or ""
    )

    success = image.remove_layer(
        layer
    )

    if not success:
        raise RuntimeError(
            f"GIMP could not delete layer ID {layer_id}"
        )

    Gimp.displays_flush()

    return {
        "image_id": int(image.get_id()),
        "layer_id": int(layer_id),
        "name": name,
        "deleted": True,
    }


def gimp_rename_layer(
    image_id,
    layer_id,
    name,
):
    """
    MAIN THREAD ONLY.

    Rename a layer or group layer.
    """

    image, layer = (
        _gimp_resolve_image_layer(
            image_id,
            layer_id,
        )
    )

    name = str(
        name or ""
    ).strip()

    if not name:
        raise ValueError(
            "Layer name cannot be empty"
        )

    success = layer.set_name(
        name
    )

    if not success:
        raise RuntimeError(
            f"GIMP could not rename layer ID {layer_id}"
        )

    Gimp.displays_flush()

    return {
        "image_id": int(image.get_id()),
        "layer_id": int(layer.get_id()),
        "name": str(layer.get_name() or ""),
    }


def gimp_duplicate_layer(
    image_id,
    layer_id,
):
    """
    MAIN THREAD ONLY.

    Duplicate a layer or group layer directly above the source item.
    """

    image, layer = (
        _gimp_resolve_image_layer(
            image_id,
            layer_id,
        )
    )

    parent = (
        _gimp_get_layer_parent_as_layer(
            layer
        )
    )

    position = int(
        image.get_item_position(
            layer
        )
    )

    duplicate = layer.copy()

    if duplicate is None or not duplicate.is_valid():
        raise RuntimeError(
            f"GIMP could not copy layer ID {layer_id}"
        )

    original_name = str(
        layer.get_name() or "Layer"
    )

    duplicate.set_name(
        f"{original_name} copy"
    )

    success = image.insert_layer(
        duplicate,
        parent,
        position,
    )

    if not success:
        raise RuntimeError(
            f"GIMP could not insert duplicate of layer ID {layer_id}"
        )

    image.set_selected_layers(
        [duplicate]
    )

    Gimp.displays_flush()

    parent_id = (
        None
        if parent is None
        else int(parent.get_id())
    )

    return {
        "image_id": int(image.get_id()),
        "source_layer_id": int(layer.get_id()),
        "layer_id": int(duplicate.get_id()),
        "name": str(duplicate.get_name() or ""),
        "parent_id": parent_id,
        "position": int(
            image.get_item_position(
                duplicate
            )
        ),
    }


def gimp_reorder_layer(
    image_id,
    layer_id,
    direction,
):
    """
    MAIN THREAD ONLY.

    Move a layer one step up or down within its current hierarchy level.
    """

    image, layer = (
        _gimp_resolve_image_layer(
            image_id,
            layer_id,
        )
    )

    direction = str(
        direction or ""
    ).upper()

    if direction == "UP":
        success = image.raise_item(
            layer
        )

    elif direction == "DOWN":
        success = image.lower_item(
            layer
        )

    else:
        raise ValueError(
            "direction must be UP or DOWN"
        )

    if not success:
        raise RuntimeError(
            f"GIMP could not move layer ID {layer_id} {direction.lower()}"
        )

    Gimp.displays_flush()

    parent_item = layer.get_parent()

    parent_id = (
        None
        if parent_item is None
        else int(parent_item.get_id())
    )

    return {
        "image_id": int(image.get_id()),
        "layer_id": int(layer.get_id()),
        "direction": direction,
        "parent_id": parent_id,
        "position": int(
            image.get_item_position(
                layer
            )
        ),
    }


def gimp_move_layer_to_parent(
    image_id,
    layer_id,
    parent_id,
):
    """
    MAIN THREAD ONLY.

    Move a layer or group layer into a target group, or to the image's
    top-level layer stack when parent_id is None / negative.

    The item is placed at position 0 in the target level. Fine reordering can
    then be done with REORDER_LAYER.
    """

    image, layer = (
        _gimp_resolve_image_layer(
            image_id,
            layer_id,
        )
    )

    parent = (
        _gimp_resolve_group_parent(
            image,
            parent_id,
        )
    )

    if (
        parent is not None
        and int(parent.get_id()) == int(layer.get_id())
    ):
        raise ValueError(
            "A layer group cannot be moved into itself"
        )

    success = image.reorder_item(
        layer,
        parent,
        0,
    )

    if not success:
        raise RuntimeError(
            f"GIMP could not move layer ID {layer_id}"
        )

    image.set_selected_layers(
        [layer]
    )

    Gimp.displays_flush()

    actual_parent_item = layer.get_parent()

    actual_parent_id = (
        None
        if actual_parent_item is None
        else int(actual_parent_item.get_id())
    )

    return {
        "image_id": int(image.get_id()),
        "layer_id": int(layer.get_id()),
        "parent_id": actual_parent_id,
        "position": int(
            image.get_item_position(
                layer
            )
        ),
    }



# -----------------------------------------------------------------------------
# Remaining layer-feature helpers
# -----------------------------------------------------------------------------

def gimp_create_group(
    image_id,
    name,
):
    """
    MAIN THREAD ONLY.

    Create a group at the same hierarchy level as the selected layer,
    immediately above it, and make the group active.
    """

    image_id = int(
        image_id
    )

    image = Gimp.Image.get_by_id(
        image_id
    )

    if image is None or not image.is_valid():
        raise ValueError(
            f"Image ID {image_id} is not a valid open GIMP image"
        )

    name = str(
        name or "Layer Group"
    ).strip()

    if not name:
        name = "Layer Group"

    parent = None
    position = 0

    selected_layers = list(
        image.get_selected_layers()
    )

    if selected_layers:

        selected = selected_layers[0]

        parent = (
            _gimp_get_layer_parent_as_layer(
                selected
            )
        )

        position = int(
            image.get_item_position(
                selected
            )
        )

    group = Gimp.GroupLayer.new(
        image,
        name,
    )

    if group is None or not group.is_valid():
        raise RuntimeError(
            "GIMP could not create the group layer"
        )

    success = image.insert_layer(
        group,
        parent,
        position,
    )

    if not success:
        raise RuntimeError(
            "GIMP could not insert the group layer"
        )

    image.set_selected_layers(
        [group]
    )

    Gimp.displays_flush()

    return {
        "image_id": image_id,
        "layer_id": int(group.get_id()),
        "name": str(group.get_name() or ""),
        "parent_id": (
            None
            if parent is None
            else int(parent.get_id())
        ),
        "position": int(
            image.get_item_position(
                group
            )
        ),
    }


def gimp_merge_layer_down(
    image_id,
    layer_id,
):
    """
    MAIN THREAD ONLY.

    Merge the requested layer with the first visible layer below it.
    The merged layer expands as necessary.
    """

    image, layer = (
        _gimp_resolve_image_layer(
            image_id,
            layer_id,
        )
    )

    source_name = str(
        layer.get_name() or ""
    )

    merged = image.merge_down(
        layer,
        Gimp.MergeType.EXPAND_AS_NECESSARY,
    )

    if merged is None or not merged.is_valid():
        raise RuntimeError(
            f"GIMP could not merge layer ID {layer_id} down. "
            "Make sure a visible layer exists below it."
        )

    image.set_selected_layers(
        [merged]
    )

    Gimp.displays_flush()

    return {
        "image_id": int(image.get_id()),
        "source_layer_id": int(layer_id),
        "source_name": source_name,
        "layer_id": int(merged.get_id()),
        "name": str(merged.get_name() or ""),
    }


def gimp_set_layer_lock(
    image_id,
    layer_id,
    lock_type,
    locked,
):
    """
    MAIN THREAD ONLY.

    Set content, position, or alpha lock state.
    """

    image, layer = (
        _gimp_resolve_image_layer(
            image_id,
            layer_id,
        )
    )

    lock_type = str(
        lock_type or ""
    ).upper()

    locked = bool(
        locked
    )

    if lock_type == "CONTENT":

        success = layer.set_lock_content(
            locked
        )

        actual = bool(
            layer.get_lock_content()
        )

    elif lock_type == "POSITION":

        success = layer.set_lock_position(
            locked
        )

        actual = bool(
            layer.get_lock_position()
        )

    elif lock_type == "ALPHA":

        success = layer.set_lock_alpha(
            locked
        )

        actual = bool(
            layer.get_lock_alpha()
        )

    else:

        raise ValueError(
            "lock_type must be CONTENT, POSITION, or ALPHA"
        )

    if not success:
        raise RuntimeError(
            f"GIMP could not set {lock_type.lower()} lock "
            f"for layer ID {layer_id}"
        )

    Gimp.displays_flush()

    return {
        "image_id": int(image.get_id()),
        "layer_id": int(layer.get_id()),
        "lock_type": lock_type,
        "locked": actual,
    }


def gimp_set_layer_mode(
    image_id,
    layer_id,
    mode_name,
):
    """
    MAIN THREAD ONLY.

    Change a layer's GIMP blend/combination mode.
    """

    image, layer = (
        _gimp_resolve_image_layer(
            image_id,
            layer_id,
        )
    )

    mode_name = str(
        mode_name or ""
    ).upper()

    if mode_name not in BLENDGIMP_LAYER_MODE_NAMES:
        raise ValueError(
            f"Unsupported GIMP layer mode: {mode_name}"
        )

    mode = getattr(
        Gimp.LayerMode,
        mode_name,
        None
    )

    if mode is None:
        raise ValueError(
            f"GIMP runtime does not expose layer mode {mode_name}"
        )

    success = layer.set_mode(
        mode
    )

    if not success:
        raise RuntimeError(
            f"GIMP could not set blend mode {mode_name} "
            f"for layer ID {layer_id}"
        )

    Gimp.displays_flush()

    actual_mode = layer.get_mode()

    return {
        "image_id": int(image.get_id()),
        "layer_id": int(layer.get_id()),
        "mode": _gimp_layer_mode_name(
            actual_mode
        ),
        "mode_value": int(
            actual_mode
        ),
    }



def _blendgimp_find_named_raster_layer(
    image,
    name
):
    target_name = str(
        name
    )

    def search_layers(
        layers
    ):
        for item in layers:
            layer = Gimp.Layer.get_by_id(
                int(
                    item.get_id()
                )
            )

            if layer is None or not layer.is_valid():
                continue

            if (
                not layer.is_group()
                and str(
                    layer.get_name()
                    or ""
                ) == target_name
            ):
                return layer

            if layer.is_group():
                found = search_layers(
                    layer.get_children()
                )

                if found is not None:
                    return found

        return None

    return search_layers(
        image.get_layers()
    )


def gimp_ensure_blendgimp_paint_layer(
    image_id,
    name=BLENDGIMP_PAINT_LAYER_NAME
):
    """
    MAIN THREAD ONLY.

    Return a persistent full-image raster layer dedicated to paint originating
    from Blender's 3D Texture Paint workflow. Create it at the top level when
    it does not exist.
    """

    image_id = int(
        image_id
    )

    image = Gimp.Image.get_by_id(
        image_id
    )

    if image is None or not image.is_valid():
        raise ValueError(
            f"Image ID {image_id} is not a valid open GIMP image"
        )

    name = str(
        name
        or BLENDGIMP_PAINT_LAYER_NAME
    ).strip()

    if not name:
        name = BLENDGIMP_PAINT_LAYER_NAME

    layer = _blendgimp_find_named_raster_layer(
        image,
        name
    )

    created = False

    if layer is None:

        layer = Gimp.Layer.new(
            image,
            name,
            int(
                image.get_width()
            ),
            int(
                image.get_height()
            ),
            _gimp_new_layer_type_for_image(
                image
            ),
            100.0,
            image.get_default_new_layer_mode(),
        )

        if layer is None or not layer.is_valid():
            raise RuntimeError(
                "GIMP could not create the BlendGimp paint layer"
            )

        success = image.insert_layer(
            layer,
            None,
            0,
        )

        if not success:
            raise RuntimeError(
                "GIMP could not insert the BlendGimp paint layer"
            )

        created = True

    try:
        layer.set_visible(
            True
        )
    except Exception:
        pass

    try:
        layer.set_opacity(
            100.0
        )
    except Exception:
        pass

    Gimp.displays_flush()

    return {
        "image_id": image_id,
        "layer_id": int(
            layer.get_id()
        ),
        "name": str(
            layer.get_name()
            or name
        ),
        "created": bool(
            created
        ),
        "width": int(
            layer.get_width()
        ),
        "height": int(
            layer.get_height()
        ),
    }


def _blendgimp_accept_blender_originated_write(
    image_id
):
    """
    Re-baseline GIMP -> Blender state after a Blender-originated write.

    GIMP 3.2.4 may update the composite thumbnail cache slightly after the
    drawable write itself. A pure thumbnail fingerprint can therefore change
    on the next IMAGE_STATE poll even though the final composite pixels are
    exactly the pixels BlendGimp just accepted from Blender.

    Store the authoritative full-composite hash for a short verification
    window. IMAGE_STATE can then suppress only self-originated cache settling,
    while a real GIMP edit (different composite hash) still passes through.
    """

    image_id = int(
        image_id
    )

    image = Gimp.Image.get_by_id(
        image_id
    )

    if image is None or not image.is_valid():
        return

    try:
        width, height, composite_pixels = (
            _gimp_get_raw_composite_rgba(
                image
            )
        )

        _blendgimp_store_pixel_snapshot(
            image_id,
            width,
            height,
            composite_pixels
        )

        composite_sha256 = hashlib.sha256(
            composite_pixels
        ).hexdigest()

        fingerprint_info = (
            _gimp_visual_fingerprint(
                image
            )
        )

        tracker = (
            _blendgimp_ensure_damage_tracker(
                image
            )
        )

        previous_state = (
            BLENDGIMP_IMAGE_VISUAL_STATE.get(
                image_id
            )
            or {}
        )

        BLENDGIMP_IMAGE_VISUAL_STATE[
            image_id
        ] = {
            "fingerprint": fingerprint_info[
                "fingerprint"
            ],
            "native_revision": int(
                tracker.get(
                    "revision",
                    1
                )
            ),
            "revision": int(
                previous_state.get(
                    "revision",
                    1
                )
            ),
        }

        BLENDGIMP_ECHO_SUPPRESSION[
            image_id
        ] = {
            "composite_sha256": composite_sha256,
            "deadline": (
                time.monotonic()
                + BLENDGIMP_ECHO_SUPPRESSION_SECONDS
            ),
        }

        _blendgimp_clear_pending_damage(
            image_id
        )

    except Exception as exc:
        log(
            "Warning: could not establish Blender-originated "
            f"echo suppression for image ID {image_id}: {exc}"
        )


def _blendgimp_filter_self_originated_state_change(
    image,
    image_id,
    fingerprint,
    native_revision,
    fingerprint_changed,
    native_changed
):
    """
    Return (fingerprint_changed, native_changed, suppressed).

    Verification is only performed after a Blender-originated write and only
    when IMAGE_STATE would otherwise report a new change.
    """

    image_id = int(
        image_id
    )

    suppression = BLENDGIMP_ECHO_SUPPRESSION.get(
        image_id
    )

    if suppression is None:
        return (
            fingerprint_changed,
            native_changed,
            False,
        )

    now = time.monotonic()

    if now > float(
        suppression.get(
            "deadline",
            0.0
        )
    ):
        BLENDGIMP_ECHO_SUPPRESSION.pop(
            image_id,
            None
        )

        return (
            fingerprint_changed,
            native_changed,
            False,
        )

    # If nothing appears to have changed, the thumbnail/native state has
    # settled to our accepted baseline; suppression is no longer needed.
    if not fingerprint_changed and not native_changed:
        BLENDGIMP_ECHO_SUPPRESSION.pop(
            image_id,
            None
        )

        return (
            False,
            False,
            False,
        )

    try:
        width, height, current_pixels = (
            _gimp_get_raw_composite_rgba(
                image
            )
        )

        current_sha256 = hashlib.sha256(
            current_pixels
        ).hexdigest()

        expected_sha256 = str(
            suppression.get(
                "composite_sha256",
                ""
            )
        )

        if current_sha256 == expected_sha256:
            # The final composite is unchanged. Only the thumbnail/native
            # observation lagged behind. Accept the new observer state without
            # advancing BlendGimp's external revision.
            _blendgimp_store_pixel_snapshot(
                image_id,
                width,
                height,
                current_pixels
            )

            previous_state = (
                BLENDGIMP_IMAGE_VISUAL_STATE.get(
                    image_id
                )
                or {}
            )

            BLENDGIMP_IMAGE_VISUAL_STATE[
                image_id
            ] = {
                "fingerprint": fingerprint,
                "native_revision": int(
                    native_revision
                ),
                "revision": int(
                    previous_state.get(
                        "revision",
                        1
                    )
                ),
            }

            _blendgimp_clear_pending_damage(
                image_id
            )

            return (
                False,
                False,
                True,
            )

        # Composite pixels differ from the Blender-originated accepted state:
        # this is a real new GIMP-side edit, so stop suppressing immediately.
        BLENDGIMP_ECHO_SUPPRESSION.pop(
            image_id,
            None
        )

        return (
            fingerprint_changed,
            native_changed,
            False,
        )

    except Exception as exc:
        log(
            "Echo-suppression verification failed for "
            f"image ID {image_id}: {exc}"
        )

        BLENDGIMP_ECHO_SUPPRESSION.pop(
            image_id,
            None
        )

        return (
            fingerprint_changed,
            native_changed,
            False,
        )


# -----------------------------------------------------------------------------
# Direct Blender 3D stroke -> GIMP brush engine
# -----------------------------------------------------------------------------

def gimp_get_brush_state():
    """
    MAIN THREAD ONLY.

    Return the active GIMP brush context that direct 3D painting will use.
    """

    brush = Gimp.context_get_brush()

    brush_name = ""

    if brush is not None:
        try:
            brush_name = str(
                brush.get_name()
                or ""
            )
        except Exception:
            brush_name = str(
                brush
            )

    try:
        dynamics_name = str(
            Gimp.context_get_dynamics_name()
            or ""
        )
    except Exception:
        dynamics_name = ""

    try:
        emulate_dynamics = bool(
            Gimp.context_get_emulate_brush_dynamics()
        )
    except Exception:
        emulate_dynamics = False

    return {
        "brush_name": brush_name,
        "brush_size": float(
            Gimp.context_get_brush_size()
        ),
        "brush_opacity": float(
            Gimp.context_get_opacity()
        ),
        "brush_spacing": int(
            Gimp.context_get_brush_spacing()
        ),
        "paint_method": str(
            Gimp.context_get_paint_method()
            or ""
        ),
        "dynamics_name": dynamics_name,
        "emulate_dynamics": emulate_dynamics,
    }


def gimp_paint_stroke(
    image_id,
    layer_id,
    strokes
):
    """
    MAIN THREAD ONLY.

    Paint one UV-space stroke with GIMP's active paintbrush. `strokes` is a
    flat list:
        [x0, y0, x1, y1, ...]

    This deliberately does NOT invoke Blender-originated pixel echo
    suppression. GIMP owns this paint operation, so normal GIMP -> Blender
    Auto Sync must observe the resulting raster change.
    """

    image_id = int(
        image_id
    )

    layer_id = int(
        layer_id
    )

    image, layer = _gimp_resolve_image_layer(
        image_id,
        layer_id
    )

    if layer.is_group():
        raise ValueError(
            "Direct GIMP painting requires a raster layer"
        )

    if not isinstance(
        strokes,
        (list, tuple)
    ):
        raise ValueError(
            "Stroke coordinates must be a list"
        )

    if len(
        strokes
    ) < 2:
        raise ValueError(
            "A GIMP stroke requires at least one x/y point"
        )

    if (
        len(
            strokes
        )
        % 2
        != 0
    ):
        raise ValueError(
            "GIMP stroke coordinate count must be even"
        )

    if len(
        strokes
    ) > 32768:
        raise ValueError(
            "GIMP stroke contains too many coordinate values"
        )

    coordinates = [
        float(
            value
        )
        for value in strokes
    ]

    # GIMP 3 Python GI hides the C array-length argument on current builds.
    # Retain the explicit-length fallback for binding compatibility.
    try:
        success = Gimp.paintbrush_default(
            layer,
            coordinates
        )
    except TypeError:
        success = Gimp.paintbrush_default(
            layer,
            len(
                coordinates
            ),
            coordinates
        )

    if success is False:
        raise RuntimeError(
            "GIMP paintbrush_default returned failure"
        )

    Gimp.displays_flush()

    brush_state = gimp_get_brush_state()

    return {
        "image_id": image_id,
        "layer_id": layer_id,
        "point_count": int(
            len(
                coordinates
            )
            // 2
        ),
        **brush_state,
    }


def gimp_begin_direct_paint_stroke(
    image_id,
    layer_id,
    stroke_id
):
    """
    MAIN THREAD ONLY.

    Open one GIMP undo group for a streamed Blender 3D stroke.
    """

    image_id = int(
        image_id
    )

    layer_id = int(
        layer_id
    )

    stroke_id = str(
        stroke_id
    ).strip()

    if not stroke_id:
        raise ValueError(
            "Direct paint stroke ID is required"
        )

    image, layer = _gimp_resolve_image_layer(
        image_id,
        layer_id
    )

    if layer.is_group():
        raise ValueError(
            "Direct GIMP painting requires a raster layer"
        )

    if stroke_id in BLENDGIMP_ACTIVE_DIRECT_STROKES:
        raise ValueError(
            f"Direct paint stroke {stroke_id} is already active"
        )

    # Only one direct stroke should remain open for a given image. If a stale
    # one survived an interrupted client operation, close it before opening
    # the new group.
    stale_ids = [
        active_id
        for active_id, active in BLENDGIMP_ACTIVE_DIRECT_STROKES.items()
        if int(
            active.get(
                "image_id",
                -1
            )
        ) == image_id
    ]

    for stale_id in stale_ids:

        stale = BLENDGIMP_ACTIVE_DIRECT_STROKES.pop(
            stale_id,
            None
        )

        if stale is None:
            continue

        stale_image = Gimp.Image.get_by_id(
            int(
                stale.get(
                    "image_id",
                    -1
                )
            )
        )

        if stale_image is not None and stale_image.is_valid():

            try:
                stale_image.undo_group_end()
            except Exception:
                pass

    if not image.undo_group_start():
        raise RuntimeError(
            "GIMP could not start the direct-paint undo group"
        )

    BLENDGIMP_ACTIVE_DIRECT_STROKES[
        stroke_id
    ] = {
        "image_id": image_id,
        "layer_id": layer_id,
        "chunk_count": 0,
        "point_count": 0,
    }

    return {
        "stroke_id": stroke_id,
        "image_id": image_id,
        "layer_id": layer_id,
        **gimp_get_brush_state(),
    }


def gimp_paint_direct_stroke_chunk(
    image_id,
    layer_id,
    stroke_id,
    strokes
):
    """
    MAIN THREAD ONLY.

    Paint one chunk inside an already-open undo group.
    """

    image_id = int(
        image_id
    )

    layer_id = int(
        layer_id
    )

    stroke_id = str(
        stroke_id
    )

    active = BLENDGIMP_ACTIVE_DIRECT_STROKES.get(
        stroke_id
    )

    if active is None:
        raise ValueError(
            f"Direct paint stroke {stroke_id} is not active"
        )

    if (
        int(
            active.get(
                "image_id",
                -1
            )
        ) != image_id
        or int(
            active.get(
                "layer_id",
                -1
            )
        ) != layer_id
    ):
        raise ValueError(
            "Direct paint stroke target changed while streaming"
        )

    result = gimp_paint_stroke(
        image_id,
        layer_id,
        strokes
    )

    active[
        "chunk_count"
    ] = int(
        active.get(
            "chunk_count",
            0
        )
    ) + 1

    active[
        "point_count"
    ] = int(
        active.get(
            "point_count",
            0
        )
    ) + int(
        result.get(
            "point_count",
            0
        )
    )

    return {
        "stroke_id": stroke_id,
        "chunk_index": int(
            active[
                "chunk_count"
            ]
        ),
        "total_point_count": int(
            active[
                "point_count"
            ]
        ),
        **result,
    }


def gimp_end_direct_paint_stroke(
    stroke_id
):
    """
    MAIN THREAD ONLY.

    Close the undo group for one streamed stroke.
    """

    stroke_id = str(
        stroke_id
    )

    active = BLENDGIMP_ACTIVE_DIRECT_STROKES.pop(
        stroke_id,
        None
    )

    if active is None:
        return {
            "stroke_id": stroke_id,
            "ended": False,
            "chunk_count": 0,
            "point_count": 0,
        }

    image_id = int(
        active.get(
            "image_id",
            -1
        )
    )

    image = Gimp.Image.get_by_id(
        image_id
    )

    if image is not None and image.is_valid():

        if not image.undo_group_end():
            raise RuntimeError(
                "GIMP could not close the direct-paint undo group"
            )

    Gimp.displays_flush()

    return {
        "stroke_id": stroke_id,
        "ended": True,
        "image_id": image_id,
        "layer_id": int(
            active.get(
                "layer_id",
                -1
            )
        ),
        "chunk_count": int(
            active.get(
                "chunk_count",
                0
            )
        ),
        "point_count": int(
            active.get(
                "point_count",
                0
            )
        ),
    }


def gimp_close_all_direct_paint_strokes():
    """
    MAIN THREAD ONLY.

    Safety cleanup for a Blender disconnect or GIMP plug-in shutdown.
    """

    active_ids = list(
        BLENDGIMP_ACTIVE_DIRECT_STROKES.keys()
    )

    closed = 0

    for stroke_id in active_ids:

        try:
            result = gimp_end_direct_paint_stroke(
                stroke_id
            )

            if result.get(
                "ended",
                False
            ):
                closed += 1

        except Exception as exc:
            log(
                "Could not close stale direct paint stroke "
                f"{stroke_id}: {exc}"
            )

    return closed


# -----------------------------------------------------------------------------
# Blender -> GIMP binary pixel writes
# -----------------------------------------------------------------------------

def _blendgimp_crop_rgba_region(
    raw_pixels,
    source_width,
    source_height,
    crop_x,
    crop_y,
    crop_width,
    crop_height
):
    """
    Crop top-left-origin RGBA8 bytes.
    """

    if (
        crop_x == 0
        and crop_y == 0
        and crop_width == source_width
        and crop_height == source_height
    ):
        return bytes(
            raw_pixels
        )

    source_row_bytes = (
        source_width
        * 4
    )

    crop_row_bytes = (
        crop_width
        * 4
    )

    result = bytearray(
        crop_row_bytes
        * crop_height
    )

    for row in range(
        crop_height
    ):

        source_start = (
            (
                crop_y
                + row
            )
            * source_row_bytes
            + crop_x
            * 4
        )

        source_end = (
            source_start
            + crop_row_bytes
        )

        destination_start = (
            row
            * crop_row_bytes
        )

        result[
            destination_start:
            destination_start + crop_row_bytes
        ] = raw_pixels[
            source_start:
            source_end
        ]

    return bytes(
        result
    )


def gimp_set_layer_pixels_binary(
    image_id,
    layer_id,
    image_x,
    image_y,
    region_width,
    region_height,
    raw_pixels
):
    """
    MAIN THREAD ONLY.

    Apply top-left-origin straight RGBA8 pixels from Blender to a GIMP layer.

    Coordinates from Blender are image-space coordinates. They are converted
    to drawable-local coordinates before writing to GIMP's shadow buffer.

    The shadow buffer is merged with undo=True so a Blender-originated paint
    push remains undoable from GIMP.
    """

    image_id = int(
        image_id
    )

    layer_id = int(
        layer_id
    )

    image_x = int(
        image_x
    )

    image_y = int(
        image_y
    )

    region_width = int(
        region_width
    )

    region_height = int(
        region_height
    )

    if region_width <= 0 or region_height <= 0:
        raise ValueError(
            "Pixel write region must be larger than zero"
        )

    image, layer = _gimp_resolve_image_layer(
        image_id,
        layer_id
    )

    if layer.is_group():
        raise ValueError(
            "Cannot write raster pixels directly to a GIMP group layer"
        )

    expected_length = (
        region_width
        * region_height
        * 4
    )

    if len(
        raw_pixels
    ) != expected_length:
        raise ValueError(
            "Blender pixel payload has the wrong size. "
            f"Expected {expected_length}, got {len(raw_pixels)}"
        )

    layer_offset_x, layer_offset_y = (
        _blendgimp_layer_offsets(
            layer
        )
    )

    layer_width = int(
        layer.get_width()
    )

    layer_height = int(
        layer.get_height()
    )

    requested_left = image_x
    requested_top = image_y
    requested_right = (
        image_x
        + region_width
    )
    requested_bottom = (
        image_y
        + region_height
    )

    layer_left = layer_offset_x
    layer_top = layer_offset_y
    layer_right = (
        layer_offset_x
        + layer_width
    )
    layer_bottom = (
        layer_offset_y
        + layer_height
    )

    write_left = max(
        requested_left,
        layer_left
    )

    write_top = max(
        requested_top,
        layer_top
    )

    write_right = min(
        requested_right,
        layer_right
    )

    write_bottom = min(
        requested_bottom,
        layer_bottom
    )

    if (
        write_right <= write_left
        or write_bottom <= write_top
    ):
        raise ValueError(
            "Blender pixel region does not overlap the selected GIMP layer"
        )

    write_width = (
        write_right
        - write_left
    )

    write_height = (
        write_bottom
        - write_top
    )

    source_crop_x = (
        write_left
        - requested_left
    )

    source_crop_y = (
        write_top
        - requested_top
    )

    write_pixels = (
        _blendgimp_crop_rgba_region(
            raw_pixels,
            region_width,
            region_height,
            source_crop_x,
            source_crop_y,
            write_width,
            write_height,
        )
    )

    local_x = (
        write_left
        - layer_offset_x
    )

    local_y = (
        write_top
        - layer_offset_y
    )

    shadow = layer.get_shadow_buffer()

    if shadow is None:
        raise RuntimeError(
            "GIMP did not return a shadow buffer for the selected layer"
        )

    rectangle = Gegl.Rectangle.new(
        local_x,
        local_y,
        write_width,
        write_height,
    )

    # GEGL's introspectable setter is exposed as Buffer.set() in Python
    # bindings. Different GI builds may expose the inferred-length and
    # explicit-length variants, so support both.
    try:

        shadow.set(
            rectangle,
            "R'G'B'A u8",
            write_pixels,
        )

    except TypeError:

        shadow.set(
            rectangle,
            "R'G'B'A u8",
            write_pixels,
            len(
                write_pixels
            ),
        )

    shadow.flush()

    merged = layer.merge_shadow(
        True
    )

    if merged is False:
        raise RuntimeError(
            "GIMP could not merge the Blender pixel write into the layer"
        )

    updated = layer.update(
        local_x,
        local_y,
        write_width,
        write_height,
    )

    if updated is False:
        raise RuntimeError(
            "GIMP could not update the written drawable region"
        )

    Gimp.displays_flush()

    _blendgimp_accept_blender_originated_write(
        image_id
    )

    return {
        "image_id": image_id,
        "layer_id": layer_id,
        "image_x": write_left,
        "image_y": write_top,
        "layer_x": local_x,
        "layer_y": local_y,
        "width": write_width,
        "height": write_height,
        "byte_length": len(
            write_pixels
        ),
        "clipped": bool(
            write_width != region_width
            or write_height != region_height
            or write_left != requested_left
            or write_top != requested_top
        ),
    }


# -----------------------------------------------------------------------------
# Native GEGL damage tracking
# -----------------------------------------------------------------------------

def _blendgimp_union_rect(
    current,
    incoming
):
    if incoming is None:
        return current

    (
        x,
        y,
        width,
        height,
    ) = incoming

    if width <= 0 or height <= 0:
        return current

    if current is None:
        return (
            int(x),
            int(y),
            int(width),
            int(height),
        )

    (
        current_x,
        current_y,
        current_width,
        current_height,
    ) = current

    left = min(
        current_x,
        x
    )

    top = min(
        current_y,
        y
    )

    right = max(
        current_x + current_width,
        x + width
    )

    bottom = max(
        current_y + current_height,
        y + height
    )

    return (
        int(left),
        int(top),
        int(right - left),
        int(bottom - top),
    )


def _blendgimp_clip_rect_to_image(
    image,
    x,
    y,
    width,
    height
):
    image_width = int(
        image.get_width()
    )

    image_height = int(
        image.get_height()
    )

    left = max(
        0,
        int(x)
    )

    top = max(
        0,
        int(y)
    )

    right = min(
        image_width,
        int(x + width)
    )

    bottom = min(
        image_height,
        int(y + height)
    )

    if right <= left or bottom <= top:
        return None

    return (
        left,
        top,
        right - left,
        bottom - top,
    )


def _blendgimp_mark_damage(
    image_id,
    rectangle,
    full=False,
    reason="buffer"
):
    image_id = int(
        image_id
    )

    tracker = BLENDGIMP_DAMAGE_TRACKERS.get(
        image_id
    )

    if tracker is None:
        return

    image = Gimp.Image.get_by_id(
        image_id
    )

    if image is None or not image.is_valid():
        return

    if full:
        rectangle = (
            0,
            0,
            int(image.get_width()),
            int(image.get_height()),
        )

    if rectangle is None:
        return

    rectangle = _blendgimp_clip_rect_to_image(
        image,
        *rectangle
    )

    if rectangle is None:
        return

    tracker[
        "damage"
    ] = _blendgimp_union_rect(
        tracker.get(
            "damage"
        ),
        rectangle
    )

    tracker[
        "revision"
    ] = int(
        tracker.get(
            "revision",
            1
        )
    ) + 1

    tracker[
        "last_reason"
    ] = str(
        reason
    )


def _blendgimp_drawable_buffer_changed(
    buffer,
    rectangle,
    context
):
    """
    GEGL Buffer::changed callback.

    `rectangle` is in drawable-local GEGL coordinates. Convert it to image
    coordinates using the drawable offsets captured in mutable callback
    context.
    """

    if not context.get(
        "active",
        True
    ):
        return

    try:
        image_id = int(
            context[
                "image_id"
            ]
        )

        offset_x = int(
            context.get(
                "offset_x",
                0
            )
        )

        offset_y = int(
            context.get(
                "offset_y",
                0
            )
        )

        if rectangle is None:
            image = Gimp.Image.get_by_id(
                image_id
            )

            if image is None or not image.is_valid():
                return

            _blendgimp_mark_damage(
                image_id,
                (
                    0,
                    0,
                    int(image.get_width()),
                    int(image.get_height()),
                ),
                reason="buffer-full"
            )

            return

        rect_x = int(
            getattr(
                rectangle,
                "x",
                0
            )
        )

        rect_y = int(
            getattr(
                rectangle,
                "y",
                0
            )
        )

        rect_width = int(
            getattr(
                rectangle,
                "width",
                0
            )
        )

        rect_height = int(
            getattr(
                rectangle,
                "height",
                0
            )
        )

        _blendgimp_mark_damage(
            image_id,
            (
                offset_x + rect_x,
                offset_y + rect_y,
                rect_width,
                rect_height,
            ),
            reason="gegl-buffer"
        )

    except Exception as exc:
        log(
            "GEGL damage callback failed: "
            f"{exc}"
        )


def _blendgimp_layer_offsets(
    drawable
):
    try:
        offsets = drawable.get_offsets()

        if isinstance(
            offsets,
            tuple
        ) and len(offsets) >= 2:
            return (
                int(
                    offsets[-2]
                ),
                int(
                    offsets[-1]
                ),
            )

    except Exception:
        pass

    return (
        0,
        0,
    )


def _blendgimp_layer_structure_signature(
    image
):
    """
    Cheap non-pixel signature covering visual layer-stack changes that do not
    necessarily modify a drawable GEGL buffer: visibility, opacity, modes,
    offsets, ordering, masks, dimensions, and hierarchy.
    """

    signature = [
        "image",
        int(
            image.get_width()
        ),
        int(
            image.get_height()
        ),
    ]

    def append_layer(
        layer,
        parent_id
    ):
        layer_id = int(
            layer.get_id()
        )

        offset_x, offset_y = (
            _blendgimp_layer_offsets(
                layer
            )
        )

        mask_id = -1
        apply_mask = False
        show_mask = False

        try:
            mask = layer.get_mask()

            if (
                mask is not None
                and mask.is_valid()
            ):
                mask_id = int(
                    mask.get_id()
                )

                apply_mask = bool(
                    layer.get_apply_mask()
                )

                show_mask = bool(
                    layer.get_show_mask()
                )

        except Exception:
            pass

        try:
            composite_mode = int(
                layer.get_composite_mode()
            )
        except Exception:
            composite_mode = -1

        signature.extend(
            [
                "layer",
                layer_id,
                int(
                    parent_id
                ),
                int(
                    bool(
                        layer.is_group()
                    )
                ),
                int(
                    bool(
                        layer.get_visible()
                    )
                ),
                round(
                    float(
                        layer.get_opacity()
                    ),
                    5
                ),
                int(
                    layer.get_mode()
                ),
                composite_mode,
                offset_x,
                offset_y,
                int(
                    layer.get_width()
                ),
                int(
                    layer.get_height()
                ),
                mask_id,
                int(
                    apply_mask
                ),
                int(
                    show_mask
                ),
            ]
        )

        if layer.is_group():
            for child_item in layer.get_children():

                child_layer = Gimp.Layer.get_by_id(
                    int(
                        child_item.get_id()
                    )
                )

                if child_layer is not None:
                    append_layer(
                        child_layer,
                        layer_id
                    )

    for root_layer in image.get_layers():
        append_layer(
            root_layer,
            -1
        )

    return tuple(
        signature
    )


def _blendgimp_collect_watch_drawables(
    image
):
    """
    Yield leaf layers and layer masks that can emit real pixel-buffer damage.
    Group buffers are intentionally skipped to avoid duplicate child damage.
    """

    result = []

    def append_layer(
        layer
    ):
        if layer.is_group():

            for child_item in layer.get_children():

                child_layer = Gimp.Layer.get_by_id(
                    int(
                        child_item.get_id()
                    )
                )

                if child_layer is not None:
                    append_layer(
                        child_layer
                    )

            return

        result.append(
            (
                "layer",
                layer,
                _blendgimp_layer_offsets(
                    layer
                ),
            )
        )

        try:
            mask = layer.get_mask()

            if (
                mask is not None
                and mask.is_valid()
            ):

                result.append(
                    (
                        "mask",
                        mask,
                        _blendgimp_layer_offsets(
                            layer
                        ),
                    )
                )

        except Exception:
            pass

    for root_layer in image.get_layers():
        append_layer(
            root_layer
        )

    return result


def _blendgimp_ensure_damage_tracker(
    image
):
    image_id = int(
        image.get_id()
    )

    tracker = BLENDGIMP_DAMAGE_TRACKERS.get(
        image_id
    )

    if tracker is None:

        tracker = {
            "revision": 1,
            "reported_revision": 0,
            "damage": None,
            "watchers": {},
            "signal_ok": True,
            "signal_error": "",
            "structure_signature": None,
            "last_reason": "initial",
        }

        BLENDGIMP_DAMAGE_TRACKERS[
            image_id
        ] = tracker

    current_signature = (
        _blendgimp_layer_structure_signature(
            image
        )
    )

    previous_signature = tracker.get(
        "structure_signature"
    )

    if previous_signature is None:

        tracker[
            "structure_signature"
        ] = current_signature

    elif previous_signature != current_signature:

        tracker[
            "structure_signature"
        ] = current_signature

        _blendgimp_mark_damage(
            image_id,
            None,
            full=True,
            reason="layer-structure"
        )

    desired_keys = set()

    if tracker.get(
        "signal_ok",
        True
    ):

        for (
            kind,
            drawable,
            offsets,
        ) in _blendgimp_collect_watch_drawables(
            image
        ):

            drawable_id = int(
                drawable.get_id()
            )

            key = (
                str(
                    kind
                ),
                drawable_id,
            )

            desired_keys.add(
                key
            )

            existing = tracker[
                "watchers"
            ].get(
                key
            )

            offset_x, offset_y = (
                offsets
            )

            if existing is not None:

                existing[
                    "context"
                ][
                    "offset_x"
                ] = offset_x

                existing[
                    "context"
                ][
                    "offset_y"
                ] = offset_y

                continue

            try:

                buffer = drawable.get_buffer()

                if buffer is None:
                    continue

                callback_context = {
                    "active": True,
                    "image_id": image_id,
                    "drawable_id": drawable_id,
                    "kind": str(
                        kind
                    ),
                    "offset_x": offset_x,
                    "offset_y": offset_y,
                }

                handler_id = buffer.signal_connect(
                    "changed",
                    _blendgimp_drawable_buffer_changed,
                    callback_context
                )

                tracker[
                    "watchers"
                ][
                    key
                ] = {
                    "buffer": buffer,
                    "handler_id": int(
                        handler_id
                    ),
                    "context": callback_context,
                }

            except Exception as exc:

                tracker[
                    "signal_ok"
                ] = False

                tracker[
                    "signal_error"
                ] = str(
                    exc
                )

                log(
                    "GEGL Buffer::changed unavailable for "
                    f"image ID {image_id}: {exc}. "
                    "Falling back to thumbnail revision detection."
                )

                break

    # Mark stale callback contexts inactive. We intentionally keep the GObject
    # reference/handler safe instead of relying on disconnect behavior across
    # every PyGObject/GEGL build.
    for key, watcher in list(
        tracker[
            "watchers"
        ].items()
    ):

        if key not in desired_keys:

            watcher[
                "context"
            ][
                "active"
            ] = False

            tracker[
                "watchers"
            ].pop(
                key,
                None
            )

    return tracker


def _blendgimp_pending_damage(
    image_id
):
    tracker = BLENDGIMP_DAMAGE_TRACKERS.get(
        int(
            image_id
        )
    )

    if tracker is None:
        return None

    return tracker.get(
        "damage"
    )


def _blendgimp_clear_pending_damage(
    image_id
):
    tracker = BLENDGIMP_DAMAGE_TRACKERS.get(
        int(
            image_id
        )
    )

    if tracker is not None:
        tracker[
            "damage"
        ] = None


# -----------------------------------------------------------------------------
# Direct full-composite RGBA transport
# -----------------------------------------------------------------------------

def _blendgimp_bytes_from_gi(value):
    """
    MAIN THREAD ONLY.

    Normalize byte-array shapes PyGObject may return for introspectable GEGL
    methods into a normal Python bytes object.
    """

    if value is None:
        return b""

    if isinstance(value, bytes):
        return value

    if isinstance(value, bytearray):
        return bytes(value)

    if isinstance(value, memoryview):
        return value.tobytes()

    if isinstance(value, tuple):
        for part in value:
            if isinstance(
                part,
                (bytes, bytearray, memoryview)
            ):
                return _blendgimp_bytes_from_gi(part)

            if hasattr(part, "get_data"):
                try:
                    return _blendgimp_bytes_from_gi(
                        part.get_data()
                    )
                except Exception:
                    pass

    if hasattr(value, "get_data"):
        try:
            return _blendgimp_bytes_from_gi(
                value.get_data()
            )
        except Exception:
            pass

    try:
        return bytes(value)
    except Exception as exc:
        raise TypeError(
            "Could not convert GEGL pixel result to bytes: "
            f"{type(value).__name__}: {exc}"
        )


def _gimp_get_raw_composite_rgba(
    image
):
    """
    MAIN THREAD ONLY.

    Return (width, height, raw_rgba8_bytes) for the current visible composite.
    The user's original GIMP document is never flattened or modified.
    """

    if image is None or not image.is_valid():
        raise ValueError(
            "Cannot extract pixels from an invalid GIMP image"
        )

    width = int(
        image.get_width()
    )

    height = int(
        image.get_height()
    )

    if width <= 0 or height <= 0:
        raise RuntimeError(
            f"GIMP image has invalid dimensions {width}x{height}"
        )

    duplicate = None

    try:
        duplicate = image.duplicate()

        if duplicate is None or not duplicate.is_valid():
            raise RuntimeError(
                f"GIMP could not duplicate image ID {image.get_id()} "
                "for composite extraction"
            )

        merged = duplicate.merge_visible_layers(
            Gimp.MergeType.CLIP_TO_IMAGE
        )

        if merged is None or not merged.is_valid():
            raw_pixels = bytes(
                width
                * height
                * 4
            )

        else:
            buffer = merged.get_buffer()

            if buffer is None:
                raise RuntimeError(
                    "GIMP did not return a GEGL buffer for the "
                    "merged visible composite"
                )

            rectangle = Gegl.Rectangle.new(
                0,
                0,
                width,
                height,
            )

            pixel_result = buffer.get(
                rectangle,
                1.0,
                "R'G'B'A u8",
                Gegl.AbyssPolicy.NONE,
            )

            raw_pixels = _blendgimp_bytes_from_gi(
                pixel_result
            )

        expected_length = (
            width
            * height
            * 4
        )

        if len(
            raw_pixels
        ) != expected_length:
            raise RuntimeError(
                "Unexpected composite byte count. "
                f"Expected {expected_length}, got {len(raw_pixels)}"
            )

        return (
            width,
            height,
            raw_pixels,
        )

    finally:
        if (
            duplicate is not None
            and duplicate.is_valid()
        ):
            try:
                duplicate.delete()
            except Exception:
                log(
                    "Warning: could not delete temporary "
                    "pixel-extraction image duplicate"
                )


def _blendgimp_store_pixel_snapshot(
    image_id,
    width,
    height,
    raw_pixels
):
    BLENDGIMP_PIXEL_SNAPSHOTS[
        int(image_id)
    ] = {
        "width": int(width),
        "height": int(height),
        "pixels": bytes(raw_pixels),
    }


def _blendgimp_dirty_bbox(
    previous_pixels,
    current_pixels,
    width,
    height
):
    """
    Find the exact changed-pixel bounding rectangle.

    Coordinates use GIMP/GEGL convention:
        x grows left -> right
        y grows top -> bottom
    """

    if len(
        previous_pixels
    ) != len(
        current_pixels
    ):
        return (
            0,
            0,
            width,
            height,
        )

    if previous_pixels == current_pixels:
        return None

    row_bytes = (
        width
        * 4
    )

    previous = memoryview(
        previous_pixels
    )

    current = memoryview(
        current_pixels
    )

    min_x = width
    min_y = height
    max_x = -1
    max_y = -1

    for y in range(
        height
    ):
        row_start = (
            y
            * row_bytes
        )

        row_end = (
            row_start
            + row_bytes
        )

        old_row = previous[
            row_start:
            row_end
        ]

        new_row = current[
            row_start:
            row_end
        ]

        if old_row == new_row:
            continue

        min_y = min(
            min_y,
            y
        )

        max_y = max(
            max_y,
            y
        )

        first_x = None

        for x in range(
            width
        ):
            byte_index = (
                x
                * 4
            )

            if (
                old_row[
                    byte_index:
                    byte_index + 4
                ]
                !=
                new_row[
                    byte_index:
                    byte_index + 4
                ]
            ):
                first_x = x
                break

        last_x = None

        for x in range(
            width - 1,
            -1,
            -1
        ):
            byte_index = (
                x
                * 4
            )

            if (
                old_row[
                    byte_index:
                    byte_index + 4
                ]
                !=
                new_row[
                    byte_index:
                    byte_index + 4
                ]
            ):
                last_x = x
                break

        if first_x is not None:
            min_x = min(
                min_x,
                first_x
            )

        if last_x is not None:
            max_x = max(
                max_x,
                last_x
            )

    if (
        max_x < min_x
        or max_y < min_y
    ):
        return None

    return (
        min_x,
        min_y,
        max_x - min_x + 1,
        max_y - min_y + 1,
    )


def _blendgimp_extract_region_rgba(
    raw_pixels,
    image_width,
    x,
    y,
    region_width,
    region_height
):
    row_bytes = (
        image_width
        * 4
    )

    region_row_bytes = (
        region_width
        * 4
    )

    result = bytearray(
        region_row_bytes
        * region_height
    )

    for region_row in range(
        region_height
    ):
        source_y = (
            y
            + region_row
        )

        source_start = (
            source_y
            * row_bytes
            + x
            * 4
        )

        source_end = (
            source_start
            + region_row_bytes
        )

        destination_start = (
            region_row
            * region_row_bytes
        )

        result[
            destination_start:
            destination_start + region_row_bytes
        ] = raw_pixels[
            source_start:
            source_end
        ]

    return bytes(
        result
    )


def gimp_get_image_pixels_binary(
    image_id
):
    """
    MAIN THREAD ONLY.

    Full-image direct RGBA8 transfer using a binary payload following a small
    newline-delimited JSON header. This avoids base64 expansion and JSON pixel
    parsing while retaining the existing command socket.
    """

    image_id = int(
        image_id
    )

    image = Gimp.Image.get_by_id(
        image_id
    )

    if image is None or not image.is_valid():
        raise ValueError(
            f"Image ID {image_id} is not a valid open GIMP image"
        )

    (
        width,
        height,
        raw_pixels,
    ) = _gimp_get_raw_composite_rgba(
        image
    )

    _blendgimp_store_pixel_snapshot(
        image_id,
        width,
        height,
        raw_pixels
    )

    _blendgimp_clear_pending_damage(
        image_id
    )

    image_name = image.get_name()

    return {
        "image_id": image_id,
        "sync_token": _blendgimp_composite_token(
            image_id
        ),
        "image_name": (
            ""
            if image_name is None
            else str(image_name)
        ),
        "width": width,
        "height": height,
        "channels": 4,
        "pixel_format": "R'G'B'A u8",
        "alpha": "straight",
        "origin": "top-left",
        "encoding": "binary",
        "transport": "direct-rgba-binary",
        "byte_length": len(
            raw_pixels
        ),
        "sha256": hashlib.sha256(
            raw_pixels
        ).hexdigest(),
        "_binary_payload": raw_pixels,
    }


def gimp_get_image_pixels(
    image_id
):
    """
    MAIN THREAD ONLY.

    Full-image direct RGBA transfer. This also establishes/refreshes the dirty
    rectangle baseline for later automatic partial updates.
    """

    image_id = int(
        image_id
    )

    image = Gimp.Image.get_by_id(
        image_id
    )

    if image is None or not image.is_valid():
        raise ValueError(
            f"Image ID {image_id} is not a valid open GIMP image"
        )

    (
        width,
        height,
        raw_pixels,
    ) = _gimp_get_raw_composite_rgba(
        image
    )

    _blendgimp_store_pixel_snapshot(
        image_id,
        width,
        height,
        raw_pixels
    )

    encoded_pixels = base64.b64encode(
        raw_pixels
    ).decode(
        "ascii"
    )

    image_name = image.get_name()

    return {
        "image_id": image_id,
        "sync_token": _blendgimp_composite_token(
            image_id
        ),
        "image_name": (
            ""
            if image_name is None
            else str(image_name)
        ),
        "width": width,
        "height": height,
        "channels": 4,
        "pixel_format": "R'G'B'A u8",
        "alpha": "straight",
        "origin": "top-left",
        "encoding": "base64",
        "transport": "direct-rgba-json",
        "byte_length": len(
            raw_pixels
        ),
        "encoded_length": len(
            encoded_pixels
        ),
        "sha256": hashlib.sha256(
            raw_pixels
        ).hexdigest(),
        "pixels_b64": encoded_pixels,
    }


def gimp_get_image_dirty_pixels_binary(
    image_id
):
    """
    MAIN THREAD ONLY.

    Hybrid dirty-region synchronization.

    If native GEGL damage supplied a rectangle, compare/transmit only that
    rectangle. If no native rectangle exists, render once and run the proven
    full-image pixel diff to discover the changed bounding box.

    This intentionally does NOT interpret "signal connected but no damage
    callback" as "nothing changed".
    """

    image_id = int(
        image_id
    )

    image = Gimp.Image.get_by_id(
        image_id
    )

    if image is None or not image.is_valid():
        raise ValueError(
            f"Image ID {image_id} is not a valid open GIMP image"
        )

    tracker = (
        _blendgimp_ensure_damage_tracker(
            image
        )
    )

    previous = (
        BLENDGIMP_PIXEL_SNAPSHOTS.get(
            image_id
        )
    )

    native_damage = (
        _blendgimp_pending_damage(
            image_id
        )
    )

    width = int(
        image.get_width()
    )

    height = int(
        image.get_height()
    )

    dimensions_changed = (
        previous is None
        or int(
            previous.get(
                "width",
                -1
            )
        ) != width
        or int(
            previous.get(
                "height",
                -1
            )
        ) != height
    )

    (
        current_width,
        current_height,
        current_pixels,
    ) = _gimp_get_raw_composite_rgba(
        image
    )

    width = current_width
    height = current_height

    damage_detector = (
        "gegl-damage"
        if (
            native_damage is not None
            and not dimensions_changed
        )
        else "pixel-diff"
    )

    common = {
        "image_id": image_id,
        "sync_token": _blendgimp_composite_token(
            image_id
        ),
        "image_name": (
            ""
            if image.get_name() is None
            else str(
                image.get_name()
            )
        ),
        "width": width,
        "height": height,
        "channels": 4,
        "pixel_format": "R'G'B'A u8",
        "alpha": "straight",
        "origin": "top-left",
        "encoding": "binary",
        "transport": "dirty-rgba-binary",
        "damage_detector": damage_detector,
        "native_signal_connected": bool(
            tracker.get(
                "signal_ok",
                False
            )
        ),
    }

    if dimensions_changed:

        dirty_bbox = (
            0,
            0,
            width,
            height,
        )

        full_refresh = True

    elif native_damage is not None:

        dirty_bbox = (
            native_damage
        )

        full_refresh = (
            dirty_bbox
            == (
                0,
                0,
                width,
                height,
            )
        )

    else:

        # Critical reliability path for GIMP 3.2.4:
        # the GEGL signal may be connected but silent.
        dirty_bbox = _blendgimp_dirty_bbox(
            previous.get(
                "pixels",
                b""
            ),
            current_pixels,
            width,
            height,
        )

        full_refresh = False

    if dirty_bbox is None:

        _blendgimp_store_pixel_snapshot(
            image_id,
            width,
            height,
            current_pixels
        )

        _blendgimp_clear_pending_damage(
            image_id
        )

        return {
            **common,
            "changed": False,
            "full_refresh": False,
            "x": 0,
            "y": 0,
            "region_width": 0,
            "region_height": 0,
            "byte_length": 0,
            "_binary_payload": b"",
        }

    (
        x,
        y,
        region_width,
        region_height,
    ) = dirty_bbox

    current_region = (
        _blendgimp_extract_region_rgba(
            current_pixels,
            width,
            x,
            y,
            region_width,
            region_height,
        )
    )

    # Filter native structural/damage events that do not alter the visible
    # composite. When the full pixel-diff path produced the bbox, a visible
    # difference is already guaranteed.
    if (
        native_damage is not None
        and previous is not None
        and not dimensions_changed
    ):

        previous_region = (
            _blendgimp_extract_region_rgba(
                previous.get(
                    "pixels",
                    b""
                ),
                width,
                x,
                y,
                region_width,
                region_height,
            )
        )

        if previous_region == current_region:

            _blendgimp_store_pixel_snapshot(
                image_id,
                width,
                height,
                current_pixels
            )

            _blendgimp_clear_pending_damage(
                image_id
            )

            return {
                **common,
                "changed": False,
                "full_refresh": False,
                "x": int(
                    x
                ),
                "y": int(
                    y
                ),
                "region_width": int(
                    region_width
                ),
                "region_height": int(
                    region_height
                ),
                "byte_length": 0,
                "filtered_damage": True,
                "_binary_payload": b"",
            }

    _blendgimp_store_pixel_snapshot(
        image_id,
        width,
        height,
        current_pixels
    )

    _blendgimp_clear_pending_damage(
        image_id
    )

    full_byte_length = (
        width
        * height
        * 4
    )

    region_byte_length = len(
        current_region
    )

    return {
        **common,
        "changed": True,
        "full_refresh": bool(
            full_refresh
        ),
        "x": int(
            x
        ),
        "y": int(
            y
        ),
        "region_width": int(
            region_width
        ),
        "region_height": int(
            region_height
        ),
        "byte_length": region_byte_length,
        "full_byte_length": full_byte_length,
        "saved_bytes": max(
            0,
            full_byte_length
            - region_byte_length
        ),
        "sha256": hashlib.sha256(
            current_region
        ).hexdigest(),
        "_binary_payload": current_region,
    }


def gimp_get_image_dirty_pixels(
    image_id
):
    """
    MAIN THREAD ONLY.

    Compare the current visible composite to BlendGimp's previous full RGBA
    snapshot and return only the exact changed bounding rectangle.

    If no baseline exists (or image dimensions changed), the full image is
    returned once and becomes the new baseline.
    """

    image_id = int(
        image_id
    )

    image = Gimp.Image.get_by_id(
        image_id
    )

    if image is None or not image.is_valid():
        raise ValueError(
            f"Image ID {image_id} is not a valid open GIMP image"
        )

    (
        width,
        height,
        current_pixels,
    ) = _gimp_get_raw_composite_rgba(
        image
    )

    previous = BLENDGIMP_PIXEL_SNAPSHOTS.get(
        image_id
    )

    full_refresh = (
        previous is None
        or int(
            previous.get(
                "width",
                -1
            )
        ) != width
        or int(
            previous.get(
                "height",
                -1
            )
        ) != height
    )

    if full_refresh:
        dirty_bbox = (
            0,
            0,
            width,
            height,
        )

    else:
        dirty_bbox = _blendgimp_dirty_bbox(
            previous.get(
                "pixels",
                b""
            ),
            current_pixels,
            width,
            height,
        )

    _blendgimp_store_pixel_snapshot(
        image_id,
        width,
        height,
        current_pixels
    )

    image_name = image.get_name()

    if dirty_bbox is None:
        return {
            "image_id": image_id,
            "sync_token": _blendgimp_composite_token(
                image_id
            ),
            "image_name": (
                ""
                if image_name is None
                else str(image_name)
            ),
            "width": width,
            "height": height,
            "channels": 4,
            "pixel_format": "R'G'B'A u8",
            "alpha": "straight",
            "origin": "top-left",
            "encoding": "base64",
            "transport": "dirty-rgba-json",
            "changed": False,
            "full_refresh": False,
            "x": 0,
            "y": 0,
            "region_width": 0,
            "region_height": 0,
            "byte_length": 0,
            "encoded_length": 0,
            "pixels_b64": "",
        }

    (
        x,
        y,
        region_width,
        region_height,
    ) = dirty_bbox

    region_pixels = _blendgimp_extract_region_rgba(
        current_pixels,
        width,
        x,
        y,
        region_width,
        region_height,
    )

    encoded_pixels = base64.b64encode(
        region_pixels
    ).decode(
        "ascii"
    )

    full_byte_length = (
        width
        * height
        * 4
    )

    region_byte_length = len(
        region_pixels
    )

    return {
        "image_id": image_id,
        "sync_token": _blendgimp_composite_token(
            image_id
        ),
        "image_name": (
            ""
            if image_name is None
            else str(image_name)
        ),
        "width": width,
        "height": height,
        "channels": 4,
        "pixel_format": "R'G'B'A u8",
        "alpha": "straight",
        "origin": "top-left",
        "encoding": "base64",
        "transport": "dirty-rgba-json",
        "changed": True,
        "full_refresh": bool(
            full_refresh
        ),
        "x": int(x),
        "y": int(y),
        "region_width": int(
            region_width
        ),
        "region_height": int(
            region_height
        ),
        "byte_length": region_byte_length,
        "full_byte_length": full_byte_length,
        "saved_bytes": max(
            0,
            full_byte_length
            - region_byte_length
        ),
        "encoded_length": len(
            encoded_pixels
        ),
        "sha256": hashlib.sha256(
            region_pixels
        ).hexdigest(),
        "pixels_b64": encoded_pixels,
    }


# -----------------------------------------------------------------------------
# Automatic texture-sync state tracking
# -----------------------------------------------------------------------------

def _gimp_visual_fingerprint(image):
    """
    MAIN THREAD ONLY.

    Compute a compact fingerprint of the current visible image composite.
    This does not modify GIMP's dirty flag and does not export a file.
    """

    thumbnail = image.get_thumbnail(
        BLENDGIMP_STATE_THUMBNAIL_SIZE,
        BLENDGIMP_STATE_THUMBNAIL_SIZE,
        Gimp.PixbufTransparency.KEEP_ALPHA,
    )

    if thumbnail is None:
        raise RuntimeError(
            f"GIMP could not create a state thumbnail for image ID {image.get_id()}"
        )

    pixels = thumbnail.get_pixels()

    try:
        pixel_bytes = bytes(pixels)
    except Exception:
        pixel_bytes = pixels.tobytes()

    digest = hashlib.sha256()

    metadata = (
        f"{thumbnail.get_width()}x{thumbnail.get_height()}|"
        f"{thumbnail.get_rowstride()}|"
        f"{thumbnail.get_n_channels()}|"
        f"{int(bool(thumbnail.get_has_alpha()))}|"
        f"{image.get_width()}x{image.get_height()}"
    ).encode("utf-8")

    digest.update(metadata)
    digest.update(pixel_bytes)

    return {
        "fingerprint": digest.hexdigest(),
        "thumbnail_width": int(thumbnail.get_width()),
        "thumbnail_height": int(thumbnail.get_height()),
    }


def gimp_get_image_state(image_id):
    """
    MAIN THREAD ONLY.

    Reliable hybrid change detector.

    The visible-composite thumbnail fingerprint is always checked because the
    GIMP 3.2.4 Python runtime can successfully connect to GEGL Buffer::changed
    without delivering paint callbacks to this persistent plug-in.

    GEGL damage revision is still included as a second signal. If it fires it
    can catch changes the thumbnail misses and later provide an exact dirty
    rectangle. If it stays silent, Auto Sync still works through the proven
    fingerprint + pixel-diff path.
    """

    image_id = int(
        image_id
    )

    image = Gimp.Image.get_by_id(
        image_id
    )

    if image is None or not image.is_valid():
        raise ValueError(
            f"Image ID {image_id} is not a valid open GIMP image"
        )

    tracker = (
        _blendgimp_ensure_damage_tracker(
            image
        )
    )

    fingerprint_info = (
        _gimp_visual_fingerprint(
            image
        )
    )

    fingerprint = fingerprint_info[
        "fingerprint"
    ]

    native_revision = int(
        tracker.get(
            "revision",
            1
        )
    )

    previous = (
        BLENDGIMP_IMAGE_VISUAL_STATE.get(
            image_id
        )
    )

    if previous is None:

        fingerprint_changed = True
        native_changed = True
        revision = 1
        self_originated_suppressed = False

    else:

        fingerprint_changed = (
            previous.get(
                "fingerprint"
            )
            != fingerprint
        )

        native_changed = (
            int(
                previous.get(
                    "native_revision",
                    native_revision
                )
            )
            != native_revision
        )

        (
            fingerprint_changed,
            native_changed,
            self_originated_suppressed,
        ) = _blendgimp_filter_self_originated_state_change(
            image,
            image_id,
            fingerprint,
            native_revision,
            fingerprint_changed,
            native_changed,
        )

        previous = (
            BLENDGIMP_IMAGE_VISUAL_STATE.get(
                image_id
            )
            or previous
        )

        revision = int(
            previous.get(
                "revision",
                1
            )
        )

        if (
            fingerprint_changed
            or native_changed
        ):
            revision += 1

    changed = (
        previous is None
        or fingerprint_changed
        or native_changed
    )

    BLENDGIMP_IMAGE_VISUAL_STATE[
        image_id
    ] = {
        "fingerprint": fingerprint,
        "native_revision": native_revision,
        "revision": revision,
    }

    image_name = image.get_name()

    pending_damage = tracker.get(
        "damage"
    )

    if pending_damage is None:

        damage_x = 0
        damage_y = 0
        damage_width = 0
        damage_height = 0

    else:

        (
            damage_x,
            damage_y,
            damage_width,
            damage_height,
        ) = pending_damage

    return {
        "image_id": image_id,
        "sync_token": _blendgimp_composite_token(
            image_id
        ),
        "image_name": (
            ""
            if image_name is None
            else str(image_name)
        ),
        "width": int(
            image.get_width()
        ),
        "height": int(
            image.get_height()
        ),
        "dirty": bool(
            image.is_dirty()
        ),
        "revision": revision,
        "changed": changed,
        "detector": "hybrid",
        "fingerprint_changed": bool(
            fingerprint_changed
        ),
        "native_changed": bool(
            native_changed
        ),
        "self_originated_suppressed": bool(
            self_originated_suppressed
        ),
        "native_signal_connected": bool(
            tracker.get(
                "signal_ok",
                False
            )
        ),
        "native_revision": native_revision,
        "damage_available": bool(
            pending_damage is not None
        ),
        "damage_x": int(
            damage_x
        ),
        "damage_y": int(
            damage_y
        ),
        "damage_width": int(
            damage_width
        ),
        "damage_height": int(
            damage_height
        ),
        "damage_reason": str(
            tracker.get(
                "last_reason",
                ""
            )
        ),
        "watcher_count": len(
            tracker.get(
                "watchers",
                {}
            )
        ),
        "signal_error": str(
            tracker.get(
                "signal_error",
                ""
            )
        ),
        "fingerprint": fingerprint,
        "thumbnail_width": fingerprint_info[
            "thumbnail_width"
        ],
        "thumbnail_height": fingerprint_info[
            "thumbnail_height"
        ],
    }


# -----------------------------------------------------------------------------
# Composite export transport
# -----------------------------------------------------------------------------

def gimp_export_composite(image_id):
    """
    MAIN THREAD ONLY.

    Export the current visible GIMP image composite to BlendGimp's temporary
    cache as PNG. The GIMP image/layer structure is not flattened or modified.

    This is the first reliable full-image transport used by Stage 1. Later,
    direct pixel/shared-memory transport can replace it for high-frequency
    synchronization while this remains a useful fallback/debug path.
    """

    image_id = int(image_id)
    image = Gimp.Image.get_by_id(image_id)

    if image is None or not image.is_valid():
        raise ValueError(
            f"Image ID {image_id} is not a valid open GIMP image"
        )

    cache_dir = os.path.join(
        tempfile.gettempdir(),
        "BlendGimp",
        "cache",
    )

    os.makedirs(
        cache_dir,
        exist_ok=True,
    )

    sync_token = _blendgimp_composite_token(
        image_id
    )

    output_path = os.path.join(
        cache_dir,
        f"blendgimp_{sync_token}.png",
    )

    output_file = Gio.File.new_for_path(
        output_path
    )

    success = Gimp.file_save(
        Gimp.RunMode.NONINTERACTIVE,
        image,
        output_file,
        None,
    )

    if not success:
        raise RuntimeError(
            f"GIMP could not export image ID {image_id} to PNG"
        )

    if not os.path.isfile(output_path):
        raise RuntimeError(
            "GIMP reported a successful export but the cache file was not created"
        )

    stat = os.stat(output_path)
    image_name = image.get_name()

    return {
        "image_id": image_id,
        "sync_token": sync_token,
        "image_name": "" if image_name is None else str(image_name),
        "width": int(image.get_width()),
        "height": int(image.get_height()),
        "format": "PNG",
        "transport": "file",
        "path": os.path.abspath(output_path),
        "file_size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


# -----------------------------------------------------------------------------
# IPC server
# -----------------------------------------------------------------------------

class BlendGimpIPCServer:
    """Newline-delimited JSON server used by the Blender extension."""

    def __init__(self, dispatcher, gimp_version):
        self.dispatcher = dispatcher
        self.gimp_version = gimp_version

        self._stop_event = threading.Event()
        self._thread = None
        self._server_socket = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return

        self._thread = threading.Thread(
            target=self._server_loop,
            name="BlendGimpIPCServer",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()

        server_socket = self._server_socket
        self._server_socket = None

        if server_socket is not None:
            try:
                server_socket.close()
            except OSError:
                pass

    def _server_loop(self):
        log(f"Starting server on {HOST}:{PORT}")

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                self._server_socket = server

                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((HOST, PORT))
                server.listen(1)
                server.settimeout(0.5)

                log(f"Server listening on {HOST}:{PORT}")

                while not self._stop_event.is_set():
                    try:
                        client, address = server.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        if self._stop_event.is_set():
                            break
                        raise

                    log(f"Blender connected from {address[0]}:{address[1]}")

                    try:
                        with client:
                            client.settimeout(0.5)
                            self._handle_client(client)
                    except Exception:
                        log("Client connection error:\n" + traceback.format_exc())
                    finally:

                        try:
                            closed = self.dispatcher.call(
                                gimp_close_all_direct_paint_strokes,
                                timeout=5.0,
                            )

                            if closed:
                                log(
                                    "Closed "
                                    f"{closed} active direct paint "
                                    "stroke(s) after disconnect"
                                )

                        except Exception as exc:
                            log(
                                "Direct paint disconnect cleanup failed: "
                                f"{exc}"
                            )

                        log("Blender disconnected")

        except Exception:
            if not self._stop_event.is_set():
                log("IPC server failed:\n" + traceback.format_exc())
        finally:
            self._server_socket = None
            log("Server stopped")

    def _handle_client(self, client):
        """
        Receive newline-delimited JSON commands.

        Commands may optionally declare:
            binary_payload = true
            binary_length  = N

        In that case exactly N raw bytes immediately follow the JSON newline
        and are attached internally as `_binary_payload` before dispatch.
        """

        buffer = b""
        pending_message = None
        pending_length = 0

        while not self._stop_event.is_set():

            try:
                chunk = client.recv(
                    65536
                )
            except socket.timeout:
                continue

            if not chunk:
                return

            buffer += chunk

            while True:

                # ------------------------------------------------------------
                # Complete an inbound raw binary frame first.
                # ------------------------------------------------------------

                if pending_message is not None:

                    if len(
                        buffer
                    ) < pending_length:
                        break

                    binary_payload = buffer[
                        :pending_length
                    ]

                    buffer = buffer[
                        pending_length:
                    ]

                    message = pending_message

                    pending_message = None
                    pending_length = 0

                    message[
                        "_binary_payload"
                    ] = binary_payload

                    response = self._dispatch_message(
                        message
                    )

                    self._send_response(
                        client,
                        response
                    )

                    continue

                # ------------------------------------------------------------
                # Otherwise read one JSON command header.
                # ------------------------------------------------------------

                if b"\n" not in buffer:
                    break

                raw_line, buffer = buffer.split(
                    b"\n",
                    1
                )

                raw_line = raw_line.strip()

                if not raw_line:
                    continue

                try:
                    message = json.loads(
                        raw_line.decode(
                            "utf-8"
                        )
                    )
                except Exception as exc:
                    self._send_json(
                        client,
                        {
                            "type": "ERROR",
                            "ok": False,
                            "error": f"Invalid JSON: {exc}",
                        },
                    )
                    continue

                if message.get(
                    "binary_payload",
                    False
                ):

                    try:
                        binary_length = int(
                            message.get(
                                "binary_length",
                                0
                            )
                        )
                    except Exception:
                        binary_length = -1

                    if binary_length < 0:
                        self._send_json(
                            client,
                            {
                                "type": "ERROR",
                                "ok": False,
                                "error": (
                                    "Invalid binary payload length"
                                ),
                            },
                        )
                        continue

                    pending_message = message
                    pending_length = binary_length

                    # A zero-length binary request can dispatch immediately.
                    if pending_length == 0:

                        message[
                            "_binary_payload"
                        ] = b""

                        pending_message = None

                        response = (
                            self._dispatch_message(
                                message
                            )
                        )

                        self._send_response(
                            client,
                            response
                        )

                    continue

                response = self._dispatch_message(
                    message
                )

                self._send_response(
                    client,
                    response
                )

    def _dispatch_message(self, message):
        if not isinstance(message, dict):
            return {
                "type": "ERROR",
                "ok": False,
                "error": "Message must be a JSON object",
            }

        # Accept both names while the protocol is still young. The currently
        # used protocol sends the command in `type`.
        message_type = message.get("type") or message.get("command")

        if not isinstance(message_type, str) or not message_type:
            return self._error_response(
                message,
                command=None,
                error="Missing message type",
            )

        message_type = message_type.upper()

        # GET_IMAGE_STATE is intentionally quiet because Auto Sync polls it
        # frequently. Actual revision changes are logged below.
        if message_type != "GET_IMAGE_STATE":
            log(f"Received message: {message_type}")

        if message_type == "HELLO":
            response = {
                "type": "READY",
                "ok": True,
                "protocol": PROTOCOL_VERSION,
                "server": "BlendGimp-GIMP",
                "gimp_version": self.gimp_version,
            }
            self._copy_request_id(message, response)
            log("Sent READY")
            return response

        if message_type == "PING":
            response = {
                "type": "PONG",
                "ok": True,
            }
            self._copy_request_id(message, response)
            log("Sent PONG")
            return response

        if message_type == "STATUS":
            response = {
                "type": "STATUS",
                "ok": True,
                "status": "READY",
                "protocol": PROTOCOL_VERSION,
                "gimp_version": self.gimp_version,
            }
            self._copy_request_id(message, response)
            log("Sent STATUS")
            return response

        if message_type == "GET_IMAGES":
            log("GET_IMAGES received")

            try:
                images = self.dispatcher.call(
                    gimp_get_images_snapshot,
                    timeout=5.0,
                )

                response = {
                    "type": "IMAGES",
                    "ok": True,
                    "images": images,
                }
                self._copy_request_id(message, response)

                log(f"GET_IMAGES returned {len(images)} image(s)")
                return response

            except Exception as exc:
                log(f"GET_IMAGES failed: {exc}")
                return self._error_response(
                    message,
                    command="GET_IMAGES",
                    error=str(exc),
                )


        if message_type == "GET_IMAGE_LAYERS":
            log("GET_IMAGE_LAYERS received")

            if "image_id" not in message:
                return self._error_response(
                    message,
                    command="GET_IMAGE_LAYERS",
                    error="GET_IMAGE_LAYERS requires image_id",
                )

            try:
                image_id = int(message["image_id"])

            except (TypeError, ValueError):
                return self._error_response(
                    message,
                    command="GET_IMAGE_LAYERS",
                    error="image_id must be an integer",
                )

            try:
                snapshot = self.dispatcher.call(
                    gimp_get_image_layers_snapshot,
                    image_id,
                    timeout=5.0,
                )

                response = {
                    "type": "IMAGE_LAYERS",
                    "ok": True,
                    **snapshot,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    "GET_IMAGE_LAYERS returned "
                    f"{snapshot['layer_count']} layer(s) "
                    f"for image ID {image_id}"
                )

                return response

            except Exception as exc:
                log(
                    "GET_IMAGE_LAYERS failed "
                    f"for image ID {image_id}: {exc}"
                )

                return self._error_response(
                    message,
                    command="GET_IMAGE_LAYERS",
                    error=str(exc),
                )


        if message_type == "SET_ACTIVE_LAYER":
            log("SET_ACTIVE_LAYER received")

            try:
                image_id = int(message["image_id"])
                layer_id = int(message["layer_id"])

            except KeyError as exc:
                return self._error_response(
                    message,
                    command="SET_ACTIVE_LAYER",
                    error=f"Missing required field: {exc.args[0]}",
                )

            except (TypeError, ValueError):
                return self._error_response(
                    message,
                    command="SET_ACTIVE_LAYER",
                    error="image_id and layer_id must be integers",
                )

            try:
                result = self.dispatcher.call(
                    gimp_set_active_layer,
                    image_id,
                    layer_id,
                    timeout=5.0,
                )

                response = {
                    "type": "ACTIVE_LAYER_SET",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    f"SET_ACTIVE_LAYER selected layer ID {layer_id} "
                    f"in image ID {image_id}"
                )

                return response

            except Exception as exc:
                log(
                    f"SET_ACTIVE_LAYER failed for layer ID {layer_id}: {exc}"
                )

                return self._error_response(
                    message,
                    command="SET_ACTIVE_LAYER",
                    error=str(exc),
                )

        if message_type == "SET_LAYER_VISIBILITY":
            log("SET_LAYER_VISIBILITY received")

            try:
                image_id = int(message["image_id"])
                layer_id = int(message["layer_id"])

                if "visible" not in message:
                    raise KeyError("visible")

                visible_value = message["visible"]

                if not isinstance(visible_value, bool):
                    raise TypeError(
                        "visible must be a JSON boolean"
                    )

            except KeyError as exc:
                return self._error_response(
                    message,
                    command="SET_LAYER_VISIBILITY",
                    error=f"Missing required field: {exc.args[0]}",
                )

            except (TypeError, ValueError) as exc:
                return self._error_response(
                    message,
                    command="SET_LAYER_VISIBILITY",
                    error=str(exc),
                )

            try:
                result = self.dispatcher.call(
                    gimp_set_layer_visibility,
                    image_id,
                    layer_id,
                    visible_value,
                    timeout=5.0,
                )

                response = {
                    "type": "LAYER_VISIBILITY_SET",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    f"SET_LAYER_VISIBILITY layer ID {layer_id} "
                    f"visible={result['visible']}"
                )

                return response

            except Exception as exc:
                log(
                    f"SET_LAYER_VISIBILITY failed for layer ID {layer_id}: {exc}"
                )

                return self._error_response(
                    message,
                    command="SET_LAYER_VISIBILITY",
                    error=str(exc),
                )

        if message_type == "SET_LAYER_OPACITY":
            log("SET_LAYER_OPACITY received")

            try:
                image_id = int(message["image_id"])
                layer_id = int(message["layer_id"])
                opacity = float(message["opacity"])

            except KeyError as exc:
                return self._error_response(
                    message,
                    command="SET_LAYER_OPACITY",
                    error=f"Missing required field: {exc.args[0]}",
                )

            except (TypeError, ValueError):
                return self._error_response(
                    message,
                    command="SET_LAYER_OPACITY",
                    error="image_id/layer_id must be integers and opacity must be numeric",
                )

            try:
                result = self.dispatcher.call(
                    gimp_set_layer_opacity,
                    image_id,
                    layer_id,
                    opacity,
                    timeout=5.0,
                )

                response = {
                    "type": "LAYER_OPACITY_SET",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    f"SET_LAYER_OPACITY layer ID {layer_id} "
                    f"opacity={result['opacity']:.1f}"
                )

                return response

            except Exception as exc:
                log(
                    f"SET_LAYER_OPACITY failed for layer ID {layer_id}: {exc}"
                )

                return self._error_response(
                    message,
                    command="SET_LAYER_OPACITY",
                    error=str(exc),
                )


        if message_type == "ADD_LAYER":
            log("ADD_LAYER received")

            try:
                image_id = int(message["image_id"])
                name = str(message.get("name", "New Layer"))

                result = self.dispatcher.call(
                    gimp_add_layer,
                    image_id,
                    name,
                    timeout=5.0,
                )

                response = {
                    "type": "LAYER_ADDED",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    f"ADD_LAYER created layer ID {result['layer_id']} "
                    f"name={result['name']}"
                )

                return response

            except Exception as exc:
                log(f"ADD_LAYER failed: {exc}")

                return self._error_response(
                    message,
                    command="ADD_LAYER",
                    error=str(exc),
                )

        if message_type == "DELETE_LAYER":
            log("DELETE_LAYER received")

            try:
                image_id = int(message["image_id"])
                layer_id = int(message["layer_id"])

                result = self.dispatcher.call(
                    gimp_delete_layer,
                    image_id,
                    layer_id,
                    timeout=5.0,
                )

                response = {
                    "type": "LAYER_DELETED",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    f"DELETE_LAYER deleted layer ID {layer_id}"
                )

                return response

            except Exception as exc:
                log(f"DELETE_LAYER failed: {exc}")

                return self._error_response(
                    message,
                    command="DELETE_LAYER",
                    error=str(exc),
                )

        if message_type == "RENAME_LAYER":
            log("RENAME_LAYER received")

            try:
                image_id = int(message["image_id"])
                layer_id = int(message["layer_id"])
                name = str(message["name"])

                result = self.dispatcher.call(
                    gimp_rename_layer,
                    image_id,
                    layer_id,
                    name,
                    timeout=5.0,
                )

                response = {
                    "type": "LAYER_RENAMED",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    f"RENAME_LAYER layer ID {layer_id} "
                    f"name={result['name']}"
                )

                return response

            except Exception as exc:
                log(f"RENAME_LAYER failed: {exc}")

                return self._error_response(
                    message,
                    command="RENAME_LAYER",
                    error=str(exc),
                )

        if message_type == "DUPLICATE_LAYER":
            log("DUPLICATE_LAYER received")

            try:
                image_id = int(message["image_id"])
                layer_id = int(message["layer_id"])

                result = self.dispatcher.call(
                    gimp_duplicate_layer,
                    image_id,
                    layer_id,
                    timeout=5.0,
                )

                response = {
                    "type": "LAYER_DUPLICATED",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    f"DUPLICATE_LAYER copied layer ID {layer_id} "
                    f"to new layer ID {result['layer_id']}"
                )

                return response

            except Exception as exc:
                log(f"DUPLICATE_LAYER failed: {exc}")

                return self._error_response(
                    message,
                    command="DUPLICATE_LAYER",
                    error=str(exc),
                )

        if message_type == "REORDER_LAYER":
            log("REORDER_LAYER received")

            try:
                image_id = int(message["image_id"])
                layer_id = int(message["layer_id"])
                direction = str(message["direction"]).upper()

                result = self.dispatcher.call(
                    gimp_reorder_layer,
                    image_id,
                    layer_id,
                    direction,
                    timeout=5.0,
                )

                response = {
                    "type": "LAYER_REORDERED",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    f"REORDER_LAYER moved layer ID {layer_id} "
                    f"{direction.lower()} to position {result['position']}"
                )

                return response

            except Exception as exc:
                log(f"REORDER_LAYER failed: {exc}")

                return self._error_response(
                    message,
                    command="REORDER_LAYER",
                    error=str(exc),
                )

        if message_type == "MOVE_LAYER":
            log("MOVE_LAYER received")

            try:
                image_id = int(message["image_id"])
                layer_id = int(message["layer_id"])

                parent_id = message.get(
                    "parent_id",
                    None
                )

                if parent_id is not None:
                    parent_id = int(parent_id)

                result = self.dispatcher.call(
                    gimp_move_layer_to_parent,
                    image_id,
                    layer_id,
                    parent_id,
                    timeout=5.0,
                )

                response = {
                    "type": "LAYER_MOVED",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    f"MOVE_LAYER moved layer ID {layer_id} "
                    f"to parent {result['parent_id']}"
                )

                return response

            except Exception as exc:
                log(f"MOVE_LAYER failed: {exc}")

                return self._error_response(
                    message,
                    command="MOVE_LAYER",
                    error=str(exc),
                )


        if message_type == "CREATE_GROUP":
            log("CREATE_GROUP received")

            try:
                image_id = int(message["image_id"])
                name = str(
                    message.get(
                        "name",
                        "Layer Group"
                    )
                )

                result = self.dispatcher.call(
                    gimp_create_group,
                    image_id,
                    name,
                    timeout=5.0,
                )

                response = {
                    "type": "GROUP_CREATED",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    f"CREATE_GROUP created group ID {result['layer_id']} "
                    f"name={result['name']}"
                )

                return response

            except Exception as exc:
                log(
                    f"CREATE_GROUP failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="CREATE_GROUP",
                    error=str(exc),
                )

        if message_type == "MERGE_LAYER_DOWN":
            log("MERGE_LAYER_DOWN received")

            try:
                image_id = int(message["image_id"])
                layer_id = int(message["layer_id"])

                result = self.dispatcher.call(
                    gimp_merge_layer_down,
                    image_id,
                    layer_id,
                    timeout=5.0,
                )

                response = {
                    "type": "LAYER_MERGED_DOWN",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    f"MERGE_LAYER_DOWN merged source layer ID {layer_id} "
                    f"into resulting layer ID {result['layer_id']}"
                )

                return response

            except Exception as exc:
                log(
                    f"MERGE_LAYER_DOWN failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="MERGE_LAYER_DOWN",
                    error=str(exc),
                )

        if message_type == "SET_LAYER_LOCK":
            log("SET_LAYER_LOCK received")

            try:
                image_id = int(message["image_id"])
                layer_id = int(message["layer_id"])
                lock_type = str(message["lock_type"]).upper()

                if "locked" not in message:
                    raise KeyError("locked")

                locked = message["locked"]

                if not isinstance(
                    locked,
                    bool
                ):
                    raise TypeError(
                        "locked must be a JSON boolean"
                    )

                result = self.dispatcher.call(
                    gimp_set_layer_lock,
                    image_id,
                    layer_id,
                    lock_type,
                    locked,
                    timeout=5.0,
                )

                response = {
                    "type": "LAYER_LOCK_SET",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    f"SET_LAYER_LOCK layer ID {layer_id} "
                    f"{lock_type}={result['locked']}"
                )

                return response

            except Exception as exc:
                log(
                    f"SET_LAYER_LOCK failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="SET_LAYER_LOCK",
                    error=str(exc),
                )

        if message_type == "SET_LAYER_MODE":
            log("SET_LAYER_MODE received")

            try:
                image_id = int(message["image_id"])
                layer_id = int(message["layer_id"])
                mode_name = str(message["mode"]).upper()

                result = self.dispatcher.call(
                    gimp_set_layer_mode,
                    image_id,
                    layer_id,
                    mode_name,
                    timeout=5.0,
                )

                response = {
                    "type": "LAYER_MODE_SET",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    f"SET_LAYER_MODE layer ID {layer_id} "
                    f"mode={result['mode']}"
                )

                return response

            except Exception as exc:
                log(
                    f"SET_LAYER_MODE failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="SET_LAYER_MODE",
                    error=str(exc),
                )


        if message_type == "GET_BRUSH_STATE":
            log("GET_BRUSH_STATE received")

            try:
                result = self.dispatcher.call(
                    gimp_get_brush_state,
                    timeout=5.0,
                )

                response = {
                    "type": "BRUSH_STATE",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                return response

            except Exception as exc:
                log(
                    f"GET_BRUSH_STATE failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="GET_BRUSH_STATE",
                    error=str(exc),
                )

        if message_type == "BEGIN_PAINT_STROKE":
            log("BEGIN_PAINT_STROKE received")

            try:
                result = self.dispatcher.call(
                    gimp_begin_direct_paint_stroke,
                    int(
                        message["image_id"]
                    ),
                    int(
                        message["layer_id"]
                    ),
                    str(
                        message["stroke_id"]
                    ),
                    timeout=5.0,
                )

                response = {
                    "type": "PAINT_STROKE_BEGUN",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    "BEGIN_PAINT_STROKE "
                    f"stroke={result['stroke_id']} "
                    f"image ID {result['image_id']} "
                    f"layer ID {result['layer_id']}"
                )

                return response

            except Exception as exc:
                log(
                    f"BEGIN_PAINT_STROKE failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="BEGIN_PAINT_STROKE",
                    error=str(exc),
                )

        if message_type == "PAINT_STROKE_CHUNK":
            try:
                result = self.dispatcher.call(
                    gimp_paint_direct_stroke_chunk,
                    int(
                        message["image_id"]
                    ),
                    int(
                        message["layer_id"]
                    ),
                    str(
                        message["stroke_id"]
                    ),
                    message.get(
                        "strokes",
                        []
                    ),
                    timeout=10.0,
                )

                response = {
                    "type": "PAINT_STROKE_CHUNKED",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    "PAINT_STROKE_CHUNK "
                    f"stroke={result['stroke_id']} "
                    f"chunk={result['chunk_index']} "
                    f"points={result['point_count']} "
                    f"total={result['total_point_count']}"
                )

                return response

            except Exception as exc:
                log(
                    f"PAINT_STROKE_CHUNK failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="PAINT_STROKE_CHUNK",
                    error=str(exc),
                )

        if message_type == "END_PAINT_STROKE":
            log("END_PAINT_STROKE received")

            try:
                result = self.dispatcher.call(
                    gimp_end_direct_paint_stroke,
                    str(
                        message["stroke_id"]
                    ),
                    timeout=5.0,
                )

                response = {
                    "type": "PAINT_STROKE_ENDED",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    "END_PAINT_STROKE "
                    f"stroke={result['stroke_id']} "
                    f"chunks={result.get('chunk_count', 0)} "
                    f"points={result.get('point_count', 0)}"
                )

                return response

            except Exception as exc:
                log(
                    f"END_PAINT_STROKE failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="END_PAINT_STROKE",
                    error=str(exc),
                )

        if message_type == "PAINT_STROKE":
            log("PAINT_STROKE received")

            try:
                image_id = int(
                    message["image_id"]
                )

                layer_id = int(
                    message["layer_id"]
                )

                strokes = message.get(
                    "strokes",
                    []
                )

                result = self.dispatcher.call(
                    gimp_paint_stroke,
                    image_id,
                    layer_id,
                    strokes,
                    timeout=15.0,
                )

                response = {
                    "type": "STROKE_PAINTED",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    "PAINT_STROKE "
                    f"image ID {image_id} layer ID {layer_id} -> "
                    f"{result['point_count']} point(s), "
                    f"brush={result.get('brush_name', '')}, "
                    f"size={result.get('brush_size', 0):.1f}"
                )

                return response

            except Exception as exc:
                log(
                    f"PAINT_STROKE failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="PAINT_STROKE",
                    error=str(exc),
                )


        if message_type == "ENSURE_PAINT_LAYER":
            log("ENSURE_PAINT_LAYER received")

            try:
                image_id = int(
                    message["image_id"]
                )

                name = str(
                    message.get(
                        "name",
                        BLENDGIMP_PAINT_LAYER_NAME
                    )
                )

                result = self.dispatcher.call(
                    gimp_ensure_blendgimp_paint_layer,
                    image_id,
                    name,
                    timeout=10.0,
                )

                response = {
                    "type": "PAINT_LAYER_READY",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    "ENSURE_PAINT_LAYER "
                    f"image ID {image_id} -> "
                    f"layer ID {result['layer_id']} "
                    f"name={result['name']} "
                    f"created={result['created']}"
                )

                return response

            except Exception as exc:
                log(
                    f"ENSURE_PAINT_LAYER failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="ENSURE_PAINT_LAYER",
                    error=str(exc),
                )


        if message_type == "SET_LAYER_PIXELS_BINARY":
            log("SET_LAYER_PIXELS_BINARY received")

            try:

                image_id = int(
                    message["image_id"]
                )

                layer_id = int(
                    message["layer_id"]
                )

                image_x = int(
                    message.get(
                        "x",
                        0
                    )
                )

                image_y = int(
                    message.get(
                        "y",
                        0
                    )
                )

                region_width = int(
                    message["width"]
                )

                region_height = int(
                    message["height"]
                )

                raw_pixels = message.get(
                    "_binary_payload",
                    b""
                )

                result = self.dispatcher.call(
                    gimp_set_layer_pixels_binary,
                    image_id,
                    layer_id,
                    image_x,
                    image_y,
                    region_width,
                    region_height,
                    raw_pixels,
                    timeout=30.0,
                )

                response = {
                    "type": "LAYER_PIXELS_SET",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    "SET_LAYER_PIXELS_BINARY "
                    f"image ID {image_id} layer ID {layer_id} -> "
                    f"x={result['image_x']} y={result['image_y']} "
                    f"{result['width']}x{result['height']} "
                    f"{result['byte_length']} raw RGBA bytes"
                )

                return response

            except Exception as exc:

                log(
                    f"SET_LAYER_PIXELS_BINARY failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="SET_LAYER_PIXELS_BINARY",
                    error=str(exc),
                )


        if message_type == "GET_IMAGE_DIRTY_PIXELS_BINARY":
            log("GET_IMAGE_DIRTY_PIXELS_BINARY received")

            try:
                image_id = int(
                    message["image_id"]
                )

                result = self.dispatcher.call(
                    gimp_get_image_dirty_pixels_binary,
                    image_id,
                    timeout=30.0,
                )

                response = {
                    "type": "IMAGE_DIRTY_PIXELS_BINARY",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                if result.get(
                    "changed",
                    False
                ):
                    log(
                        "GET_IMAGE_DIRTY_PIXELS_BINARY "
                        f"image ID {image_id} -> "
                        f"x={result['x']} y={result['y']} "
                        f"{result['region_width']}x"
                        f"{result['region_height']} "
                        f"{result['byte_length']} raw RGBA bytes"
                    )
                else:
                    log(
                        "GET_IMAGE_DIRTY_PIXELS_BINARY "
                        f"image ID {image_id} -> no pixel delta"
                    )

                return response

            except Exception as exc:
                log(
                    f"GET_IMAGE_DIRTY_PIXELS_BINARY failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="GET_IMAGE_DIRTY_PIXELS_BINARY",
                    error=str(exc),
                )


        if message_type == "GET_IMAGE_DIRTY_PIXELS":
            log("GET_IMAGE_DIRTY_PIXELS received")

            try:
                image_id = int(
                    message["image_id"]
                )

                result = self.dispatcher.call(
                    gimp_get_image_dirty_pixels,
                    image_id,
                    timeout=30.0,
                )

                response = {
                    "type": "IMAGE_DIRTY_PIXELS",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                if result.get(
                    "changed",
                    False
                ):
                    log(
                        "GET_IMAGE_DIRTY_PIXELS "
                        f"image ID {image_id} -> "
                        f"x={result['x']} y={result['y']} "
                        f"{result['region_width']}x"
                        f"{result['region_height']} "
                        f"{result['byte_length']} RGBA bytes"
                    )
                else:
                    log(
                        "GET_IMAGE_DIRTY_PIXELS "
                        f"image ID {image_id} -> no pixel delta"
                    )

                return response

            except Exception as exc:
                log(
                    f"GET_IMAGE_DIRTY_PIXELS failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="GET_IMAGE_DIRTY_PIXELS",
                    error=str(exc),
                )


        if message_type == "GET_IMAGE_PIXELS_BINARY":
            log("GET_IMAGE_PIXELS_BINARY received")

            try:
                image_id = int(
                    message["image_id"]
                )

                result = self.dispatcher.call(
                    gimp_get_image_pixels_binary,
                    image_id,
                    timeout=30.0,
                )

                response = {
                    "type": "IMAGE_PIXELS_BINARY",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    "GET_IMAGE_PIXELS_BINARY "
                    f"image ID {image_id} -> "
                    f"{result['width']}x{result['height']} "
                    f"{result['byte_length']} raw RGBA bytes"
                )

                return response

            except Exception as exc:
                log(
                    f"GET_IMAGE_PIXELS_BINARY failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="GET_IMAGE_PIXELS_BINARY",
                    error=str(exc),
                )


        if message_type == "GET_IMAGE_PIXELS":
            log("GET_IMAGE_PIXELS received")

            try:
                image_id = int(
                    message["image_id"]
                )

                result = self.dispatcher.call(
                    gimp_get_image_pixels,
                    image_id,
                    timeout=30.0,
                )

                response = {
                    "type": "IMAGE_PIXELS",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    "GET_IMAGE_PIXELS "
                    f"image ID {image_id} -> "
                    f"{result['width']}x{result['height']} "
                    f"{result['byte_length']} RGBA bytes"
                )

                return response

            except Exception as exc:
                log(
                    f"GET_IMAGE_PIXELS failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="GET_IMAGE_PIXELS",
                    error=str(exc),
                )


        if message_type == "GET_IMAGE_STATE":
            try:
                image_id = int(
                    message["image_id"]
                )

                result = self.dispatcher.call(
                    gimp_get_image_state,
                    image_id,
                    timeout=5.0,
                )

                response = {
                    "type": "IMAGE_STATE",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                if result.get(
                    "changed",
                    False
                ):
                    detector = str(
                        result.get(
                            "detector",
                            "unknown"
                        )
                    )

                    log(
                        "IMAGE_STATE "
                        f"image ID {image_id} "
                        f"revision={result['revision']} "
                        f"detector={detector} "
                        f"fingerprint_changed="
                        f"{result.get('fingerprint_changed', False)} "
                        f"native_changed="
                        f"{result.get('native_changed', False)} "
                        f"suppressed="
                        f"{result.get('self_originated_suppressed', False)} "
                        f"damage={result.get('damage_width', 0)}x"
                        f"{result.get('damage_height', 0)} "
                        f"@ {result.get('damage_x', 0)},"
                        f"{result.get('damage_y', 0)}"
                    )

                return response

            except Exception as exc:
                log(
                    f"GET_IMAGE_STATE failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="GET_IMAGE_STATE",
                    error=str(exc),
                )


        if message_type == "EXPORT_COMPOSITE":
            log("EXPORT_COMPOSITE received")

            try:
                image_id = int(message["image_id"])

                result = self.dispatcher.call(
                    gimp_export_composite,
                    image_id,
                    timeout=30.0,
                )

                response = {
                    "type": "COMPOSITE_EXPORTED",
                    "ok": True,
                    **result,
                }

                self._copy_request_id(
                    message,
                    response,
                )

                log(
                    f"EXPORT_COMPOSITE image ID {image_id} -> "
                    f"{result['path']} ({result['width']}x{result['height']})"
                )

                return response

            except Exception as exc:
                log(
                    f"EXPORT_COMPOSITE failed: {exc}"
                )

                return self._error_response(
                    message,
                    command="EXPORT_COMPOSITE",
                    error=str(exc),
                )

        return self._error_response(
            message,
            command=message_type,
            error=f"Unknown command: {message_type}",
        )

    @staticmethod
    def _copy_request_id(request, response):
        if "request_id" in request:
            response["request_id"] = request["request_id"]

    @staticmethod
    def _error_response(request, command, error):
        response = {
            "type": "ERROR",
            "ok": False,
            "error": error,
        }

        if command is not None:
            response["command"] = command

        if isinstance(request, dict) and "request_id" in request:
            response["request_id"] = request["request_id"]

        return response

    @staticmethod
    def _send_response(
        client,
        payload
    ):
        """
        Send either normal newline JSON or a small JSON header followed by an
        exact raw binary payload.

        `_binary_payload` is internal-only and is never serialized to JSON.
        """

        if (
            isinstance(
                payload,
                dict
            )
            and "_binary_payload" in payload
        ):
            binary_payload = payload.get(
                "_binary_payload",
                b""
            )

            if binary_payload is None:
                binary_payload = b""

            if not isinstance(
                binary_payload,
                bytes
            ):
                binary_payload = bytes(
                    binary_payload
                )

            header = dict(
                payload
            )

            header.pop(
                "_binary_payload",
                None
            )

            header[
                "binary_payload"
            ] = True

            header[
                "binary_length"
            ] = len(
                binary_payload
            )

            BlendGimpIPCServer._send_json(
                client,
                header
            )

            if binary_payload:
                client.sendall(
                    binary_payload
                )

            return

        BlendGimpIPCServer._send_json(
            client,
            payload
        )

    @staticmethod
    def _send_json(client, payload):
        encoded = (
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            + "\n"
        ).encode("utf-8")

        client.sendall(encoded)


# -----------------------------------------------------------------------------
# Persistent GIMP procedure
# -----------------------------------------------------------------------------

def blendgimp_run(procedure, config, run_data):
    """Lifetime entry point for the automatic persistent BlendGimp plug-in."""

    plug_in = procedure.get_plug_in()
    return plug_in.run_blendgimp(procedure)


class BlendGimpPlugin(Gimp.PlugIn):
    def __init__(self):
        super().__init__()
        self._main_loop = None
        self._dispatcher = None
        self._ipc_server = None
        self._shutting_down = False

    def do_set_i18n(self, procedure_name):
        # BlendGimp has no localization catalog yet. Explicitly disable GIMP's
        # default locale lookup so startup stays clean.
        log("Localization disabled")
        return False, None, None

    def do_query_procedures(self):
        return [PLUGIN_PROC]

    def do_create_procedure(self, name):
        if name != PLUGIN_PROC:
            return None

        procedure = Gimp.Procedure.new(
            self,
            name,
            Gimp.PDBProcType.PERSISTENT,
            blendgimp_run,
            None,
        )

        procedure.set_documentation(
            "BlendGimp persistent integration service",
            "Maintains the local IPC connection used by the BlendGimp Blender extension.",
            name,
        )
        procedure.set_attribution(
            "Afro Lion Studios, LLC",
            "Afro Lion Studios, LLC",
            "2026",
        )

        return procedure

    def run_blendgimp(self, procedure):
        log("BlendGimp plug-in executable loaded")

        # Any GIMP API values needed by the worker thread are captured here,
        # before that worker starts.
        gimp_version = str(Gimp.version())

        # Construct the dispatcher on this thread. GLib idle callbacks then
        # bring requested GIMP API work back here from the socket worker.
        self._dispatcher = GimpMainThreadDispatcher()
        self._ipc_server = BlendGimpIPCServer(
            dispatcher=self._dispatcher,
            gimp_version=gimp_version,
        )

        # Because this persistent plug-in remains inside a GLib main loop,
        # enable asynchronous processing of messages from GIMP.
        self.persistent_enable()

        # REQUIRED for PERSISTENT procedures. GIMP waits for this notification.
        procedure.persistent_ready()
        log("Persistent procedure READY")

        self._ipc_server.start()

        self._main_loop = GLib.MainLoop()

        try:
            self._main_loop.run()
        except KeyboardInterrupt:
            log("KeyboardInterrupt received")
        except Exception:
            log("Persistent main loop failed:\n" + traceback.format_exc())
        finally:
            self._shutdown()

        return procedure.new_return_values(
            Gimp.PDBStatusType.SUCCESS,
            None,
        )

    def do_quit(self):
        self._shutdown()

    def _shutdown(self):
        if self._shutting_down:
            return

        self._shutting_down = True
        log("Shutting down")

        if self._ipc_server is not None:
            self._ipc_server.stop()

        if self._main_loop is not None and self._main_loop.is_running():
            self._main_loop.quit()


# -----------------------------------------------------------------------------
# GIMP entry point
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    Gimp.main(BlendGimpPlugin.__gtype__, sys.argv)
