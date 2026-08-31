import bpy
import json
import os

from ..core import gimp_manager
from ..ipc.connection import connection_manager


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

        lock_names = []

        if layer.get(
            "lock_content",
            False
        ):
            lock_names.append(
                "Content"
            )

        if layer.get(
            "lock_position",
            False
        ):
            lock_names.append(
                "Position"
            )

        if layer.get(
            "lock_alpha",
            False
        ):
            lock_names.append(
                "Alpha"
            )

        if lock_names:

            layer_box.label(
                text=(
                    "Locks: "
                    + ", ".join(
                        lock_names
                    )
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
    bl_label = "Launch GIMP"

    bl_description = (
        "Launch the detected GIMP installation"
    )

    def execute(
        self,
        context
    ):

        scene = context.scene

        gimp_path = (
            scene.blendgimp_gimp_path
        )

        if not gimp_path:

            self.report(
                {"ERROR"},
                "GIMP has not been detected"
            )

            return {"CANCELLED"}

        if not os.path.isfile(
            gimp_path
        ):

            scene.blendgimp_gimp_detected = False
            scene.blendgimp_gimp_running = False

            self.report(
                {"ERROR"},
                "The detected GIMP executable "
                "no longer exists"
            )

            return {"CANCELLED"}

        success, pid = (
            gimp_manager.launch_gimp(
                gimp_path
            )
        )

        if success:

            scene.blendgimp_gimp_running = True

            self.report(
                {"INFO"},
                f"GIMP running PID {pid}"
            )

            return {"FINISHED"}

        scene.blendgimp_gimp_running = False

        self.report(
            {"ERROR"},
            "Could not launch GIMP"
        )

        return {"CANCELLED"}


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

        running = (
            gimp_manager.is_gimp_running()
        )

        context.scene.blendgimp_gimp_running = (
            running
        )

        if running:

            self.report(
                {"INFO"},
                "GIMP is running"
            )

        else:

            self.report(
                {"INFO"},
                "GIMP is not running"
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

            response = (
                connection_manager.connect()
            )

            scene.blendgimp_connected = True

            scene.blendgimp_protocol_version = (
                int(
                    response.get(
                        "protocol",
                        0
                    )
                )
            )

            scene.blendgimp_remote_version = (
                str(
                    response.get(
                        "blendgimp_version",
                        response.get(
                            "server",
                            ""
                        )
                    )
                )
            )

            scene.blendgimp_runtime_gimp_version = (
                str(
                    response.get(
                        "gimp_version",
                        ""
                    )
                )
            )

            clear_image_results(
                scene
            )

            self.report(
                {"INFO"},
                "BlendGimp connected to GIMP"
            )

            return {"FINISHED"}

        except Exception as exc:

            scene.blendgimp_connected = False

            clear_image_results(
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

        connection_manager.disconnect()

        context.scene.blendgimp_connected = False

        clear_image_results(
            context.scene
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
            text="GIMP"
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

            # ================================================
            # PROCESS
            # ================================================

            if scene.blendgimp_gimp_running:

                gimp_box.label(
                    text="Process Running",
                    icon="CHECKMARK"
                )

                gimp_box.operator(
                    "blendgimp.check_gimp",
                    text="Check Process"
                )

            else:

                gimp_box.label(
                    text="Process Not Running"
                )

                gimp_box.operator(
                    "blendgimp.launch_gimp",
                    text="Launch GIMP",
                    icon="PLAY"
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

        layout.operator(
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

        if scene.blendgimp_connected:

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

    BLENDGIMP_OT_check_gimp,

    BLENDGIMP_OT_connect,

    BLENDGIMP_OT_ping,

    BLENDGIMP_OT_get_images,

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

    print(
        "BLENDGIMP: Main panel registered"
    )


# ============================================================
# UNREGISTER
# ============================================================

def unregister():

    # --------------------------------------------------------
    # Close Blender's socket.
    #
    # Do not terminate GIMP.
    # --------------------------------------------------------

    connection_manager.disconnect()

    gimp_manager.clear_process_reference()

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
