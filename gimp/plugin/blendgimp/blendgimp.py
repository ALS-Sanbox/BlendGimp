#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import json
import socket
import threading
import traceback

import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp, GLib


# -----------------------------------------------------------------------------
# BlendGimp IPC configuration
# -----------------------------------------------------------------------------

PLUGIN_PROC = "extension-blendgimp"
HOST = "127.0.0.1"
PORT = 8765
PROTOCOL_VERSION = 1


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
                        log("Blender disconnected")

        except Exception:
            if not self._stop_event.is_set():
                log("IPC server failed:\n" + traceback.format_exc())
        finally:
            self._server_socket = None
            log("Server stopped")

    def _handle_client(self, client):
        buffer = b""

        while not self._stop_event.is_set():
            try:
                chunk = client.recv(65536)
            except socket.timeout:
                continue

            if not chunk:
                return

            buffer += chunk

            while b"\n" in buffer:
                raw_line, buffer = buffer.split(b"\n", 1)
                raw_line = raw_line.strip()

                if not raw_line:
                    continue

                try:
                    message = json.loads(raw_line.decode("utf-8"))
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

                response = self._dispatch_message(message)
                self._send_json(client, response)

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
