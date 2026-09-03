import bpy
import json
import os
import time
import base64

from ..core import gimp_manager
from ..ipc.connection import (
    connection_manager,
    direct_paint_owns_refresh,
    GimpEngineShutdownRefusedError,
    GimpImageNotFoundError,
    set_direct_paint_refresh_owner,
)
from ..painting.stroke_tool import (
    BLENDGIMP_OT_direct_gimp_brush_paint,
)


try:
    import numpy as np
except Exception:
    np = None


# ============================================================
# GIMP ENGINE LIFECYCLE RUNTIME
# ============================================================

ENGINE_LIFECYCLE_POLL_INTERVAL = 0.5
ENGINE_HEALTHCHECK_INTERVAL = 5.0
ENGINE_CONNECT_BACKOFF_MAX = 5.0
ENGINE_RESTART_BACKOFF_MAX = 30.0

_ENGINE_LIFECYCLE_RUNTIME = {
    "next_connect_at": 0.0,
    "next_healthcheck_at": 0.0,
    "next_restart_at": 0.0,
    "connect_failures": 0,
    "restart_failures": 0,
}


def reset_engine_lifecycle_runtime():
    _ENGINE_LIFECYCLE_RUNTIME.update(
        {
            "next_connect_at": 0.0,
            "next_healthcheck_at": 0.0,
            "next_restart_at": 0.0,
            "connect_failures": 0,
            "restart_failures": 0,
        }
    )


# ============================================================
# AUTOMATIC TEXTURE SYNC RUNTIME
# ============================================================

AUTO_SYNC_POLL_INTERVAL = 0.5

_AUTO_SYNC_RUNTIME = {
    "image_id": -1,
    "last_seen_revision": None,
    "last_synced_revision": None,
    "pending_revision": None,
    "pending_since": 0.0,
}

BLENDER_PAINT_SYNC_POLL_INTERVAL = 0.5
BLENDER_PAINT_LAYER_NAME = "BlendGimp Paint"

_BLENDER_PAINT_SYNC_RUNTIME = {
    "image_id": -1,
    "layer_id": -1,
    "baseline": None,
    "pending_pixels": None,
    "pending_bbox": None,
    "pending_since": 0.0,
}


def reset_blender_paint_sync_runtime(
    image_id=-1,
    layer_id=-1,
    baseline=None
):
    _BLENDER_PAINT_SYNC_RUNTIME[
        "image_id"
    ] = int(
        image_id
    )

    _BLENDER_PAINT_SYNC_RUNTIME[
        "layer_id"
    ] = int(
        layer_id
    )

    _BLENDER_PAINT_SYNC_RUNTIME[
        "baseline"
    ] = baseline

    _BLENDER_PAINT_SYNC_RUNTIME[
        "pending_pixels"
    ] = None

    _BLENDER_PAINT_SYNC_RUNTIME[
        "pending_bbox"
    ] = None

    _BLENDER_PAINT_SYNC_RUNTIME[
        "pending_since"
    ] = 0.0


def reset_auto_sync_runtime(
    image_id=-1,
    revision=None
):
    _AUTO_SYNC_RUNTIME[
        "image_id"
    ] = int(image_id)

    _AUTO_SYNC_RUNTIME[
        "last_seen_revision"
    ] = (
        None
        if revision is None
        else int(revision)
    )

    _AUTO_SYNC_RUNTIME[
        "last_synced_revision"
    ] = (
        None
        if revision is None
        else int(revision)
    )

    _AUTO_SYNC_RUNTIME[
        "pending_revision"
    ] = None

    _AUTO_SYNC_RUNTIME[
        "pending_since"
    ] = 0.0


# ============================================================
# GIMP LAYER MODES
# ============================================================

BLENDGIMP_LAYER_MODE_ITEMS = [
    ("NORMAL_LEGACY", "Normal (Legacy)", "GIMP Normal (Legacy) blend mode"),
    ("DISSOLVE", "Dissolve", "GIMP Dissolve blend mode"),
    ("BEHIND_LEGACY", "Behind (Legacy)", "GIMP Behind (Legacy) blend mode"),
    ("MULTIPLY_LEGACY", "Multiply (Legacy)", "GIMP Multiply (Legacy) blend mode"),
    ("SCREEN_LEGACY", "Screen (Legacy)", "GIMP Screen (Legacy) blend mode"),
    ("OVERLAY_LEGACY", "Overlay (Legacy)", "GIMP Overlay (Legacy) blend mode"),
    ("DIFFERENCE_LEGACY", "Difference (Legacy)", "GIMP Difference (Legacy) blend mode"),
    ("ADDITION_LEGACY", "Addition (Legacy)", "GIMP Addition (Legacy) blend mode"),
    ("SUBTRACT_LEGACY", "Subtract (Legacy)", "GIMP Subtract (Legacy) blend mode"),
    ("DARKEN_ONLY_LEGACY", "Darken Only (Legacy)", "GIMP Darken Only (Legacy) blend mode"),
    ("LIGHTEN_ONLY_LEGACY", "Lighten Only (Legacy)", "GIMP Lighten Only (Legacy) blend mode"),
    ("HSV_HUE_LEGACY", "HSV Hue (Legacy)", "GIMP HSV Hue (Legacy) blend mode"),
    ("HSV_SATURATION_LEGACY", "HSV Saturation (Legacy)", "GIMP HSV Saturation (Legacy) blend mode"),
    ("HSL_COLOR_LEGACY", "HSL Color (Legacy)", "GIMP HSL Color (Legacy) blend mode"),
    ("HSV_VALUE_LEGACY", "HSV Value (Legacy)", "GIMP HSV Value (Legacy) blend mode"),
    ("DIVIDE_LEGACY", "Divide (Legacy)", "GIMP Divide (Legacy) blend mode"),
    ("DODGE_LEGACY", "Dodge (Legacy)", "GIMP Dodge (Legacy) blend mode"),
    ("BURN_LEGACY", "Burn (Legacy)", "GIMP Burn (Legacy) blend mode"),
    ("HARDLIGHT_LEGACY", "Hardlight (Legacy)", "GIMP Hardlight (Legacy) blend mode"),
    ("SOFTLIGHT_LEGACY", "Softlight (Legacy)", "GIMP Softlight (Legacy) blend mode"),
    ("GRAIN_EXTRACT_LEGACY", "Grain Extract (Legacy)", "GIMP Grain Extract (Legacy) blend mode"),
    ("GRAIN_MERGE_LEGACY", "Grain Merge (Legacy)", "GIMP Grain Merge (Legacy) blend mode"),
    ("COLOR_ERASE_LEGACY", "Color Erase (Legacy)", "GIMP Color Erase (Legacy) blend mode"),
    ("OVERLAY", "Overlay", "GIMP Overlay blend mode"),
    ("LCH_HUE", "LCh Hue", "GIMP LCh Hue blend mode"),
    ("LCH_CHROMA", "LCh Chroma", "GIMP LCh Chroma blend mode"),
    ("LCH_COLOR", "LCh Color", "GIMP LCh Color blend mode"),
    ("LCH_LIGHTNESS", "LCh Lightness", "GIMP LCh Lightness blend mode"),
    ("NORMAL", "Normal", "GIMP Normal blend mode"),
    ("BEHIND", "Behind", "GIMP Behind blend mode"),
    ("MULTIPLY", "Multiply", "GIMP Multiply blend mode"),
    ("SCREEN", "Screen", "GIMP Screen blend mode"),
    ("DIFFERENCE", "Difference", "GIMP Difference blend mode"),
    ("ADDITION", "Addition", "GIMP Addition blend mode"),
    ("SUBTRACT", "Subtract", "GIMP Subtract blend mode"),
    ("DARKEN_ONLY", "Darken Only", "GIMP Darken Only blend mode"),
    ("LIGHTEN_ONLY", "Lighten Only", "GIMP Lighten Only blend mode"),
    ("HSV_HUE", "HSV Hue", "GIMP HSV Hue blend mode"),
    ("HSV_SATURATION", "HSV Saturation", "GIMP HSV Saturation blend mode"),
    ("HSL_COLOR", "HSL Color", "GIMP HSL Color blend mode"),
    ("HSV_VALUE", "HSV Value", "GIMP HSV Value blend mode"),
    ("DIVIDE", "Divide", "GIMP Divide blend mode"),
    ("DODGE", "Dodge", "GIMP Dodge blend mode"),
    ("BURN", "Burn", "GIMP Burn blend mode"),
    ("HARDLIGHT", "Hardlight", "GIMP Hardlight blend mode"),
    ("SOFTLIGHT", "Softlight", "GIMP Softlight blend mode"),
    ("GRAIN_EXTRACT", "Grain Extract", "GIMP Grain Extract blend mode"),
    ("GRAIN_MERGE", "Grain Merge", "GIMP Grain Merge blend mode"),
    ("VIVID_LIGHT", "Vivid Light", "GIMP Vivid Light blend mode"),
    ("PIN_LIGHT", "Pin Light", "GIMP Pin Light blend mode"),
    ("LINEAR_LIGHT", "Linear Light", "GIMP Linear Light blend mode"),
    ("HARD_MIX", "Hard Mix", "GIMP Hard Mix blend mode"),
    ("EXCLUSION", "Exclusion", "GIMP Exclusion blend mode"),
    ("LINEAR_BURN", "Linear Burn", "GIMP Linear Burn blend mode"),
    ("LUMA_DARKEN_ONLY", "Luma Darken Only", "GIMP Luma Darken Only blend mode"),
    ("LUMA_LIGHTEN_ONLY", "Luma Lighten Only", "GIMP Luma Lighten Only blend mode"),
    ("LUMINANCE", "Luminance", "GIMP Luminance blend mode"),
    ("COLOR_ERASE", "Color Erase", "GIMP Color Erase blend mode"),
    ("ERASE", "Erase", "GIMP Erase blend mode"),
    ("MERGE", "Merge", "GIMP Merge blend mode"),
    ("SPLIT", "Split", "GIMP Split blend mode"),
    ("PASS_THROUGH", "Pass Through", "GIMP Pass Through blend mode"),
    ("REPLACE", "Replace", "GIMP Replace blend mode"),
    ("OVERWRITE", "Overwrite", "GIMP Overwrite blend mode"),
]


def blendgimp_layer_mode_label(
    mode_name
):

    mode_name = str(
        mode_name or ""
    )

    for identifier, label, description in (
        BLENDGIMP_LAYER_MODE_ITEMS
    ):

        if identifier == mode_name:
            return label

    return mode_name or "Unknown"


# ============================================================
# IMAGE RESULT HELPERS
# ============================================================

def clear_image_results(
    scene
):

    scene.blendgimp_images_queried = False
    scene.blendgimp_image_count = 0
    scene.blendgimp_images_json = "[]"
    scene.blendgimp_layers_json = "{}"
    scene.blendgimp_texture_sync_json = "{}"


def clear_gimp_session_bindings(
    scene,
    status="No GIMP image selected"
):
    """Clear IDs that are valid only for one GIMP process session."""

    clear_image_results(
        scene
    )

    scene.blendgimp_auto_sync_enabled = False
    scene.blendgimp_auto_sync_image_id = -1
    scene.blendgimp_auto_sync_revision = 0
    scene.blendgimp_auto_sync_status = str(status)
    scene.blendgimp_auto_sync_detector = ""

    scene.blendgimp_blender_paint_sync_enabled = False
    scene.blendgimp_blender_paint_sync_image_id = -1
    scene.blendgimp_blender_paint_sync_layer_id = -1
    scene.blendgimp_blender_paint_sync_status = "Off"

    set_direct_paint_refresh_owner(
        False
    )
    reset_auto_sync_runtime()
    reset_blender_paint_sync_runtime()


def store_engine_connection(
    scene,
    response
):
    """Store a successful HELLO/READY handshake in Blender scene state."""

    scene.blendgimp_connected = True

    scene.blendgimp_protocol_version = int(
        response.get(
            "protocol",
            0
        )
    )

    scene.blendgimp_remote_version = str(
        response.get(
            "blendgimp_version",
            response.get(
                "server",
                ""
            )
        )
    )

    scene.blendgimp_runtime_gimp_version = str(
        response.get(
            "gimp_version",
            ""
        )
    )

    scene.blendgimp_engine_state = (
        gimp_manager.ENGINE_STATE_CONNECTED
    )
    scene.blendgimp_engine_last_error = ""

    gimp_manager.mark_engine_connected()

    _ENGINE_LIFECYCLE_RUNTIME[
        "connect_failures"
    ] = 0
    _ENGINE_LIFECYCLE_RUNTIME[
        "restart_failures"
    ] = 0
    _ENGINE_LIFECYCLE_RUNTIME[
        "next_restart_at"
    ] = 0.0
    _ENGINE_LIFECYCLE_RUNTIME[
        "next_healthcheck_at"
    ] = (
        time.monotonic()
        + ENGINE_HEALTHCHECK_INTERVAL
    )


def clear_engine_connection(
    scene,
    clear_results=True
):
    scene.blendgimp_connected = False
    scene.blendgimp_protocol_version = 0
    scene.blendgimp_remote_version = ""
    scene.blendgimp_runtime_gimp_version = ""

    if clear_results:
        clear_image_results(
            scene
        )


def start_scene_engine(
    scene,
    automatic=False
):
    """Start the selected engine mode and schedule a non-blocking connect."""

    gimp_path = str(
        scene.blendgimp_gimp_path
        or ""
    )

    if not gimp_path:
        scene.blendgimp_engine_state = (
            gimp_manager.ENGINE_STATE_FAILED
        )
        scene.blendgimp_engine_last_error = (
            "GIMP has not been detected"
        )
        return False, None

    if not os.path.isfile(
        gimp_path
    ):
        scene.blendgimp_gimp_detected = False
        scene.blendgimp_gimp_running = False
        scene.blendgimp_engine_state = (
            gimp_manager.ENGINE_STATE_FAILED
        )
        scene.blendgimp_engine_last_error = (
            "The detected GIMP executable no longer exists"
        )
        return False, None

    process_was_running = (
        gimp_manager.is_gimp_running()
    )

    success, pid = gimp_manager.launch_gimp(
        gimp_path,
        scene.blendgimp_engine_mode
    )

    if not success:
        snapshot = (
            gimp_manager.get_engine_snapshot()
        )
        scene.blendgimp_gimp_running = False
        scene.blendgimp_engine_state = str(
            snapshot.get(
                "state",
                gimp_manager.ENGINE_STATE_FAILED
            )
        )
        scene.blendgimp_engine_last_error = str(
            snapshot.get(
                "last_error",
                "Could not launch GIMP"
            )
        )
        return False, None

    if not process_was_running:
        clear_gimp_session_bindings(
            scene,
            status=(
                "Waiting for a Blender-owned texture"
            )
        )

    scene.blendgimp_engine_should_run = True
    scene.blendgimp_gimp_running = True
    scene.blendgimp_engine_state = (
        gimp_manager.ENGINE_STATE_STARTING
    )
    scene.blendgimp_engine_last_error = ""

    if automatic:
        scene.blendgimp_engine_restart_count += 1

    _ENGINE_LIFECYCLE_RUNTIME[
        "next_connect_at"
    ] = time.monotonic() + 0.25
    _ENGINE_LIFECYCLE_RUNTIME[
        "next_restart_at"
    ] = 0.0

    return True, pid


def connect_scene_engine(
    scene
):
    """Attempt one handshake; the lifecycle timer owns retry scheduling."""

    scene.blendgimp_engine_state = (
        gimp_manager.ENGINE_STATE_CONNECTING
    )
    gimp_manager.mark_engine_connecting()

    try:
        response = (
            connection_manager.connect()
        )

        store_engine_connection(
            scene,
            response
        )

        clear_image_results(
            scene
        )

        print(
            "BLENDGIMP: "
            "GIMP engine connection established"
        )

        return response

    except Exception as exc:
        clear_engine_connection(
            scene
        )

        gimp_manager.mark_engine_disconnected(
            str(exc)
        )

        snapshot = (
            gimp_manager.get_engine_snapshot()
        )

        scene.blendgimp_engine_state = str(
            snapshot.get(
                "state",
                gimp_manager.ENGINE_STATE_DISCONNECTED
            )
        )
        scene.blendgimp_engine_last_error = str(
            exc
        )

        raise


def stop_scene_engine(
    scene
):
    """Disconnect Blender and stop only the GIMP process BlendGimp owns."""

    scene.blendgimp_engine_should_run = False

    graceful_requested = False

    if connection_manager.is_connected():
        try:
            connection_manager.shutdown_engine(
                force=False
            )
            graceful_requested = True

        except GimpEngineShutdownRefusedError as exc:
            scene.blendgimp_engine_should_run = True
            scene.blendgimp_engine_state = (
                gimp_manager.ENGINE_STATE_CONNECTED
            )
            scene.blendgimp_engine_last_error = str(exc)
            print(
                "BLENDGIMP: "
                f"GIMP shutdown refused: {exc}"
            )
            return False, None, False

        except Exception as exc:
            # If the plug-in or socket disappeared, the process manager still
            # performs its bounded platform-termination fallback.
            print(
                "BLENDGIMP: "
                "Clean shutdown request failed; "
                f"using fallback: {exc}"
            )

    connection_manager.disconnect()
    clear_engine_connection(
        scene
    )

    success, exit_code, forced = (
        gimp_manager.stop_gimp(
            graceful_requested=graceful_requested
        )
    )

    if success:
        clear_gimp_session_bindings(
            scene,
            status="Engine stopped"
        )

    reset_engine_lifecycle_runtime()

    scene.blendgimp_gimp_running = (
        gimp_manager.is_gimp_running()
    )

    snapshot = (
        gimp_manager.get_engine_snapshot()
    )
    scene.blendgimp_engine_state = str(
        snapshot.get(
            "state",
            gimp_manager.ENGINE_STATE_STOPPED
        )
    )
    scene.blendgimp_engine_last_error = str(
        snapshot.get(
            "last_error",
            ""
        )
    )

    return success, exit_code, forced


def blendgimp_engine_lifecycle_timer():
    """Monitor, reconnect, and recover the persistent GIMP engine."""

    if not hasattr(
        bpy.types.Scene,
        "blendgimp_engine_state"
    ):
        return None

    scene = getattr(
        bpy.context,
        "scene",
        None
    )

    if scene is None:
        return ENGINE_LIFECYCLE_POLL_INTERVAL

    now = time.monotonic()
    running = (
        gimp_manager.is_gimp_running()
    )

    scene.blendgimp_gimp_running = running

    # A socket can look connected until the next read. Use a quiet, infrequent
    # PING so disappearance is detected even when painting and Auto Sync are
    # idle.
    if connection_manager.is_connected():
        if now >= float(
            _ENGINE_LIFECYCLE_RUNTIME.get(
                "next_healthcheck_at",
                0.0
            )
        ):
            try:
                connection_manager.ping(
                    quiet=True
                )

                store_engine_connection(
                    scene,
                    {
                        "protocol": connection_manager.remote_protocol,
                        "blendgimp_version": connection_manager.remote_version,
                        "gimp_version": connection_manager.remote_gimp_version,
                    }
                )

            except Exception as exc:
                clear_engine_connection(
                    scene
                )
                gimp_manager.mark_engine_disconnected(
                    str(exc)
                )
                scene.blendgimp_engine_last_error = str(
                    exc
                )

        else:
            scene.blendgimp_connected = True
            scene.blendgimp_engine_state = (
                gimp_manager.ENGINE_STATE_CONNECTED
            )

    if connection_manager.is_connected():
        return ENGINE_LIFECYCLE_POLL_INTERVAL

    scene.blendgimp_connected = False

    if not scene.blendgimp_engine_should_run:
        snapshot = (
            gimp_manager.get_engine_snapshot()
        )
        scene.blendgimp_engine_state = str(
            snapshot.get(
                "state",
                gimp_manager.ENGINE_STATE_STOPPED
            )
        )
        return ENGINE_LIFECYCLE_POLL_INTERVAL

    if running:
        scene.blendgimp_engine_state = (
            gimp_manager.ENGINE_STATE_CONNECTING
        )
        gimp_manager.mark_engine_connecting()

        if now >= float(
            _ENGINE_LIFECYCLE_RUNTIME.get(
                "next_connect_at",
                0.0
            )
        ):
            try:
                connect_scene_engine(
                    scene
                )

            except Exception as exc:
                failures = int(
                    _ENGINE_LIFECYCLE_RUNTIME.get(
                        "connect_failures",
                        0
                    )
                ) + 1

                _ENGINE_LIFECYCLE_RUNTIME[
                    "connect_failures"
                ] = failures

                delay = min(
                    ENGINE_CONNECT_BACKOFF_MAX,
                    0.5 * (2 ** min(failures - 1, 4))
                )

                _ENGINE_LIFECYCLE_RUNTIME[
                    "next_connect_at"
                ] = now + delay

                print(
                    "BLENDGIMP: "
                    "Engine connection not ready; "
                    f"retrying in {delay:.1f}s: {exc}"
                )

        return ENGINE_LIFECYCLE_POLL_INTERVAL

    # The owned process exited. Close any stale socket state and either wait
    # in FAILED or schedule an automatic restart with bounded backoff.
    connection_manager.disconnect()
    clear_engine_connection(
        scene
    )

    snapshot = (
        gimp_manager.get_engine_snapshot()
    )
    scene.blendgimp_engine_last_error = str(
        snapshot.get(
            "last_error",
            "GIMP engine is not running"
        )
    )

    if not scene.blendgimp_engine_auto_reconnect:
        scene.blendgimp_engine_state = (
            gimp_manager.ENGINE_STATE_FAILED
        )
        return ENGINE_LIFECYCLE_POLL_INTERVAL

    next_restart_at = float(
        _ENGINE_LIFECYCLE_RUNTIME.get(
            "next_restart_at",
            0.0
        )
    )

    if next_restart_at <= 0.0:
        failures = int(
            _ENGINE_LIFECYCLE_RUNTIME.get(
                "restart_failures",
                0
            )
        )
        delay = min(
            ENGINE_RESTART_BACKOFF_MAX,
            1.0 * (2 ** min(failures, 5))
        )
        _ENGINE_LIFECYCLE_RUNTIME[
            "next_restart_at"
        ] = now + delay
        scene.blendgimp_engine_state = (
            gimp_manager.ENGINE_STATE_DISCONNECTED
        )
        return ENGINE_LIFECYCLE_POLL_INTERVAL

    if now >= next_restart_at:
        failures = int(
            _ENGINE_LIFECYCLE_RUNTIME.get(
                "restart_failures",
                0
            )
        ) + 1
        _ENGINE_LIFECYCLE_RUNTIME[
            "restart_failures"
        ] = failures

        success, _pid = start_scene_engine(
            scene,
            automatic=True
        )

        if not success:
            _ENGINE_LIFECYCLE_RUNTIME[
                "next_restart_at"
            ] = 0.0

    return ENGINE_LIFECYCLE_POLL_INTERVAL


def get_stored_images(
    scene
):

    try:

        images = json.loads(
            scene.blendgimp_images_json
            or
            "[]"
        )

        if isinstance(
            images,
            list
        ):

            return images

    except Exception:

        pass

    return []


def get_stored_layer_results(
    scene
):

    try:

        results = json.loads(
            scene.blendgimp_layers_json
            or
            "{}"
        )

        if isinstance(
            results,
            dict
        ):

            return results

    except Exception:

        pass

    return {}


def get_stored_layer_result(
    scene,
    image_id
):

    results = (
        get_stored_layer_results(
            scene
        )
    )

    return results.get(
        str(int(image_id))
    )


def store_layer_result(
    scene,
    image_id,
    response
):

    results = (
        get_stored_layer_results(
            scene
        )
    )

    results[
        str(int(image_id))
    ] = response

    scene.blendgimp_layers_json = (
        json.dumps(
            results,
            ensure_ascii=False
        )
    )


def get_texture_sync_results(
    scene
):

    try:
        result = json.loads(
            scene.blendgimp_texture_sync_json
            or
            "{}"
        )

        if isinstance(
            result,
            dict
        ):
            return result

    except Exception:
        pass

    return {}


def get_texture_sync_result(
    scene,
    image_id
):

    return get_texture_sync_results(
        scene
    ).get(
        str(int(image_id))
    )


def store_texture_sync_result(
    scene,
    image_id,
    result
):

    results = get_texture_sync_results(
        scene
    )

    results[
        str(int(image_id))
    ] = result

    scene.blendgimp_texture_sync_json = json.dumps(
        results,
        ensure_ascii=False
    )


def _find_selected_gimp_layer(
    layers
):
    """
    Return the first selected non-group layer from a nested GIMP layer tree.
    """

    for layer in layers or []:

        if (
            layer.get(
                "selected",
                False
            )
            and not layer.get(
                "is_group",
                False
            )
        ):
            return layer

        selected = _find_selected_gimp_layer(
            layer.get(
                "children",
                []
            )
        )

        if selected is not None:
            return selected

    return None


def _blender_image_to_top_left_rgba8(
    blender_image
):
    """
    Read a Blender Image and return straight RGBA8 bytes in top-left row order
    for GIMP/GEGL.
    """

    width = int(
        blender_image.size[0]
    )

    height = int(
        blender_image.size[1]
    )

    if width <= 0 or height <= 0:
        raise RuntimeError(
            "Blender Image has invalid dimensions"
        )

    total_values = (
        width
        * height
        * 4
    )

    if np is not None:

        pixels = np.empty(
            total_values,
            dtype=np.float32
        )

        blender_image.pixels.foreach_get(
            pixels
        )

        rgba = pixels.reshape(
            (
                height,
                width,
                4,
            )
        )

        # Blender Image rows -> GIMP top-left rows.
        rgba = np.flip(
            rgba,
            axis=0
        )

        rgba_u8 = np.clip(
            np.rint(
                rgba
                * 255.0
            ),
            0,
            255
        ).astype(
            np.uint8
        )

        return (
            width,
            height,
            rgba_u8.tobytes()
        )

    from array import array

    pixels = array(
        "f",
        [0.0]
        * total_values
    )

    blender_image.pixels.foreach_get(
        pixels
    )

    row_values = (
        width
        * 4
    )

    result = bytearray(
        width
        * height
        * 4
    )

    destination = 0

    for blender_y in range(
        height - 1,
        -1,
        -1
    ):

        start = (
            blender_y
            * row_values
        )

        end = (
            start
            + row_values
        )

        for value in pixels[
            start:
            end
        ]:

            converted = int(
                round(
                    max(
                        0.0,
                        min(
                            1.0,
                            float(
                                value
                            )
                        )
                    )
                    * 255.0
                )
            )

            result[
                destination
            ] = converted

            destination += 1

    return (
        width,
        height,
        bytes(
            result
        )
    )


def _blendgimp_rgba_dirty_bbox(
    previous_pixels,
    current_pixels,
    width,
    height
):
    if (
        previous_pixels is None
        or len(
            previous_pixels
        ) != len(
            current_pixels
        )
    ):
        return (
            0,
            0,
            int(
                width
            ),
            int(
                height
            ),
        )

    if previous_pixels == current_pixels:
        return None

    if np is not None:

        previous = np.frombuffer(
            previous_pixels,
            dtype=np.uint8
        ).reshape(
            (
                height,
                width,
                4,
            )
        )

        current = np.frombuffer(
            current_pixels,
            dtype=np.uint8
        ).reshape(
            (
                height,
                width,
                4,
            )
        )

        changed = np.any(
            previous
            != current,
            axis=2
        )

        positions = np.argwhere(
            changed
        )

        if positions.size == 0:
            return None

        min_y, min_x = positions.min(
            axis=0
        )

        max_y, max_x = positions.max(
            axis=0
        )

        return (
            int(
                min_x
            ),
            int(
                min_y
            ),
            int(
                max_x - min_x + 1
            ),
            int(
                max_y - min_y + 1
            ),
        )

    row_bytes = (
        width
        * 4
    )

    min_x = width
    min_y = height
    max_x = -1
    max_y = -1

    previous = memoryview(
        previous_pixels
    )

    current = memoryview(
        current_pixels
    )

    for y in range(
        height
    ):
        start = (
            y
            * row_bytes
        )

        end = (
            start
            + row_bytes
        )

        old_row = previous[
            start:
            end
        ]

        new_row = current[
            start:
            end
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

        for x in range(
            width
        ):
            pixel = (
                x
                * 4
            )

            if (
                old_row[
                    pixel:
                    pixel + 4
                ]
                !=
                new_row[
                    pixel:
                    pixel + 4
                ]
            ):
                min_x = min(
                    min_x,
                    x
                )
                break

        for x in range(
            width - 1,
            -1,
            -1
        ):
            pixel = (
                x
                * 4
            )

            if (
                old_row[
                    pixel:
                    pixel + 4
                ]
                !=
                new_row[
                    pixel:
                    pixel + 4
                ]
            ):
                max_x = max(
                    max_x,
                    x
                )
                break

    if (
        max_x < min_x
        or max_y < min_y
    ):
        return None

    return (
        int(
            min_x
        ),
        int(
            min_y
        ),
        int(
            max_x - min_x + 1
        ),
        int(
            max_y - min_y + 1
        ),
    )


def _blendgimp_extract_top_left_rgba_region(
    raw_pixels,
    image_width,
    x,
    y,
    width,
    height
):
    image_row_bytes = (
        image_width
        * 4
    )

    region_row_bytes = (
        width
        * 4
    )

    result = bytearray(
        region_row_bytes
        * height
    )

    for row in range(
        height
    ):

        source_start = (
            (
                y
                + row
            )
            * image_row_bytes
            + x
            * 4
        )

        source_end = (
            source_start
            + region_row_bytes
        )

        destination_start = (
            row
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


def update_blender_paint_sync_baseline(
    image_id,
    blender_image=None
):
    """
    Accept the current Blender image as the reverse-sync baseline.

    Called after GIMP-originated updates so those pixels are never pushed back
    to GIMP as Blender-originated paint.
    """

    scene = getattr(
        bpy.context,
        "scene",
        None
    )

    if scene is None:
        return

    if not hasattr(
        scene,
        "blendgimp_blender_paint_sync_enabled"
    ):
        return

    if not scene.blendgimp_blender_paint_sync_enabled:
        return

    if int(
        scene.blendgimp_blender_paint_sync_image_id
    ) != int(
        image_id
    ):
        return

    try:

        if blender_image is None:

            sync_result = (
                get_texture_sync_result(
                    scene,
                    image_id
                )
                or {}
            )

            blender_image = _find_blendgimp_image(
                image_id,
                sync_result.get(
                    "sync_token",
                    ""
                )
            )

        if blender_image is None:
            return

        width, height, raw_pixels = (
            _blender_image_to_top_left_rgba8(
                blender_image
            )
        )

        _BLENDER_PAINT_SYNC_RUNTIME[
            "image_id"
        ] = int(
            image_id
        )

        _BLENDER_PAINT_SYNC_RUNTIME[
            "baseline"
        ] = raw_pixels

        _BLENDER_PAINT_SYNC_RUNTIME[
            "pending_pixels"
        ] = None

        _BLENDER_PAINT_SYNC_RUNTIME[
            "pending_bbox"
        ] = None

        _BLENDER_PAINT_SYNC_RUNTIME[
            "pending_since"
        ] = 0.0

    except Exception as exc:

        print(
            "BLENDGIMP: "
            f"Could not update Blender paint baseline: {exc}"
        )


def _find_blendgimp_image(
    image_id,
    sync_token
):
    """
    Find the persistent Blender Image associated with a GIMP runtime image.

    A non-empty synchronization token is authoritative. GIMP runtime image IDs
    are process-local and can be reused after a headless engine restart, while
    Blender Image datablocks survive that restart. Falling back to a matching
    runtime ID when the incoming token is different can therefore bind a new
    GIMP image to a stale Blender image with an incompatible pixel buffer.

    Runtime-ID fallback is retained only for legacy responses that do not carry
    a synchronization token.
    """

    image_id = int(image_id)
    sync_token = str(
        sync_token or ""
    ).strip()

    if sync_token:
        for candidate in bpy.data.images:
            candidate_token = str(
                candidate.get(
                    "blendgimp_sync_token",
                    ""
                )
            ).strip()

            if candidate_token == sync_token:
                return candidate

        # A token was supplied but no token match exists. Do not reuse a
        # datablock solely because its old process-local GIMP ID happens to
        # equal the current process-local ID. Log the collision for diagnostics.
        for candidate in bpy.data.images:
            try:
                candidate_id = int(
                    candidate.get(
                        "blendgimp_gimp_image_id",
                        -1
                    )
                )
            except Exception:
                candidate_id = -1

            if candidate_id == image_id:
                candidate_token = str(
                    candidate.get(
                        "blendgimp_sync_token",
                        ""
                    )
                ).strip()
                print(
                    "BLENDGIMP: Ignoring stale Blender Image pairing "
                    f"for reused GIMP runtime image ID {image_id}; "
                    f"incoming token={sync_token[:12]} "
                    f"stored token={candidate_token[:12] or 'legacy'}"
                )
                break

        return None

    # Legacy compatibility path: only token-less protocol responses may use
    # process-local image IDs for pairing.
    for candidate in bpy.data.images:
        try:
            candidate_id = int(
                candidate.get(
                    "blendgimp_gimp_image_id",
                    -1
                )
            )
        except Exception:
            candidate_id = -1

        if candidate_id == image_id:
            return candidate

    return None


def get_or_update_blender_image_from_pixels(
    pixel_response
):
    """
    Create/update the persistent Blender Image directly from GIMP RGBA8 bytes.

    GIMP sends rows with a top-left origin. Blender's pixel buffer is uploaded
    bottom row first, so rows are vertically flipped during conversion.
    """

    image_id = int(
        pixel_response["image_id"]
    )

    width = int(
        pixel_response["width"]
    )

    height = int(
        pixel_response["height"]
    )

    sync_token = str(
        pixel_response.get(
            "sync_token",
            ""
        )
    ).strip()

    if not sync_token:
        sync_token = f"image-{image_id:04d}"

    source_name = str(
        pixel_response.get(
            "image_name",
            ""
        )
    )

    desired_name = (
        "BlendGimp::Temp-"
        + sync_token[:12].upper()
    )

    raw_pixels = pixel_response.get(
        "pixels_raw",
        None
    )

    if raw_pixels is not None:

        if not isinstance(
            raw_pixels,
            bytes
        ):
            raw_pixels = bytes(
                raw_pixels
            )

    else:

        encoded = str(
            pixel_response.get(
                "pixels_b64",
                ""
            )
        )

        if not encoded:
            raise RuntimeError(
                "Direct RGBA response contains no pixel payload"
            )

        try:
            raw_pixels = base64.b64decode(
                encoded,
                validate=True
            )
        except Exception as exc:
            raise RuntimeError(
                f"Could not decode direct RGBA payload: {exc}"
            )

    expected_length = width * height * 4

    if len(raw_pixels) != expected_length:
        raise RuntimeError(
            "Direct RGBA byte count mismatch. "
            f"Expected {expected_length}, got {len(raw_pixels)}"
        )

    blender_image = _find_blendgimp_image(
        image_id,
        sync_token
    )

    if blender_image is not None:
        blender_owned_name = str(
            blender_image.get(
                "blendgimp_texture_name",
                ""
            )
        ).strip()

        if blender_owned_name:
            desired_name = blender_owned_name

    if blender_image is None:
        blender_image = bpy.data.images.new(
            name=desired_name,
            width=width,
            height=height,
            alpha=True,
            float_buffer=False,
        )
    else:
        current_width = int(
            blender_image.size[0]
        )
        current_height = int(
            blender_image.size[1]
        )

        if (
            current_width != width
            or current_height != height
        ):
            blender_image.scale(
                width,
                height
            )

    blender_image.name = desired_name

    try:
        blender_image.colorspace_settings.name = "sRGB"
    except Exception:
        pass

    try:
        blender_image.alpha_mode = "STRAIGHT"
    except Exception:
        pass

    expected_components = width * height * 4
    actual_components = len(
        blender_image.pixels
    )
    actual_channels = int(
        getattr(
            blender_image,
            "channels",
            0
        )
    )

    if actual_components != expected_components:
        raise RuntimeError(
            "Blender Image pixel buffer mismatch. "
            f"Image={blender_image.name!r} "
            f"size={width}x{height} channels={actual_channels}; "
            f"expected {expected_components} RGBA float components, "
            f"got {actual_components}. "
            "This usually indicates a stale runtime-image pairing."
        )

    if np is not None:
        rgba_u8 = np.frombuffer(
            raw_pixels,
            dtype=np.uint8
        ).reshape(
            (height, width, 4)
        )

        # GIMP / GEGL coordinates are top-left; Blender upload order is
        # bottom-row first.
        rgba_u8 = np.flip(
            rgba_u8,
            axis=0
        )

        rgba_float = (
            rgba_u8.astype(np.float32)
            * (1.0 / 255.0)
        )

        blender_image.pixels.foreach_set(
            rgba_float.reshape(-1)
        )

    else:
        from array import array

        row_bytes = width * 4
        float_pixels = array("f")

        for y in range(
            height - 1,
            -1,
            -1
        ):
            row_start = y * row_bytes
            row = raw_pixels[
                row_start:
                row_start + row_bytes
            ]

            float_pixels.extend(
                (
                    value / 255.0
                    for value in row
                )
            )

        blender_image.pixels.foreach_set(
            float_pixels
        )

    blender_image[
        "blendgimp_gimp_image_id"
    ] = image_id

    blender_image[
        "blendgimp_sync_token"
    ] = sync_token

    blender_image[
        "blendgimp_source_name"
    ] = source_name

    blender_image[
        "blendgimp_transport"
    ] = str(
        pixel_response.get(
            "transport",
            "direct-rgba-json"
        )
    )

    blender_image[
        "blendgimp_pixel_sha256"
    ] = str(
        pixel_response.get(
            "sha256",
            ""
        )
    )

    blender_image[
        "blendgimp_cache_path"
    ] = ""

    blender_image.update()

    return blender_image


def apply_blender_image_dirty_pixels(
    dirty_response
):
    """
    Apply a GIMP top-left-origin RGBA8 dirty rectangle directly to the
    existing Blender Image without replacing the image datablock.
    """

    image_id = int(
        dirty_response[
            "image_id"
        ]
    )

    image_width = int(
        dirty_response[
            "width"
        ]
    )

    image_height = int(
        dirty_response[
            "height"
        ]
    )

    sync_token = str(
        dirty_response.get(
            "sync_token",
            ""
        )
    ).strip()

    blender_image = _find_blendgimp_image(
        image_id,
        sync_token
    )

    if blender_image is None:
        raise RuntimeError(
            "No existing Blender Image is available for a dirty update"
        )

    if (
        int(
            blender_image.size[0]
        ) != image_width
        or int(
            blender_image.size[1]
        ) != image_height
    ):
        raise RuntimeError(
            "Blender Image dimensions changed; a full refresh is required"
        )

    if not dirty_response.get(
        "changed",
        False
    ):
        return blender_image

    x = int(
        dirty_response[
            "x"
        ]
    )

    y = int(
        dirty_response[
            "y"
        ]
    )

    region_width = int(
        dirty_response[
            "region_width"
        ]
    )

    region_height = int(
        dirty_response[
            "region_height"
        ]
    )

    if (
        x < 0
        or y < 0
        or region_width <= 0
        or region_height <= 0
        or x + region_width > image_width
        or y + region_height > image_height
    ):
        raise RuntimeError(
            "Dirty rectangle falls outside the Blender Image"
        )

    raw_pixels = dirty_response.get(
        "pixels_raw",
        None
    )

    if raw_pixels is not None:

        if not isinstance(
            raw_pixels,
            bytes
        ):
            raw_pixels = bytes(
                raw_pixels
            )

    else:

        encoded = str(
            dirty_response.get(
                "pixels_b64",
                ""
            )
        )

        try:
            raw_pixels = base64.b64decode(
                encoded,
                validate=True
            )
        except Exception as exc:
            raise RuntimeError(
                f"Could not decode dirty RGBA payload: {exc}"
            )

    expected_length = (
        region_width
        * region_height
        * 4
    )

    if len(
        raw_pixels
    ) != expected_length:
        raise RuntimeError(
            "Dirty RGBA byte count mismatch. "
            f"Expected {expected_length}, got {len(raw_pixels)}"
        )

    if np is not None:
        region_u8 = np.frombuffer(
            raw_pixels,
            dtype=np.uint8
        ).reshape(
            (
                region_height,
                region_width,
                4,
            )
        )

        region_float = (
            region_u8.astype(
                np.float32
            )
            * (
                1.0
                / 255.0
            )
        )

        for source_row in range(
            region_height
        ):
            gimp_y = (
                y
                + source_row
            )

            blender_y = (
                image_height
                - 1
                - gimp_y
            )

            pixel_start = (
                (
                    blender_y
                    * image_width
                    + x
                )
                * 4
            )

            pixel_end = (
                pixel_start
                + region_width
                * 4
            )

            blender_image.pixels[
                pixel_start:
                pixel_end
            ] = region_float[
                source_row
            ].reshape(
                -1
            )

    else:
        row_bytes = (
            region_width
            * 4
        )

        for source_row in range(
            region_height
        ):
            source_start = (
                source_row
                * row_bytes
            )

            source_end = (
                source_start
                + row_bytes
            )

            row = raw_pixels[
                source_start:
                source_end
            ]

            row_float = [
                value / 255.0
                for value in row
            ]

            gimp_y = (
                y
                + source_row
            )

            blender_y = (
                image_height
                - 1
                - gimp_y
            )

            pixel_start = (
                (
                    blender_y
                    * image_width
                    + x
                )
                * 4
            )

            pixel_end = (
                pixel_start
                + region_width
                * 4
            )

            blender_image.pixels[
                pixel_start:
                pixel_end
            ] = row_float

    blender_image[
        "blendgimp_transport"
    ] = str(
        dirty_response.get(
            "transport",
            "dirty-rgba-json"
        )
    )

    blender_image[
        "blendgimp_last_dirty_x"
    ] = x

    blender_image[
        "blendgimp_last_dirty_y"
    ] = y

    blender_image[
        "blendgimp_last_dirty_width"
    ] = region_width

    blender_image[
        "blendgimp_last_dirty_height"
    ] = region_height

    blender_image.update()

    return blender_image


def get_or_reload_blender_image(
    export_response
):
    """
    Create or reload one stable temporary Blender Image datablock for a GIMP
    image.

    The temporary texture identity is generated by BlendGimp and does not
    depend on the GIMP document name. The document name is stored only as
    source metadata.
    """

    image_id = int(
        export_response["image_id"]
    )

    sync_token = str(
        export_response.get(
            "sync_token",
            ""
        )
    ).strip()

    if not sync_token:
        # Compatibility fallback for older GIMP-side builds. This is still
        # generated from runtime identity rather than the document filename.
        sync_token = (
            f"image-{image_id:04d}"
        )

    cache_path = os.path.abspath(
        str(
            export_response["path"]
        )
    )

    if not os.path.isfile(
        cache_path
    ):
        raise FileNotFoundError(
            f"GIMP composite cache file does not exist: {cache_path}"
        )

    source_name = str(
        export_response.get(
            "image_name",
            ""
        )
    )

    desired_name = (
        "BlendGimp::Temp-"
        + sync_token[:12].upper()
    )

    blender_image = None

    # Prefer an exact sync-token match.
    for candidate in bpy.data.images:

        candidate_token = str(
            candidate.get(
                "blendgimp_sync_token",
                ""
            )
        )

        if (
            candidate_token
            and candidate_token == sync_token
        ):
            blender_image = candidate
            break

    # Migration / reconnect fallback: reuse an existing BlendGimp image that
    # was mapped to this GIMP runtime image ID, then update it to the new token.
    if blender_image is None:

        for candidate in bpy.data.images:

            try:
                candidate_id = int(
                    candidate.get(
                        "blendgimp_gimp_image_id",
                        -1
                    )
                )
            except Exception:
                candidate_id = -1

            if candidate_id == image_id:
                blender_image = candidate
                break

    if blender_image is None:

        blender_image = bpy.data.images.load(
            cache_path,
            check_existing=False
        )

    else:

        blender_image.filepath_raw = (
            cache_path
        )

        blender_image.reload()

    # Always migrate old document-name-derived image names such as
    # "BlendGimp::Demo.xcf" to BlendGimp's generated temporary name.
    blender_image.name = desired_name

    blender_image.filepath_raw = (
        cache_path
    )

    # The exported visible composite is display/color texture data for this
    # first Stage-1 path, so prefer sRGB when available.
    try:
        blender_image.colorspace_settings.name = (
            "sRGB"
        )
    except Exception:
        pass

    blender_image[
        "blendgimp_gimp_image_id"
    ] = image_id

    blender_image[
        "blendgimp_sync_token"
    ] = sync_token

    blender_image[
        "blendgimp_cache_path"
    ] = cache_path

    # Keep the real GIMP document name only as informational metadata.
    blender_image[
        "blendgimp_source_name"
    ] = source_name

    blender_image.update()

    return blender_image


def assign_blendgimp_image_to_active_material(
    context,
    blender_image,
    image_id
):
    """
    Assign the synchronized Blender Image to the active object's active
    material and connect it to Principled BSDF Base Color.

    If the object has no material, create a normal node-based material.
    Existing non-Principled custom materials are left structurally intact.
    """

    obj = context.active_object

    if obj is None:
        return {
            "assigned": False,
            "material": "",
            "reason": "No active Blender object",
        }

    if (
        getattr(
            obj,
            "data",
            None
        ) is None
        or not hasattr(
            obj.data,
            "materials"
        )
    ):
        return {
            "assigned": False,
            "material": "",
            "reason": "Active object does not support materials",
        }

    material = obj.active_material
    created_material = False

    if material is None:

        material = bpy.data.materials.new(
            name="BlendGimp Material"
        )

        material.use_nodes = True
        created_material = True

        if len(obj.data.materials) == 0:
            obj.data.materials.append(
                material
            )
        else:
            obj.active_material = material

    material.use_nodes = True

    node_tree = material.node_tree
    nodes = node_tree.nodes
    links = node_tree.links

    principled = next(
        (
            node
            for node in nodes
            if node.type == "BSDF_PRINCIPLED"
        ),
        None
    )

    if principled is None:

        if created_material:
            principled = nodes.new(
                "ShaderNodeBsdfPrincipled"
            )
        else:
            return {
                "assigned": False,
                "material": material.name,
                "reason": (
                    "Active material has no Principled BSDF; "
                    "image was refreshed but shader was not rewired"
                ),
            }

    texture_node = None

    for node in nodes:

        if node.type != "TEX_IMAGE":
            continue

        try:
            node_image_id = int(
                node.get(
                    "blendgimp_gimp_image_id",
                    -1
                )
            )
        except Exception:
            node_image_id = -1

        if node_image_id == int(image_id):
            texture_node = node
            break

    if texture_node is None:

        texture_node = nodes.new(
            "ShaderNodeTexImage"
        )

        texture_node.label = (
            "BlendGimp Composite"
        )

        texture_node.name = (
            f"BlendGimp Composite {image_id}"
        )

        texture_node.location = (
            principled.location.x - 360.0,
            principled.location.y + 160.0,
        )

    texture_node[
        "blendgimp_gimp_image_id"
    ] = int(image_id)

    texture_node.image = blender_image

    base_color_input = principled.inputs.get(
        "Base Color"
    )

    if base_color_input is None:
        return {
            "assigned": False,
            "material": material.name,
            "reason": "Principled BSDF has no Base Color input",
        }

    for link in list(
        base_color_input.links
    ):
        links.remove(
            link
        )

    links.new(
        texture_node.outputs["Color"],
        base_color_input
    )

    material[
        "blendgimp_gimp_image_id"
    ] = int(image_id)

    return {
        "assigned": True,
        "material": material.name,
        "reason": "",
    }


def tag_texture_views_for_redraw(
    context
):

    screen = getattr(
        context,
        "screen",
        None
    )

    if screen is not None:
        for area in screen.areas:
            if area.type in {
                "VIEW_3D",
                "IMAGE_EDITOR",
                "NODE_EDITOR",
            }:
                area.tag_redraw()

    try:
        context.view_layer.update()
    except Exception:
        pass


def synchronize_gimp_composite(
    context,
    image_id,
    assign_material=True,
    dirty_only=False
):
    """
    Synchronize GIMP's visible composite into one persistent Blender Image.

    Preferred automatic transport:
        dirty rectangle + raw binary RGBA

    Fallback chain:
        dirty binary
        -> dirty base64 JSON
        -> full binary
        -> full base64 JSON
        -> PNG export
    """

    scene = context.scene
    image_id = int(
        image_id
    )

    print(
        "BLENDGIMP: "
        f"Refreshing GIMP image ID {image_id} into Blender"
    )

    transport_response = None
    transport = ""
    direct_errors = []
    blender_image = None

    # --------------------------------------------------------
    # Automatic dirty-region path
    # --------------------------------------------------------

    if dirty_only:

        dirty_response = None

        try:

            dirty_response = (
                connection_manager.get_image_dirty_pixels_binary(
                    image_id
                )
            )

        except Exception as exc:

            direct_errors.append(
                "Dirty binary: "
                + str(
                    exc
                )
            )

            print(
                "BLENDGIMP: "
                f"Dirty binary unavailable: {exc}; "
                "trying dirty base64"
            )

            if connection_manager.is_connected():

                try:

                    dirty_response = (
                        connection_manager.get_image_dirty_pixels(
                            image_id
                        )
                    )

                except Exception as fallback_exc:

                    direct_errors.append(
                        "Dirty base64: "
                        + str(
                            fallback_exc
                        )
                    )

                    print(
                        "BLENDGIMP: "
                        f"Dirty base64 unavailable: {fallback_exc}; "
                        "trying full direct transfer"
                    )

        if dirty_response is not None:

            if dirty_response.get(
                "changed",
                False
            ):

                blender_image = (
                    apply_blender_image_dirty_pixels(
                        dirty_response
                    )
                )

                transport = str(
                    dirty_response.get(
                        "transport",
                        "dirty-rgba-json"
                    )
                )

                print(
                    "BLENDGIMP: "
                    "Dirty RGBA received: "
                    f"x={dirty_response.get('x')} "
                    f"y={dirty_response.get('y')} "
                    f"{dirty_response.get('region_width')}x"
                    f"{dirty_response.get('region_height')} "
                    f"{dirty_response.get('byte_length', 0)} bytes "
                    f"via {transport}"
                )

            else:

                blender_image = _find_blendgimp_image(
                    image_id,
                    dirty_response.get(
                        "sync_token",
                        ""
                    )
                )

                if blender_image is None:
                    direct_errors.append(
                        "Dirty response had no changes but no existing "
                        "Blender Image was available"
                    )
                else:
                    transport = str(
                        dirty_response.get(
                            "transport",
                            "dirty-rgba-json"
                        )
                    )

                    print(
                        "BLENDGIMP: "
                        "Dirty RGBA check returned no pixel delta "
                        f"via {transport}"
                    )

            transport_response = (
                dirty_response
            )

    # --------------------------------------------------------
    # Full direct path
    # --------------------------------------------------------

    if blender_image is None:

        pixel_response = None

        try:

            pixel_response = (
                connection_manager.get_image_pixels_binary(
                    image_id
                )
            )

        except Exception as exc:

            direct_errors.append(
                "Full binary: "
                + str(
                    exc
                )
            )

            print(
                "BLENDGIMP: "
                f"Full binary unavailable: {exc}; "
                "trying full base64"
            )

            if connection_manager.is_connected():

                try:

                    pixel_response = (
                        connection_manager.get_image_pixels(
                            image_id
                        )
                    )

                except Exception as fallback_exc:

                    direct_errors.append(
                        "Full base64: "
                        + str(
                            fallback_exc
                        )
                    )

                    print(
                        "BLENDGIMP: "
                        f"Full base64 unavailable: {fallback_exc}; "
                        "using PNG fallback"
                    )

        if pixel_response is not None:

            blender_image = (
                get_or_update_blender_image_from_pixels(
                    pixel_response
                )
            )

            transport_response = (
                pixel_response
            )

            transport = str(
                pixel_response.get(
                    "transport",
                    "direct-rgba-json"
                )
            )

            print(
                "BLENDGIMP: "
                f"Direct RGBA received: "
                f"{pixel_response.get('byte_length', 0)} bytes "
                f"for {pixel_response.get('width')}x"
                f"{pixel_response.get('height')} "
                f"via {transport}"
            )

    # --------------------------------------------------------
    # Last-resort proven PNG path
    # --------------------------------------------------------

    if blender_image is None:

        if not connection_manager.is_connected():
            raise RuntimeError(
                "BlendGimp disconnected before PNG fallback. "
                + " | ".join(
                    direct_errors
                )
            )

        export_response = (
            connection_manager.export_composite(
                image_id
            )
        )

        print(
            "BLENDGIMP: "
            f"Composite exported to {export_response.get('path')}"
        )

        blender_image = (
            get_or_reload_blender_image(
                export_response
            )
        )

        transport_response = (
            export_response
        )

        transport = (
            "png-fallback"
        )

    # --------------------------------------------------------
    # Material mapping
    # --------------------------------------------------------

    if assign_material:

        assignment = (
            assign_blendgimp_image_to_active_material(
                context,
                blender_image,
                image_id
            )
        )

    else:

        previous = (
            get_texture_sync_result(
                scene,
                image_id
            )
            or {}
        )

        assignment = {
            "material": str(
                previous.get(
                    "material",
                    ""
                )
            ),
            "assigned": bool(
                previous.get(
                    "assigned",
                    False
                )
            ),
            "reason": str(
                previous.get(
                    "reason",
                    ""
                )
            ),
        }

    sync_result = {
        "blender_image": blender_image.name,
        "sync_token": str(
            transport_response.get(
                "sync_token",
                ""
            )
        ),
        "source_name": str(
            transport_response.get(
                "image_name",
                ""
            )
        ),
        "transport": transport,
        "direct_error": " | ".join(
            direct_errors
        ),
        "cache_path": str(
            transport_response.get(
                "path",
                ""
            )
        ),
        "width": int(
            transport_response.get(
                "width",
                blender_image.size[0]
            )
        ),
        "height": int(
            transport_response.get(
                "height",
                blender_image.size[1]
            )
        ),
        "byte_length": int(
            transport_response.get(
                "byte_length",
                0
            )
        ),
        "full_byte_length": int(
            transport_response.get(
                "full_byte_length",
                0
            )
        ),
        "saved_bytes": int(
            transport_response.get(
                "saved_bytes",
                0
            )
        ),
        "dirty_x": int(
            transport_response.get(
                "x",
                0
            )
        ),
        "dirty_y": int(
            transport_response.get(
                "y",
                0
            )
        ),
        "dirty_width": int(
            transport_response.get(
                "region_width",
                0
            )
        ),
        "dirty_height": int(
            transport_response.get(
                "region_height",
                0
            )
        ),
        "pixel_sha256": str(
            transport_response.get(
                "sha256",
                ""
            )
        ),
        "material": assignment.get(
            "material",
            ""
        ),
        "assigned": bool(
            assignment.get(
                "assigned",
                False
            )
        ),
        "reason": str(
            assignment.get(
                "reason",
                ""
            )
        ),
        "mtime_ns": int(
            transport_response.get(
                "mtime_ns",
                0
            )
        ),
    }

    store_texture_sync_result(
        scene,
        image_id,
        sync_result
    )

    tag_texture_views_for_redraw(
        context
    )

    scene.blendgimp_connected = True

    print(
        "BLENDGIMP: "
        f"Blender image refreshed = {blender_image.name} "
        f"via {transport}"
    )

    if (
        assign_material
        and assignment.get(
            "assigned",
            False
        )
    ):
        print(
            "BLENDGIMP: "
            f"Assigned to material = {assignment.get('material')}"
        )

    update_blender_paint_sync_baseline(
        image_id,
        blender_image
    )

    return {
        "export_response": transport_response,
        "transport_response": transport_response,
        "transport": transport,
        "blender_image": blender_image,
        "assignment": assignment,
        "sync_result": sync_result,
    }


def blendgimp_blender_paint_sync_timer():
    """
    Blender main-thread timer for Blender -> GIMP Texture Paint.

    While the active object is in Texture Paint mode, compare the synchronized
    Blender Image against the last accepted baseline. After a short debounce,
    send only the changed bounding rectangle to the dedicated BlendGimp Paint
    layer using raw binary RGBA.
    """

    if not hasattr(
        bpy.types.Scene,
        "blendgimp_blender_paint_sync_enabled"
    ):
        return None

    scene = getattr(
        bpy.context,
        "scene",
        None
    )

    if scene is None:
        return BLENDER_PAINT_SYNC_POLL_INTERVAL

    if not scene.blendgimp_blender_paint_sync_enabled:
        return BLENDER_PAINT_SYNC_POLL_INTERVAL

    image_id = int(
        scene.blendgimp_blender_paint_sync_image_id
    )

    if image_id < 0:
        scene.blendgimp_blender_paint_sync_status = (
            "No GIMP image selected"
        )
        return BLENDER_PAINT_SYNC_POLL_INTERVAL

    if not connection_manager.is_connected():
        scene.blendgimp_connected = False
        scene.blendgimp_blender_paint_sync_status = (
            "Waiting for GIMP connection"
        )
        return BLENDER_PAINT_SYNC_POLL_INTERVAL

    active_object = getattr(
        bpy.context,
        "active_object",
        None
    )

    if (
        active_object is None
        or str(
            active_object.mode
        ) != "TEXTURE_PAINT"
    ):
        scene.blendgimp_blender_paint_sync_status = (
            "Waiting for Texture Paint mode"
        )
        return BLENDER_PAINT_SYNC_POLL_INTERVAL

    sync_result = (
        get_texture_sync_result(
            scene,
            image_id
        )
        or {}
    )

    blender_image = _find_blendgimp_image(
        image_id,
        sync_result.get(
            "sync_token",
            ""
        )
    )

    if blender_image is None:
        scene.blendgimp_blender_paint_sync_status = (
            "Refresh From GIMP first"
        )
        return BLENDER_PAINT_SYNC_POLL_INTERVAL

    try:

        width, height, current_pixels = (
            _blender_image_to_top_left_rgba8(
                blender_image
            )
        )

    except Exception as exc:

        scene.blendgimp_blender_paint_sync_status = (
            f"Pixel read failed: {exc}"
        )

        return 1.0

    if (
        _BLENDER_PAINT_SYNC_RUNTIME.get(
            "image_id"
        )
        != image_id
        or _BLENDER_PAINT_SYNC_RUNTIME.get(
            "baseline"
        )
        is None
    ):

        reset_blender_paint_sync_runtime(
            image_id=image_id,
            layer_id=int(
                scene.blendgimp_blender_paint_sync_layer_id
            ),
            baseline=current_pixels
        )

        scene.blendgimp_blender_paint_sync_status = (
            "Watching Blender texture"
        )

        return BLENDER_PAINT_SYNC_POLL_INTERVAL

    baseline = _BLENDER_PAINT_SYNC_RUNTIME.get(
        "baseline"
    )

    bbox = _blendgimp_rgba_dirty_bbox(
        baseline,
        current_pixels,
        width,
        height
    )

    now = time.monotonic()

    pending_pixels = _BLENDER_PAINT_SYNC_RUNTIME.get(
        "pending_pixels"
    )

    pending_bbox = _BLENDER_PAINT_SYNC_RUNTIME.get(
        "pending_bbox"
    )

    # If the image has returned to the accepted baseline, any pending paint
    # change was undone/cancelled before it was transmitted.
    if bbox is None:

        if (
            pending_pixels is not None
            or pending_bbox is not None
        ):
            _BLENDER_PAINT_SYNC_RUNTIME[
                "pending_pixels"
            ] = None

            _BLENDER_PAINT_SYNC_RUNTIME[
                "pending_bbox"
            ] = None

            _BLENDER_PAINT_SYNC_RUNTIME[
                "pending_since"
            ] = 0.0

        scene.blendgimp_blender_paint_sync_status = (
            "Watching Blender texture"
        )

        return BLENDER_PAINT_SYNC_POLL_INTERVAL

    # Start the debounce window on the first observed Blender-side change.
    if (
        pending_pixels is None
        or pending_bbox is None
    ):

        _BLENDER_PAINT_SYNC_RUNTIME[
            "pending_pixels"
        ] = current_pixels

        _BLENDER_PAINT_SYNC_RUNTIME[
            "pending_bbox"
        ] = bbox

        _BLENDER_PAINT_SYNC_RUNTIME[
            "pending_since"
        ] = now

        scene.blendgimp_blender_paint_sync_status = (
            "Blender paint change detected"
        )

        return BLENDER_PAINT_SYNC_POLL_INTERVAL

    # Only restart the debounce timer if Blender's pixels actually changed
    # again. The previous implementation reset this timestamp every poll,
    # which meant the debounce could never expire.
    if pending_pixels != current_pixels:

        _BLENDER_PAINT_SYNC_RUNTIME[
            "pending_pixels"
        ] = current_pixels

        _BLENDER_PAINT_SYNC_RUNTIME[
            "pending_bbox"
        ] = bbox

        _BLENDER_PAINT_SYNC_RUNTIME[
            "pending_since"
        ] = now

        scene.blendgimp_blender_paint_sync_status = (
            "Blender paint changing"
        )

        return BLENDER_PAINT_SYNC_POLL_INTERVAL

    # Pixel content is stable. Let the debounce window expire and then push
    # the already-captured dirty rectangle.
    pending_pixels = _BLENDER_PAINT_SYNC_RUNTIME[
        "pending_pixels"
    ]

    pending_bbox = _BLENDER_PAINT_SYNC_RUNTIME[
        "pending_bbox"
    ]

    debounce = max(
        0.1,
        float(
            scene.blendgimp_blender_paint_sync_debounce
        )
    )

    elapsed = (
        now
        - float(
            _BLENDER_PAINT_SYNC_RUNTIME.get(
                "pending_since",
                now
            )
        )
    )

    if elapsed < debounce:

        scene.blendgimp_blender_paint_sync_status = (
            "Debouncing Blender paint"
        )

        return BLENDER_PAINT_SYNC_POLL_INTERVAL

    (
        x,
        y,
        region_width,
        region_height,
    ) = pending_bbox

    region_pixels = (
        _blendgimp_extract_top_left_rgba_region(
            pending_pixels,
            width,
            x,
            y,
            region_width,
            region_height
        )
    )

    try:

        layer_id = int(
            scene.blendgimp_blender_paint_sync_layer_id
        )

        if layer_id < 0:

            layer_response = (
                connection_manager.ensure_paint_layer(
                    image_id,
                    BLENDER_PAINT_LAYER_NAME
                )
            )

            layer_id = int(
                layer_response[
                    "layer_id"
                ]
            )

            scene.blendgimp_blender_paint_sync_layer_id = (
                layer_id
            )

        try:

            response = (
                connection_manager.set_layer_pixels_binary(
                    image_id,
                    layer_id,
                    x,
                    y,
                    region_width,
                    region_height,
                    region_pixels
                )
            )

        except Exception as first_exc:

            error_text = str(
                first_exc
            )

            # GIMP runtime object IDs are session-local. If GIMP restarted or
            # the dedicated layer was deleted, resolve/create BlendGimp Paint
            # and retry this exact dirty rectangle immediately.
            if (
                "not a valid GIMP layer" not in error_text
                and "does not overlap" not in error_text
            ):
                raise

            print(
                "BLENDGIMP: "
                f"3D Paint Sync target layer became stale: {error_text}. "
                "Resolving BlendGimp Paint and retrying."
            )

            layer_response = (
                connection_manager.ensure_paint_layer(
                    image_id,
                    BLENDER_PAINT_LAYER_NAME
                )
            )

            layer_id = int(
                layer_response[
                    "layer_id"
                ]
            )

            scene.blendgimp_blender_paint_sync_layer_id = (
                layer_id
            )

            _BLENDER_PAINT_SYNC_RUNTIME[
                "layer_id"
            ] = layer_id

            response = (
                connection_manager.set_layer_pixels_binary(
                    image_id,
                    layer_id,
                    x,
                    y,
                    region_width,
                    region_height,
                    region_pixels
                )
            )

        _BLENDER_PAINT_SYNC_RUNTIME[
            "baseline"
        ] = pending_pixels

        _BLENDER_PAINT_SYNC_RUNTIME[
            "pending_pixels"
        ] = None

        _BLENDER_PAINT_SYNC_RUNTIME[
            "pending_bbox"
        ] = None

        _BLENDER_PAINT_SYNC_RUNTIME[
            "pending_since"
        ] = 0.0

        scene.blendgimp_blender_paint_sync_status = (
            f"Sent {region_width}x{region_height} region"
        )

        print(
            "BLENDGIMP: "
            "3D Paint Sync pushed "
            f"x={x} y={y} "
            f"{region_width}x{region_height} "
            f"{len(region_pixels)} raw RGBA bytes "
            f"to GIMP layer ID {layer_id}"
        )

    except Exception as exc:

        # If the paint layer was deleted/replaced in GIMP, resolve/create it
        # again on the next pass.
        scene.blendgimp_blender_paint_sync_layer_id = (
            -1
        )

        scene.blendgimp_blender_paint_sync_status = (
            f"Push failed: {exc}"
        )

        print(
            "BLENDGIMP: "
            f"3D Paint Sync failed: {exc}"
        )

        return 1.0

    return BLENDER_PAINT_SYNC_POLL_INTERVAL


def blendgimp_auto_sync_timer():
    """
    Blender main-thread timer.

    Poll only the lightweight GIMP visual revision. Once a revision remains
    stable for the configured debounce interval, export/reload the composite
    exactly once.
    """

    if not hasattr(
        bpy.types.Scene,
        "blendgimp_auto_sync_enabled"
    ):
        return None

    scene = getattr(
        bpy.context,
        "scene",
        None
    )

    if scene is None:
        return AUTO_SYNC_POLL_INTERVAL

    if not scene.blendgimp_auto_sync_enabled:
        return AUTO_SYNC_POLL_INTERVAL

    # Direct GIMP Brush 3D Paint owns GIMP->Blender refreshes while its
    # modal tool is active. Use shared module runtime rather than bpy.context
    # Scene state because Blender app timers may execute under another UI
    # context/window.
    if direct_paint_owns_refresh():
        return AUTO_SYNC_POLL_INTERVAL

    image_id = int(
        scene.blendgimp_auto_sync_image_id
    )

    if image_id < 0:
        scene.blendgimp_auto_sync_status = (
            "No GIMP image selected"
        )
        return AUTO_SYNC_POLL_INTERVAL

    if not connection_manager.is_connected():
        scene.blendgimp_connected = False
        scene.blendgimp_auto_sync_status = (
            "Waiting for GIMP connection"
        )
        return AUTO_SYNC_POLL_INTERVAL

    try:
        state = (
            connection_manager.get_image_state(
                image_id
            )
        )

    except GimpImageNotFoundError:
        scene.blendgimp_connected = (
            connection_manager.is_connected()
        )
        clear_gimp_session_bindings(
            scene,
            status=(
                "Previous GIMP image closed; "
                "waiting for a Blender-owned texture"
            )
        )
        print(
            "BLENDGIMP: "
            "Auto Sync released a stale GIMP image ID"
        )
        return AUTO_SYNC_POLL_INTERVAL

    except Exception as exc:
        scene.blendgimp_connected = (
            connection_manager.is_connected()
        )
        scene.blendgimp_auto_sync_status = (
            f"State check failed: {exc}"
        )
        return 1.0

    revision = int(
        state.get(
            "revision",
            0
        )
    )

    scene.blendgimp_auto_sync_revision = (
        revision
    )

    detector = str(
        state.get(
            "detector",
            ""
        )
    )

    if detector:
        if hasattr(
            scene,
            "blendgimp_auto_sync_detector"
        ):
            scene.blendgimp_auto_sync_detector = (
                detector
            )

    if (
        _AUTO_SYNC_RUNTIME.get(
            "image_id"
        )
        != image_id
    ):
        reset_auto_sync_runtime(
            image_id,
            revision
        )

        scene.blendgimp_auto_sync_status = (
            f"Watching revision {revision}"
        )

        return AUTO_SYNC_POLL_INTERVAL

    last_seen = (
        _AUTO_SYNC_RUNTIME.get(
            "last_seen_revision"
        )
    )

    now = time.monotonic()

    if last_seen is None:

        _AUTO_SYNC_RUNTIME[
            "last_seen_revision"
        ] = revision

        _AUTO_SYNC_RUNTIME[
            "last_synced_revision"
        ] = revision

        scene.blendgimp_auto_sync_status = (
            f"Watching revision {revision}"
        )

        return AUTO_SYNC_POLL_INTERVAL

    if revision != int(
        last_seen
    ):

        _AUTO_SYNC_RUNTIME[
            "last_seen_revision"
        ] = revision

        _AUTO_SYNC_RUNTIME[
            "pending_revision"
        ] = revision

        _AUTO_SYNC_RUNTIME[
            "pending_since"
        ] = now

        scene.blendgimp_auto_sync_status = (
            f"Change detected - revision {revision}"
        )

        return AUTO_SYNC_POLL_INTERVAL

    pending_revision = (
        _AUTO_SYNC_RUNTIME.get(
            "pending_revision"
        )
    )

    if pending_revision is None:
        scene.blendgimp_auto_sync_status = (
            f"Watching revision {revision}"
        )
        return AUTO_SYNC_POLL_INTERVAL

    debounce = max(
        0.1,
        float(
            scene.blendgimp_auto_sync_debounce
        )
    )

    elapsed = (
        now
        - float(
            _AUTO_SYNC_RUNTIME.get(
                "pending_since",
                now
            )
        )
    )

    if elapsed < debounce:
        scene.blendgimp_auto_sync_status = (
            f"Debouncing revision {pending_revision}"
        )
        return AUTO_SYNC_POLL_INTERVAL

    try:

        synchronize_gimp_composite(
            bpy.context,
            image_id,
            assign_material=False,
            dirty_only=True
        )

        _AUTO_SYNC_RUNTIME[
            "last_synced_revision"
        ] = int(
            pending_revision
        )

        _AUTO_SYNC_RUNTIME[
            "pending_revision"
        ] = None

        _AUTO_SYNC_RUNTIME[
            "pending_since"
        ] = 0.0

        scene.blendgimp_auto_sync_status = (
            f"Synced revision {pending_revision}"
        )

        print(
            "BLENDGIMP: "
            f"Auto Sync completed for image ID {image_id} "
            f"revision {pending_revision}"
        )

    except Exception as exc:

        scene.blendgimp_connected = (
            connection_manager.is_connected()
        )

        scene.blendgimp_auto_sync_status = (
            f"Auto Sync failed: {exc}"
        )

        # Keep the revision pending, but delay the next retry so a transient
        # failure does not hammer GIMP.
        _AUTO_SYNC_RUNTIME[
            "pending_since"
        ] = time.monotonic()

        return 1.0

    return AUTO_SYNC_POLL_INTERVAL


def refresh_layer_result(
    scene,
    image_id
):
    """
    Re-read GIMP's real layer state after a write command.
    """

    response = (
        connection_manager.get_image_layers(
            image_id
        )
    )

    store_layer_result(
        scene,
        image_id,
        response
    )

    return response


# Blender keeps dynamic EnumProperty strings by reference. Retain the
# generated tuples at module scope so the Move Layer dialog remains stable.
_MOVE_TARGET_ITEMS_CACHE = []


def _find_layer_in_tree(
    layers,
    layer_id
):

    for layer in layers:

        try:
            current_id = int(
                layer.get(
                    "id",
                    -1
                )
            )
        except Exception:
            current_id = -1

        if current_id == int(layer_id):
            return layer

        found = _find_layer_in_tree(
            layer.get(
                "children",
                []
            ),
            layer_id
        )

        if found is not None:
            return found

    return None


def _collect_descendant_ids(
    layer
):

    result = set()

    if layer is None:
        return result

    for child in layer.get(
        "children",
        []
    ):

        try:
            child_id = int(
                child.get(
                    "id",
                    -1
                )
            )
        except Exception:
            child_id = -1

        if child_id >= 0:
            result.add(
                child_id
            )

        result.update(
            _collect_descendant_ids(
                child
            )
        )

    return result


def _collect_group_targets(
    layers,
    excluded_ids,
    depth=0
):

    targets = []

    for layer in layers:

        try:
            layer_id = int(
                layer.get(
                    "id",
                    -1
                )
            )
        except Exception:
            layer_id = -1

        if layer_id < 0:
            continue

        is_group = bool(
            layer.get(
                "is_group",
                False
            )
        )

        if (
            is_group
            and layer_id not in excluded_ids
        ):

            targets.append(
                (
                    str(layer_id),
                    (
                        "    " * depth
                        + str(
                            layer.get(
                                "name",
                                "Layer Group"
                            )
                        )
                    ),
                    (
                        "Move the layer into "
                        + str(
                            layer.get(
                                "name",
                                "this group"
                            )
                        )
                    ),
                )
            )

        targets.extend(
            _collect_group_targets(
                layer.get(
                    "children",
                    []
                ),
                excluded_ids,
                depth + 1
            )
        )

    return targets


def blendgimp_move_target_items(
    self,
    context
):

    global _MOVE_TARGET_ITEMS_CACHE

    items = [
        (
            "-1",
            "Top Level",
            "Move the layer out of groups to the image's top-level layer stack",
        )
    ]

    scene = context.scene

    layer_result = (
        get_stored_layer_result(
            scene,
            self.image_id
        )
    )

    if layer_result is not None:

        layers = layer_result.get(
            "layers",
            []
        )

        moving_layer = (
            _find_layer_in_tree(
                layers,
                self.layer_id
            )
        )

        excluded_ids = {
            int(self.layer_id)
        }

        excluded_ids.update(
            _collect_descendant_ids(
                moving_layer
            )
        )

        items.extend(
            _collect_group_targets(
                layers,
                excluded_ids
            )
        )

    _MOVE_TARGET_ITEMS_CACHE = items

    return _MOVE_TARGET_ITEMS_CACHE


def draw_layer_tree(
    layout,
    layers,
    image_id,
    depth=0
):
    """
    Draw the GIMP layer tree with selection, visibility, opacity,
    rename, duplicate, delete, reorder, and group-move controls.
    """

    sibling_count = len(
        layers
    )

    for sibling_index, layer in enumerate(
        layers
    ):

        try:
            layer_id = int(
                layer.get(
                    "id",
                    -1
                )
            )
        except (
            TypeError,
            ValueError
        ):
            layer_id = -1

        if layer_id < 0:
            continue

        selected = bool(
            layer.get(
                "selected",
                False
            )
        )

        visible = bool(
            layer.get(
                "visible",
                False
            )
        )

        is_group = bool(
            layer.get(
                "is_group",
                False
            )
        )

        try:
            opacity = float(
                layer.get(
                    "opacity",
                    100.0
                )
            )
        except (
            TypeError,
            ValueError
        ):
            opacity = 100.0

        opacity = max(
            0.0,
            min(
                100.0,
                opacity
            )
        )

        mode_name = str(
            layer.get(
                "mode",
                "NORMAL"
            )
        )

        layer_box = layout.box()

        # ----------------------------------------------------
        # Main row: active / visible / opacity
        # ----------------------------------------------------

        row = layer_box.row(
            align=True
        )

        if depth > 0:
            row.label(
                text=(
                    "  " * depth
                )
            )

        active_operator = (
            row.operator(
                "blendgimp.set_active_layer",
                text=str(
                    layer.get(
                        "name",
                        "[Unnamed]"
                    )
                ),
                icon=(
                    "RADIOBUT_ON"
                    if selected
                    else
                    "RADIOBUT_OFF"
                )
            )
        )

        active_operator.image_id = (
            int(image_id)
        )

        active_operator.layer_id = (
            layer_id
        )

        visibility_operator = (
            row.operator(
                "blendgimp.set_layer_visibility",
                text="",
                icon=(
                    "HIDE_OFF"
                    if visible
                    else
                    "HIDE_ON"
                )
            )
        )

        visibility_operator.image_id = (
            int(image_id)
        )

        visibility_operator.layer_id = (
            layer_id
        )

        visibility_operator.visible = (
            not visible
        )

        opacity_operator = (
            row.operator(
                "blendgimp.set_layer_opacity",
                text=f"{opacity:.1f}%"
            )
        )

        opacity_operator.image_id = (
            int(image_id)
        )

        opacity_operator.layer_id = (
            layer_id
        )

        opacity_operator.opacity = (
            opacity
        )

        # ----------------------------------------------------
        # Blend mode
        # ----------------------------------------------------

        mode_row = layer_box.row(
            align=True
        )

        mode_row.label(
            text="Blend:"
        )

        mode_operator = (
            mode_row.operator(
                "blendgimp.set_layer_mode",
                text=blendgimp_layer_mode_label(
                    mode_name
                )
            )
        )

        mode_operator.image_id = (
            int(image_id)
        )

        mode_operator.layer_id = (
            layer_id
        )

        mode_operator.mode = (
            mode_name
            if any(
                item[0] == mode_name
                for item in BLENDGIMP_LAYER_MODE_ITEMS
            )
            else "NORMAL"
        )

        # ----------------------------------------------------
        # Editing controls
        # ----------------------------------------------------

        edit_row = layer_box.row(
            align=True
        )

        rename_operator = (
            edit_row.operator(
                "blendgimp.rename_layer",
                text="Rename"
            )
        )

        rename_operator.image_id = (
            int(image_id)
        )

        rename_operator.layer_id = (
            layer_id
        )

        rename_operator.layer_name = str(
            layer.get(
                "name",
                ""
            )
        )

        duplicate_operator = (
            edit_row.operator(
                "blendgimp.duplicate_layer",
                text="Duplicate"
            )
        )

        duplicate_operator.image_id = (
            int(image_id)
        )

        duplicate_operator.layer_id = (
            layer_id
        )

        delete_operator = (
            edit_row.operator(
                "blendgimp.delete_layer",
                text="Delete"
            )
        )

        delete_operator.image_id = (
            int(image_id)
        )

        delete_operator.layer_id = (
            layer_id
        )

        delete_operator.layer_name = str(
            layer.get(
                "name",
                ""
            )
        )

        if sibling_index < (
            sibling_count - 1
        ):

            merge_operator = (
                edit_row.operator(
                    "blendgimp.merge_layer_down",
                    text="Merge Down"
                )
            )

            merge_operator.image_id = (
                int(image_id)
            )

            merge_operator.layer_id = (
                layer_id
            )

            merge_operator.layer_name = str(
                layer.get(
                    "name",
                    ""
                )
            )

        # ----------------------------------------------------
        # Layer locks
        # ----------------------------------------------------

        lock_row = layer_box.row(
            align=True
        )

        lock_row.label(
            text="Locks:"
        )

        for (
            lock_type,
            label,
            field_name
        ) in (
            (
                "CONTENT",
                "Content",
                "lock_content"
            ),
            (
                "POSITION",
                "Position",
                "lock_position"
            ),
            (
                "ALPHA",
                "Alpha",
                "lock_alpha"
            ),
        ):

            current_locked = bool(
                layer.get(
                    field_name,
                    False
                )
            )

            lock_operator = (
                lock_row.operator(
                    "blendgimp.set_layer_lock",
                    text=(
                        f"{label} ON"
                        if current_locked
                        else label
                    ),
                    depress=current_locked
                )
            )

            lock_operator.image_id = (
                int(image_id)
            )

            lock_operator.layer_id = (
                layer_id
            )

            lock_operator.lock_type = (
                lock_type
            )

            lock_operator.locked = (
                not current_locked
            )

        # ----------------------------------------------------
        # Hierarchy / ordering controls
        # ----------------------------------------------------

        move_row = layer_box.row(
            align=True
        )

        if sibling_index > 0:

            up_operator = (
                move_row.operator(
                    "blendgimp.reorder_layer",
                    text="Up"
                )
            )

            up_operator.image_id = (
                int(image_id)
            )

            up_operator.layer_id = (
                layer_id
            )

            up_operator.direction = (
                "UP"
            )

        if sibling_index < (
            sibling_count - 1
        ):

            down_operator = (
                move_row.operator(
                    "blendgimp.reorder_layer",
                    text="Down"
                )
            )

            down_operator.image_id = (
                int(image_id)
            )

            down_operator.layer_id = (
                layer_id
            )

            down_operator.direction = (
                "DOWN"
            )

        move_operator = (
            move_row.operator(
                "blendgimp.move_layer",
                text="Move..."
            )
        )

        move_operator.image_id = (
            int(image_id)
        )

        move_operator.layer_id = (
            layer_id
        )

        # ----------------------------------------------------
        # Metadata
        # ----------------------------------------------------

        info_row = layer_box.row()

        kind = (
            "Group"
            if is_group
            else
            "Layer"
        )

        parent_id = layer.get(
            "parent_id",
            None
        )

        if parent_id is None:
            parent_text = "Top Level"
        else:
            parent_text = (
                f"Parent {parent_id}"
            )

        info_row.label(
            text=(
                f"{kind} ID {layer_id} | "
                f"{parent_text}"
            )
        )

        children = layer.get(
            "children",
            []
        )

        if children:

            draw_layer_tree(
                layer_box,
                children,
                image_id,
                depth + 1
            )


# ============================================================
# DETECT GIMP
# ============================================================

class BLENDGIMP_OT_detect_gimp(
    bpy.types.Operator
):

    bl_idname = "blendgimp.detect_gimp"
    bl_label = "Detect GIMP"

    bl_description = (
        "Search for a supported GIMP installation"
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        gimp_path = (
            gimp_manager.find_gimp()
        )

        if gimp_path:

            version = (
                gimp_manager.get_gimp_version(
                    gimp_path
                )
            )

            scene.blendgimp_gimp_path = (
                gimp_path
            )

            scene.blendgimp_gimp_version = (
                version
                or
                "Unknown Version"
            )

            scene.blendgimp_gimp_detected = True

            print(
                "BLENDGIMP: "
                f"GIMP found at {gimp_path}"
            )

            print(
                "BLENDGIMP: "
                f"Version stored = {version}"
            )

            self.report(
                {"INFO"},
                "GIMP detected successfully"
            )

        else:

            scene.blendgimp_gimp_detected = False
            scene.blendgimp_gimp_path = ""
            scene.blendgimp_gimp_version = ""
            scene.blendgimp_gimp_running = False
            scene.blendgimp_connected = False

            clear_image_results(
                scene
            )

            self.report(
                {"WARNING"},
                "GIMP installation not found"
            )

        return {"FINISHED"}


# ============================================================
# LAUNCH GIMP
# ============================================================

class BLENDGIMP_OT_launch_gimp(
    bpy.types.Operator
):

    bl_idname = "blendgimp.launch_gimp"
    bl_label = "Start GIMP Engine"

    bl_description = (
        "Start the selected persistent GIMP engine mode and connect to it"
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        success, pid = start_scene_engine(
            scene
        )

        if success:
            self.report(
                {"INFO"},
                (
                    "GIMP engine starting "
                    f"PID {pid} "
                    f"({scene.blendgimp_engine_mode})"
                )
            )

            return {"FINISHED"}

        self.report(
            {"ERROR"},
            (
                scene.blendgimp_engine_last_error
                or "Could not launch GIMP engine"
            )
        )

        return {"CANCELLED"}


# ============================================================
# STOP GIMP ENGINE
# ============================================================

class BLENDGIMP_OT_stop_gimp(
    bpy.types.Operator
):

    bl_idname = "blendgimp.stop_gimp"
    bl_label = "Stop GIMP Engine"

    bl_description = (
        "Disconnect and stop the GIMP process started by BlendGimp"
    )

    def execute(
        self,
        context
    ):
        success, _exit_code, forced = (
            stop_scene_engine(
                context.scene
            )
        )

        if not success:
            self.report(
                {"ERROR"},
                (
                    context.scene.blendgimp_engine_last_error
                    or "Could not stop GIMP engine"
                )
            )
            return {"CANCELLED"}

        self.report(
            {"WARNING" if forced else "INFO"},
            (
                "GIMP engine stopped"
                + (
                    " after forced timeout"
                    if forced
                    else ""
                )
            )
        )
        return {"FINISHED"}


# ============================================================
# RESTART GIMP ENGINE
# ============================================================

class BLENDGIMP_OT_restart_gimp(
    bpy.types.Operator
):

    bl_idname = "blendgimp.restart_gimp"
    bl_label = "Restart GIMP Engine"

    bl_description = (
        "Restart GIMP in the selected engine mode and reconnect"
    )

    def execute(
        self,
        context
    ):
        scene = context.scene

        success, _exit_code, _forced = (
            stop_scene_engine(
                scene
            )
        )

        if not success:
            self.report(
                {"ERROR"},
                (
                    scene.blendgimp_engine_last_error
                    or "Could not stop GIMP engine"
                )
            )
            return {"CANCELLED"}

        success, pid = start_scene_engine(
            scene
        )

        if not success:
            self.report(
                {"ERROR"},
                (
                    scene.blendgimp_engine_last_error
                    or "Could not restart GIMP engine"
                )
            )
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                "GIMP engine restarting "
                f"PID {pid} "
                f"({scene.blendgimp_engine_mode})"
            )
        )
        return {"FINISHED"}


# ============================================================
# CHECK PROCESS
# ============================================================

class BLENDGIMP_OT_check_gimp(
    bpy.types.Operator
):

    bl_idname = "blendgimp.check_gimp"
    bl_label = "Check GIMP Status"

    def execute(
        self,
        context
    ):

        snapshot = (
            gimp_manager.get_engine_snapshot()
        )
        running = bool(
            snapshot.get(
                "running",
                False
            )
        )

        context.scene.blendgimp_gimp_running = (
            running
        )

        if running:

            context.scene.blendgimp_engine_state = str(
                snapshot.get(
                    "state",
                    gimp_manager.ENGINE_STATE_STARTING
                )
            )

            self.report(
                {"INFO"},
                (
                    "GIMP engine is running "
                    f"PID {snapshot.get('pid')}"
                )
            )

        else:

            context.scene.blendgimp_engine_state = str(
                snapshot.get(
                    "state",
                    gimp_manager.ENGINE_STATE_STOPPED
                )
            )
            context.scene.blendgimp_engine_last_error = str(
                snapshot.get(
                    "last_error",
                    ""
                )
            )

            self.report(
                {"INFO"},
                "GIMP engine is not running"
            )

        return {"FINISHED"}


# ============================================================
# CONNECT TO GIMP
# ============================================================

class BLENDGIMP_OT_connect(
    bpy.types.Operator
):

    bl_idname = "blendgimp.connect"
    bl_label = "Connect to GIMP"

    bl_description = (
        "Connect Blender to the BlendGimp GIMP component"
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        try:

            response = connect_scene_engine(
                scene
            )

            scene.blendgimp_engine_should_run = True

            self.report(
                {"INFO"},
                "BlendGimp connected to GIMP"
            )

            return {"FINISHED"}

        except Exception as exc:

            clear_engine_connection(
                scene
            )

            print(
                "BLENDGIMP: "
                f"Connection failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"Connection failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# PING GIMP
# ============================================================

class BLENDGIMP_OT_ping(
    bpy.types.Operator
):

    bl_idname = "blendgimp.ping"
    bl_label = "Ping GIMP"

    bl_description = (
        "Test the active BlendGimp connection"
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        try:

            response = (
                connection_manager.ping()
            )

            scene.blendgimp_connected = True

            print(
                "BLENDGIMP: "
                f"PING response = {response}"
            )

            self.report(
                {"INFO"},
                "PONG received from GIMP"
            )

            return {"FINISHED"}

        except Exception as exc:

            scene.blendgimp_connected = False

            print(
                "BLENDGIMP: "
                f"PING failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"PING failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# CREATE BLENDER-OWNED GIMP IMAGE
# ============================================================

class BLENDGIMP_OT_create_image(
    bpy.types.Operator
):

    bl_idname = "blendgimp.create_image"
    bl_label = "Create BlendGimp Texture"

    bl_description = (
        "Create a new GIMP image and initial layer, create its paired Blender "
        "Image, assign it to the active material, and start Auto Sync"
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        if not connection_manager.is_connected():
            scene.blendgimp_connected = False
            scene.blendgimp_create_status = "Create failed: not connected"
            self.report(
                {"ERROR"},
                "BlendGimp is not connected to GIMP"
            )
            return {"CANCELLED"}

        if (
            scene.blendgimp_create_format == "RGB"
            and scene.blendgimp_create_background == "TRANSPARENT"
        ):
            scene.blendgimp_create_status = (
                "Create failed: RGB requires a solid background"
            )
            self.report(
                {"ERROR"},
                "RGB textures require a solid background"
            )
            return {"CANCELLED"}

        scene.blendgimp_create_status = "Creating texture in GIMP..."

        try:
            response = connection_manager.create_image(
                name=scene.blendgimp_create_name,
                width=scene.blendgimp_create_width,
                height=scene.blendgimp_create_height,
                image_format=scene.blendgimp_create_format,
                background=scene.blendgimp_create_background,
                background_color=scene.blendgimp_create_background_color,
                layer_name=scene.blendgimp_create_layer_name,
            )

            image_id = int(response["image_id"])
            layer_id = int(response["layer_id"])

            scene.blendgimp_created_image_id = image_id
            scene.blendgimp_created_layer_id = layer_id

            # Populate the normal image cards immediately. The created image
            # uses the same painting and synchronization paths as any other
            # open GIMP image.
            images_response = connection_manager.get_images()
            images = images_response.get("images", [])
            scene.blendgimp_images_queried = True
            scene.blendgimp_image_count = len(images)
            scene.blendgimp_images_json = json.dumps(
                images,
                ensure_ascii=False
            )

            sync_result = synchronize_gimp_composite(
                context,
                image_id,
                assign_material=True
            )

            blender_image = sync_result["blender_image"]
            texture_name = str(
                scene.blendgimp_create_name
                or "BlendGimp Texture"
            ).strip()

            blender_image["blendgimp_created_by_blender"] = True
            blender_image["blendgimp_texture_name"] = texture_name
            blender_image["blendgimp_gimp_image_id"] = image_id
            blender_image["blendgimp_gimp_layer_id"] = layer_id
            blender_image["blendgimp_texture_format"] = str(
                scene.blendgimp_create_format
            )
            blender_image.name = texture_name
            blender_image.update()

            state = connection_manager.get_image_state(image_id)
            revision = int(state.get("revision", 0))

            scene.blendgimp_auto_sync_enabled = True
            scene.blendgimp_auto_sync_image_id = image_id
            scene.blendgimp_auto_sync_revision = revision
            scene.blendgimp_auto_sync_status = (
                f"Watching revision {revision}"
            )
            scene.blendgimp_auto_sync_detector = str(
                state.get("detector", "")
            )

            reset_auto_sync_runtime(
                image_id,
                revision
            )

            scene.blendgimp_create_status = (
                f"Created {texture_name} — image {image_id}, layer {layer_id}"
            )

            print(
                "BLENDGIMP: "
                f"Blender-owned texture created: {texture_name} "
                f"image ID={image_id} layer ID={layer_id}; Auto Sync started"
            )

            self.report(
                {"INFO"},
                f"Created {texture_name} and started Auto Sync"
            )
            return {"FINISHED"}

        except Exception as exc:
            scene.blendgimp_connected = connection_manager.is_connected()
            scene.blendgimp_create_status = f"Create failed: {exc}"

            print(
                "BLENDGIMP: "
                f"CREATE_IMAGE workflow failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"Create BlendGimp Texture failed: {exc}"
            )
            return {"CANCELLED"}


# ============================================================
# GET GIMP IMAGES
# ============================================================

class BLENDGIMP_OT_get_images(
    bpy.types.Operator
):

    bl_idname = "blendgimp.get_images"
    bl_label = "Get GIMP Images"

    bl_description = (
        "Read the list of images currently open in GIMP"
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        try:

            print(
                "BLENDGIMP: "
                "Requesting open GIMP images"
            )

            response = (
                connection_manager.get_images()
            )

            images = response.get(
                "images",
                []
            )

            scene.blendgimp_connected = True
            scene.blendgimp_images_queried = True
            scene.blendgimp_image_count = (
                len(images)
            )
            scene.blendgimp_images_json = (
                json.dumps(
                    images,
                    ensure_ascii=False
                )
            )

            print(
                "BLENDGIMP: "
                f"GIMP Images = {len(images)}"
            )

            for image in images:

                print(
                    "BLENDGIMP: "
                    f"Image ID={image.get('id')} "
                    f"Name={image.get('name')} "
                    f"Size={image.get('width')}x"
                    f"{image.get('height')}"
                )

            if images:

                self.report(
                    {"INFO"},
                    (
                        f"GIMP returned "
                        f"{len(images)} open image(s)"
                    )
                )

            else:

                self.report(
                    {"INFO"},
                    "GIMP has no images open"
                )

            return {"FINISHED"}

        except Exception as exc:

            scene.blendgimp_connected = False

            print(
                "BLENDGIMP: "
                f"GET_IMAGES failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"GET_IMAGES failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# REFRESH GIMP COMPOSITE INTO BLENDER
# ============================================================

class BLENDGIMP_OT_refresh_from_gimp(
    bpy.types.Operator
):

    bl_idname = "blendgimp.refresh_from_gimp"
    bl_label = "Refresh From GIMP"

    bl_description = (
        "Export GIMP's visible composite, reload it into Blender, "
        "and assign it to the active material"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        if not connection_manager.is_connected():

            scene.blendgimp_connected = False

            self.report(
                {"ERROR"},
                "BlendGimp is not connected to GIMP"
            )

            return {"CANCELLED"}

        try:

            result = (
                synchronize_gimp_composite(
                    context,
                    self.image_id,
                    assign_material=True
                )
            )

            blender_image = result[
                "blender_image"
            ]

            assignment = result[
                "assignment"
            ]

            if (
                scene.blendgimp_auto_sync_enabled
                and int(
                    scene.blendgimp_auto_sync_image_id
                ) == int(
                    self.image_id
                )
                and connection_manager.is_connected()
            ):
                try:
                    state = (
                        connection_manager.get_image_state(
                            self.image_id
                        )
                    )

                    revision = int(
                        state.get(
                            "revision",
                            0
                        )
                    )

                    reset_auto_sync_runtime(
                        self.image_id,
                        revision
                    )

                    scene.blendgimp_auto_sync_revision = revision
                    scene.blendgimp_auto_sync_status = (
                        f"Synced revision {revision}"
                    )
                except Exception:
                    pass

            if assignment.get(
                "assigned",
                False
            ):

                self.report(
                    {"INFO"},
                    (
                        f"Refreshed {blender_image.name} "
                        f"on {assignment.get('material')}"
                    )
                )

            else:

                reason = assignment.get(
                    "reason",
                    "Material assignment skipped"
                )

                print(
                    "BLENDGIMP: "
                    f"Material assignment skipped: {reason}"
                )

                self.report(
                    {"WARNING"},
                    (
                        f"Image refreshed; material assignment skipped: {reason}"
                    )
                )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"REFRESH_FROM_GIMP failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"Refresh From GIMP failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# DIRECT GIMP PAINT LIVE VIEWPORT FEEDBACK
# ============================================================

class BLENDGIMP_OT_direct_live_refresh(
    bpy.types.Operator
):

    bl_idname = "blendgimp.direct_live_refresh"
    bl_label = "BlendGimp Direct Live Refresh"

    bl_options = {
        "INTERNAL"
    }

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    def execute(
        self,
        context
    ):

        if not connection_manager.is_connected():
            return {"CANCELLED"}

        try:

            result = synchronize_gimp_composite(
                context,
                int(
                    self.image_id
                ),
                assign_material=False,
                dirty_only=True
            )

            transport_response = result.get(
                "transport_response",
                {}
            )

            if transport_response.get(
                "changed",
                False
            ):

                print(
                    "BLENDGIMP: "
                    "Direct live viewport feedback applied "
                    f"x={transport_response.get('x', 0)} "
                    f"y={transport_response.get('y', 0)} "
                    f"{transport_response.get('region_width', 0)}x"
                    f"{transport_response.get('region_height', 0)}"
                )

            # Consume/acknowledge the observer revision associated with the
            # pixels we just applied. This is lightweight compared with the
            # composite read that already happened above and prevents normal
            # Auto Sync from performing another dirty composite check for the
            # same direct-paint chunk.
            state = connection_manager.get_image_state(
                int(
                    self.image_id
                )
            )

            revision = int(
                state.get(
                    "revision",
                    0
                )
            )

            _AUTO_SYNC_RUNTIME[
                "image_id"
            ] = int(
                self.image_id
            )

            _AUTO_SYNC_RUNTIME[
                "last_seen_revision"
            ] = revision

            _AUTO_SYNC_RUNTIME[
                "last_synced_revision"
            ] = revision

            _AUTO_SYNC_RUNTIME[
                "pending_revision"
            ] = None

            _AUTO_SYNC_RUNTIME[
                "pending_since"
            ] = 0.0

            scene = context.scene

            scene.blendgimp_auto_sync_revision = (
                revision
            )

            detector = str(
                state.get(
                    "detector",
                    ""
                )
            )

            if (
                detector
                and hasattr(
                    scene,
                    "blendgimp_auto_sync_detector"
                )
            ):
                scene.blendgimp_auto_sync_detector = (
                    detector
                )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"Direct live viewport feedback failed: {exc}"
            )

            return {"CANCELLED"}


class BLENDGIMP_OT_direct_paint_resume_auto_sync(
    bpy.types.Operator
):

    bl_idname = "blendgimp.direct_paint_resume_auto_sync"
    bl_label = "BlendGimp Resume Auto Sync"

    bl_options = {
        "INTERNAL"
    }

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        try:

            set_direct_paint_refresh_owner(
                False
            )

            if (
                scene.blendgimp_auto_sync_enabled
                and int(
                    scene.blendgimp_auto_sync_image_id
                ) == int(
                    self.image_id
                )
            ):

                print(
                    "BLENDGIMP: "
                    "Normal GIMP Auto Sync resumed after direct paint"
                )

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"Could not resume Auto Sync cleanly: {exc}"
            )

        return {"FINISHED"}


# ============================================================
# AUTO SYNC TOGGLE
# ============================================================

class BLENDGIMP_OT_toggle_auto_sync(
    bpy.types.Operator
):

    bl_idname = "blendgimp.toggle_auto_sync"
    bl_label = "Toggle GIMP Auto Sync"

    bl_description = (
        "Automatically refresh this GIMP image in Blender when its visible "
        "composite changes"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    def execute(
        self,
        context
    ):

        scene = context.scene
        image_id = int(
            self.image_id
        )

        already_enabled = (
            scene.blendgimp_auto_sync_enabled
            and int(
                scene.blendgimp_auto_sync_image_id
            ) == image_id
        )

        if already_enabled:

            scene.blendgimp_auto_sync_enabled = False
            scene.blendgimp_auto_sync_image_id = -1
            scene.blendgimp_auto_sync_revision = 0
            scene.blendgimp_auto_sync_status = "Off"
            if hasattr(
                scene,
                "blendgimp_auto_sync_detector"
            ):
                scene.blendgimp_auto_sync_detector = ""

            reset_auto_sync_runtime()

            print(
                "BLENDGIMP: Auto Sync disabled"
            )

            self.report(
                {"INFO"},
                "GIMP Auto Sync disabled"
            )

            return {"FINISHED"}

        if not connection_manager.is_connected():

            scene.blendgimp_connected = False

            self.report(
                {"ERROR"},
                "BlendGimp is not connected to GIMP"
            )

            return {"CANCELLED"}

        try:

            # Establish a visual revision baseline first.
            state = (
                connection_manager.get_image_state(
                    image_id
                )
            )

            revision = int(
                state.get(
                    "revision",
                    0
                )
            )

            if hasattr(
                scene,
                "blendgimp_auto_sync_detector"
            ):
                scene.blendgimp_auto_sync_detector = str(
                    state.get(
                        "detector",
                        ""
                    )
                )

            # Initial enable also performs normal material assignment to the
            # currently active object/material. Future auto refreshes only
            # reload this existing Blender Image.
            synchronize_gimp_composite(
                context,
                image_id,
                assign_material=True
            )

            scene.blendgimp_auto_sync_enabled = True
            scene.blendgimp_auto_sync_image_id = image_id
            scene.blendgimp_auto_sync_revision = revision
            scene.blendgimp_auto_sync_status = (
                f"Watching revision {revision}"
            )

            reset_auto_sync_runtime(
                image_id,
                revision
            )

            print(
                "BLENDGIMP: "
                f"Auto Sync enabled for GIMP image ID {image_id} "
                f"at revision {revision}"
            )

            self.report(
                {"INFO"},
                f"Auto Sync enabled for GIMP image ID {image_id}"
            )

            return {"FINISHED"}

        except Exception as exc:

            scene.blendgimp_auto_sync_enabled = False
            scene.blendgimp_auto_sync_image_id = -1
            scene.blendgimp_auto_sync_status = (
                f"Enable failed: {exc}"
            )

            print(
                "BLENDGIMP: "
                f"ENABLE_AUTO_SYNC failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"Enable Auto Sync failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# PUSH BLENDER TEXTURE TO GIMP
# ============================================================

class BLENDGIMP_OT_toggle_blender_paint_sync(
    bpy.types.Operator
):

    bl_idname = "blendgimp.toggle_blender_paint_sync"
    bl_label = "Toggle 3D Paint Sync"

    bl_description = (
        "Automatically push Blender Texture Paint changes into the dedicated "
        "BlendGimp Paint layer in GIMP"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    def execute(
        self,
        context
    ):

        scene = context.scene
        image_id = int(
            self.image_id
        )

        active = (
            scene.blendgimp_blender_paint_sync_enabled
            and int(
                scene.blendgimp_blender_paint_sync_image_id
            ) == image_id
        )

        if active:

            scene.blendgimp_blender_paint_sync_enabled = (
                False
            )

            scene.blendgimp_blender_paint_sync_image_id = (
                -1
            )

            scene.blendgimp_blender_paint_sync_layer_id = (
                -1
            )

            scene.blendgimp_blender_paint_sync_status = (
                "Off"
            )

            reset_blender_paint_sync_runtime()

            print(
                "BLENDGIMP: "
                "3D Paint Sync disabled"
            )

            self.report(
                {"INFO"},
                "3D Paint Sync disabled"
            )

            return {"FINISHED"}

        if not connection_manager.is_connected():

            self.report(
                {"ERROR"},
                "BlendGimp is not connected to GIMP"
            )

            return {"CANCELLED"}

        try:

            sync_result = (
                get_texture_sync_result(
                    scene,
                    image_id
                )
                or {}
            )

            blender_image = _find_blendgimp_image(
                image_id,
                sync_result.get(
                    "sync_token",
                    ""
                )
            )

            if blender_image is None:

                raise RuntimeError(
                    "Refresh From GIMP before enabling 3D Paint Sync"
                )

            layer_response = (
                connection_manager.ensure_paint_layer(
                    image_id,
                    BLENDER_PAINT_LAYER_NAME
                )
            )

            layer_id = int(
                layer_response[
                    "layer_id"
                ]
            )

            width, height, baseline = (
                _blender_image_to_top_left_rgba8(
                    blender_image
                )
            )

            scene.blendgimp_blender_paint_sync_enabled = (
                True
            )

            scene.blendgimp_blender_paint_sync_image_id = (
                image_id
            )

            scene.blendgimp_blender_paint_sync_layer_id = (
                layer_id
            )

            scene.blendgimp_blender_paint_sync_status = (
                "Waiting for Texture Paint mode"
            )

            reset_blender_paint_sync_runtime(
                image_id=image_id,
                layer_id=layer_id,
                baseline=baseline
            )

            print(
                "BLENDGIMP: "
                f"3D Paint Sync enabled for image ID {image_id} "
                f"using GIMP layer ID {layer_id}"
            )

            self.report(
                {"INFO"},
                (
                    "3D Paint Sync enabled - "
                    f"GIMP layer {layer_response.get('name')}"
                )
            )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"ENABLE_3D_PAINT_SYNC failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"3D Paint Sync failed: {exc}"
            )

            return {"CANCELLED"}


class BLENDGIMP_OT_push_to_gimp(
    bpy.types.Operator
):

    bl_idname = "blendgimp.push_to_gimp"
    bl_label = "Push Blender Texture to GIMP"

    bl_description = (
        "Write the synchronized Blender Image into the currently selected "
        "raster layer in GIMP"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    def execute(
        self,
        context
    ):

        scene = context.scene
        image_id = int(
            self.image_id
        )

        if not connection_manager.is_connected():

            scene.blendgimp_connected = False

            self.report(
                {"ERROR"},
                "BlendGimp is not connected to GIMP"
            )

            return {"CANCELLED"}

        try:

            sync_result = (
                get_texture_sync_result(
                    scene,
                    image_id
                )
                or {}
            )

            blender_image = _find_blendgimp_image(
                image_id,
                sync_result.get(
                    "sync_token",
                    ""
                )
            )

            if blender_image is None:

                raise RuntimeError(
                    "No synchronized Blender Image exists yet. "
                    "Refresh From GIMP first."
                )

            layer_response = (
                connection_manager.ensure_paint_layer(
                    image_id,
                    BLENDER_PAINT_LAYER_NAME
                )
            )

            layer_id = int(
                layer_response[
                    "layer_id"
                ]
            )

            width, height, raw_pixels = (
                _blender_image_to_top_left_rgba8(
                    blender_image
                )
            )

            response = (
                connection_manager.set_layer_pixels_binary(
                    image_id,
                    layer_id,
                    0,
                    0,
                    width,
                    height,
                    raw_pixels
                )
            )

            refresh_layer_result(
                scene,
                image_id
            )

            scene.blendgimp_connected = True

            update_blender_paint_sync_baseline(
                image_id,
                blender_image
            )

            print(
                "BLENDGIMP: "
                f"Pushed Blender image {blender_image.name} "
                f"to GIMP layer ID {layer_id} "
                f"({response.get('width')}x{response.get('height')}, "
                f"{response.get('byte_length')} bytes)"
            )

            self.report(
                {"INFO"},
                (
                    f"Pushed {blender_image.name} to "
                    f"GIMP layer {layer_response.get('name', layer_id)}"
                )
            )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"PUSH_TO_GIMP failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"Push to GIMP failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# GET IMAGE LAYERS
# ============================================================

class BLENDGIMP_OT_get_image_layers(
    bpy.types.Operator
):

    bl_idname = "blendgimp.get_image_layers"
    bl_label = "Get Image Layers"

    bl_description = (
        "Read the complete layer tree for this GIMP image"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        if self.image_id < 0:

            self.report(
                {"ERROR"},
                "Invalid GIMP image ID"
            )

            return {"CANCELLED"}

        try:

            print(
                "BLENDGIMP: "
                "Requesting layers for "
                f"GIMP image ID {self.image_id}"
            )

            response = (
                connection_manager.
                get_image_layers(
                    self.image_id
                )
            )

            store_layer_result(
                scene,
                self.image_id,
                response
            )

            scene.blendgimp_connected = True

            layer_count = int(
                response.get(
                    "layer_count",
                    0
                )
            )

            print(
                "BLENDGIMP: "
                f"Image ID {self.image_id} "
                f"has {layer_count} layer(s)"
            )

            def print_layers(
                layers,
                depth=0
            ):

                for layer in layers:

                    indent = (
                        "  " * depth
                    )

                    print(
                        "BLENDGIMP: "
                        f"{indent}"
                        f"Layer ID={layer.get('id')} "
                        f"Name={layer.get('name')} "
                        f"Visible={layer.get('visible')} "
                        f"Opacity={layer.get('opacity')} "
                        f"Group={layer.get('is_group')} "
                        f"Selected={layer.get('selected')}"
                    )

                    print_layers(
                        layer.get(
                            "children",
                            []
                        ),
                        depth + 1
                    )

            print_layers(
                response.get(
                    "layers",
                    []
                )
            )

            self.report(
                {"INFO"},
                (
                    f"GIMP returned "
                    f"{layer_count} layer(s)"
                )
            )

            return {"FINISHED"}

        except Exception as exc:

            scene.blendgimp_connected = False

            print(
                "BLENDGIMP: "
                f"GET_IMAGE_LAYERS failed: {exc}"
            )

            self.report(
                {"ERROR"},
                (
                    "GET_IMAGE_LAYERS failed: "
                    f"{exc}"
                )
            )

            return {"CANCELLED"}



# ============================================================
# SET ACTIVE LAYER
# ============================================================

class BLENDGIMP_OT_set_active_layer(
    bpy.types.Operator
):

    bl_idname = "blendgimp.set_active_layer"
    bl_label = "Set Active GIMP Layer"

    bl_description = (
        "Make this the active selected layer in GIMP"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    layer_id: bpy.props.IntProperty(
        name="Layer ID",
        default=-1
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        try:

            connection_manager.set_active_layer(
                self.image_id,
                self.layer_id
            )

            refresh_layer_result(
                scene,
                self.image_id
            )

            scene.blendgimp_connected = True

            print(
                "BLENDGIMP: "
                f"Active GIMP layer set to ID {self.layer_id}"
            )

            self.report(
                {"INFO"},
                f"Active GIMP layer set to ID {self.layer_id}"
            )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"SET_ACTIVE_LAYER failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"SET_ACTIVE_LAYER failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# SET LAYER VISIBILITY
# ============================================================

class BLENDGIMP_OT_set_layer_visibility(
    bpy.types.Operator
):

    bl_idname = "blendgimp.set_layer_visibility"
    bl_label = "Set GIMP Layer Visibility"

    bl_description = (
        "Show or hide this layer in GIMP"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    layer_id: bpy.props.IntProperty(
        name="Layer ID",
        default=-1
    )

    visible: bpy.props.BoolProperty(
        name="Visible",
        default=True
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        try:

            response = (
                connection_manager.
                set_layer_visibility(
                    self.image_id,
                    self.layer_id,
                    bool(self.visible)
                )
            )

            refresh_layer_result(
                scene,
                self.image_id
            )

            scene.blendgimp_connected = True

            visible = bool(
                response.get(
                    "visible",
                    self.visible
                )
            )

            print(
                "BLENDGIMP: "
                f"Layer ID {self.layer_id} "
                f"visibility={visible}"
            )

            self.report(
                {"INFO"},
                (
                    f"Layer ID {self.layer_id} "
                    f"{'shown' if visible else 'hidden'}"
                )
            )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"SET_LAYER_VISIBILITY failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"SET_LAYER_VISIBILITY failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# SET LAYER OPACITY
# ============================================================

class BLENDGIMP_OT_set_layer_opacity(
    bpy.types.Operator
):

    bl_idname = "blendgimp.set_layer_opacity"
    bl_label = "Set GIMP Layer Opacity"

    bl_description = (
        "Change this layer's opacity in GIMP"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    layer_id: bpy.props.IntProperty(
        name="Layer ID",
        default=-1
    )

    opacity: bpy.props.FloatProperty(
        name="Opacity",
        description="GIMP layer opacity from 0 to 100 percent",
        default=100.0,
        min=0.0,
        max=100.0,
        precision=1
    )

    def invoke(
        self,
        context,
        event
    ):

        return (
            context.window_manager.
            invoke_props_dialog(
                self,
                width=320
            )
        )

    def draw(
        self,
        context
    ):

        self.layout.prop(
            self,
            "opacity",
            slider=True
        )

    def execute(
        self,
        context
    ):

        scene = context.scene

        try:

            response = (
                connection_manager.
                set_layer_opacity(
                    self.image_id,
                    self.layer_id,
                    self.opacity
                )
            )

            refresh_layer_result(
                scene,
                self.image_id
            )

            scene.blendgimp_connected = True

            actual_opacity = float(
                response.get(
                    "opacity",
                    self.opacity
                )
            )

            print(
                "BLENDGIMP: "
                f"Layer ID {self.layer_id} "
                f"opacity={actual_opacity:.1f}"
            )

            self.report(
                {"INFO"},
                (
                    f"Layer ID {self.layer_id} "
                    f"opacity {actual_opacity:.1f}%"
                )
            )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"SET_LAYER_OPACITY failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"SET_LAYER_OPACITY failed: {exc}"
            )

            return {"CANCELLED"}



# ============================================================
# ADD LAYER
# ============================================================

class BLENDGIMP_OT_add_layer(
    bpy.types.Operator
):

    bl_idname = "blendgimp.add_layer"
    bl_label = "Add GIMP Layer"

    bl_description = (
        "Create a new full-size transparent GIMP layer above the active layer"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    layer_name: bpy.props.StringProperty(
        name="Layer Name",
        default="New Layer"
    )

    def invoke(
        self,
        context,
        event
    ):

        return (
            context.window_manager.
            invoke_props_dialog(
                self,
                width=320
            )
        )

    def draw(
        self,
        context
    ):

        self.layout.prop(
            self,
            "layer_name"
        )

    def execute(
        self,
        context
    ):

        scene = context.scene

        name = (
            self.layer_name
            or
            "New Layer"
        ).strip()

        try:

            response = (
                connection_manager.add_layer(
                    self.image_id,
                    name
                )
            )

            refresh_layer_result(
                scene,
                self.image_id
            )

            new_layer_id = int(
                response.get(
                    "layer_id",
                    -1
                )
            )

            print(
                "BLENDGIMP: "
                f"Added GIMP layer ID {new_layer_id} "
                f"Name={response.get('name')}"
            )

            self.report(
                {"INFO"},
                f"Added layer {response.get('name', '')}"
            )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"ADD_LAYER failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"ADD_LAYER failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# DELETE LAYER
# ============================================================

class BLENDGIMP_OT_delete_layer(
    bpy.types.Operator
):

    bl_idname = "blendgimp.delete_layer"
    bl_label = "Delete GIMP Layer"

    bl_description = (
        "Delete this GIMP layer or group"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    layer_id: bpy.props.IntProperty(
        name="Layer ID",
        default=-1
    )

    layer_name: bpy.props.StringProperty(
        name="Layer Name",
        default=""
    )

    def invoke(
        self,
        context,
        event
    ):

        return (
            context.window_manager.
            invoke_confirm(
                self,
                event
            )
        )

    def execute(
        self,
        context
    ):

        scene = context.scene

        try:

            connection_manager.delete_layer(
                self.image_id,
                self.layer_id
            )

            refresh_layer_result(
                scene,
                self.image_id
            )

            print(
                "BLENDGIMP: "
                f"Deleted GIMP layer ID {self.layer_id} "
                f"Name={self.layer_name}"
            )

            self.report(
                {"INFO"},
                f"Deleted layer {self.layer_name}"
            )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"DELETE_LAYER failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"DELETE_LAYER failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# RENAME LAYER
# ============================================================

class BLENDGIMP_OT_rename_layer(
    bpy.types.Operator
):

    bl_idname = "blendgimp.rename_layer"
    bl_label = "Rename GIMP Layer"

    bl_description = (
        "Rename this GIMP layer or group"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    layer_id: bpy.props.IntProperty(
        name="Layer ID",
        default=-1
    )

    layer_name: bpy.props.StringProperty(
        name="Layer Name",
        default=""
    )

    def invoke(
        self,
        context,
        event
    ):

        return (
            context.window_manager.
            invoke_props_dialog(
                self,
                width=320
            )
        )

    def draw(
        self,
        context
    ):

        self.layout.prop(
            self,
            "layer_name"
        )

    def execute(
        self,
        context
    ):

        scene = context.scene

        new_name = (
            self.layer_name
            or
            ""
        ).strip()

        if not new_name:

            self.report(
                {"ERROR"},
                "Layer name cannot be empty"
            )

            return {"CANCELLED"}

        try:

            response = (
                connection_manager.rename_layer(
                    self.image_id,
                    self.layer_id,
                    new_name
                )
            )

            refresh_layer_result(
                scene,
                self.image_id
            )

            print(
                "BLENDGIMP: "
                f"Renamed GIMP layer ID {self.layer_id} "
                f"to {response.get('name')}"
            )

            self.report(
                {"INFO"},
                f"Renamed layer to {response.get('name', new_name)}"
            )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"RENAME_LAYER failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"RENAME_LAYER failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# DUPLICATE LAYER
# ============================================================

class BLENDGIMP_OT_duplicate_layer(
    bpy.types.Operator
):

    bl_idname = "blendgimp.duplicate_layer"
    bl_label = "Duplicate GIMP Layer"

    bl_description = (
        "Duplicate this GIMP layer or group"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    layer_id: bpy.props.IntProperty(
        name="Layer ID",
        default=-1
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        try:

            response = (
                connection_manager.duplicate_layer(
                    self.image_id,
                    self.layer_id
                )
            )

            refresh_layer_result(
                scene,
                self.image_id
            )

            print(
                "BLENDGIMP: "
                f"Duplicated GIMP layer ID {self.layer_id} "
                f"as ID {response.get('layer_id')}"
            )

            self.report(
                {"INFO"},
                f"Duplicated layer as {response.get('name', '')}"
            )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"DUPLICATE_LAYER failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"DUPLICATE_LAYER failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# REORDER LAYER
# ============================================================

class BLENDGIMP_OT_reorder_layer(
    bpy.types.Operator
):

    bl_idname = "blendgimp.reorder_layer"
    bl_label = "Reorder GIMP Layer"

    bl_description = (
        "Move this GIMP layer one step up or down"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    layer_id: bpy.props.IntProperty(
        name="Layer ID",
        default=-1
    )

    direction: bpy.props.StringProperty(
        name="Direction",
        default="UP"
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        try:

            response = (
                connection_manager.reorder_layer(
                    self.image_id,
                    self.layer_id,
                    self.direction
                )
            )

            refresh_layer_result(
                scene,
                self.image_id
            )

            print(
                "BLENDGIMP: "
                f"Reordered layer ID {self.layer_id} "
                f"{self.direction} "
                f"to position {response.get('position')}"
            )

            self.report(
                {"INFO"},
                f"Layer moved {self.direction.lower()}"
            )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"REORDER_LAYER failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"REORDER_LAYER failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# MOVE LAYER INTO / OUT OF GROUP
# ============================================================

class BLENDGIMP_OT_move_layer(
    bpy.types.Operator
):

    bl_idname = "blendgimp.move_layer"
    bl_label = "Move GIMP Layer"

    bl_description = (
        "Move this layer into a group or back to the top-level layer stack"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    layer_id: bpy.props.IntProperty(
        name="Layer ID",
        default=-1
    )

    target_parent: bpy.props.EnumProperty(
        name="Move To",
        description="Choose the destination group or Top Level",
        items=blendgimp_move_target_items
    )

    def invoke(
        self,
        context,
        event
    ):

        # Default to top-level so the dialog always has a valid selection.
        self.target_parent = "-1"

        return (
            context.window_manager.
            invoke_props_dialog(
                self,
                width=360
            )
        )

    def draw(
        self,
        context
    ):

        self.layout.prop(
            self,
            "target_parent"
        )

    def execute(
        self,
        context
    ):

        scene = context.scene

        try:

            parent_id = int(
                self.target_parent
            )

            response = (
                connection_manager.move_layer(
                    self.image_id,
                    self.layer_id,
                    parent_id
                )
            )

            refresh_layer_result(
                scene,
                self.image_id
            )

            actual_parent_id = (
                response.get(
                    "parent_id",
                    None
                )
            )

            destination = (
                "Top Level"
                if actual_parent_id is None
                else f"Group ID {actual_parent_id}"
            )

            print(
                "BLENDGIMP: "
                f"Moved GIMP layer ID {self.layer_id} "
                f"to {destination}"
            )

            self.report(
                {"INFO"},
                f"Layer moved to {destination}"
            )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"MOVE_LAYER failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"MOVE_LAYER failed: {exc}"
            )

            return {"CANCELLED"}



# ============================================================
# CREATE GROUP
# ============================================================

class BLENDGIMP_OT_create_group(
    bpy.types.Operator
):

    bl_idname = "blendgimp.create_group"
    bl_label = "Create GIMP Group"

    bl_description = (
        "Create a new GIMP layer group above the active layer"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    group_name: bpy.props.StringProperty(
        name="Group Name",
        default="Layer Group"
    )

    def invoke(
        self,
        context,
        event
    ):

        return (
            context.window_manager.
            invoke_props_dialog(
                self,
                width=320
            )
        )

    def draw(
        self,
        context
    ):

        self.layout.prop(
            self,
            "group_name"
        )

    def execute(
        self,
        context
    ):

        scene = context.scene

        try:

            response = (
                connection_manager.create_group(
                    self.image_id,
                    self.group_name
                )
            )

            refresh_layer_result(
                scene,
                self.image_id
            )

            print(
                "BLENDGIMP: "
                f"Created GIMP group ID {response.get('layer_id')} "
                f"Name={response.get('name')}"
            )

            self.report(
                {"INFO"},
                f"Created group {response.get('name', '')}"
            )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"CREATE_GROUP failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"CREATE_GROUP failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# MERGE LAYER DOWN
# ============================================================

class BLENDGIMP_OT_merge_layer_down(
    bpy.types.Operator
):

    bl_idname = "blendgimp.merge_layer_down"
    bl_label = "Merge GIMP Layer Down"

    bl_description = (
        "Merge this layer with the first visible GIMP layer below it"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    layer_id: bpy.props.IntProperty(
        name="Layer ID",
        default=-1
    )

    layer_name: bpy.props.StringProperty(
        name="Layer Name",
        default=""
    )

    def invoke(
        self,
        context,
        event
    ):

        return (
            context.window_manager.
            invoke_confirm(
                self,
                event
            )
        )

    def execute(
        self,
        context
    ):

        scene = context.scene

        try:

            response = (
                connection_manager.merge_layer_down(
                    self.image_id,
                    self.layer_id
                )
            )

            refresh_layer_result(
                scene,
                self.image_id
            )

            print(
                "BLENDGIMP: "
                f"Merged layer ID {self.layer_id} down "
                f"into resulting layer ID {response.get('layer_id')}"
            )

            self.report(
                {"INFO"},
                f"Merged {self.layer_name} down"
            )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"MERGE_LAYER_DOWN failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"MERGE_LAYER_DOWN failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# SET LAYER LOCK
# ============================================================

class BLENDGIMP_OT_set_layer_lock(
    bpy.types.Operator
):

    bl_idname = "blendgimp.set_layer_lock"
    bl_label = "Set GIMP Layer Lock"

    bl_description = (
        "Toggle a GIMP layer lock"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    layer_id: bpy.props.IntProperty(
        name="Layer ID",
        default=-1
    )

    lock_type: bpy.props.StringProperty(
        name="Lock Type",
        default="CONTENT"
    )

    locked: bpy.props.BoolProperty(
        name="Locked",
        default=True
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        try:

            response = (
                connection_manager.set_layer_lock(
                    self.image_id,
                    self.layer_id,
                    self.lock_type,
                    bool(self.locked)
                )
            )

            refresh_layer_result(
                scene,
                self.image_id
            )

            actual_locked = bool(
                response.get(
                    "locked",
                    self.locked
                )
            )

            print(
                "BLENDGIMP: "
                f"Layer ID {self.layer_id} "
                f"{self.lock_type} lock={actual_locked}"
            )

            self.report(
                {"INFO"},
                (
                    f"{self.lock_type.title()} lock "
                    f"{'enabled' if actual_locked else 'disabled'}"
                )
            )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"SET_LAYER_LOCK failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"SET_LAYER_LOCK failed: {exc}"
            )

            return {"CANCELLED"}


# ============================================================
# SET LAYER BLEND MODE
# ============================================================

class BLENDGIMP_OT_set_layer_mode(
    bpy.types.Operator
):

    bl_idname = "blendgimp.set_layer_mode"
    bl_label = "Set GIMP Blend Mode"

    bl_description = (
        "Change this layer's GIMP blend mode"
    )

    image_id: bpy.props.IntProperty(
        name="Image ID",
        default=-1
    )

    layer_id: bpy.props.IntProperty(
        name="Layer ID",
        default=-1
    )

    mode: bpy.props.EnumProperty(
        name="Blend Mode",
        description="GIMP layer blend mode",
        items=BLENDGIMP_LAYER_MODE_ITEMS,
        default="NORMAL"
    )

    def invoke(
        self,
        context,
        event
    ):

        return (
            context.window_manager.
            invoke_props_dialog(
                self,
                width=420
            )
        )

    def draw(
        self,
        context
    ):

        self.layout.prop(
            self,
            "mode"
        )

    def execute(
        self,
        context
    ):

        scene = context.scene

        try:

            response = (
                connection_manager.set_layer_mode(
                    self.image_id,
                    self.layer_id,
                    self.mode
                )
            )

            refresh_layer_result(
                scene,
                self.image_id
            )

            actual_mode = str(
                response.get(
                    "mode",
                    self.mode
                )
            )

            print(
                "BLENDGIMP: "
                f"Layer ID {self.layer_id} "
                f"blend mode={actual_mode}"
            )

            self.report(
                {"INFO"},
                (
                    "Blend mode set to "
                    + blendgimp_layer_mode_label(
                        actual_mode
                    )
                )
            )

            return {"FINISHED"}

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"SET_LAYER_MODE failed: {exc}"
            )

            self.report(
                {"ERROR"},
                f"SET_LAYER_MODE failed: {exc}"
            )

            return {"CANCELLED"}



# ============================================================
# DISCONNECT
# ============================================================

class BLENDGIMP_OT_disconnect(
    bpy.types.Operator
):

    bl_idname = "blendgimp.disconnect"
    bl_label = "Disconnect"

    bl_description = (
        "Disconnect Blender from GIMP"
    )

    def execute(
        self,
        context
    ):

        context.scene.blendgimp_engine_should_run = False

        connection_manager.disconnect()

        clear_engine_connection(
            context.scene
        )

        if gimp_manager.is_gimp_running():
            gimp_manager.mark_engine_disconnected(
                "Disconnected manually"
            )
            context.scene.blendgimp_engine_state = (
                gimp_manager.ENGINE_STATE_DISCONNECTED
            )
        else:
            context.scene.blendgimp_engine_state = (
                gimp_manager.ENGINE_STATE_STOPPED
            )

        print(
            "BLENDGIMP: "
            "Disconnected from GIMP"
        )

        self.report(
            {"INFO"},
            "BlendGimp disconnected"
        )

        return {"FINISHED"}


# ============================================================
# MAIN PANEL
# ============================================================

class BLENDGIMP_PT_main_panel(
    bpy.types.Panel
):

    bl_label = "BlendGimp"

    bl_idname = (
        "BLENDGIMP_PT_main_panel"
    )

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlendGimp"

    def draw(
        self,
        context
    ):

        layout = self.layout
        scene = context.scene

        # ====================================================
        # HEADER
        # ====================================================

        layout.label(
            text="BlendGimp 0.1.0"
        )

        layout.separator()

        # ====================================================
        # GIMP DETECTION
        # ====================================================

        gimp_box = layout.box()

        gimp_box.label(
            text="GIMP Engine"
        )

        gimp_box.prop(
            scene,
            "blendgimp_engine_mode",
            text="Mode"
        )

        gimp_box.prop(
            scene,
            "blendgimp_engine_auto_reconnect",
            text="Automatic Recovery"
        )

        engine_snapshot = (
            gimp_manager.get_engine_snapshot()
        )
        engine_state = str(
            scene.blendgimp_engine_state
            or engine_snapshot.get(
                "state",
                gimp_manager.ENGINE_STATE_STOPPED
            )
        )

        state_icon = (
            "CHECKMARK"
            if engine_state == gimp_manager.ENGINE_STATE_CONNECTED
            else (
                "ERROR"
                if engine_state == gimp_manager.ENGINE_STATE_FAILED
                else "INFO"
            )
        )

        gimp_box.label(
            text=(
                "State: "
                + engine_state.replace(
                    "_",
                    " "
                ).title()
            ),
            icon=state_icon
        )

        if engine_snapshot.get(
            "pid"
        ):
            gimp_box.label(
                text=(
                    "PID: "
                    f"{engine_snapshot.get('pid')}"
                )
            )

        if scene.blendgimp_engine_restart_count > 0:
            gimp_box.label(
                text=(
                    "Automatic Restarts: "
                    f"{scene.blendgimp_engine_restart_count}"
                )
            )

        if scene.blendgimp_engine_last_error:
            error_box = gimp_box.box()
            error_box.label(
                text=scene.blendgimp_engine_last_error,
                icon="ERROR"
            )

        if scene.blendgimp_gimp_detected:

            gimp_box.label(
                text="Detected",
                icon="CHECKMARK"
            )

            gimp_box.label(
                text=(
                    f"GIMP "
                    f"{scene.blendgimp_gimp_version}"
                )
            )

            if scene.blendgimp_gimp_running:
                controls = gimp_box.row(
                    align=True
                )
                controls.operator(
                    "blendgimp.stop_gimp",
                    text="Stop",
                    icon="CANCEL"
                )
                controls.operator(
                    "blendgimp.restart_gimp",
                    text="Restart",
                    icon="FILE_REFRESH"
                )

            else:
                gimp_box.operator(
                    "blendgimp.launch_gimp",
                    text="Start Engine",
                    icon="PLAY"
                )

            if (
                scene.blendgimp_gimp_running
                and engine_snapshot.get(
                    "mode"
                )
                and engine_snapshot.get(
                    "mode"
                ) != scene.blendgimp_engine_mode
            ):
                gimp_box.label(
                    text="Restart to apply the selected mode",
                    icon="INFO"
                )

            gimp_box.operator(
                "blendgimp.check_gimp",
                text="Check Process"
            )

            gimp_box.separator()

            gimp_box.label(
                text="Executable:"
            )

            gimp_box.label(
                text=scene.blendgimp_gimp_path
            )

        else:

            gimp_box.label(
                text="Not Detected",
                icon="ERROR"
            )

        gimp_box.operator(
            "blendgimp.detect_gimp",
            text="Detect GIMP",
            icon="VIEWZOOM"
        )

        layout.separator()

        # ====================================================
        # CONNECTION
        # ====================================================

        connection_box = layout.box()

        connection_box.label(
            text="BlendGimp Connection"
        )

        if (
            scene.blendgimp_connected
            and connection_manager.is_connected()
        ):

            connection_box.label(
                text="Connected",
                icon="CHECKMARK"
            )

            connection_box.label(
                text=(
                    "Runtime GIMP: "
                    f"{scene.blendgimp_runtime_gimp_version}"
                )
            )

            connection_box.label(
                text=(
                    "Protocol: "
                    f"{scene.blendgimp_protocol_version}"
                )
            )

            connection_box.label(
                text=(
                    "GIMP Component: "
                    f"{scene.blendgimp_remote_version}"
                )
            )

            connection_box.separator()

            create_box = connection_box.box()
            create_box.label(
                text="New BlendGimp Texture",
                icon="ADD"
            )
            create_box.prop(
                scene,
                "blendgimp_create_name",
                text="Name"
            )

            size_row = create_box.row(align=True)
            size_row.prop(
                scene,
                "blendgimp_create_width",
                text="Width"
            )
            size_row.prop(
                scene,
                "blendgimp_create_height",
                text="Height"
            )

            create_box.prop(
                scene,
                "blendgimp_create_format",
                text="Format"
            )
            create_box.prop(
                scene,
                "blendgimp_create_background",
                text="Background"
            )

            if scene.blendgimp_create_background == "SOLID":
                create_box.prop(
                    scene,
                    "blendgimp_create_background_color",
                    text="Color"
                )

            if (
                scene.blendgimp_create_format == "RGB"
                and scene.blendgimp_create_background == "TRANSPARENT"
            ):
                create_box.label(
                    text="RGB requires a solid background",
                    icon="ERROR"
                )

            create_box.prop(
                scene,
                "blendgimp_create_layer_name",
                text="Initial Layer"
            )

            create_button_row = create_box.row()
            create_button_row.enabled = not (
                scene.blendgimp_create_format == "RGB"
                and scene.blendgimp_create_background == "TRANSPARENT"
            )
            create_button_row.operator(
                "blendgimp.create_image",
                text="Create",
                icon="IMAGE_DATA"
            )

            if scene.blendgimp_create_status:
                create_box.label(
                    text=scene.blendgimp_create_status,
                    icon=(
                        "ERROR"
                        if scene.blendgimp_create_status.startswith(
                            "Create failed"
                        )
                        else "CHECKMARK"
                    )
                )

            connection_box.separator()

            connection_box.operator(
                "blendgimp.ping",
                text="Ping GIMP"
            )

            connection_box.operator(
                "blendgimp.get_images",
                text="Get GIMP Images",
                icon="IMAGE_DATA"
            )

            # ================================================
            # LAST GET_IMAGES RESULT
            # ================================================

            if scene.blendgimp_images_queried:

                connection_box.separator()

                images = (
                    get_stored_images(
                        scene
                    )
                )

                connection_box.label(
                    text=(
                        "Open GIMP Images: "
                        f"{scene.blendgimp_image_count}"
                    )
                )

                if images:

                    for image in images:

                        image_box = (
                            connection_box.box()
                        )

                        image_box.label(
                            text=str(
                                image.get(
                                    "name",
                                    "[Unnamed]"
                                )
                            ),
                            icon="IMAGE_DATA"
                        )

                        image_box.label(
                            text=(
                                f"{image.get('width', '?')} "
                                f"x "
                                f"{image.get('height', '?')}"
                            )
                        )

                        image_box.label(
                            text=(
                                "Image ID: "
                                f"{image.get('id', '?')}"
                            )
                        )

                        try:
                            image_id = int(
                                image.get(
                                    "id",
                                    -1
                                )
                            )
                        except (
                            TypeError,
                            ValueError
                        ):
                            image_id = -1

                        if image_id >= 0:

                            refresh_operator = (
                                image_box.operator(
                                    "blendgimp.refresh_from_gimp",
                                    text="Refresh From GIMP"
                                )
                            )

                            refresh_operator.image_id = (
                                image_id
                            )

                            push_operator = (
                                image_box.operator(
                                    "blendgimp.push_to_gimp",
                                    text="Push Blender Texture to GIMP",
                                    icon="EXPORT"
                                )
                            )

                            push_operator.image_id = (
                                image_id
                            )

                            direct_paint_active = (
                                scene.blendgimp_direct_paint_active
                                and int(
                                    scene.blendgimp_direct_paint_image_id
                                ) == image_id
                            )

                            direct_box = image_box.box()

                            direct_box.label(
                                text="Direct GIMP Brush 3D Paint — Live",
                                icon="BRUSH_DATA"
                            )

                            if direct_paint_active:

                                direct_box.label(
                                    text=(
                                        "ACTIVE — LMB paint, Esc/RMB exit"
                                    ),
                                    icon="CHECKMARK"
                                )

                            else:

                                operator_registered = hasattr(
                                    bpy.types,
                                    "BLENDGIMP_OT_direct_gimp_brush_paint"
                                )

                                if operator_registered:

                                    direct_operator = (
                                        direct_box.operator(
                                            "blendgimp.direct_gimp_brush_paint",
                                            text="Start GIMP Brush 3D Paint",
                                            icon="BRUSH_DATA"
                                        )
                                    )

                                    if direct_operator is not None:

                                        direct_operator.image_id = (
                                            image_id
                                        )

                                        direct_operator.image_width = int(
                                            image.get(
                                                "width",
                                                0
                                            )
                                        )

                                        direct_operator.image_height = int(
                                            image.get(
                                                "height",
                                                0
                                            )
                                        )

                                else:

                                    direct_box.label(
                                        text=(
                                            "Direct paint operator is not "
                                            "registered — restart Blender"
                                        ),
                                        icon="ERROR"
                                    )

                            if scene.blendgimp_direct_paint_brush:

                                direct_box.label(
                                    text=(
                                        "GIMP Brush: "
                                        f"{scene.blendgimp_direct_paint_brush}"
                                    )
                                )

                            geometry_box = direct_box.box()

                            geometry_box.enabled = (
                                not direct_paint_active
                            )

                            geometry_box.label(
                                text="Geometry Protection"
                            )

                            geometry_box.prop(
                                scene,
                                "blendgimp_direct_paint_projection_mesh",
                                text="Projection Mesh"
                            )

                            if scene.blendgimp_direct_paint_projection_mesh == "AUTO":

                                geometry_box.label(
                                    text=(
                                        "Auto uses evaluated modifiers when "
                                        "the active UV map is preserved"
                                    )
                                )

                            geometry_box.prop(
                                scene,
                                "blendgimp_direct_paint_occlusion_mode",
                                text="Surface Mode"
                            )

                            if scene.blendgimp_direct_paint_occlusion_mode == "THROUGH":

                                geometry_box.label(
                                    text=(
                                        "Paint Through traces every surface "
                                        "under the cursor"
                                    )
                                )

                            geometry_box.prop(
                                scene,
                                "blendgimp_direct_paint_footprint_protection",
                                text="Protect Brush Footprint"
                            )

                            if scene.blendgimp_direct_paint_footprint_protection:

                                geometry_box.prop(
                                    scene,
                                    "blendgimp_direct_paint_footprint_samples",
                                    text="Footprint Rays"
                                )

                                geometry_box.prop(
                                    scene,
                                    "blendgimp_direct_paint_footprint_safe_ratio",
                                    text="Surface Coverage"
                                )

                                geometry_box.label(
                                    text=(
                                        "Silhouette misses remain strictly "
                                        "protected"
                                    )
                                )

                            geometry_box.prop(
                                scene,
                                "blendgimp_direct_paint_front_faces_only",
                                text="Front Faces Only"
                            )

                            geometry_box.prop(
                                scene,
                                "blendgimp_direct_paint_normal_angle_enabled",
                                text="Limit View Angle"
                            )

                            if scene.blendgimp_direct_paint_normal_angle_enabled:

                                geometry_box.prop(
                                    scene,
                                    "blendgimp_direct_paint_normal_angle_limit",
                                    text="Maximum Angle (degrees)"
                                )

                            if scene.blendgimp_direct_paint_status:

                                direct_box.label(
                                    text=(
                                        "Status: "
                                        f"{scene.blendgimp_direct_paint_status}"
                                    )
                                )

                            direct_box.label(
                                text="Live • seam-safe • brush-footprint protected"
                            )

                            paint_sync_active = (
                                scene.blendgimp_blender_paint_sync_enabled
                                and int(
                                    scene.blendgimp_blender_paint_sync_image_id
                                ) == image_id
                            )

                            paint_toggle = (
                                image_box.operator(
                                    "blendgimp.toggle_blender_paint_sync",
                                    text=(
                                        "Disable 3D Paint Sync"
                                        if paint_sync_active
                                        else "Enable 3D Paint Sync"
                                    ),
                                    icon=(
                                        "PAUSE"
                                        if paint_sync_active
                                        else "BRUSH_DATA"
                                    )
                                )
                            )

                            paint_toggle.image_id = (
                                image_id
                            )

                            if paint_sync_active:

                                image_box.label(
                                    text=(
                                        "3D Paint Sync: "
                                        f"{scene.blendgimp_blender_paint_sync_status}"
                                    ),
                                    icon="CHECKMARK"
                                )

                                image_box.label(
                                    text=(
                                        "GIMP Target: "
                                        f"{BLENDER_PAINT_LAYER_NAME}"
                                    )
                                )

                                image_box.prop(
                                    scene,
                                    "blendgimp_blender_paint_sync_debounce",
                                    text="3D Paint Delay"
                                )

                            auto_sync_active = (
                                scene.blendgimp_auto_sync_enabled
                                and int(
                                    scene.blendgimp_auto_sync_image_id
                                ) == image_id
                            )

                            auto_sync_operator = (
                                image_box.operator(
                                    "blendgimp.toggle_auto_sync",
                                    text=(
                                        "Auto Sync: ON"
                                        if auto_sync_active
                                        else (
                                            "Switch Auto Sync Here"
                                            if scene.blendgimp_auto_sync_enabled
                                            else "Enable Auto Sync"
                                        )
                                    ),
                                    icon="FILE_REFRESH",
                                    depress=auto_sync_active
                                )
                            )

                            auto_sync_operator.image_id = (
                                image_id
                            )

                            if auto_sync_active:

                                image_box.prop(
                                    scene,
                                    "blendgimp_auto_sync_debounce",
                                    text="Auto Sync Delay"
                                )

                                image_box.label(
                                    text=(
                                        "Auto Sync: "
                                        f"{scene.blendgimp_auto_sync_status}"
                                    ),
                                    icon="CHECKMARK"
                                )

                                detector_value = str(
                                    getattr(
                                        scene,
                                        "blendgimp_auto_sync_detector",
                                        ""
                                    )
                                )

                                detector_label = (
                                    "Hybrid (Fingerprint + GEGL)"
                                    if detector_value
                                    == "hybrid"
                                    else (
                                        "GEGL Damage"
                                        if detector_value
                                        == "gegl-damage"
                                        else (
                                            "Thumbnail Fallback"
                                            if detector_value
                                            == "thumbnail-fallback"
                                            else detector_value
                                        )
                                    )
                                )

                                if detector_label:

                                    image_box.label(
                                        text=(
                                            "Change Detector: "
                                            f"{detector_label}"
                                        ),
                                        icon=(
                                            "CHECKMARK"
                                            if detector_value in {
                                                "hybrid",
                                                "gegl-damage"
                                            }
                                            else "INFO"
                                        )
                                    )

                            sync_result = (
                                get_texture_sync_result(
                                    scene,
                                    image_id
                                )
                            )

                            if sync_result is not None:

                                image_box.label(
                                    text=(
                                        "Blender Image: "
                                        f"{sync_result.get('blender_image', '')}"
                                    )
                                )

                                sync_transport = str(
                                    sync_result.get(
                                        "transport",
                                        ""
                                    )
                                )

                                if sync_transport:
                                    if sync_transport == "dirty-rgba-binary":
                                        transport_label = "Dirty RGBA Binary"
                                    elif sync_transport == "dirty-rgba-json":
                                        transport_label = "Dirty RGBA Base64"
                                    elif sync_transport == "direct-rgba-binary":
                                        transport_label = "Direct RGBA Binary"
                                    elif sync_transport == "direct-rgba-json":
                                        transport_label = "Direct RGBA Base64"
                                    else:
                                        transport_label = "PNG Fallback"

                                    image_box.label(
                                        text=(
                                            "Transport: "
                                            f"{transport_label}"
                                        ),
                                        icon=(
                                            "CHECKMARK"
                                            if sync_transport in {
                                                "dirty-rgba-binary",
                                                "dirty-rgba-json",
                                                "direct-rgba-binary",
                                                "direct-rgba-json"
                                            }
                                            else "INFO"
                                        )
                                    )

                                    if (
                                        sync_transport in {
                                            "dirty-rgba-binary",
                                            "dirty-rgba-json"
                                        }
                                        and int(
                                            sync_result.get(
                                                "dirty_width",
                                                0
                                            )
                                        ) > 0
                                    ):
                                        image_box.label(
                                            text=(
                                                "Last Region: "
                                                f"{sync_result.get('dirty_width')}x"
                                                f"{sync_result.get('dirty_height')} "
                                                f"@ {sync_result.get('dirty_x')},"
                                                f"{sync_result.get('dirty_y')}"
                                            )
                                        )

                                        image_box.label(
                                            text=(
                                                "Transferred: "
                                                f"{sync_result.get('byte_length', 0):,} bytes"
                                            )
                                        )

                                if sync_result.get(
                                    "assigned",
                                    False
                                ):

                                    image_box.label(
                                        text=(
                                            "Material: "
                                            f"{sync_result.get('material', '')}"
                                        ),
                                        icon="CHECKMARK"
                                    )

                                elif sync_result.get(
                                    "reason",
                                    ""
                                ):

                                    image_box.label(
                                        text=str(
                                            sync_result.get(
                                                "reason",
                                                ""
                                            )
                                        ),
                                        icon="INFO"
                                    )

                            layer_operator = (
                                image_box.operator(
                                    "blendgimp.get_image_layers",
                                    text="Get Layers"
                                )
                            )

                            layer_operator.image_id = (
                                image_id
                            )

                            add_layer_operator = (
                                image_box.operator(
                                    "blendgimp.add_layer",
                                    text="Add Layer"
                                )
                            )

                            add_layer_operator.image_id = (
                                image_id
                            )

                            create_group_operator = (
                                image_box.operator(
                                    "blendgimp.create_group",
                                    text="Create Group"
                                )
                            )

                            create_group_operator.image_id = (
                                image_id
                            )

                            layer_result = (
                                get_stored_layer_result(
                                    scene,
                                    image_id
                                )
                            )

                            if layer_result is not None:

                                image_box.separator()

                                image_box.label(
                                    text=(
                                        "Layers: "
                                        f"{layer_result.get('layer_count', 0)}"
                                    )
                                )

                                draw_layer_tree(
                                    image_box,
                                    layer_result.get(
                                        "layers",
                                        []
                                    ),
                                    image_id
                                )

                else:

                    connection_box.label(
                        text="No images are open in GIMP"
                    )

            connection_box.separator()

            connection_box.operator(
                "blendgimp.disconnect",
                text="Disconnect"
            )

        else:

            connection_box.label(
                text="Disconnected"
            )

            connection_box.operator(
                "blendgimp.connect",
                text="Connect to GIMP"
            )


# ============================================================
# CLASSES
# ============================================================

classes = (

    BLENDGIMP_OT_detect_gimp,

    BLENDGIMP_OT_launch_gimp,

    BLENDGIMP_OT_stop_gimp,

    BLENDGIMP_OT_restart_gimp,

    BLENDGIMP_OT_check_gimp,

    BLENDGIMP_OT_connect,

    BLENDGIMP_OT_ping,

    BLENDGIMP_OT_create_image,

    BLENDGIMP_OT_get_images,

    BLENDGIMP_OT_refresh_from_gimp,

    BLENDGIMP_OT_direct_live_refresh,

    BLENDGIMP_OT_direct_paint_resume_auto_sync,

    BLENDGIMP_OT_direct_gimp_brush_paint,

    BLENDGIMP_OT_toggle_blender_paint_sync,

    BLENDGIMP_OT_push_to_gimp,

    BLENDGIMP_OT_toggle_auto_sync,

    BLENDGIMP_OT_get_image_layers,

    BLENDGIMP_OT_set_active_layer,

    BLENDGIMP_OT_set_layer_visibility,

    BLENDGIMP_OT_set_layer_opacity,

    BLENDGIMP_OT_add_layer,

    BLENDGIMP_OT_delete_layer,

    BLENDGIMP_OT_rename_layer,

    BLENDGIMP_OT_duplicate_layer,

    BLENDGIMP_OT_reorder_layer,

    BLENDGIMP_OT_move_layer,

    BLENDGIMP_OT_create_group,

    BLENDGIMP_OT_merge_layer_down,

    BLENDGIMP_OT_set_layer_lock,

    BLENDGIMP_OT_set_layer_mode,

    BLENDGIMP_OT_disconnect,

    BLENDGIMP_PT_main_panel,

)


# ============================================================
# REGISTER
# ============================================================

def register():

    for cls in classes:

        bpy.utils.register_class(
            cls
        )

        if cls is BLENDGIMP_OT_direct_gimp_brush_paint:

            print(
                "BLENDGIMP: "
                "Direct GIMP Brush 3D Paint operator registered"
            )

    bpy.types.Scene.blendgimp_engine_mode = (
        bpy.props.EnumProperty(
            name="GIMP Engine Mode",
            description=(
                "Run GIMP invisibly for normal BlendGimp work or visibly "
                "for debugging and regression testing"
            ),
            items=(
                (
                    gimp_manager.ENGINE_MODE_HEADLESS,
                    "Headless",
                    "Run a dedicated persistent GIMP engine without its "
                    "traditional interface"
                ),
                (
                    gimp_manager.ENGINE_MODE_VISIBLE_DEBUG,
                    "Visible / Debug",
                    "Use the original visible GIMP launch path for "
                    "troubleshooting and regression testing"
                ),
            ),
            default=gimp_manager.ENGINE_MODE_HEADLESS
        )
    )

    bpy.types.Scene.blendgimp_engine_auto_reconnect = (
        bpy.props.BoolProperty(
            name="Automatic Engine Recovery",
            description=(
                "Reconnect when the socket drops and restart the managed "
                "GIMP engine when its process exits unexpectedly"
            ),
            default=True
        )
    )

    bpy.types.Scene.blendgimp_engine_should_run = (
        bpy.props.BoolProperty(
            name="GIMP Engine Requested",
            default=False,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_engine_state = (
        bpy.props.StringProperty(
            name="GIMP Engine State",
            default=gimp_manager.ENGINE_STATE_STOPPED,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_engine_last_error = (
        bpy.props.StringProperty(
            name="GIMP Engine Last Error",
            default="",
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_engine_restart_count = (
        bpy.props.IntProperty(
            name="GIMP Engine Automatic Restarts",
            default=0,
            min=0,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_gimp_detected = (
        bpy.props.BoolProperty(
            name="GIMP Detected",
            default=False
        )
    )

    bpy.types.Scene.blendgimp_gimp_path = (
        bpy.props.StringProperty(
            name="GIMP Path",
            default=""
        )
    )

    bpy.types.Scene.blendgimp_gimp_version = (
        bpy.props.StringProperty(
            name="GIMP Version",
            default=""
        )
    )

    bpy.types.Scene.blendgimp_gimp_running = (
        bpy.props.BoolProperty(
            name="GIMP Running",
            default=False
        )
    )

    bpy.types.Scene.blendgimp_connected = (
        bpy.props.BoolProperty(
            name="BlendGimp Connected",
            default=False
        )
    )

    bpy.types.Scene.blendgimp_protocol_version = (
        bpy.props.IntProperty(
            name="Protocol Version",
            default=0
        )
    )

    bpy.types.Scene.blendgimp_remote_version = (
        bpy.props.StringProperty(
            name="GIMP Component Version",
            default=""
        )
    )

    bpy.types.Scene.blendgimp_runtime_gimp_version = (
        bpy.props.StringProperty(
            name="Runtime GIMP Version",
            default=""
        )
    )

    bpy.types.Scene.blendgimp_create_name = (
        bpy.props.StringProperty(
            name="Texture Name",
            description="Name shared by the Blender-owned texture pair",
            default="BaseColor"
        )
    )

    bpy.types.Scene.blendgimp_create_width = (
        bpy.props.IntProperty(
            name="Texture Width",
            default=2048,
            min=1,
            max=32768
        )
    )

    bpy.types.Scene.blendgimp_create_height = (
        bpy.props.IntProperty(
            name="Texture Height",
            default=2048,
            min=1,
            max=32768
        )
    )

    bpy.types.Scene.blendgimp_create_format = (
        bpy.props.EnumProperty(
            name="Texture Format",
            items=(
                (
                    "RGBA",
                    "RGBA",
                    "RGB color with an alpha channel"
                ),
                (
                    "RGB",
                    "RGB",
                    "Opaque RGB color without an alpha channel"
                ),
            ),
            default="RGBA"
        )
    )

    bpy.types.Scene.blendgimp_create_background = (
        bpy.props.EnumProperty(
            name="Texture Background",
            items=(
                (
                    "TRANSPARENT",
                    "Transparent",
                    "Initialize the RGBA layer with transparent pixels"
                ),
                (
                    "SOLID",
                    "Solid",
                    "Initialize the layer with the selected opaque color"
                ),
            ),
            default="TRANSPARENT"
        )
    )

    bpy.types.Scene.blendgimp_create_background_color = (
        bpy.props.FloatVectorProperty(
            name="Background Color",
            subtype="COLOR",
            size=4,
            min=0.0,
            max=1.0,
            default=(0.0, 0.0, 0.0, 1.0)
        )
    )

    bpy.types.Scene.blendgimp_create_layer_name = (
        bpy.props.StringProperty(
            name="Initial Layer Name",
            default="BaseColor"
        )
    )

    bpy.types.Scene.blendgimp_created_image_id = (
        bpy.props.IntProperty(
            name="Last Created GIMP Image ID",
            default=-1,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_created_layer_id = (
        bpy.props.IntProperty(
            name="Last Created GIMP Layer ID",
            default=-1,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_create_status = (
        bpy.props.StringProperty(
            name="Create Texture Status",
            default="",
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_images_queried = (
        bpy.props.BoolProperty(
            name="GIMP Images Queried",
            default=False,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_image_count = (
        bpy.props.IntProperty(
            name="Open GIMP Image Count",
            default=0,
            min=0,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_images_json = (
        bpy.props.StringProperty(
            name="Open GIMP Images JSON",
            default="[]",
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_layers_json = (
        bpy.props.StringProperty(
            name="GIMP Layer Results JSON",
            default="{}",
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_texture_sync_json = (
        bpy.props.StringProperty(
            name="BlendGimp Texture Sync JSON",
            default="{}",
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_direct_paint_active = (
        bpy.props.BoolProperty(
            name="Direct GIMP 3D Paint Active",
            default=False,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_direct_paint_image_id = (
        bpy.props.IntProperty(
            name="Direct GIMP 3D Paint Image ID",
            default=-1,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_direct_paint_status = (
        bpy.props.StringProperty(
            name="Direct GIMP 3D Paint Status",
            default="",
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_direct_paint_brush = (
        bpy.props.StringProperty(
            name="Direct GIMP Brush",
            default="",
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_direct_paint_front_faces_only = (
        bpy.props.BoolProperty(
            name="Front Faces Only",
            description=(
                "Reject direct-paint ray hits whose surface normal faces "
                "away from the viewport"
            ),
            default=True
        )
    )

    bpy.types.Scene.blendgimp_direct_paint_occlusion_mode = (
        bpy.props.EnumProperty(
            name="Direct Paint Surface Mode",
            description=(
                "Choose nearest-visible-surface painting or raycast through "
                "all mesh surfaces"
            ),
            items=(
                (
                    "VISIBLE",
                    "Visible Surface",
                    "Paint only the nearest visible surface"
                ),
                (
                    "THROUGH",
                    "Paint Through",
                    "Paint every allowed mesh surface along the viewport ray"
                ),
            ),
            default="VISIBLE"
        )
    )

    bpy.types.Scene.blendgimp_direct_paint_projection_mesh = (
        bpy.props.EnumProperty(
            name="Direct Paint Projection Mesh",
            description=(
                "Choose whether direct paint projects onto the viewport's "
                "evaluated modifier result or the original mesh"
            ),
            items=(
                (
                    "AUTO",
                    "Evaluated (Auto Fallback)",
                    "Use evaluated geometry and automatically fall back to "
                    "the original mesh if the active UV map is unavailable"
                ),
                (
                    "EVALUATED",
                    "Require Evaluated",
                    "Require modifier-evaluated geometry and cancel startup "
                    "instead of falling back when its UV map is unavailable"
                ),
                (
                    "ORIGINAL",
                    "Original Mesh",
                    "Project onto the original unmodified mesh"
                ),
            ),
            default="AUTO"
        )
    )

    bpy.types.Scene.blendgimp_direct_paint_footprint_protection = (
        bpy.props.BoolProperty(
            name="Protect Brush Footprint",
            description=(
                "Raycast around the projected GIMP brush radius and reject "
                "stamps that cross a silhouette, UV boundary, or thin "
                "occluding surface"
            ),
            default=True
        )
    )

    bpy.types.Scene.blendgimp_direct_paint_footprint_samples = (
        bpy.props.IntProperty(
            name="Footprint Rays",
            description=(
                "Number of protective raycasts around the brush perimeter"
            ),
            default=8,
            min=4,
            max=16
        )
    )

    bpy.types.Scene.blendgimp_direct_paint_footprint_safe_ratio = (
        bpy.props.FloatProperty(
            name="Footprint Surface Coverage",
            description=(
                "Minimum compatible perimeter-ray coverage for surface "
                "boundaries; true silhouette misses are always rejected"
            ),
            default=0.75,
            min=0.5,
            max=1.0,
            subtype="FACTOR",
            precision=2
        )
    )

    bpy.types.Scene.blendgimp_direct_paint_normal_angle_enabled = (
        bpy.props.BoolProperty(
            name="Limit View Angle",
            description=(
                "Reject surfaces viewed beyond the configured normal angle"
            ),
            default=False
        )
    )

    bpy.types.Scene.blendgimp_direct_paint_normal_angle_limit = (
        bpy.props.FloatProperty(
            name="Maximum View Angle",
            description=(
                "Largest allowed angle between the surface normal and view"
            ),
            default=75.0,
            min=0.0,
            max=180.0,
            precision=1
        )
    )

    bpy.types.Scene.blendgimp_blender_paint_sync_enabled = (
        bpy.props.BoolProperty(
            name="3D Paint Sync",
            default=False,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_blender_paint_sync_image_id = (
        bpy.props.IntProperty(
            name="3D Paint Sync GIMP Image ID",
            default=-1,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_blender_paint_sync_layer_id = (
        bpy.props.IntProperty(
            name="3D Paint Sync GIMP Layer ID",
            default=-1,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_blender_paint_sync_status = (
        bpy.props.StringProperty(
            name="3D Paint Sync Status",
            default="Off",
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_blender_paint_sync_debounce = (
        bpy.props.FloatProperty(
            name="3D Paint Sync Delay",
            description=(
                "Wait this long after the most recent Blender Texture Paint "
                "change before pushing its dirty region to GIMP"
            ),
            default=0.4,
            min=0.1,
            max=5.0,
            precision=2,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_auto_sync_enabled = (
        bpy.props.BoolProperty(
            name="GIMP Auto Sync",
            default=False,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_auto_sync_image_id = (
        bpy.props.IntProperty(
            name="Auto Sync GIMP Image ID",
            default=-1,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_auto_sync_revision = (
        bpy.props.IntProperty(
            name="Auto Sync Revision",
            default=0,
            min=0,
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_auto_sync_status = (
        bpy.props.StringProperty(
            name="Auto Sync Status",
            default="Off",
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_auto_sync_detector = (
        bpy.props.StringProperty(
            name="Auto Sync Detector",
            default="",
            options={"SKIP_SAVE"}
        )
    )

    bpy.types.Scene.blendgimp_auto_sync_debounce = (
        bpy.props.FloatProperty(
            name="Auto Sync Delay",
            description=(
                "Wait this long after the most recent detected GIMP change "
                "before exporting the composite"
            ),
            default=0.6,
            min=0.1,
            max=5.0,
            precision=2,
            options={"SKIP_SAVE"}
        )
    )

    reset_engine_lifecycle_runtime()
    reset_auto_sync_runtime()
    reset_blender_paint_sync_runtime()

    if not bpy.app.timers.is_registered(
        blendgimp_engine_lifecycle_timer
    ):
        bpy.app.timers.register(
            blendgimp_engine_lifecycle_timer,
            first_interval=ENGINE_LIFECYCLE_POLL_INTERVAL,
            persistent=True
        )

    if not bpy.app.timers.is_registered(
        blendgimp_blender_paint_sync_timer
    ):
        bpy.app.timers.register(
            blendgimp_blender_paint_sync_timer,
            first_interval=BLENDER_PAINT_SYNC_POLL_INTERVAL,
            persistent=True
        )

    if not bpy.app.timers.is_registered(
        blendgimp_auto_sync_timer
    ):
        bpy.app.timers.register(
            blendgimp_auto_sync_timer,
            first_interval=AUTO_SYNC_POLL_INTERVAL,
            persistent=True
        )

    print(
        "BLENDGIMP: Main panel registered"
    )


# ============================================================
# UNREGISTER
# ============================================================

def unregister():

    # --------------------------------------------------------
    # Stop lifecycle recovery before closing the connection.
    # --------------------------------------------------------

    try:
        if bpy.app.timers.is_registered(
            blendgimp_engine_lifecycle_timer
        ):
            bpy.app.timers.unregister(
                blendgimp_engine_lifecycle_timer
            )
    except Exception:
        pass

    engine_snapshot = (
        gimp_manager.get_engine_snapshot()
    )

    # A normal headless session belongs to BlendGimp and should not be left
    # orphaned after the extension is disabled. A visible debug session may
    # contain manual work, so preserve the original behavior and leave it open.
    if (
        engine_snapshot.get(
            "running",
            False
        )
        and engine_snapshot.get(
            "mode"
        ) == gimp_manager.ENGINE_MODE_HEADLESS
    ):
        graceful_requested = False
        shutdown_refused = False

        if connection_manager.is_connected():
            try:
                connection_manager.shutdown_engine(
                    force=False
                )
                graceful_requested = True
            except GimpEngineShutdownRefusedError as exc:
                shutdown_refused = True
                print(
                    "BLENDGIMP: "
                    "Headless engine kept alive to protect unsaved "
                    f"image changes: {exc}"
                )
            except Exception as exc:
                print(
                    "BLENDGIMP: "
                    "Extension shutdown request failed; "
                    f"using process fallback: {exc}"
                )

        connection_manager.disconnect()

        if shutdown_refused:
            gimp_manager.clear_process_reference()
        else:
            gimp_manager.stop_gimp(
                graceful_requested=graceful_requested
            )
    else:
        connection_manager.disconnect()
        gimp_manager.clear_process_reference()

    try:
        if bpy.app.timers.is_registered(
            blendgimp_blender_paint_sync_timer
        ):
            bpy.app.timers.unregister(
                blendgimp_blender_paint_sync_timer
            )
    except Exception:
        pass

    try:
        if bpy.app.timers.is_registered(
            blendgimp_auto_sync_timer
        ):
            bpy.app.timers.unregister(
                blendgimp_auto_sync_timer
            )
    except Exception:
        pass

    reset_engine_lifecycle_runtime()
    reset_auto_sync_runtime()
    reset_blender_paint_sync_runtime()

    del (
        bpy.types.Scene.
        blendgimp_engine_restart_count
    )

    del (
        bpy.types.Scene.
        blendgimp_engine_last_error
    )

    del (
        bpy.types.Scene.
        blendgimp_engine_state
    )

    del (
        bpy.types.Scene.
        blendgimp_engine_should_run
    )

    del (
        bpy.types.Scene.
        blendgimp_engine_auto_reconnect
    )

    del (
        bpy.types.Scene.
        blendgimp_engine_mode
    )

    del (
        bpy.types.Scene.
        blendgimp_direct_paint_normal_angle_limit
    )

    del (
        bpy.types.Scene.
        blendgimp_direct_paint_normal_angle_enabled
    )

    del (
        bpy.types.Scene.
        blendgimp_direct_paint_footprint_safe_ratio
    )

    del (
        bpy.types.Scene.
        blendgimp_direct_paint_footprint_samples
    )

    del (
        bpy.types.Scene.
        blendgimp_direct_paint_footprint_protection
    )

    del (
        bpy.types.Scene.
        blendgimp_direct_paint_occlusion_mode
    )

    del (
        bpy.types.Scene.
        blendgimp_direct_paint_projection_mesh
    )

    del (
        bpy.types.Scene.
        blendgimp_direct_paint_front_faces_only
    )

    del (
        bpy.types.Scene.
        blendgimp_direct_paint_brush
    )

    del (
        bpy.types.Scene.
        blendgimp_direct_paint_status
    )

    del (
        bpy.types.Scene.
        blendgimp_direct_paint_image_id
    )

    del (
        bpy.types.Scene.
        blendgimp_direct_paint_active
    )

    del (
        bpy.types.Scene.
        blendgimp_blender_paint_sync_debounce
    )

    del (
        bpy.types.Scene.
        blendgimp_blender_paint_sync_status
    )

    del (
        bpy.types.Scene.
        blendgimp_blender_paint_sync_layer_id
    )

    del (
        bpy.types.Scene.
        blendgimp_blender_paint_sync_image_id
    )

    del (
        bpy.types.Scene.
        blendgimp_blender_paint_sync_enabled
    )

    del (
        bpy.types.Scene.
        blendgimp_auto_sync_debounce
    )

    del (
        bpy.types.Scene.
        blendgimp_auto_sync_detector
    )

    del (
        bpy.types.Scene.
        blendgimp_auto_sync_status
    )

    del (
        bpy.types.Scene.
        blendgimp_auto_sync_revision
    )

    del (
        bpy.types.Scene.
        blendgimp_auto_sync_image_id
    )

    del (
        bpy.types.Scene.
        blendgimp_auto_sync_enabled
    )

    del (
        bpy.types.Scene.
        blendgimp_texture_sync_json
    )

    del (
        bpy.types.Scene.
        blendgimp_layers_json
    )

    del (
        bpy.types.Scene.
        blendgimp_images_json
    )

    del (
        bpy.types.Scene.
        blendgimp_image_count
    )

    del (
        bpy.types.Scene.
        blendgimp_images_queried
    )

    del (
        bpy.types.Scene.
        blendgimp_create_status
    )

    del (
        bpy.types.Scene.
        blendgimp_created_layer_id
    )

    del (
        bpy.types.Scene.
        blendgimp_created_image_id
    )

    del (
        bpy.types.Scene.
        blendgimp_create_layer_name
    )

    del (
        bpy.types.Scene.
        blendgimp_create_background_color
    )

    del (
        bpy.types.Scene.
        blendgimp_create_background
    )

    del (
        bpy.types.Scene.
        blendgimp_create_format
    )

    del (
        bpy.types.Scene.
        blendgimp_create_height
    )

    del (
        bpy.types.Scene.
        blendgimp_create_width
    )

    del (
        bpy.types.Scene.
        blendgimp_create_name
    )

    del (
        bpy.types.Scene.
        blendgimp_runtime_gimp_version
    )

    del (
        bpy.types.Scene.
        blendgimp_remote_version
    )

    del (
        bpy.types.Scene.
        blendgimp_protocol_version
    )

    del (
        bpy.types.Scene.
        blendgimp_connected
    )

    del (
        bpy.types.Scene.
        blendgimp_gimp_running
    )

    del (
        bpy.types.Scene.
        blendgimp_gimp_version
    )

    del (
        bpy.types.Scene.
        blendgimp_gimp_path
    )

    del (
        bpy.types.Scene.
        blendgimp_gimp_detected
    )

    for cls in reversed(
        classes
    ):

        bpy.utils.unregister_class(
            cls
        )

    print(
        "BLENDGIMP: Main panel unregistered"
    )
