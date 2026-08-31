import json
import socket


# ============================================================
# BlendGimp IPC
# ============================================================

HOST = "127.0.0.1"
PORT = 8765

PROTOCOL_VERSION = 1
BLENDGIMP_VERSION = "0.1.0"

SOCKET_TIMEOUT = 2.0


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
        message
    ):

        if not self.socket:

            raise RuntimeError(
                "BlendGimp is not connected to GIMP"
            )

        try:

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

            print(
                "BLENDGIMP: Received "
                f"{response.get('type')}"
            )

            return response

        except Exception:

            self.disconnect()

            raise

    # ========================================================
    # RECEIVE MESSAGE
    # ========================================================

    def _receive_message(self):

        while b"\n" not in self.receive_buffer:

            chunk = self.socket.recv(
                4096
            )

            if not chunk:

                raise ConnectionError(
                    "GIMP closed the BlendGimp connection"
                )

            self.receive_buffer += chunk

        raw_message, self.receive_buffer = (
            self.receive_buffer.split(
                b"\n",
                1
            )
        )

        text = raw_message.decode(
            "utf-8"
        )

        return json.loads(
            text
        )

    # ========================================================
    # PING
    # ========================================================

    def ping(self):

        response = self.request(
            {
                "type": "PING",
                "component": "blender",
                "protocol": PROTOCOL_VERSION,
            }
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
