import math
import time
import uuid

import bpy

from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.interpolate import poly_3d_calc

from ..ipc.connection import (
    connection_manager,
    set_direct_paint_refresh_owner,
)


BLENDGIMP_DIRECT_PAINT_LAYER_NAME = "BlendGimp Paint"

# Live chunking balances interaction latency against synchronous IPC overhead.
# GIMP Auto Sync can observe the raster while the button remains down.
LIVE_STROKE_CHUNK_POINTS = 16
LIVE_STROKE_CHUNK_INTERVAL = 0.08

# GIMP still has to acquire the visible composite for a dirty refresh, so
# viewport feedback is deliberately slower than stroke chunk transport.
# Five updates/second feels live without forcing a full composite read for
# every mouse event.
LIVE_VIEWPORT_REFRESH_INTERVAL = 0.20


def _window_region_for_area(
    area
):
    if area is None:
        return None

    for region in area.regions:
        if region.type == "WINDOW":
            return region

    return None


def _raycast_uv_point(
    context,
    window_region,
    event,
    image_width,
    image_height
):
    """
    Convert the current mouse position to a GIMP image-space point using the
    active mesh's UV map.

    Initial Stage-2 implementation intentionally raycasts the original mesh
    data so polygon indices and UV loops remain deterministic. Modifier-aware
    painting is a later geometry/projection milestone.
    """

    area = context.area

    if (
        area is None
        or area.type != "VIEW_3D"
        or window_region is None
    ):
        return None

    mouse_x = (
        event.mouse_x
        - window_region.x
    )

    mouse_y = (
        event.mouse_y
        - window_region.y
    )

    if (
        mouse_x < 0
        or mouse_y < 0
        or mouse_x >= window_region.width
        or mouse_y >= window_region.height
    ):
        return None

    rv3d = context.space_data.region_3d

    coord = (
        float(
            mouse_x
        ),
        float(
            mouse_y
        ),
    )

    ray_origin_world = (
        view3d_utils.region_2d_to_origin_3d(
            window_region,
            rv3d,
            coord
        )
    )

    ray_direction_world = (
        view3d_utils.region_2d_to_vector_3d(
            window_region,
            rv3d,
            coord
        )
    )

    obj = context.active_object

    if (
        obj is None
        or obj.type != "MESH"
    ):
        return None

    mesh = obj.data

    if (
        mesh is None
        or mesh.uv_layers.active is None
    ):
        return None

    inverse_world = (
        obj.matrix_world.inverted_safe()
    )

    ray_origin_local = (
        inverse_world
        @ ray_origin_world
    )

    ray_direction_local = (
        inverse_world.to_3x3()
        @ ray_direction_world
    )

    if ray_direction_local.length_squared == 0.0:
        return None

    ray_direction_local.normalize()

    (
        hit,
        hit_location,
        hit_normal,
        face_index,
    ) = obj.ray_cast(
        ray_origin_local,
        ray_direction_local
    )

    if (
        not hit
        or face_index < 0
        or face_index >= len(
            mesh.polygons
        )
    ):
        return None

    polygon = mesh.polygons[
        face_index
    ]

    if len(
        polygon.vertices
    ) < 3:
        return None

    vertex_coordinates = [
        mesh.vertices[
            vertex_index
        ].co
        for vertex_index in polygon.vertices
    ]

    weights = poly_3d_calc(
        vertex_coordinates,
        hit_location
    )

    if len(
        weights
    ) != len(
        polygon.loop_indices
    ):
        return None

    uv_layer = (
        mesh.uv_layers.active.data
    )

    uv = Vector(
        (
            0.0,
            0.0,
        )
    )

    for weight, loop_index in zip(
        weights,
        polygon.loop_indices
    ):
        uv += (
            uv_layer[
                loop_index
            ].uv
            * float(
                weight
            )
        )

    # Stage 5 will route coordinates outside 0..1 to their UDIM tile. Until
    # then, never silently clamp an out-of-tile stroke onto an image edge.
    if (
        uv.x < 0.0
        or uv.x > 1.0
        or uv.y < 0.0
        or uv.y > 1.0
    ):
        return None

    pixel_x = (
        float(
            uv.x
        )
        * max(
            1,
            int(
                image_width
            )
            - 1
        )
    )

    # Blender UV origin is bottom-left; GIMP image coordinates are top-left.
    pixel_y = (
        1.0
        - float(
            uv.y
        )
    ) * max(
        1,
        int(
            image_height
        )
        - 1
    )

    return (
        pixel_x,
        pixel_y,
        int(
            face_index
        ),
    )


def _polygon_uv_by_vertex(
    mesh,
    polygon,
    uv_layer
):
    result = {}

    for loop_index in polygon.loop_indices:

        vertex_index = int(
            mesh.loops[
                loop_index
            ].vertex_index
        )

        uv = uv_layer[
            loop_index
        ].uv

        result[
            vertex_index
        ] = (
            float(
                uv.x
            ),
            float(
                uv.y
            ),
        )

    return result


def _uv_distance_squared(
    a,
    b
):
    dx = (
        float(
            a[0]
        )
        - float(
            b[0]
        )
    )

    dy = (
        float(
            a[1]
        )
        - float(
            b[1]
        )
    )

    return (
        dx * dx
        + dy * dy
    )


def _faces_uv_continuous(
    mesh,
    previous_face_index,
    current_face_index,
    epsilon=1.0e-6
):
    """
    Determine whether two consecutive ray-hit faces are connected through an
    actual mesh edge whose UV coordinates are continuous on both sides.

    Returns:
        (continuous: bool, reason: str)

    Same-face motion is always UV-continuous. Faces that do not share exactly
    one mesh edge are conservatively split. For a shared edge, both endpoint
    UVs must match across the two polygon loop corners.
    """

    previous_face_index = int(
        previous_face_index
    )

    current_face_index = int(
        current_face_index
    )

    if previous_face_index == current_face_index:
        return (
            True,
            "same-face",
        )

    if (
        previous_face_index < 0
        or current_face_index < 0
        or previous_face_index >= len(
            mesh.polygons
        )
        or current_face_index >= len(
            mesh.polygons
        )
    ):
        return (
            False,
            "invalid-face",
        )

    previous_polygon = mesh.polygons[
        previous_face_index
    ]

    current_polygon = mesh.polygons[
        current_face_index
    ]

    previous_vertices = set(
        int(
            vertex_index
        )
        for vertex_index in previous_polygon.vertices
    )

    current_vertices = set(
        int(
            vertex_index
        )
        for vertex_index in current_polygon.vertices
    )

    shared_vertices = (
        previous_vertices
        & current_vertices
    )

    # Two manifold polygon faces sharing an edge have exactly two shared
    # endpoint vertices. Vertex-only adjacency or a non-adjacent ray jump is
    # not safe to continue as one 2D GIMP stroke.
    if len(
        shared_vertices
    ) != 2:
        return (
            False,
            "non-adjacent-faces",
        )

    shared_vertices = tuple(
        sorted(
            shared_vertices
        )
    )

    # Confirm those two vertices really form an edge on both polygons rather
    # than merely being two unrelated shared vertices on an unusual ngon.
    shared_edge_key = frozenset(
        shared_vertices
    )

    def polygon_edge_keys(
        polygon
    ):
        vertices = [
            int(
                vertex_index
            )
            for vertex_index in polygon.vertices
        ]

        count = len(
            vertices
        )

        return {
            frozenset(
                (
                    vertices[
                        index
                    ],
                    vertices[
                        (
                            index
                            + 1
                        )
                        % count
                    ],
                )
            )
            for index in range(
                count
            )
        }

    if (
        shared_edge_key
        not in polygon_edge_keys(
            previous_polygon
        )
        or shared_edge_key
        not in polygon_edge_keys(
            current_polygon
        )
    ):
        return (
            False,
            "no-shared-edge",
        )

    uv_layer = mesh.uv_layers.active.data

    previous_uvs = _polygon_uv_by_vertex(
        mesh,
        previous_polygon,
        uv_layer
    )

    current_uvs = _polygon_uv_by_vertex(
        mesh,
        current_polygon,
        uv_layer
    )

    epsilon_squared = (
        float(
            epsilon
        )
        * float(
            epsilon
        )
    )

    for vertex_index in shared_vertices:

        previous_uv = previous_uvs.get(
            vertex_index
        )

        current_uv = current_uvs.get(
            vertex_index
        )

        if (
            previous_uv is None
            or current_uv is None
        ):
            return (
                False,
                "missing-loop-uv",
            )

        if _uv_distance_squared(
            previous_uv,
            current_uv
        ) > epsilon_squared:
            return (
                False,
                "uv-seam",
            )

    return (
        True,
        "shared-edge-continuous",
    )


class BLENDGIMP_OT_direct_gimp_brush_paint(
    bpy.types.Operator
):
    """
    Modal 3D paint tool that sends viewport surface strokes to GIMP's actual
    paintbrush engine.

    Current milestone:
      - active object only
      - active UV map
      - nearest visible surface through Blender ray casting
      - active GIMP brush/foreground/tool options
      - topology-aware UV seam splitting across shared mesh edges
      - live streamed chunks while LMB remains down
      - one GIMP undo group per Blender mouse stroke
      - GIMP Auto Sync performs the raster return/update

    Pressure, exact UV-edge topology splitting, modifiers and advanced
    occlusion controls are intentionally later Stage-2 work.
    """

    bl_idname = (
        "blendgimp.direct_gimp_brush_paint"
    )

    bl_label = (
        "Direct GIMP Brush 3D Paint"
    )

    bl_description = (
        "Paint the active mesh by raycasting the 3D stroke into UV space and "
        "letting GIMP's active brush paint the texture"
    )

    image_id: bpy.props.IntProperty(
        name="GIMP Image ID",
        default=-1
    )

    image_width: bpy.props.IntProperty(
        name="Texture Width",
        default=0,
        min=1
    )

    image_height: bpy.props.IntProperty(
        name="Texture Height",
        default=0,
        min=1
    )

    def _set_status(
        self,
        context,
        text
    ):
        scene = context.scene

        if hasattr(
            scene,
            "blendgimp_direct_paint_status"
        ):
            scene.blendgimp_direct_paint_status = str(
                text
            )

        if context.area is not None:
            context.area.tag_redraw()

    def _finish(
        self,
        context,
        message="Off"
    ):
        scene = context.scene

        if hasattr(
            scene,
            "blendgimp_direct_paint_active"
        ):
            scene.blendgimp_direct_paint_active = (
                False
            )

        if hasattr(
            scene,
            "blendgimp_direct_paint_image_id"
        ):
            scene.blendgimp_direct_paint_image_id = (
                -1
            )

        self._set_status(
            context,
            message
        )

        # Release shared ownership directly first. The internal operator below
        # handles user-visible Auto Sync resume logging but is not required for
        # correctness.
        set_direct_paint_refresh_owner(
            False
        )

        try:
            bpy.ops.blendgimp.direct_paint_resume_auto_sync(
                image_id=int(
                    self.image_id
                )
            )
        except Exception as exc:
            print(
                "BLENDGIMP: "
                f"Direct paint Auto Sync resume hook failed: {exc}"
            )

        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass

    def _begin_segment(
        self
    ):
        self._segments.append(
            []
        )

        self._segment_overlap.append(
            None
        )

    def _current_segment(
        self
    ):
        if not self._segments:
            self._begin_segment()

        return self._segments[
            -1
        ]

    def _sample(
        self,
        context,
        event
    ):
        sample = _raycast_uv_point(
            context,
            self._window_region,
            event,
            self.image_width,
            self.image_height
        )

        if sample is None:
            if (
                self._segments
                and self._segments[
                    -1
                ]
            ):
                self._begin_segment()

            self._last_point = None
            self._last_face_index = None
            return False

        x, y, face_index = sample

        point = (
            float(
                x
            ),
            float(
                y
            ),
        )

        segment = self._current_segment()

        if self._last_point is not None:

            dx = (
                point[0]
                - self._last_point[0]
            )

            dy = (
                point[1]
                - self._last_point[1]
            )

            pixel_distance = math.hypot(
                dx,
                dy
            )

            if pixel_distance < 0.75:
                return False

            if self._last_face_index is not None:

                (
                    uv_continuous,
                    transition_reason,
                ) = _faces_uv_continuous(
                    context.active_object.data,
                    self._last_face_index,
                    face_index,
                )

                if not uv_continuous:

                    self._topology_split_count += 1

                    self._last_topology_split_reason = (
                        transition_reason
                    )

                    print(
                        "BLENDGIMP: "
                        "Direct paint topology split "
                        f"face {self._last_face_index} -> "
                        f"{face_index}: {transition_reason}"
                    )

                    self._begin_segment()

                    segment = self._current_segment()

        segment.append(
            point
        )

        self._last_point = point
        self._last_face_index = int(
            face_index
        )

        return True

    def _chunk_coordinates(
        self,
        segment_index,
        points
    ):
        coordinates = []

        overlap = self._segment_overlap[
            segment_index
        ]

        if overlap is not None:
            coordinates.extend(
                (
                    float(
                        overlap[0]
                    ),
                    float(
                        overlap[1]
                    ),
                )
            )

        for x, y in points:
            coordinates.extend(
                (
                    float(
                        x
                    ),
                    float(
                        y
                    ),
                )
            )

        return coordinates

    def _flush_live_chunks(
        self,
        context,
        force=False
    ):
        if not self._stroke_id:
            return 0

        now = time.monotonic()

        if (
            not force
            and (
                now
                - self._last_chunk_time
            ) < LIVE_STROKE_CHUNK_INTERVAL
        ):
            return 0

        transmitted = 0

        for segment_index, segment in enumerate(
            self._segments
        ):

            if not segment:
                continue

            if (
                not force
                and len(
                    segment
                ) < LIVE_STROKE_CHUNK_POINTS
            ):
                continue

            points = list(
                segment
            )

            coordinates = self._chunk_coordinates(
                segment_index,
                points
            )

            if not coordinates:
                continue

            response = (
                connection_manager.paint_stroke_chunk(
                    self.image_id,
                    self._layer_id,
                    self._stroke_id,
                    coordinates
                )
            )

            transmitted += int(
                response.get(
                    "point_count",
                    len(
                        points
                    )
                )
            )

            self._streamed_chunks += 1

            self._streamed_points += int(
                response.get(
                    "point_count",
                    len(
                        points
                    )
                )
            )

            self._brush_name = str(
                response.get(
                    "brush_name",
                    self._brush_name
                )
            )

            self._brush_size = float(
                response.get(
                    "brush_size",
                    self._brush_size
                )
            )

            # Keep exactly one point of overlap so successive GIMP paintbrush
            # calls join visually instead of leaving a chunk-boundary gap.
            self._segment_overlap[
                segment_index
            ] = points[
                -1
            ]

            segment.clear()

        if transmitted:

            self._last_chunk_time = now

            context.scene.blendgimp_direct_paint_brush = (
                self._brush_name
            )

            self._set_status(
                context,
                (
                    "Live GIMP stroke — "
                    f"{self._streamed_points} point(s), "
                    f"{self._streamed_chunks} chunk(s)"
                )
            )

            print(
                "BLENDGIMP: "
                "Direct GIMP live stroke chunk sent: "
                f"{transmitted} point(s); "
                f"total={self._streamed_points}; "
                f"chunks={self._streamed_chunks}"
            )

            self._refresh_live_viewport(
                context,
                force=False
            )

        return transmitted

    def _refresh_live_viewport(
        self,
        context,
        force=False
    ):
        if not self._stroke_id:
            return False

        now = time.monotonic()

        if (
            not force
            and (
                now
                - self._last_viewport_refresh_time
            ) < LIVE_VIEWPORT_REFRESH_INTERVAL
        ):
            return False

        try:

            result = bpy.ops.blendgimp.direct_live_refresh(
                image_id=int(
                    self.image_id
                )
            )

            if "FINISHED" in result:

                self._last_viewport_refresh_time = now

                if context.area is not None:
                    context.area.tag_redraw()

                return True

        except Exception as exc:

            print(
                "BLENDGIMP: "
                f"Live viewport refresh invocation failed: {exc}"
            )

        return False

    def _begin_live_stroke(
        self,
        context
    ):
        if self._stroke_id:
            return

        self._stroke_id = uuid.uuid4().hex

        response = (
            connection_manager.begin_paint_stroke(
                self.image_id,
                self._layer_id,
                self._stroke_id
            )
        )

        self._brush_name = str(
            response.get(
                "brush_name",
                self._brush_name
            )
        )

        self._brush_size = float(
            response.get(
                "brush_size",
                self._brush_size
            )
        )

        self._last_chunk_time = time.monotonic()
        self._last_viewport_refresh_time = 0.0
        self._streamed_chunks = 0
        self._streamed_points = 0

    def _end_live_stroke(
        self,
        context,
        flush=True
    ):
        stroke_id = self._stroke_id

        if not stroke_id:
            return None

        try:

            if flush:
                self._flush_live_chunks(
                    context,
                    force=True
                )

        finally:

            self._stroke_id = None

            response = (
                connection_manager.end_paint_stroke(
                    stroke_id
                )
            )

        return response

    def _abort_live_stroke(
        self
    ):
        stroke_id = self._stroke_id

        self._stroke_id = None

        if not stroke_id:
            return

        try:
            connection_manager.end_paint_stroke(
                stroke_id
            )
        except Exception:
            pass

    def invoke(
        self,
        context,
        event
    ):
        scene = context.scene

        if (
            context.area is None
            or context.area.type != "VIEW_3D"
        ):
            self.report(
                {"ERROR"},
                "Direct GIMP 3D Paint must be started from a 3D View"
            )
            return {"CANCELLED"}

        if not connection_manager.is_connected():
            self.report(
                {"ERROR"},
                "BlendGimp is not connected to GIMP"
            )
            return {"CANCELLED"}

        if (
            not getattr(
                scene,
                "blendgimp_auto_sync_enabled",
                False
            )
            or int(
                getattr(
                    scene,
                    "blendgimp_auto_sync_image_id",
                    -1
                )
            )
            != int(
                self.image_id
            )
        ):
            self.report(
                {"ERROR"},
                "Enable GIMP Auto Sync for this image before direct 3D paint"
            )
            return {"CANCELLED"}

        obj = context.active_object

        if (
            obj is None
            or obj.type != "MESH"
        ):
            self.report(
                {"ERROR"},
                "Select a mesh object first"
            )
            return {"CANCELLED"}

        if (
            obj.data is None
            or obj.data.uv_layers.active is None
        ):
            self.report(
                {"ERROR"},
                "The active mesh needs an active UV map"
            )
            return {"CANCELLED"}

        self._window_region = (
            _window_region_for_area(
                context.area
            )
        )

        if self._window_region is None:
            self.report(
                {"ERROR"},
                "Could not find the 3D View window region"
            )
            return {"CANCELLED"}

        try:
            layer_response = (
                connection_manager.ensure_paint_layer(
                    self.image_id,
                    BLENDGIMP_DIRECT_PAINT_LAYER_NAME
                )
            )

            brush_state = (
                connection_manager.get_brush_state()
            )

        except Exception as exc:
            self.report(
                {"ERROR"},
                f"Could not start direct GIMP painting: {exc}"
            )
            return {"CANCELLED"}

        self._layer_id = int(
            layer_response[
                "layer_id"
            ]
        )

        self._brush_name = str(
            brush_state.get(
                "brush_name",
                ""
            )
        )

        self._brush_size = float(
            brush_state.get(
                "brush_size",
                1.0
            )
        )

        self._painting = False
        self._segments = []
        self._segment_overlap = []
        self._last_point = None
        self._last_face_index = None
        self._topology_split_count = 0
        self._last_topology_split_reason = ""
        self._stroke_id = None
        self._last_chunk_time = 0.0
        self._last_viewport_refresh_time = 0.0
        self._streamed_chunks = 0
        self._streamed_points = 0

        scene.blendgimp_direct_paint_active = (
            True
        )

        set_direct_paint_refresh_owner(
            True,
            self.image_id
        )

        print(
            "BLENDGIMP: "
            f"Direct paint acquired Auto Sync refresh ownership "
            f"for image ID {self.image_id}"
        )

        scene.blendgimp_direct_paint_image_id = (
            int(
                self.image_id
            )
        )

        scene.blendgimp_direct_paint_brush = (
            self._brush_name
        )

        self._set_status(
            context,
            (
                "Direct GIMP brush active — "
                "LMB paint, Esc/RMB exit"
            )
        )

        context.window_manager.modal_handler_add(
            self
        )

        try:
            context.window.cursor_modal_set(
                "PAINT_BRUSH"
            )
        except Exception:
            pass

        print(
            "BLENDGIMP: "
            f"Direct GIMP Brush 3D Paint started for image ID "
            f"{self.image_id}, layer ID {self._layer_id}, "
            f"brush={self._brush_name}, size={self._brush_size:.1f}"
        )

        return {"RUNNING_MODAL"}

    def modal(
        self,
        context,
        event
    ):
        if event.type in {
            "ESC",
            "RIGHTMOUSE",
        }:

            if self._painting:

                self._painting = False
                self._abort_live_stroke()

            self._finish(
                context,
                "Direct GIMP brush stopped"
            )

            print(
                "BLENDGIMP: "
                "Direct GIMP Brush 3D Paint stopped"
            )

            return {"CANCELLED"}

        if not connection_manager.is_connected():

            self._painting = False
            self._stroke_id = None

            self._finish(
                context,
                "GIMP disconnected"
            )

            return {"CANCELLED"}

        if event.type == "LEFTMOUSE":

            if event.value == "PRESS":

                # Start collecting locally first. Do not open a GIMP undo
                # group until the mouse actually hits the mesh.
                self._painting = True
                self._segments = []
                self._segment_overlap = []
                self._last_point = None
                self._last_face_index = None
                self._topology_split_count = 0
                self._last_topology_split_reason = ""
                self._stroke_id = None
                self._last_chunk_time = 0.0
                self._last_viewport_refresh_time = 0.0
                self._streamed_chunks = 0
                self._streamed_points = 0

                point_added = self._sample(
                    context,
                    event
                )

                if point_added:

                    try:
                        self._begin_live_stroke(
                            context
                        )
                    except Exception as exc:

                        self._painting = False

                        self._finish(
                            context,
                            f"Could not begin stroke: {exc}"
                        )

                        self.report(
                            {"ERROR"},
                            f"Could not begin GIMP stroke: {exc}"
                        )

                        return {"CANCELLED"}

                    self._set_status(
                        context,
                        "Live GIMP stroke started..."
                    )

                else:

                    self._set_status(
                        context,
                        "Move onto the mesh to begin painting"
                    )

                return {"RUNNING_MODAL"}

            if (
                event.value == "RELEASE"
                and self._painting
            ):

                point_added = self._sample(
                    context,
                    event
                )

                self._painting = False

                # A click/drag that never touched the mesh should not create
                # BEGIN/END traffic or an empty GIMP undo group.
                if self._stroke_id is None:

                    if point_added:

                        try:
                            self._begin_live_stroke(
                                context
                            )
                        except Exception as exc:

                            self._finish(
                                context,
                                f"Could not begin stroke: {exc}"
                            )

                            return {"CANCELLED"}

                    else:

                        self._set_status(
                            context,
                            "Stroke missed the active mesh"
                        )

                        return {"RUNNING_MODAL"}

                try:

                    response = self._end_live_stroke(
                        context,
                        flush=True
                    )

                    # Force the last raster chunk into Blender immediately.
                    self._refresh_live_viewport(
                        context,
                        force=True
                    )

                except Exception as exc:

                    self._finish(
                        context,
                        f"Stroke failed: {exc}"
                    )

                    print(
                        "BLENDGIMP: "
                        f"Direct GIMP live stroke failed: {exc}"
                    )

                    self.report(
                        {"ERROR"},
                        f"Direct GIMP stroke failed: {exc}"
                    )

                    return {"CANCELLED"}

                if self._streamed_points > 0:

                    self._set_status(
                        context,
                        (
                            f"Completed live GIMP stroke: "
                            f"{self._streamed_points} point(s), "
                            f"{self._streamed_chunks} chunk(s), "
                            f"{self._topology_split_count} UV split(s)"
                        )
                    )

                    print(
                        "BLENDGIMP: "
                        "Direct GIMP live 3D stroke completed: "
                        f"{self._streamed_points} point(s), "
                        f"{self._streamed_chunks} chunk(s), "
                        f"uv_splits={self._topology_split_count}, "
                        f"brush={self._brush_name}"
                    )

                else:

                    self._set_status(
                        context,
                        "Stroke missed the active mesh"
                    )

                return {"RUNNING_MODAL"}

        if (
            event.type == "MOUSEMOVE"
            and self._painting
        ):

            point_added = self._sample(
                context,
                event
            )

            if point_added:

                try:

                    if self._stroke_id is None:
                        self._begin_live_stroke(
                            context
                        )

                    self._flush_live_chunks(
                        context,
                        force=False
                    )

                except Exception as exc:

                    self._painting = False
                    self._abort_live_stroke()

                    self._finish(
                        context,
                        f"Live stroke failed: {exc}"
                    )

                    self.report(
                        {"ERROR"},
                        f"Live GIMP stroke failed: {exc}"
                    )

                    return {"CANCELLED"}

            return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

