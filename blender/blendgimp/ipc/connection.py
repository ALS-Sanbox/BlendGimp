import json
import socket


# ============================================================
# BlendGimp IPC
# ============================================================

HOST = "127.0.0.1"
PORT = 8765

PROTOCOL_VERSION = 1

# ---------------------------------------------------------------------------
# Cross-module direct-paint refresh ownership
# ---------------------------------------------------------------------------
#
# Blender timers can run with a different bpy.context than the modal operator
# that started Direct GIMP Brush painting. A Scene BoolProperty is therefore
# not a reliable synchronization primitive for timer ownership.
#
# Keep the ownership flag in this module instead. Both main_panel.py and
# painting/stroke_tool.py import this exact module instance, so they see the
# same state regardless of Blender UI context.
_DIRECT_PAINT_REFRESH_RUNTIME = {
    "active": False,
    "image_id": -1,
}


def set_direct_paint_refresh_owner(
    active,
    image_id=-1
):
    _DIRECT_PAINT_REFRESH_RUNTIME[
        "active"
    ] = bool(
        active
    )

    _DIRECT_PAINT_REFRESH_RUNTIME[
        "image_id"
    ] = (
        int(
            image_id
        )
        if active
        else -1
    )


def direct_paint_owns_refresh(
    image_id=None
):
    if not _DIRECT_PAINT_REFRESH_RUNTIME.get(
        "active",
        False
    ):
        return False

    if image_id is None:
        return True

    return int(
        _DIRECT_PAINT_REFRESH_RUNTIME.get(
            "image_id",
            -1
        )
    ) == int(
        image_id
    )


def get_direct_paint_refresh_owner():
    return dict(
        _DIRECT_PAINT_REFRESH_RUNTIME
    )

BLENDGIMP_VERSION = "0.1.0"

SOCKET_TIMEOUT = 2.0
EXPORT_COMPOSITE_TIMEOUT = 30.0
IMAGE_STATE_TIMEOUT = 5.0
GET_IMAGE_PIXELS_TIMEOUT = 30.0
GET_IMAGE_DIRTY_PIXELS_TIMEOUT = 30.0
ENGINE_SHUTDOWN_TIMEOUT = 5.0
CREATE_IMAGE_TIMEOUT = 30.0


class GimpImageNotFoundError(RuntimeError):
    """The selected image ID does not exist in the current GIMP session."""


class GimpEngineShutdownRefusedError(RuntimeError):
    """GIMP refused to close because doing so could discard image changes."""


# ============================================================
# CONNECTION MANAGER
# ============================================================

class BlendGimpConnection:
    """
    Persistent Blender -> GIMP connection.

    Supported messages:

        HELLO      -> READY
        PING       -> PONG
        STATUS     -> STATUS
        CREATE_IMAGE     -> IMAGE_CREATED
        GET_IMAGES       -> IMAGES
        GET_IMAGE_LAYERS    -> IMAGE_LAYERS
        SET_ACTIVE_LAYER    -> ACTIVE_LAYER_SET
        SET_LAYER_VISIBILITY -> LAYER_VISIBILITY_SET
        SET_LAYER_OPACITY   -> LAYER_OPACITY_SET
        ADD_LAYER           -> LAYER_ADDED
        DELETE_LAYER        -> LAYER_DELETED
        RENAME_LAYER        -> LAYER_RENAMED
        DUPLICATE_LAYER     -> LAYER_DUPLICATED
        REORDER_LAYER       -> LAYER_REORDERED
        MOVE_LAYER          -> LAYER_MOVED
        CREATE_GROUP        -> GROUP_CREATED
        MERGE_LAYER_DOWN    -> LAYER_MERGED_DOWN
        SET_LAYER_LOCK      -> LAYER_LOCK_SET
        SET_LAYER_MODE      -> LAYER_MODE_SET
        EXPORT_COMPOSITE     -> COMPOSITE_EXPORTED

    Later this same connection layer will carry commands for
    textures, layers, brushes, filters, and synchronization.
    """

    def __init__(self):

        self.socket = None
        self.receive_buffer = b""

        self.remote_version = ""
        self.remote_gimp_version = ""
        self.remote_protocol = 0

        self.request_counter = 0

    # ========================================================
    # CONNECTION STATUS
    # ========================================================

    def is_connected(self):

        return self.socket is not None

    # ========================================================
    # REQUEST ID
    # ========================================================

    def _next_request_id(
        self,
        prefix="request"
    ):

        self.request_counter += 1

        return (
            f"blender-{prefix}-"
            f"{self.request_counter}"
        )

    # ========================================================
    # CONNECT
    # ========================================================

    def connect(self):

        # ----------------------------------------------------
        # Clear any previous connection first
        # ----------------------------------------------------

        self.disconnect()

        print(
            f"BLENDGIMP: Connecting to "
            f"{HOST}:{PORT}"
        )

        try:

            self.socket = socket.create_connection(
                (
                    HOST,
                    PORT
                ),
                timeout=SOCKET_TIMEOUT
            )

            self.socket.settimeout(
                SOCKET_TIMEOUT
            )

            self.receive_buffer = b""

            print(
                "BLENDGIMP: TCP connection established"
            )

            # ------------------------------------------------
            # Send HELLO
            # ------------------------------------------------

            response = self.request(
                {
                    "type": "HELLO",
                    "component": "blender",
                    "blendgimp_version":
                        BLENDGIMP_VERSION,
                    "protocol":
                        PROTOCOL_VERSION,
                }
            )

            # ------------------------------------------------
            # Validate READY
            # ------------------------------------------------

            response_type = str(
                response.get(
                    "type",
                    ""
                )
            ).upper()

            if response_type != "READY":

                raise RuntimeError(
                    "GIMP did not return READY"
                )

            # ------------------------------------------------
            # Validate protocol
            # ------------------------------------------------

            remote_protocol = int(
                response.get(
                    "protocol",
                    0
                )
            )

            if remote_protocol != PROTOCOL_VERSION:

                raise RuntimeError(
                    "BlendGimp protocol mismatch. "
                    f"Blender={PROTOCOL_VERSION}, "
                    f"GIMP={remote_protocol}"
                )

            # ------------------------------------------------
            # Store remote information
            # ------------------------------------------------

            self.remote_protocol = (
                remote_protocol
            )

            # Supports both the original field name and the
            # current GIMP-side "server" field.
            self.remote_version = str(
                response.get(
                    "blendgimp_version",
                    response.get(
                        "server",
                        ""
                    )
                )
            )

            self.remote_gimp_version = str(
                response.get(
                    "gimp_version",
                    ""
                )
            )

            print(
                "BLENDGIMP: "
                "GIMP returned READY"
            )

            print(
                "BLENDGIMP: "
                f"Protocol = "
                f"{self.remote_protocol}"
            )

            print(
                "BLENDGIMP: "
                f"GIMP runtime version = "
                f"{self.remote_gimp_version}"
            )

            print(
                "BLENDGIMP: "
                f"GIMP BlendGimp component = "
                f"{self.remote_version}"
            )

            return response

        except Exception:

            self.disconnect()

            raise

    # ========================================================
    # REQUEST
    # ========================================================

    def request(
        self,
        message,
        timeout=None,
        quiet=False
    ):

        if not self.socket:

            raise RuntimeError(
                "BlendGimp is not connected to GIMP"
            )

        previous_timeout = (
            self.socket.gettimeout()
        )

        request_timeout = (
            SOCKET_TIMEOUT
            if timeout is None
            else float(timeout)
        )

        try:

            # Use a per-request timeout. Most IPC operations remain fast at
            # SOCKET_TIMEOUT, while expensive operations such as PNG export
            # can opt into a longer wait without changing the whole protocol.
            self.socket.settimeout(
                request_timeout
            )

            # ------------------------------------------------
            # Encode newline-delimited JSON
            # ------------------------------------------------

            data = (
                json.dumps(
                    message
                )
                +
                "\n"
            ).encode(
                "utf-8"
            )

            if not quiet:
                print(
                    "BLENDGIMP: Sending "
                    f"{message.get('type')}"
                )

            self.socket.sendall(
                data
            )

            # ------------------------------------------------
            # Wait for one complete JSON response
            # ------------------------------------------------

            response = (
                self._receive_message()
            )

            if not quiet:
                print(
                    "BLENDGIMP: Received "
                    f"{response.get('type')}"
                )

            return response

        except Exception:

            self.disconnect()

            raise

        finally:

            # Restore the normal socket timeout after a successful long
            # operation. If the request failed, disconnect() has already
            # cleared the socket.
            if self.socket is not None:

                try:
                    self.socket.settimeout(
                        previous_timeout
                    )
                except Exception:
                    pass

    # ========================================================
    # BINARY PAYLOAD REQUEST
    # ========================================================

    def _send_binary_request(
        self,
        message,
        binary_payload,
        timeout
    ):
        """
        Send one JSON header followed immediately by an exact raw binary
        payload, then receive a normal JSON response.
        """

        if not self.socket:
            raise RuntimeError(
                "BlendGimp is not connected to GIMP"
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

        previous_timeout = (
            self.socket.gettimeout()
        )

        try:

            self.socket.settimeout(
                float(
                    timeout
                )
            )

            header = dict(
                message
            )

            header[
                "binary_payload"
            ] = True

            header[
                "binary_length"
            ] = len(
                binary_payload
            )

            encoded_header = (
                json.dumps(
                    header
                )
                +
                "\n"
            ).encode(
                "utf-8"
            )

            print(
                "BLENDGIMP: Sending "
                f"{message.get('type')} "
                f"with {len(binary_payload)} raw bytes"
            )

            self.socket.sendall(
                encoded_header
            )

            if binary_payload:

                self.socket.sendall(
                    binary_payload
                )

            response = (
                self._receive_message()
            )

            print(
                "BLENDGIMP: Received "
                f"{response.get('type')}"
            )

            return response

        except Exception:

            self.disconnect()

            raise

        finally:

            if self.socket is not None:

                try:
                    self.socket.settimeout(
                        previous_timeout
                    )
                except Exception:
                    pass


    # ========================================================
    # BINARY RESPONSE REQUEST
    # ========================================================

    def _request_binary(
        self,
        message,
        timeout
    ):
        """
        Send a normal JSON command, then receive:
          1. one newline-delimited JSON response header
          2. exactly `binary_length` raw bytes

        Any bytes read beyond either boundary are preserved in
        `self.receive_buffer` for the next protocol operation.
        """

        if not self.socket:
            raise RuntimeError(
                "BlendGimp is not connected to GIMP"
            )

        previous_timeout = (
            self.socket.gettimeout()
        )

        try:
            self.socket.settimeout(
                float(timeout)
            )

            data = (
                json.dumps(
                    message
                )
                +
                "\n"
            ).encode(
                "utf-8"
            )

            print(
                "BLENDGIMP: Sending "
                f"{message.get('type')}"
            )

            self.socket.sendall(
                data
            )

            response = (
                self._receive_message()
            )

            print(
                "BLENDGIMP: Received "
                f"{response.get('type')}"
            )

            if response.get(
                "type"
            ) == "ERROR":
                return response

            binary_length = int(
                response.get(
                    "binary_length",
                    0
                )
            )

            if binary_length < 0:
                raise RuntimeError(
                    "GIMP returned a negative binary payload length"
                )

            if not response.get(
                "binary_payload",
                False
            ):
                raise RuntimeError(
                    "GIMP response did not declare a binary payload"
                )

            response[
                "pixels_raw"
            ] = self._receive_exact_bytes(
                binary_length
            )

            return response

        except Exception:
            self.disconnect()
            raise

        finally:
            if self.socket is not None:
                try:
                    self.socket.settimeout(
                        previous_timeout
                    )
                except Exception:
                    pass

    def _receive_exact_bytes(
        self,
        byte_count
    ):
        """
        Receive exactly *byte_count* bytes, first consuming any data that the
        JSON-header receive already read into `self.receive_buffer`.
        """

        byte_count = int(
            byte_count
        )

        if byte_count <= 0:
            return b""

        result = bytearray()

        if self.receive_buffer:
            take = min(
                byte_count,
                len(
                    self.receive_buffer
                )
            )

            result.extend(
                self.receive_buffer[
                    :take
                ]
            )

            self.receive_buffer = (
                self.receive_buffer[
                    take:
                ]
            )

        while len(
            result
        ) < byte_count:
            remaining = (
                byte_count
                - len(
                    result
                )
            )

            chunk = self.socket.recv(
                min(
                    65536,
                    remaining
                )
            )

            if not chunk:
                raise ConnectionError(
                    "GIMP closed the connection during a binary payload"
                )

            result.extend(
                chunk
            )

        return bytes(
            result
        )

    # ========================================================
    # RECEIVE MESSAGE
    # ========================================================

    def _receive_message(self):
        """
        Receive one newline-delimited JSON response.

        Direct RGBA responses can be several megabytes, so use a bytearray and
        64 KiB socket reads rather than repeatedly concatenating immutable
        Python bytes.
        """

        incoming = bytearray(
            self.receive_buffer
        )

        self.receive_buffer = b""

        while True:
            newline_index = incoming.find(
                b"\n"
            )

            if newline_index >= 0:
                raw_message = bytes(
                    incoming[:newline_index]
                )

                self.receive_buffer = bytes(
                    incoming[newline_index + 1:]
                )

                break

            chunk = self.socket.recv(
                65536
            )

            if not chunk:
                raise ConnectionError(
                    "GIMP closed the BlendGimp connection"
                )

            incoming.extend(chunk)

        text = raw_message.decode(
            "utf-8"
        )

        return json.loads(text)

    # ========================================================
    # PING
    # ========================================================

    def ping(
        self,
        quiet=False
    ):

        response = self.request(
            {
                "type": "PING",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
            },
            quiet=quiet
        )

        response_type = str(
            response.get(
                "type",
                ""
            )
        ).upper()

        if response_type != "PONG":

            raise RuntimeError(
                "GIMP did not return PONG"
            )

        return response

    # ========================================================
    # STATUS
    # ========================================================

    def get_status(self):

        response = self.request(
            {
                "type": "STATUS",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
            }
        )

        return response

    # ========================================================
    # CREATE IMAGE
    # ========================================================

    def create_image(
        self,
        name,
        width,
        height,
        image_format="RGBA",
        background="TRANSPARENT",
        background_color=(0.0, 0.0, 0.0, 1.0),
        layer_name="BaseColor",
    ):
        """Create a new GIMP image owned and initialized by Blender."""

        name = str(name or "BlendGimp Texture").strip()
        layer_name = str(layer_name or "BaseColor").strip()
        width = int(width)
        height = int(height)
        image_format = str(image_format or "RGBA").upper()
        background = str(background or "TRANSPARENT").upper()

        if not name:
            raise ValueError("Image name cannot be empty")

        if not layer_name:
            raise ValueError("Initial layer name cannot be empty")

        if not 1 <= width <= 32768 or not 1 <= height <= 32768:
            raise ValueError("Image dimensions must be between 1 and 32768")

        if image_format not in {"RGB", "RGBA"}:
            raise ValueError("Image format must be RGB or RGBA")

        if background not in {"TRANSPARENT", "SOLID"}:
            raise ValueError("Background must be TRANSPARENT or SOLID")

        if image_format == "RGB" and background == "TRANSPARENT":
            raise ValueError("RGB images require a solid background")

        try:
            color = [
                max(0.0, min(1.0, float(component)))
                for component in background_color
            ]
        except (TypeError, ValueError):
            raise ValueError("Background color must contain four numbers")

        if len(color) != 4:
            raise ValueError("Background color must contain four numbers")

        request_id = self._next_request_id("create-image")

        response = self.request(
            {
                "type": "CREATE_IMAGE",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "request_id": request_id,
                "name": name,
                "width": width,
                "height": height,
                "format": image_format,
                "background": background,
                "background_color": color,
                "layer_name": layer_name,
            },
            timeout=CREATE_IMAGE_TIMEOUT,
        )

        response_type = str(response.get("type", "")).upper()

        if response_type == "ERROR":
            raise RuntimeError(
                str(response.get("error", "GIMP could not create the image"))
            )

        if response_type != "IMAGE_CREATED" or not response.get("ok", False):
            raise RuntimeError("GIMP did not return IMAGE_CREATED")

        image_id = int(response.get("image_id", -1))
        layer_id = int(response.get("layer_id", -1))

        if image_id < 0 or layer_id < 0:
            raise RuntimeError("GIMP returned invalid image or layer IDs")

        if (
            int(response.get("width", -1)) != width
            or int(response.get("height", -1)) != height
        ):
            raise RuntimeError("GIMP returned unexpected image dimensions")

        return response

    # ========================================================
    # GET IMAGES
    # ========================================================

    def get_images(self):
        """
        Read the list of images currently open in GIMP.

        GET_IMAGES is read-only.
        """

        request_id = (
            self._next_request_id(
                "get-images"
            )
        )

        response = self.request(
            {
                "type": "GET_IMAGES",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "request_id": request_id,
            }
        )

        response_type = str(
            response.get(
                "type",
                ""
            )
        ).upper()

        if response_type == "ERROR":

            raise RuntimeError(
                str(
                    response.get(
                        "error",
                        "GIMP returned an error"
                    )
                )
            )

        if response_type != "IMAGES":

            raise RuntimeError(
                "GIMP did not return IMAGES"
            )

        if not response.get(
            "ok",
            False
        ):

            raise RuntimeError(
                "GIMP GET_IMAGES request failed"
            )

        images = response.get(
            "images",
            []
        )

        if not isinstance(
            images,
            list
        ):

            raise RuntimeError(
                "GIMP returned an invalid images list"
            )

        return response


    # ========================================================
    # GET IMAGE LAYERS
    # ========================================================

    def get_image_layers(
        self,
        image_id
    ):
        """
        Read the complete GIMP layer tree for one open image.

        GET_IMAGE_LAYERS is read-only.
        """

        try:

            image_id = int(
                image_id
            )

        except (
            TypeError,
            ValueError
        ):

            raise ValueError(
                "image_id must be an integer"
            )

        request_id = (
            self._next_request_id(
                "get-image-layers"
            )
        )

        response = self.request(
            {
                "type": "GET_IMAGE_LAYERS",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": image_id,
                "request_id": request_id,
            }
        )

        response_type = str(
            response.get(
                "type",
                ""
            )
        ).upper()

        if response_type == "ERROR":

            raise RuntimeError(
                str(
                    response.get(
                        "error",
                        "GIMP returned an error"
                    )
                )
            )

        if response_type != "IMAGE_LAYERS":

            raise RuntimeError(
                "GIMP did not return IMAGE_LAYERS"
            )

        if not response.get(
            "ok",
            False
        ):

            raise RuntimeError(
                "GIMP GET_IMAGE_LAYERS request failed"
            )

        returned_image_id = int(
            response.get(
                "image_id",
                -1
            )
        )

        if returned_image_id != image_id:

            raise RuntimeError(
                "GIMP returned layers for the wrong image"
            )

        layers = response.get(
            "layers",
            []
        )

        if not isinstance(
            layers,
            list
        ):

            raise RuntimeError(
                "GIMP returned an invalid layer list"
            )

        return response


    # ========================================================
    # SET ACTIVE LAYER
    # ========================================================

    def set_active_layer(
        self,
        image_id,
        layer_id
    ):

        image_id = int(image_id)
        layer_id = int(layer_id)

        response = self.request(
            {
                "type": "SET_ACTIVE_LAYER",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": image_id,
                "layer_id": layer_id,
                "request_id": self._next_request_id(
                    "set-active-layer"
                ),
            }
        )

        self._validate_write_response(
            response,
            expected_type="ACTIVE_LAYER_SET"
        )

        return response

    # ========================================================
    # SET LAYER VISIBILITY
    # ========================================================

    def set_layer_visibility(
        self,
        image_id,
        layer_id,
        visible
    ):

        image_id = int(image_id)
        layer_id = int(layer_id)

        if not isinstance(
            visible,
            bool
        ):
            raise TypeError(
                "visible must be a bool"
            )

        response = self.request(
            {
                "type": "SET_LAYER_VISIBILITY",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": image_id,
                "layer_id": layer_id,
                "visible": visible,
                "request_id": self._next_request_id(
                    "set-layer-visibility"
                ),
            }
        )

        self._validate_write_response(
            response,
            expected_type="LAYER_VISIBILITY_SET"
        )

        return response

    # ========================================================
    # SET LAYER OPACITY
    # ========================================================

    def set_layer_opacity(
        self,
        image_id,
        layer_id,
        opacity
    ):

        image_id = int(image_id)
        layer_id = int(layer_id)
        opacity = float(opacity)

        opacity = max(
            0.0,
            min(
                100.0,
                opacity
            )
        )

        response = self.request(
            {
                "type": "SET_LAYER_OPACITY",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": image_id,
                "layer_id": layer_id,
                "opacity": opacity,
                "request_id": self._next_request_id(
                    "set-layer-opacity"
                ),
            }
        )

        self._validate_write_response(
            response,
            expected_type="LAYER_OPACITY_SET"
        )

        return response

    # ========================================================
    # WRITE RESPONSE VALIDATION
    # ========================================================

    @staticmethod
    def _validate_write_response(
        response,
        expected_type
    ):

        response_type = str(
            response.get(
                "type",
                ""
            )
        ).upper()

        if response_type == "ERROR":

            raise RuntimeError(
                str(
                    response.get(
                        "error",
                        "GIMP returned an error"
                    )
                )
            )

        if response_type != expected_type:

            raise RuntimeError(
                f"GIMP did not return {expected_type}"
            )

        if not response.get(
            "ok",
            False
        ):

            raise RuntimeError(
                f"GIMP {expected_type} request failed"
            )


    # ========================================================
    # ADD LAYER
    # ========================================================

    def add_layer(
        self,
        image_id,
        name="New Layer"
    ):

        response = self.request(
            {
                "type": "ADD_LAYER",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": int(image_id),
                "name": str(name),
                "request_id": self._next_request_id(
                    "add-layer"
                ),
            }
        )

        self._validate_write_response(
            response,
            expected_type="LAYER_ADDED"
        )

        return response

    # ========================================================
    # DELETE LAYER
    # ========================================================

    def delete_layer(
        self,
        image_id,
        layer_id
    ):

        response = self.request(
            {
                "type": "DELETE_LAYER",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": int(image_id),
                "layer_id": int(layer_id),
                "request_id": self._next_request_id(
                    "delete-layer"
                ),
            }
        )

        self._validate_write_response(
            response,
            expected_type="LAYER_DELETED"
        )

        return response

    # ========================================================
    # RENAME LAYER
    # ========================================================

    def rename_layer(
        self,
        image_id,
        layer_id,
        name
    ):

        response = self.request(
            {
                "type": "RENAME_LAYER",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": int(image_id),
                "layer_id": int(layer_id),
                "name": str(name),
                "request_id": self._next_request_id(
                    "rename-layer"
                ),
            }
        )

        self._validate_write_response(
            response,
            expected_type="LAYER_RENAMED"
        )

        return response

    # ========================================================
    # DUPLICATE LAYER
    # ========================================================

    def duplicate_layer(
        self,
        image_id,
        layer_id
    ):

        response = self.request(
            {
                "type": "DUPLICATE_LAYER",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": int(image_id),
                "layer_id": int(layer_id),
                "request_id": self._next_request_id(
                    "duplicate-layer"
                ),
            }
        )

        self._validate_write_response(
            response,
            expected_type="LAYER_DUPLICATED"
        )

        return response

    # ========================================================
    # REORDER LAYER
    # ========================================================

    def reorder_layer(
        self,
        image_id,
        layer_id,
        direction
    ):

        direction = str(
            direction
        ).upper()

        if direction not in {
            "UP",
            "DOWN"
        }:
            raise ValueError(
                "direction must be UP or DOWN"
            )

        response = self.request(
            {
                "type": "REORDER_LAYER",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": int(image_id),
                "layer_id": int(layer_id),
                "direction": direction,
                "request_id": self._next_request_id(
                    "reorder-layer"
                ),
            }
        )

        self._validate_write_response(
            response,
            expected_type="LAYER_REORDERED"
        )

        return response

    # ========================================================
    # MOVE LAYER / GROUP
    # ========================================================

    def move_layer(
        self,
        image_id,
        layer_id,
        parent_id=None
    ):

        payload = {
            "type": "MOVE_LAYER",
            "component": "blender",
            "protocol": PROTOCOL_VERSION,
            "image_id": int(image_id),
            "layer_id": int(layer_id),
            "request_id": self._next_request_id(
                "move-layer"
            ),
        }

        if parent_id is None:
            payload["parent_id"] = None
        else:
            parent_id = int(parent_id)

            payload["parent_id"] = (
                None
                if parent_id < 0
                else parent_id
            )

        response = self.request(
            payload
        )

        self._validate_write_response(
            response,
            expected_type="LAYER_MOVED"
        )

        return response


    # ========================================================
    # CREATE GROUP
    # ========================================================

    def create_group(
        self,
        image_id,
        name="Layer Group"
    ):

        response = self.request(
            {
                "type": "CREATE_GROUP",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": int(image_id),
                "name": str(name),
                "request_id": self._next_request_id(
                    "create-group"
                ),
            }
        )

        self._validate_write_response(
            response,
            expected_type="GROUP_CREATED"
        )

        return response

    # ========================================================
    # MERGE LAYER DOWN
    # ========================================================

    def merge_layer_down(
        self,
        image_id,
        layer_id
    ):

        response = self.request(
            {
                "type": "MERGE_LAYER_DOWN",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": int(image_id),
                "layer_id": int(layer_id),
                "request_id": self._next_request_id(
                    "merge-layer-down"
                ),
            }
        )

        self._validate_write_response(
            response,
            expected_type="LAYER_MERGED_DOWN"
        )

        return response

    # ========================================================
    # SET LAYER LOCK
    # ========================================================

    def set_layer_lock(
        self,
        image_id,
        layer_id,
        lock_type,
        locked
    ):

        lock_type = str(
            lock_type
        ).upper()

        if lock_type not in {
            "CONTENT",
            "POSITION",
            "ALPHA"
        }:
            raise ValueError(
                "lock_type must be CONTENT, POSITION, or ALPHA"
            )

        if not isinstance(
            locked,
            bool
        ):
            raise TypeError(
                "locked must be a bool"
            )

        response = self.request(
            {
                "type": "SET_LAYER_LOCK",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": int(image_id),
                "layer_id": int(layer_id),
                "lock_type": lock_type,
                "locked": locked,
                "request_id": self._next_request_id(
                    "set-layer-lock"
                ),
            }
        )

        self._validate_write_response(
            response,
            expected_type="LAYER_LOCK_SET"
        )

        return response

    # ========================================================
    # SET LAYER MODE
    # ========================================================

    def set_layer_mode(
        self,
        image_id,
        layer_id,
        mode
    ):

        mode = str(
            mode
        ).upper()

        response = self.request(
            {
                "type": "SET_LAYER_MODE",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": int(image_id),
                "layer_id": int(layer_id),
                "mode": mode,
                "request_id": self._next_request_id(
                    "set-layer-mode"
                ),
            }
        )

        self._validate_write_response(
            response,
            expected_type="LAYER_MODE_SET"
        )

        return response


    # ========================================================
    # DIRECT GIMP BRUSH
    # ========================================================

    def get_brush_state(
        self
    ):
        response = self.request(
            {
                "type": "GET_BRUSH_STATE",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "request_id": self._next_request_id(
                    "get-brush-state"
                ),
            },
            timeout=5.0
        )

        if response.get(
            "type"
        ) == "ERROR":
            raise RuntimeError(
                response.get(
                    "error",
                    "GIMP returned an error"
                )
            )

        if response.get(
            "type"
        ) != "BRUSH_STATE":
            raise RuntimeError(
                "Unexpected GET_BRUSH_STATE response: "
                f"{response.get('type')}"
            )

        if not response.get(
            "ok",
            False
        ):
            raise RuntimeError(
                response.get(
                    "error",
                    "GET_BRUSH_STATE failed"
                )
            )

        return response

    def begin_paint_stroke(
        self,
        image_id,
        layer_id,
        stroke_id
    ):
        response = self.request(
            {
                "type": "BEGIN_PAINT_STROKE",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": int(
                    image_id
                ),
                "layer_id": int(
                    layer_id
                ),
                "stroke_id": str(
                    stroke_id
                ),
                "request_id": self._next_request_id(
                    "begin-paint-stroke"
                ),
            },
            timeout=5.0
        )

        if response.get(
            "type"
        ) == "ERROR":
            raise RuntimeError(
                response.get(
                    "error",
                    "GIMP returned an error"
                )
            )

        if response.get(
            "type"
        ) != "PAINT_STROKE_BEGUN":
            raise RuntimeError(
                "Unexpected BEGIN_PAINT_STROKE response: "
                f"{response.get('type')}"
            )

        return response

    def paint_stroke_chunk(
        self,
        image_id,
        layer_id,
        stroke_id,
        strokes
    ):
        coordinates = [
            float(
                value
            )
            for value in strokes
        ]

        if len(
            coordinates
        ) < 2:
            return {
                "type": "PAINT_STROKE_CHUNKED",
                "ok": True,
                "point_count": 0,
            }

        response = self.request(
            {
                "type": "PAINT_STROKE_CHUNK",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": int(
                    image_id
                ),
                "layer_id": int(
                    layer_id
                ),
                "stroke_id": str(
                    stroke_id
                ),
                "strokes": coordinates,
                "request_id": self._next_request_id(
                    "paint-stroke-chunk"
                ),
            },
            timeout=10.0
        )

        if response.get(
            "type"
        ) == "ERROR":
            raise RuntimeError(
                response.get(
                    "error",
                    "GIMP returned an error"
                )
            )

        if response.get(
            "type"
        ) != "PAINT_STROKE_CHUNKED":
            raise RuntimeError(
                "Unexpected PAINT_STROKE_CHUNK response: "
                f"{response.get('type')}"
            )

        return response

    def paint_stroke_segments_chunk(
        self,
        image_id,
        layer_id,
        stroke_id,
        segments
    ):
        """Send several disconnected, topology-safe segments in one packet."""

        normalized_segments = []

        for segment in segments:
            coordinates = [
                float(
                    value
                )
                for value in segment
            ]

            if len(
                coordinates
            ) >= 2:
                normalized_segments.append(
                    coordinates
                )

        if not normalized_segments:
            return {
                "type": "PAINT_STROKE_CHUNKED",
                "ok": True,
                "point_count": 0,
                "segment_count": 0,
            }

        response = self.request(
            {
                "type": "PAINT_STROKE_CHUNK",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": int(
                    image_id
                ),
                "layer_id": int(
                    layer_id
                ),
                "stroke_id": str(
                    stroke_id
                ),
                "segments": normalized_segments,
                "request_id": self._next_request_id(
                    "paint-stroke-segments-chunk"
                ),
            },
            timeout=10.0
        )

        if response.get(
            "type"
        ) == "ERROR":
            raise RuntimeError(
                response.get(
                    "error",
                    "GIMP returned an error"
                )
            )

        if response.get(
            "type"
        ) != "PAINT_STROKE_CHUNKED":
            raise RuntimeError(
                "Unexpected PAINT_STROKE_CHUNK response: "
                f"{response.get('type')}"
            )

        return response

    def end_paint_stroke(
        self,
        stroke_id
    ):
        response = self.request(
            {
                "type": "END_PAINT_STROKE",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "stroke_id": str(
                    stroke_id
                ),
                "request_id": self._next_request_id(
                    "end-paint-stroke"
                ),
            },
            timeout=5.0
        )

        if response.get(
            "type"
        ) == "ERROR":
            raise RuntimeError(
                response.get(
                    "error",
                    "GIMP returned an error"
                )
            )

        if response.get(
            "type"
        ) != "PAINT_STROKE_ENDED":
            raise RuntimeError(
                "Unexpected END_PAINT_STROKE response: "
                f"{response.get('type')}"
            )

        return response

    def paint_stroke(
        self,
        image_id,
        layer_id,
        strokes
    ):
        coordinates = [
            float(
                value
            )
            for value in strokes
        ]

        if len(
            coordinates
        ) < 2:
            raise RuntimeError(
                "Direct GIMP stroke requires at least one x/y point"
            )

        if (
            len(
                coordinates
            )
            % 2
            != 0
        ):
            raise RuntimeError(
                "Direct GIMP stroke coordinate count must be even"
            )

        response = self.request(
            {
                "type": "PAINT_STROKE",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": int(
                    image_id
                ),
                "layer_id": int(
                    layer_id
                ),
                "strokes": coordinates,
                "request_id": self._next_request_id(
                    "paint-stroke"
                ),
            },
            timeout=15.0
        )

        if response.get(
            "type"
        ) == "ERROR":
            raise RuntimeError(
                response.get(
                    "error",
                    "GIMP returned an error"
                )
            )

        if response.get(
            "type"
        ) != "STROKE_PAINTED":
            raise RuntimeError(
                "Unexpected PAINT_STROKE response: "
                f"{response.get('type')}"
            )

        if not response.get(
            "ok",
            False
        ):
            raise RuntimeError(
                response.get(
                    "error",
                    "PAINT_STROKE failed"
                )
            )

        return response


    # ========================================================
    # ENSURE BLENDGIMP PAINT LAYER
    # ========================================================

    def ensure_paint_layer(
        self,
        image_id,
        name="BlendGimp Paint"
    ):
        response = self.request(
            {
                "type": "ENSURE_PAINT_LAYER",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": int(
                    image_id
                ),
                "name": str(
                    name
                ),
                "request_id": self._next_request_id(
                    "ensure-paint-layer"
                ),
            },
            timeout=10.0
        )

        if response.get(
            "type"
        ) == "ERROR":
            raise RuntimeError(
                response.get(
                    "error",
                    "GIMP returned an error"
                )
            )

        if response.get(
            "type"
        ) != "PAINT_LAYER_READY":
            raise RuntimeError(
                "Unexpected ENSURE_PAINT_LAYER response: "
                f"{response.get('type')}"
            )

        if not response.get(
            "ok",
            False
        ):
            raise RuntimeError(
                response.get(
                    "error",
                    "ENSURE_PAINT_LAYER failed"
                )
            )

        return response


    # ========================================================
    # SET GIMP LAYER PIXELS
    # ========================================================

    def set_layer_pixels_binary(
        self,
        image_id,
        layer_id,
        x,
        y,
        width,
        height,
        raw_pixels
    ):
        """
        Push top-left-origin straight RGBA8 pixels from Blender into a GIMP
        layer using a raw binary request frame.
        """

        image_id = int(
            image_id
        )

        layer_id = int(
            layer_id
        )

        width = int(
            width
        )

        height = int(
            height
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
                "Blender outbound pixel byte count mismatch. "
                f"Expected {expected_length}, got {len(raw_pixels)}"
            )

        response = self._send_binary_request(
            {
                "type": "SET_LAYER_PIXELS_BINARY",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": image_id,
                "layer_id": layer_id,
                "x": int(
                    x
                ),
                "y": int(
                    y
                ),
                "width": width,
                "height": height,
                "pixel_format": "R'G'B'A u8",
                "origin": "top-left",
                "request_id": self._next_request_id(
                    "set-layer-pixels-binary"
                ),
            },
            raw_pixels,
            timeout=30.0
        )

        if response.get(
            "type"
        ) == "ERROR":

            raise RuntimeError(
                response.get(
                    "error",
                    "GIMP returned an error"
                )
            )

        if response.get(
            "type"
        ) != "LAYER_PIXELS_SET":

            raise RuntimeError(
                "Unexpected SET_LAYER_PIXELS_BINARY response: "
                f"{response.get('type')}"
            )

        if not response.get(
            "ok",
            False
        ):

            raise RuntimeError(
                response.get(
                    "error",
                    "SET_LAYER_PIXELS_BINARY failed"
                )
            )

        return response


    # ========================================================
    # GET GIMP DIRTY PIXEL REGION
    # ========================================================

    def get_image_dirty_pixels_binary(
        self,
        image_id
    ):
        """
        Request only the changed visible-composite rectangle as raw RGBA8
        bytes, avoiding base64/JSON expansion.
        """

        image_id = int(
            image_id
        )

        response = self._request_binary(
            {
                "type": "GET_IMAGE_DIRTY_PIXELS_BINARY",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": image_id,
                "request_id": self._next_request_id(
                    "get-image-dirty-pixels-binary"
                ),
            },
            timeout=GET_IMAGE_DIRTY_PIXELS_TIMEOUT
        )

        if response.get(
            "type"
        ) == "ERROR":
            raise RuntimeError(
                response.get(
                    "error",
                    "GIMP returned an error"
                )
            )

        if response.get(
            "type"
        ) != "IMAGE_DIRTY_PIXELS_BINARY":
            raise RuntimeError(
                "Unexpected binary dirty-pixel response: "
                f"{response.get('type')}"
            )

        if not response.get(
            "ok",
            False
        ):
            raise RuntimeError(
                response.get(
                    "error",
                    "GET_IMAGE_DIRTY_PIXELS_BINARY failed"
                )
            )

        if int(
            response.get(
                "image_id",
                -1
            )
        ) != image_id:
            raise RuntimeError(
                "GIMP returned binary dirty pixels for the wrong image"
            )

        if response.get(
            "changed",
            False
        ):
            expected = int(
                response.get(
                    "byte_length",
                    -1
                )
            )

            actual = len(
                response.get(
                    "pixels_raw",
                    b""
                )
            )

            if expected != actual:
                raise RuntimeError(
                    "Binary dirty payload length mismatch. "
                    f"Expected {expected}, got {actual}"
                )

        return response


    def get_image_dirty_pixels(
        self,
        image_id
    ):
        """
        Request only the changed visible-composite bounding rectangle since
        BlendGimp's previous full/dirty pixel synchronization.
        """

        image_id = int(
            image_id
        )

        response = self.request(
            {
                "type": "GET_IMAGE_DIRTY_PIXELS",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": image_id,
                "request_id": self._next_request_id(
                    "get-image-dirty-pixels"
                ),
            },
            timeout=GET_IMAGE_DIRTY_PIXELS_TIMEOUT
        )

        if response.get(
            "type"
        ) == "ERROR":
            raise RuntimeError(
                response.get(
                    "error",
                    "GIMP returned an error"
                )
            )

        if response.get(
            "type"
        ) != "IMAGE_DIRTY_PIXELS":
            raise RuntimeError(
                "Unexpected GET_IMAGE_DIRTY_PIXELS response: "
                f"{response.get('type')}"
            )

        if not response.get(
            "ok",
            False
        ):
            raise RuntimeError(
                response.get(
                    "error",
                    "GET_IMAGE_DIRTY_PIXELS failed"
                )
            )

        returned_image_id = int(
            response.get(
                "image_id",
                -1
            )
        )

        if returned_image_id != image_id:
            raise RuntimeError(
                "GIMP returned dirty pixels for the wrong image"
            )

        width = int(
            response.get(
                "width",
                0
            )
        )

        height = int(
            response.get(
                "height",
                0
            )
        )

        if width <= 0 or height <= 0:
            raise RuntimeError(
                "GIMP returned invalid dirty-pixel image dimensions"
            )

        if response.get(
            "changed",
            False
        ):
            region_width = int(
                response.get(
                    "region_width",
                    0
                )
            )

            region_height = int(
                response.get(
                    "region_height",
                    0
                )
            )

            if (
                region_width <= 0
                or region_height <= 0
            ):
                raise RuntimeError(
                    "GIMP returned an invalid dirty rectangle"
                )

            if str(
                response.get(
                    "encoding",
                    ""
                )
            ).lower() != "base64":
                raise RuntimeError(
                    "Unsupported dirty-pixel encoding"
                )

            if not response.get(
                "pixels_b64"
            ):
                raise RuntimeError(
                    "GIMP returned an empty dirty-pixel payload"
                )

        return response


    # ========================================================
    # GET FULL GIMP COMPOSITE PIXELS
    # ========================================================

    def get_image_pixels_binary(
        self,
        image_id
    ):
        """
        Request GIMP's full visible composite as raw RGBA8 bytes following a
        compact JSON header.
        """

        image_id = int(
            image_id
        )

        response = self._request_binary(
            {
                "type": "GET_IMAGE_PIXELS_BINARY",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": image_id,
                "request_id": self._next_request_id(
                    "get-image-pixels-binary"
                ),
            },
            timeout=GET_IMAGE_PIXELS_TIMEOUT
        )

        if response.get(
            "type"
        ) == "ERROR":
            raise RuntimeError(
                response.get(
                    "error",
                    "GIMP returned an error"
                )
            )

        if response.get(
            "type"
        ) != "IMAGE_PIXELS_BINARY":
            raise RuntimeError(
                "Unexpected binary full-pixel response: "
                f"{response.get('type')}"
            )

        if not response.get(
            "ok",
            False
        ):
            raise RuntimeError(
                response.get(
                    "error",
                    "GET_IMAGE_PIXELS_BINARY failed"
                )
            )

        if int(
            response.get(
                "image_id",
                -1
            )
        ) != image_id:
            raise RuntimeError(
                "GIMP returned binary pixels for the wrong image"
            )

        expected = int(
            response.get(
                "byte_length",
                -1
            )
        )

        actual = len(
            response.get(
                "pixels_raw",
                b""
            )
        )

        if expected != actual:
            raise RuntimeError(
                "Binary full-image payload length mismatch. "
                f"Expected {expected}, got {actual}"
            )

        return response


    def get_image_pixels(
        self,
        image_id
    ):
        """
        Request GIMP's current visible composite as full-resolution RGBA8
        pixels over the existing IPC socket. No temporary image file is used.
        """

        image_id = int(image_id)

        response = self.request(
            {
                "type": "GET_IMAGE_PIXELS",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": image_id,
                "request_id": self._next_request_id(
                    "get-image-pixels"
                ),
            },
            timeout=GET_IMAGE_PIXELS_TIMEOUT
        )

        if response.get("type") == "ERROR":
            raise RuntimeError(
                response.get(
                    "error",
                    "GIMP returned an error"
                )
            )

        if response.get("type") != "IMAGE_PIXELS":
            raise RuntimeError(
                "Unexpected GET_IMAGE_PIXELS response: "
                f"{response.get('type')}"
            )

        if not response.get("ok", False):
            raise RuntimeError(
                response.get(
                    "error",
                    "GET_IMAGE_PIXELS failed"
                )
            )

        returned_image_id = int(
            response.get(
                "image_id",
                -1
            )
        )

        if returned_image_id != image_id:
            raise RuntimeError(
                "GIMP returned pixels for the wrong image"
            )

        width = int(response.get("width", 0))
        height = int(response.get("height", 0))

        if width <= 0 or height <= 0:
            raise RuntimeError(
                "GIMP returned invalid direct-pixel dimensions"
            )

        if str(
            response.get(
                "encoding",
                ""
            )
        ).lower() != "base64":
            raise RuntimeError(
                "Unsupported direct-pixel encoding"
            )

        if not response.get("pixels_b64"):
            raise RuntimeError(
                "GIMP returned no direct pixel data"
            )

        return response


    # ========================================================
    # GET GIMP IMAGE STATE
    # ========================================================

    def get_image_state(
        self,
        image_id
    ):
        """
        Read BlendGimp's lightweight visual revision for a GIMP image without
        exporting its full composite.
        """

        image_id = int(
            image_id
        )

        response = self.request(
            {
                "type": "GET_IMAGE_STATE",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": image_id,
                "request_id": self._next_request_id(
                    "get-image-state"
                ),
            },
            timeout=IMAGE_STATE_TIMEOUT,
            quiet=True
        )

        response_type = str(
            response.get(
                "type",
                ""
            )
        ).upper()

        if response_type == "IMAGE_NOT_FOUND":
            raise GimpImageNotFoundError(
                response.get(
                    "error",
                    f"GIMP image ID {image_id} is no longer open"
                )
            )

        if response_type == "ERROR":
            raise RuntimeError(
                response.get(
                    "error",
                    "GIMP returned an error"
                )
            )

        if response_type != "IMAGE_STATE":
            raise RuntimeError(
                "Unexpected GET_IMAGE_STATE response: "
                f"{response.get('type')}"
            )

        if not response.get(
            "ok",
            False
        ):
            raise RuntimeError(
                response.get(
                    "error",
                    "GET_IMAGE_STATE failed"
                )
            )

        returned_image_id = int(
            response.get(
                "image_id",
                -1
            )
        )

        if returned_image_id != image_id:
            raise RuntimeError(
                "GIMP returned state for the wrong image"
            )

        return response


    # ========================================================
    # SHUT DOWN GIMP ENGINE
    # ========================================================

    def shutdown_engine(
        self,
        force=False
    ):
        """
        Ask the connected GIMP application to close itself cleanly.

        GIMP may refuse a non-forced request when an image has unsaved
        changes. BlendGimp never silently discards those changes.
        """

        response = self.request(
            {
                "type": "SHUTDOWN_ENGINE",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "force": bool(force),
                "request_id": self._next_request_id(
                    "shutdown-engine"
                ),
            },
            timeout=ENGINE_SHUTDOWN_TIMEOUT
        )

        response_type = str(
            response.get(
                "type",
                ""
            )
        ).upper()

        if response_type == "ENGINE_SHUTDOWN_REFUSED":
            raise GimpEngineShutdownRefusedError(
                response.get(
                    "error",
                    "GIMP refused the shutdown request"
                )
            )

        if response_type == "ERROR":
            raise RuntimeError(
                response.get(
                    "error",
                    "GIMP shutdown request failed"
                )
            )

        if (
            response_type != "ENGINE_SHUTDOWN_ACCEPTED"
            or not response.get(
                "ok",
                False
            )
        ):
            raise RuntimeError(
                "Unexpected SHUTDOWN_ENGINE response: "
                f"{response.get('type')}"
            )

        return response


    # ========================================================
    # EXPORT GIMP COMPOSITE
    # ========================================================

    def export_composite(
        self,
        image_id
    ):
        """
        Ask GIMP to export its current visible image composite to the local
        BlendGimp cache and return metadata for that exported PNG.
        """

        image_id = int(image_id)

        response = self.request(
            {
                "type": "EXPORT_COMPOSITE",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
                "image_id": image_id,
                "request_id": self._next_request_id(
                    "export-composite"
                ),
            },
            timeout=EXPORT_COMPOSITE_TIMEOUT
        )

        self._validate_write_response(
            response,
            expected_type="COMPOSITE_EXPORTED"
        )

        returned_image_id = int(
            response.get(
                "image_id",
                -1
            )
        )

        if returned_image_id != image_id:
            raise RuntimeError(
                "GIMP returned a composite for the wrong image"
            )

        path = str(
            response.get(
                "path",
                ""
            )
        )

        if not path:
            raise RuntimeError(
                "GIMP did not return a composite cache path"
            )

        return response

    # ========================================================
    # DISCONNECT
    # ========================================================

    def disconnect(self):

        if self.socket:

            try:

                self.socket.shutdown(
                    socket.SHUT_RDWR
                )

            except Exception:

                pass

            try:

                self.socket.close()

            except Exception:

                pass

        self.socket = None
        self.receive_buffer = b""

        self.remote_version = ""
        self.remote_gimp_version = ""
        self.remote_protocol = 0


# ============================================================
# GLOBAL CONNECTION
# ============================================================

connection_manager = BlendGimpConnection()
