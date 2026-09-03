import math
import time
import uuid
from types import SimpleNamespace

import bpy

from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree
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

# Sparse Blender pointer events are resampled by raycasting intermediate
# screen positions. This lets topology/UV seam checks see the actual surface
# path instead of connecting two far-apart UV endpoints.
INTERPOLATION_MAX_STEPS = 64
INTERPOLATION_MAX_SCREEN_STEP = 12.0
INTERPOLATION_MIN_TEXTURE_STEP = 1.0

# A raycast close to a UV seam can alternate between the two adjacent faces.
# Release the short hysteresis envelope as soon as either threshold is
# exceeded so a deliberate later crossing is still accepted.
SEAM_CHATTER_TIME_WINDOW = 0.20
SEAM_CHATTER_SCREEN_DISTANCE = 8.0

PAINT_THROUGH_MAX_HITS = 32
PAINT_THROUGH_RAY_EPSILON = 1.0e-5

# Phase-3 brush-footprint protection samples a ring around each intended
# brush center. The ring is converted from GIMP texture pixels to viewport
# pixels from local UV derivatives, then clamped so a highly distorted UV or
# near-edge probe cannot create an unbounded raycast workload.
FOOTPRINT_PROBE_OFFSET = 2.0
FOOTPRINT_MIN_SCREEN_RADIUS = 2.0
FOOTPRINT_MAX_SCREEN_RADIUS = 96.0
FOOTPRINT_UV_DISTANCE_FACTOR = 2.5
FOOTPRINT_MAX_UV_FACE_HOPS = 12


def _window_region_for_area(
    area
):
    if area is None:
        return None

    for region in area.regions:
        if region.type == "WINDOW":
            return region

    return None


def _mesh_uv_layer(
    mesh,
    uv_layer_name=None
):
    """Resolve a named UV layer without mutating the mesh's active layer."""

    if mesh is None:
        return None

    uv_layers = getattr(
        mesh,
        "uv_layers",
        None
    )

    if uv_layers is None:
        return None

    if uv_layer_name:
        return uv_layers.get(
            str(
                uv_layer_name
            )
        )

    return uv_layers.active


def _raycast_uv_point(
    context,
    window_region,
    event,
    image_width,
    image_height,
    front_faces_only=True,
    normal_angle_enabled=False,
    normal_angle_limit=75.0,
    rejection_stats=None,
    raycast_object=None,
    raycast_mesh=None,
    raycast_bvh=None,
    uv_layer_name=None
):
    """
    Convert the current mouse position to a GIMP image-space point. The
    raycast object, polygon indices, and UV loops must all describe the same
    original or evaluated mesh.
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

    if ray_direction_world.length_squared == 0.0:
        return None

    ray_direction_world_normalized = (
        ray_direction_world.normalized()
    )

    obj = (
        raycast_object
        if raycast_object is not None
        else context.active_object
    )

    if (
        obj is None
        or obj.type != "MESH"
    ):
        return None

    mesh = (
        raycast_mesh
        if raycast_mesh is not None
        else obj.data
    )

    uv_map = _mesh_uv_layer(
        mesh,
        uv_layer_name
    )

    if (
        mesh is None
        or uv_map is None
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

    if raycast_bvh is not None:
        (
            hit_location,
            hit_normal,
            face_index,
            _hit_distance,
        ) = raycast_bvh.ray_cast(
            ray_origin_local,
            ray_direction_local
        )

        hit = (
            hit_location is not None
            and face_index is not None
        )
    else:
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

    normal_matrix = (
        obj.matrix_world.to_3x3()
        .inverted_safe()
        .transposed()
    )

    hit_normal_world = (
        normal_matrix
        @ hit_normal
    )

    if hit_normal_world.length_squared == 0.0:
        return None

    hit_normal_world.normalize()

    facing_dot = float(
        hit_normal_world.dot(
            -ray_direction_world_normalized
        )
    )

    (
        geometry_allowed,
        rejection_reason,
    ) = _geometry_hit_allowed(
        facing_dot,
        front_faces_only,
        normal_angle_enabled,
        normal_angle_limit,
    )

    if not geometry_allowed:

        if rejection_stats is not None:
            rejection_stats[
                rejection_reason
            ] = int(
                rejection_stats.get(
                    rejection_reason,
                    0
                )
            ) + 1

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

    uv_layer = uv_map.data

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


def _uv_point_from_mesh_hit(
    mesh,
    hit_location,
    face_index,
    image_width,
    image_height,
    uv_layer_name=None
):
    """Convert one already-resolved mesh hit into GIMP pixel coordinates."""

    if (
        face_index < 0
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

    uv_map = _mesh_uv_layer(
        mesh,
        uv_layer_name
    )

    if uv_map is None:
        return None

    uv_layer = uv_map.data

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

    if (
        uv.x < 0.0
        or uv.x > 1.0
        or uv.y < 0.0
        or uv.y > 1.0
    ):
        return None

    return (
        float(
            uv.x
        )
        * max(
            1,
            int(
                image_width
            )
            - 1
        ),
        (
            1.0
            - float(
                uv.y
            )
        )
        * max(
            1,
            int(
                image_height
            )
            - 1
        ),
        int(
            face_index
        ),
    )


def _raycast_uv_points_through(
    context,
    window_region,
    event,
    image_width,
    image_height,
    front_faces_only,
    normal_angle_enabled,
    normal_angle_limit,
    rejection_stats=None,
    raycast_object=None,
    raycast_mesh=None,
    raycast_bvh=None,
    uv_layer_name=None
):
    """Return every allowed UV hit along the viewport ray, nearest first."""

    area = context.area

    if (
        area is None
        or area.type != "VIEW_3D"
        or window_region is None
    ):
        return []

    mouse_x = event.mouse_x - window_region.x
    mouse_y = event.mouse_y - window_region.y

    if (
        mouse_x < 0
        or mouse_y < 0
        or mouse_x >= window_region.width
        or mouse_y >= window_region.height
    ):
        return []

    coord = (
        float(
            mouse_x
        ),
        float(
            mouse_y
        ),
    )

    rv3d = context.space_data.region_3d

    ray_origin_world = view3d_utils.region_2d_to_origin_3d(
        window_region,
        rv3d,
        coord
    )

    ray_direction_world = view3d_utils.region_2d_to_vector_3d(
        window_region,
        rv3d,
        coord
    )

    if ray_direction_world.length_squared == 0.0:
        return []

    ray_direction_world_normalized = (
        ray_direction_world.normalized()
    )

    obj = (
        raycast_object
        if raycast_object is not None
        else context.active_object
    )

    mesh = (
        raycast_mesh
        if raycast_mesh is not None
        else (
            obj.data
            if obj is not None
            else None
        )
    )

    if (
        obj is None
        or obj.type != "MESH"
        or mesh is None
        or _mesh_uv_layer(
            mesh,
            uv_layer_name
        ) is None
    ):
        return []

    inverse_world = obj.matrix_world.inverted_safe()
    ray_origin_local = inverse_world @ ray_origin_world
    ray_direction_local = (
        inverse_world.to_3x3()
        @ ray_direction_world
    )

    if ray_direction_local.length_squared == 0.0:
        return []

    ray_direction_local.normalize()

    normal_matrix = (
        obj.matrix_world.to_3x3()
        .inverted_safe()
        .transposed()
    )

    current_origin = ray_origin_local.copy()
    results = []

    if rejection_stats is not None:
        rejection_stats["occlusion-check"] = int(
            rejection_stats.get(
                "occlusion-check",
                0
            )
        ) + 1

    for depth_index in range(
        PAINT_THROUGH_MAX_HITS
    ):
        if raycast_bvh is not None:
            (
                hit_location,
                hit_normal,
                face_index,
                _hit_distance,
            ) = raycast_bvh.ray_cast(
                current_origin,
                ray_direction_local
            )

            hit = (
                hit_location is not None
                and face_index is not None
            )
        else:
            (
                hit,
                hit_location,
                hit_normal,
                face_index,
            ) = obj.ray_cast(
                current_origin,
                ray_direction_local
            )

        if (
            not hit
            or face_index < 0
            or face_index >= len(
                mesh.polygons
            )
        ):
            break

        hit_normal_world = normal_matrix @ hit_normal

        if hit_normal_world.length_squared != 0.0:
            hit_normal_world.normalize()

            facing_dot = float(
                hit_normal_world.dot(
                    -ray_direction_world_normalized
                )
            )

            allowed, reason = _geometry_hit_allowed(
                facing_dot,
                front_faces_only,
                normal_angle_enabled,
                normal_angle_limit,
            )

            if allowed:
                uv_point = _uv_point_from_mesh_hit(
                    mesh,
                    hit_location,
                    face_index,
                    image_width,
                    image_height,
                    uv_layer_name
                )

                if uv_point is not None:
                    results.append(
                        (
                            uv_point[0],
                            uv_point[1],
                            uv_point[2],
                            int(
                                depth_index
                            ),
                        )
                    )

            elif rejection_stats is not None:
                rejection_stats[reason] = int(
                    rejection_stats.get(
                        reason,
                        0
                    )
                ) + 1

        current_origin = (
            hit_location
            + ray_direction_local
            * PAINT_THROUGH_RAY_EPSILON
        )

    return results


def _geometry_hit_allowed(
    facing_dot,
    front_faces_only,
    normal_angle_enabled,
    normal_angle_limit
):
    """Apply front-face and view-normal-angle protection to a ray hit."""

    facing = max(
        -1.0,
        min(
            1.0,
            float(
                facing_dot
            )
        )
    )

    if (
        bool(
            front_faces_only
        )
        and facing <= 0.0
    ):
        return (
            False,
            "backface",
        )

    if bool(
        normal_angle_enabled
    ):
        angle_limit = max(
            0.0,
            min(
                180.0,
                float(
                    normal_angle_limit
                )
            )
        )

        minimum_facing = math.cos(
            math.radians(
                angle_limit
            )
        )

        if facing < minimum_facing:
            return (
                False,
                "normal-angle",
            )

    return (
        True,
        "accepted",
    )


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
    epsilon=1.0e-6,
    uv_layer_name=None
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

    uv_map = _mesh_uv_layer(
        mesh,
        uv_layer_name
    )

    if uv_map is None:
        return (
            False,
            "missing-uv-layer",
        )

    uv_layer = uv_map.data

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


def _build_uv_face_adjacency(
    mesh,
    uv_layer_name=None
):
    """Build a cached graph of face neighbors joined by continuous UV edges."""

    adjacency = {
        int(
            polygon.index
        ): set()
        for polygon in mesh.polygons
    }

    edge_faces = {}

    for polygon in mesh.polygons:
        vertices = [
            int(
                vertex_index
            )
            for vertex_index in polygon.vertices
        ]

        for index in range(
            len(
                vertices
            )
        ):
            edge_key = tuple(
                sorted(
                    (
                        vertices[
                            index
                        ],
                        vertices[
                            (
                                index
                                + 1
                            )
                            % len(
                                vertices
                            )
                        ],
                    )
                )
            )

            edge_faces.setdefault(
                edge_key,
                []
            ).append(
                int(
                    polygon.index
                )
            )

    for face_indices in edge_faces.values():
        for first_offset in range(
            len(
                face_indices
            )
        ):
            for second_offset in range(
                first_offset + 1,
                len(
                    face_indices
                )
            ):
                first_face = face_indices[
                    first_offset
                ]
                second_face = face_indices[
                    second_offset
                ]

                continuous, _reason = _faces_uv_continuous(
                    mesh,
                    first_face,
                    second_face,
                    uv_layer_name=uv_layer_name
                )

                if continuous:
                    adjacency[
                        first_face
                    ].add(
                        second_face
                    )

                    adjacency[
                        second_face
                    ].add(
                        first_face
                    )

    return adjacency


def _build_mesh_face_adjacency(
    mesh
):
    """Build face neighbors from shared mesh edges regardless of UV seams."""

    adjacency = {
        int(
            polygon.index
        ): set()
        for polygon in mesh.polygons
    }

    edge_faces = {}

    for polygon in mesh.polygons:
        vertices = [
            int(
                vertex_index
            )
            for vertex_index in polygon.vertices
        ]

        for index in range(
            len(
                vertices
            )
        ):
            edge_key = tuple(
                sorted(
                    (
                        vertices[
                            index
                        ],
                        vertices[
                            (
                                index
                                + 1
                            )
                            % len(
                                vertices
                            )
                        ],
                    )
                )
            )

            edge_faces.setdefault(
                edge_key,
                []
            ).append(
                int(
                    polygon.index
                )
            )

    for face_indices in edge_faces.values():
        for first_offset in range(
            len(
                face_indices
            )
        ):
            for second_offset in range(
                first_offset + 1,
                len(
                    face_indices
                )
            ):
                first_face = face_indices[
                    first_offset
                ]
                second_face = face_indices[
                    second_offset
                ]

                adjacency[
                    first_face
                ].add(
                    second_face
                )

                adjacency[
                    second_face
                ].add(
                    first_face
                )

    return adjacency


def _uv_faces_connected_within(
    adjacency,
    first_face,
    second_face,
    max_hops=FOOTPRINT_MAX_UV_FACE_HOPS
):
    """Return whether two faces share a short UV-continuous surface path."""

    first_face = int(
        first_face
    )
    second_face = int(
        second_face
    )

    if first_face == second_face:
        return True

    if (
        first_face not in adjacency
        or second_face not in adjacency
    ):
        return False

    visited = {
        first_face
    }
    frontier = {
        first_face
    }

    for _hop in range(
        max(
            0,
            int(
                max_hops
            )
        )
    ):
        next_frontier = set()

        for face_index in frontier:
            for neighbor in adjacency.get(
                face_index,
                ()
            ):
                if neighbor == second_face:
                    return True

                if neighbor not in visited:
                    visited.add(
                        neighbor
                    )
                    next_frontier.add(
                        neighbor
                    )

        if not next_frontier:
            break

        frontier = next_frontier

    return False


def _offset_pointer_event(
    event,
    offset_x,
    offset_y
):
    """Create the minimal event-like object needed by viewport raycasters."""

    return SimpleNamespace(
        mouse_x=(
            float(
                event.mouse_x
            )
            + float(
                offset_x
            )
        ),
        mouse_y=(
            float(
                event.mouse_y
            )
            + float(
                offset_y
            )
        ),
    )


def _build_mesh_bvh(
    mesh
):
    """Build a BVH whose face indices exactly match the supplied mesh."""

    vertices = [
        vertex.co.copy()
        for vertex in mesh.vertices
    ]

    polygons = [
        tuple(
            int(
                vertex_index
            )
            for vertex_index in polygon.vertices
        )
        for polygon in mesh.polygons
    ]

    if not vertices or not polygons:
        return None

    return BVHTree.FromPolygons(
        vertices,
        polygons,
        all_triangles=False
    )


def _resolve_projection_mesh(
    context,
    source_object,
    projection_mode
):
    """Select a UV-compatible original or evaluated projection mesh."""

    source_mesh = source_object.data
    source_uv = source_mesh.uv_layers.active
    source_uv_name = str(
        source_uv.name
    )
    normalized_mode = str(
        projection_mode
    ).upper()

    if normalized_mode == "ORIGINAL":
        return {
            "object": source_object,
            "mesh": source_mesh,
            "depsgraph": None,
            "uv_layer_name": source_uv_name,
            "kind": "original",
            "fallback": False,
            "fallback_reason": "",
        }

    fallback_reason = ""

    try:
        depsgraph = context.evaluated_depsgraph_get()
        evaluated_object = source_object.evaluated_get(
            depsgraph
        )
        evaluated_mesh = getattr(
            evaluated_object,
            "data",
            None
        )

        if (
            evaluated_object is None
            or evaluated_object.type != "MESH"
            or evaluated_mesh is None
        ):
            fallback_reason = "evaluated mesh unavailable"
        elif len(
            evaluated_mesh.polygons
        ) == 0:
            fallback_reason = "evaluated mesh has no polygons"
        else:
            evaluated_uv = evaluated_mesh.uv_layers.get(
                source_uv_name
            )

            if evaluated_uv is None:
                fallback_reason = (
                    f"active UV map '{source_uv_name}' was not preserved"
                )
            elif len(
                evaluated_uv.data
            ) != len(
                evaluated_mesh.loops
            ):
                fallback_reason = (
                    f"active UV map '{source_uv_name}' has incompatible "
                    "evaluated loop data"
                )
            else:
                return {
                    "object": evaluated_object,
                    "mesh": evaluated_mesh,
                    "depsgraph": depsgraph,
                    "uv_layer_name": source_uv_name,
                    "kind": "evaluated",
                    "fallback": False,
                    "fallback_reason": "",
                }

    except Exception as exc:
        fallback_reason = str(
            exc
        ) or "evaluated mesh lookup failed"

    if normalized_mode == "EVALUATED":
        raise RuntimeError(
            "Evaluated projection requested, but "
            f"{fallback_reason}"
        )

    return {
        "object": source_object,
        "mesh": source_mesh,
        "depsgraph": None,
        "uv_layer_name": source_uv_name,
        "kind": "original",
        "fallback": True,
        "fallback_reason": fallback_reason,
    }


def _normalize_gimp_brush_spacing(
    spacing
):
    """
    Normalize GIMP brush spacing to a fraction of brush diameter.

    GIMP documentation calls this percent-of-size, while bindings/documentation
    can expose values as fractions. Accept both:
        0.10 -> 10%
        10.0 -> 10%
    """

    value = max(
        0.0,
        float(
            spacing
        )
    )

    if value > 1.0:
        value = (
            value
            / 100.0
        )

    return max(
        0.01,
        value
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
      - modifier-aware evaluated-mesh projection with original fallback
      - mesh-local BVH indices shared by ray hits, polygons, and UV loops
      - nearest visible surface or paint-through projection
      - active GIMP brush/foreground/tool options
      - brush-spacing-aware intermediate surface raycasts
      - topology-aware UV seam splitting across shared mesh edges
      - short-window UV seam chatter suppression
      - front-face and view-normal-angle geometry protection
      - brush-footprint silhouette and thin-occluder protection
      - adaptive boundary-ray coverage with strict silhouette rejection
      - footprint rejection/resumption hysteresis
      - stable paint-through surface identities across changing hit depths
      - one protected break per consecutive footprint-rejection interval
      - live streamed chunks while LMB remains down
      - one GIMP undo group per Blender mouse stroke
      - GIMP Auto Sync performs the raster return/update

    Pressure, modifiers and texture-space per-pixel masking are intentionally
    later Stage-2 work.
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

        return (
            len(
                self._segments
            )
            - 1
        )

    def _current_segment(
        self
    ):
        if not self._segments:
            self._begin_segment()

        return self._segments[
            -1
        ]

    def _sample_visible_legacy(
        self,
        context,
        event
    ):
        current_mouse = self._event_window_position(
            event
        )

        now = time.monotonic()

        self._release_seam_hysteresis_if_needed(
            current_mouse,
            now
        )

        sample = _raycast_uv_point(
            context,
            self._window_region,
            event,
            self.image_width,
            self.image_height,
            self._front_faces_only,
            self._normal_angle_enabled,
            self._normal_angle_limit,
            self._geometry_rejections,
            self._raycast_object,
            self._raycast_mesh,
            self._raycast_bvh,
            self._raycast_uv_layer_name
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
            self._seam_hysteresis_mouse = None
            self._seam_hysteresis_time = 0.0
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
                    self._raycast_mesh,
                    self._last_face_index,
                    face_index,
                    uv_layer_name=self._raycast_uv_layer_name
                )

                if not uv_continuous:

                    previous_face_index = int(
                        self._last_face_index
                    )

                    current_face_index = int(
                        face_index
                    )

                    # Canonical ordering makes A->B and B->A the same seam.
                    seam_pair = tuple(
                        sorted(
                            (
                                previous_face_index,
                                current_face_index,
                            )
                        )
                    )

                    if (
                        transition_reason == "uv-seam"
                        and seam_pair == self._last_crossed_seam_pair
                        and self._seam_hysteresis_mouse is not None
                        and (
                            now
                            - self._seam_hysteresis_time
                        ) <= SEAM_CHATTER_TIME_WINDOW
                        and math.hypot(
                            current_mouse[0]
                            - self._seam_hysteresis_mouse[0],
                            current_mouse[1]
                            - self._seam_hysteresis_mouse[1],
                        ) <= SEAM_CHATTER_SCREEN_DISTANCE
                    ):

                        self._seam_suppressed_count += 1

                        print(
                            "BLENDGIMP: "
                            "Direct paint seam chatter suppressed "
                            f"face {previous_face_index} -> "
                            f"{current_face_index}"
                        )

                        # Drop the unstable reverse-side hit. Keeping the
                        # last accepted face/UV prevents GIMP from drawing a
                        # line across disconnected UV islands.
                        return False

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

                    if transition_reason == "uv-seam":
                        self._last_crossed_seam_pair = seam_pair
                        self._seam_hysteresis_time = now
                        self._seam_hysteresis_mouse = current_mouse

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

    def _raycast_surface_samples(
        self,
        context,
        event,
        rejection_stats=None
    ):
        """Raycast one screen location using the selected surface mode."""

        if self._paint_through:
            return _raycast_uv_points_through(
                context,
                self._window_region,
                event,
                self.image_width,
                self.image_height,
                self._front_faces_only,
                self._normal_angle_enabled,
                self._normal_angle_limit,
                rejection_stats,
                self._raycast_object,
                self._raycast_mesh,
                self._raycast_bvh,
                self._raycast_uv_layer_name
            )

        visible_sample = _raycast_uv_point(
            context,
            self._window_region,
            event,
            self.image_width,
            self.image_height,
            self._front_faces_only,
            self._normal_angle_enabled,
            self._normal_angle_limit,
            rejection_stats,
            self._raycast_object,
            self._raycast_mesh,
            self._raycast_bvh,
            self._raycast_uv_layer_name
        )

        if visible_sample is None:
            return []

        return [
            (
                visible_sample[0],
                visible_sample[1],
                visible_sample[2],
                0,
            )
        ]

    def _footprint_match(
        self,
        center_sample,
        candidates,
        maximum_texture_distance
    ):
        """Find the same local UV surface among one offset ray's hits."""

        center_x, center_y, center_face, _center_depth = (
            center_sample
        )

        maximum_distance_squared = (
            float(
                maximum_texture_distance
            )
            * float(
                maximum_texture_distance
            )
        )

        best_candidate = None
        best_distance_squared = None

        for candidate in candidates:
            candidate_x, candidate_y, candidate_face, _candidate_depth = (
                candidate
            )

            if not _uv_faces_connected_within(
                self._uv_face_adjacency,
                center_face,
                candidate_face
            ):
                continue

            texture_distance_squared = (
                (
                    float(
                        candidate_x
                    )
                    - float(
                        center_x
                    )
                )
                ** 2
                + (
                    float(
                        candidate_y
                    )
                    - float(
                        center_y
                    )
                )
                ** 2
            )

            if texture_distance_squared > maximum_distance_squared:
                continue

            if (
                best_distance_squared is None
                or texture_distance_squared < best_distance_squared
            ):
                best_candidate = candidate
                best_distance_squared = texture_distance_squared

        return best_candidate

    def _estimate_footprint_screen_radius(
        self,
        context,
        event,
        center_sample
    ):
        """Estimate the GIMP brush radius in viewport pixels at the hit."""

        texture_scales = []
        maximum_probe_distance = max(
            4.0,
            float(
                self._brush_size
            )
            * FOOTPRINT_UV_DISTANCE_FACTOR
        )

        for offset_x, offset_y in (
            (
                FOOTPRINT_PROBE_OFFSET,
                0.0,
            ),
            (
                -FOOTPRINT_PROBE_OFFSET,
                0.0,
            ),
            (
                0.0,
                FOOTPRINT_PROBE_OFFSET,
            ),
            (
                0.0,
                -FOOTPRINT_PROBE_OFFSET,
            ),
        ):
            probe_samples = self._raycast_surface_samples(
                context,
                _offset_pointer_event(
                    event,
                    offset_x,
                    offset_y
                )
            )

            self._footprint_ray_count += 1

            probe_match = self._footprint_match(
                center_sample,
                probe_samples,
                maximum_probe_distance
            )

            if probe_match is None:
                continue

            texture_distance = math.hypot(
                float(
                    probe_match[0]
                )
                - float(
                    center_sample[0]
                ),
                float(
                    probe_match[1]
                )
                - float(
                    center_sample[1]
                ),
            )

            if texture_distance > 1.0e-6:
                texture_scales.append(
                    texture_distance
                    / FOOTPRINT_PROBE_OFFSET
                )

        if texture_scales:
            texture_scales.sort()
            middle = len(
                texture_scales
            ) // 2

            if len(
                texture_scales
            ) % 2:
                texels_per_screen_pixel = texture_scales[
                    middle
                ]
            else:
                texels_per_screen_pixel = (
                    texture_scales[
                        middle - 1
                    ]
                    + texture_scales[
                        middle
                    ]
                ) * 0.5
        else:
            region_width = max(
                1.0,
                float(
                    self._window_region.width
                )
            )
            region_height = max(
                1.0,
                float(
                    self._window_region.height
                )
            )

            # Conservative global fallback for clicks whose local derivative
            # probes all land outside the protected surface.
            texels_per_screen_pixel = math.sqrt(
                (
                    float(
                        self.image_width
                    )
                    * float(
                        self.image_height
                    )
                )
                / (
                    region_width
                    * region_height
                )
            )

        raw_radius = (
            max(
                0.5,
                float(
                    self._brush_size
                )
                * 0.5
            )
            / max(
                1.0e-6,
                texels_per_screen_pixel
            )
        )

        return max(
            FOOTPRINT_MIN_SCREEN_RADIUS,
            min(
                FOOTPRINT_MAX_SCREEN_RADIUS,
                raw_radius
            )
        )

    def _filter_footprint_assignments(
        self,
        context,
        event,
        assignments
    ):
        """Apply strict silhouettes and adaptive boundary-ray coverage."""

        if (
            not self._footprint_protection
            or not assignments
        ):
            return {
                track_key
                for track_key, _sample in assignments
            }

        center_samples = [
            sample
            for _track_key, sample in assignments
        ]

        screen_radius = self._estimate_footprint_screen_radius(
            context,
            event,
            center_samples[0]
        )

        self._footprint_radius_total += screen_radius
        self._footprint_radius_sample_count += 1

        ring_sample_sets = []

        for sample_index in range(
            self._footprint_sample_count
        ):
            angle = (
                2.0
                * math.pi
                * float(
                    sample_index
                )
                / float(
                    self._footprint_sample_count
                )
            )

            ring_sample_sets.append(
                self._raycast_surface_samples(
                    context,
                    _offset_pointer_event(
                        event,
                        math.cos(
                            angle
                        )
                        * screen_radius,
                        math.sin(
                            angle
                        )
                        * screen_radius
                    )
                )
            )

            self._footprint_ray_count += 1

        maximum_texture_distance = max(
            4.0,
            float(
                self._brush_size
            )
            * 0.5
            * FOOTPRINT_UV_DISTANCE_FACTOR
        )

        entry_required = max(
            1,
            min(
                self._footprint_sample_count,
                int(
                    math.ceil(
                        float(
                            self._footprint_sample_count
                        )
                        * float(
                            self._footprint_safe_ratio
                        )
                    )
                )
            )
        )

        resume_required = min(
            self._footprint_sample_count,
            entry_required + 1
        )

        accepted_tracks = set()

        for track_key, center_sample in assignments:
            self._footprint_check_count += 1
            safe_rays = 0
            missing_rays = 0

            for ring_samples in ring_sample_sets:
                if not ring_samples:
                    missing_rays += 1

                if self._footprint_match(
                    center_sample,
                    ring_samples,
                    maximum_texture_distance
                ) is not None:
                    safe_rays += 1

            state = self._surface_tracks.get(
                track_key,
                {}
            )

            footprint_blocked = bool(
                state.get(
                    "footprint_blocked",
                    False
                )
            )

            required_rays = (
                resume_required
                if footprint_blocked
                else entry_required
            )

            if missing_rays > 0:
                rejection_reason = "silhouette"
                accepted = False
                self._footprint_silhouette_rejected_count += 1
            elif safe_rays >= required_rays:
                rejection_reason = "accepted"
                accepted = True
            else:
                rejection_reason = "surface-boundary"
                accepted = False
                self._footprint_boundary_rejected_count += 1

                if (
                    footprint_blocked
                    and safe_rays >= entry_required
                ):
                    self._footprint_hysteresis_held_count += 1

            if accepted:
                accepted_tracks.add(
                    track_key
                )

                if safe_rays < self._footprint_sample_count:
                    self._footprint_adaptive_accepted_count += 1

                    if (
                        self._footprint_adaptive_accepted_count <= 6
                        or self._footprint_adaptive_accepted_count % 50 == 0
                    ):
                        print(
                            "BLENDGIMP: "
                            "Direct paint footprint adaptive accept "
                            f"depth={int(center_sample[3])} "
                            f"track={track_key} "
                            f"face {int(center_sample[2])}: "
                            f"{safe_rays}/{self._footprint_sample_count} safe "
                            f"(required={required_rays}, "
                            f"radius={screen_radius:.1f}px)"
                        )

                if footprint_blocked:
                    self._footprint_hysteresis_resumed_count += 1

                continue

            self._footprint_rejected_count += 1

            if (
                self._footprint_rejected_count <= 8
                or self._footprint_rejected_count % 25 == 0
            ):
                print(
                    "BLENDGIMP: "
                    "Direct paint footprint rejected "
                    f"depth={int(center_sample[3])} "
                    f"track={track_key} "
                    f"face {int(center_sample[2])}: "
                    f"{rejection_reason} "
                    f"({safe_rays}/{self._footprint_sample_count} safe, "
                    f"missing={missing_rays}, "
                    f"required={required_rays}, "
                    f"radius={screen_radius:.1f}px)"
                )

        return accepted_tracks

    def _sample(
        self,
        context,
        event
    ):
        current_mouse = self._event_window_position(
            event
        )

        now = time.monotonic()

        self._occlusion_check_count += 1

        raw_samples = self._raycast_surface_samples(
            context,
            event,
            self._geometry_rejections
        )

        if not hasattr(
            self,
            "_surface_tracks"
        ):
            self._surface_tracks = {}

        self._surface_sample_serial += 1

        assignments = self._assign_surface_tracks(
            raw_samples
        )

        accepted_tracks = self._filter_footprint_assignments(
            context,
            event,
            assignments
        )

        seen_tracks = set()
        added_count = 0

        for track_key, sample in assignments:
            x, y, face_index, depth_index = sample

            seen_tracks.add(
                track_key
            )

            if track_key not in accepted_tracks:
                self._update_surface_track_identity(
                    track_key,
                    sample
                )

                self._block_surface_track(
                    track_key,
                    int(
                        depth_index
                    ),
                    footprint=True
                )
                continue

            if self._append_surface_track_sample(
                context,
                track_key,
                (
                    float(
                        x
                    ),
                    float(
                        y
                    ),
                ),
                int(
                    face_index
                ),
                int(
                    depth_index
                ),
                current_mouse,
                now
            ):
                added_count += 1

                self._update_surface_track_identity(
                    track_key,
                    sample
                )

                if int(
                    depth_index
                ) > 0:
                    self._paint_through_point_count += 1

        self._block_missing_surface_tracks(
            seen_tracks
        )

        return added_count > 0

    def _new_surface_track(
        self,
        raw_depth
    ):
        track_key = int(
            self._next_surface_track_id
        )

        self._next_surface_track_id += 1

        self._surface_tracks[
            track_key
        ] = {
            "segment_index": None,
            "last_point": None,
            "last_face_index": None,
            "last_seam_pair": None,
            "seam_mouse": None,
            "seam_time": 0.0,
            "identity_point": None,
            "identity_face_index": None,
            "raw_depth": int(
                raw_depth
            ),
            "last_seen_serial": int(
                self._surface_sample_serial
            ),
            "blocked": True,
            "footprint_blocked": False,
        }

        self._surface_track_created_count += 1

        return track_key

    def _surface_track_match_score(
        self,
        state,
        sample
    ):
        identity_point = state.get(
            "identity_point"
        )
        identity_face = state.get(
            "identity_face_index"
        )

        if (
            identity_point is None
            or identity_face is None
        ):
            return None

        sample_x, sample_y, sample_face, sample_depth = sample
        identity_face = int(
            identity_face
        )
        sample_face = int(
            sample_face
        )

        mesh_adjacent = (
            sample_face == identity_face
            or sample_face in self._mesh_face_adjacency.get(
                identity_face,
                ()
            )
        )

        uv_connected = _uv_faces_connected_within(
            self._uv_face_adjacency,
            identity_face,
            sample_face
        )

        if not (
            mesh_adjacent
            or uv_connected
        ):
            return None

        texture_distance = math.hypot(
            float(
                sample_x
            )
            - float(
                identity_point[0]
            ),
            float(
                sample_y
            )
            - float(
                identity_point[1]
            ),
        )

        maximum_distance = max(
            32.0,
            float(
                self._brush_size
            )
            * 2.5
        )

        # A direct shared mesh edge is allowed to jump in texture space so
        # the existing topology code can split a real UV seam safely.
        if (
            not mesh_adjacent
            and texture_distance > maximum_distance
        ):
            return None

        depth_penalty = (
            abs(
                int(
                    sample_depth
                )
                - int(
                    state.get(
                        "raw_depth",
                        sample_depth
                    )
                )
            )
            * max(
                4.0,
                float(
                    self._brush_size
                )
                * 0.25
            )
        )

        age_penalty = max(
            0,
            int(
                self._surface_sample_serial
            )
            - int(
                state.get(
                    "last_seen_serial",
                    self._surface_sample_serial
                )
            )
            - 1
        ) * 0.25

        return (
            texture_distance
            + depth_penalty
            + age_penalty
        )

    def _assign_surface_tracks(
        self,
        samples
    ):
        if not samples:
            return []

        if not self._paint_through:
            if 0 not in self._surface_tracks:
                self._surface_tracks[
                    0
                ] = {
                    "segment_index": None,
                    "last_point": None,
                    "last_face_index": None,
                    "last_seam_pair": None,
                    "seam_mouse": None,
                    "seam_time": 0.0,
                    "identity_point": None,
                    "identity_face_index": None,
                    "raw_depth": 0,
                    "last_seen_serial": int(
                        self._surface_sample_serial
                    ),
                    "blocked": True,
                    "footprint_blocked": False,
                }

                self._next_surface_track_id = max(
                    self._next_surface_track_id,
                    1
                )
                self._surface_track_created_count += 1

            self._raw_depth_track_map[
                0
            ] = 0
            return [
                (
                    0,
                    samples[0],
                )
            ]

        candidates = []

        for sample_index, sample in enumerate(
            samples
        ):
            for track_key, state in self._surface_tracks.items():
                score = self._surface_track_match_score(
                    state,
                    sample
                )

                if score is not None:
                    candidates.append(
                        (
                            float(
                                score
                            ),
                            int(
                                sample_index
                            ),
                            int(
                                track_key
                            ),
                        )
                    )

        candidates.sort()
        assigned_samples = {}
        assigned_tracks = set()

        for _score, sample_index, track_key in candidates:
            if (
                sample_index in assigned_samples
                or track_key in assigned_tracks
            ):
                continue

            assigned_samples[
                sample_index
            ] = track_key
            assigned_tracks.add(
                track_key
            )

        assignments = []

        for sample_index, sample in enumerate(
            samples
        ):
            raw_depth = int(
                sample[3]
            )
            previous_track = self._raw_depth_track_map.get(
                raw_depth
            )
            track_key = assigned_samples.get(
                sample_index
            )

            if track_key is None:
                track_key = self._new_surface_track(
                    raw_depth
                )

            if (
                previous_track is not None
                and previous_track != track_key
            ):
                self._surface_track_reassignment_count += 1

                previous_state = self._surface_tracks.get(
                    previous_track,
                    {}
                )
                previous_face = previous_state.get(
                    "identity_face_index"
                )

                if (
                    previous_face is not None
                    and (
                        self._surface_track_reassignment_count <= 8
                        or self._surface_track_reassignment_count % 25 == 0
                    )
                ):
                    print(
                        "BLENDGIMP: "
                        "Direct paint depth reassigned "
                        f"depth={raw_depth} "
                        f"track={previous_track}->{track_key} "
                        f"face {int(previous_face)} -> "
                        f"{int(sample[2])}: non-adjacent-protected"
                    )

            self._raw_depth_track_map[
                raw_depth
            ] = track_key

            assignments.append(
                (
                    track_key,
                    sample,
                )
            )

        return assignments

    def _update_surface_track_identity(
        self,
        track_key,
        sample
    ):
        state = self._surface_tracks[
            track_key
        ]

        state[
            "identity_point"
        ] = (
            float(
                sample[0]
            ),
            float(
                sample[1]
            ),
        )
        state[
            "identity_face_index"
        ] = int(
            sample[2]
        )
        state[
            "raw_depth"
        ] = int(
            sample[3]
        )
        state[
            "last_seen_serial"
        ] = int(
            self._surface_sample_serial
        )

    def _block_surface_track(
        self,
        track_key,
        raw_depth,
        footprint=False
    ):
        state = self._surface_tracks.get(
            track_key
        )

        if state is None:
            return

        was_blocked = bool(
            state.get(
                "blocked",
                False
            )
        )

        if (
            footprint
            and not was_blocked
        ):
            self._footprint_track_break_count += 1

        state[
            "blocked"
        ] = True
        state[
            "footprint_blocked"
        ] = bool(
            footprint
        )
        state[
            "last_point"
        ] = None
        state[
            "last_face_index"
        ] = None
        state[
            "segment_index"
        ] = None
        state[
            "seam_mouse"
        ] = None
        state[
            "seam_time"
        ] = 0.0

        if int(
            raw_depth
        ) == 0:
            self._last_point = None
            self._last_face_index = None
            self._seam_hysteresis_mouse = None
            self._seam_hysteresis_time = 0.0

    def _append_surface_track_sample(
        self,
        context,
        track_key,
        point,
        face_index,
        raw_depth,
        current_mouse,
        now
    ):
        state = self._surface_tracks.get(
            track_key
        )

        if state is None:
            self._surface_tracks[
                track_key
            ] = {
                "segment_index": None,
                "last_point": None,
                "last_face_index": None,
                "last_seam_pair": None,
                "seam_mouse": None,
                "seam_time": 0.0,
                "identity_point": None,
                "identity_face_index": None,
                "raw_depth": int(
                    raw_depth
                ),
                "last_seen_serial": int(
                    self._surface_sample_serial
                ),
                "blocked": True,
                "footprint_blocked": False,
            }

            self._next_surface_track_id = max(
                self._next_surface_track_id,
                int(
                    track_key
                )
                + 1
            )
            self._surface_track_created_count += 1
            state = self._surface_tracks[
                track_key
            ]

        if (
            state[
                "segment_index"
            ] is None
            or bool(
                state.get(
                    "blocked",
                    False
                )
            )
        ):
            state[
                "segment_index"
            ] = self._begin_segment()

            state[
                "blocked"
            ] = False

            state[
                "footprint_blocked"
            ] = False

        seam_mouse = state[
            "seam_mouse"
        ]

        if seam_mouse is not None:
            if (
                now
                - state[
                    "seam_time"
                ]
                > SEAM_CHATTER_TIME_WINDOW
                or math.hypot(
                    current_mouse[0]
                    - seam_mouse[0],
                    current_mouse[1]
                    - seam_mouse[1],
                ) > SEAM_CHATTER_SCREEN_DISTANCE
            ):
                state[
                    "seam_mouse"
                ] = None

                state[
                    "seam_time"
                ] = 0.0

        last_point = state[
            "last_point"
        ]

        if last_point is not None:
            if math.hypot(
                point[0]
                - last_point[0],
                point[1]
                - last_point[1],
            ) < 0.75:
                return False

            last_face_index = state[
                "last_face_index"
            ]

            if last_face_index is not None:
                uv_continuous, transition_reason = (
                    _faces_uv_continuous(
                        self._raycast_mesh,
                        last_face_index,
                        face_index,
                        uv_layer_name=self._raycast_uv_layer_name
                    )
                )

                if not uv_continuous:
                    seam_pair = tuple(
                        sorted(
                            (
                                int(
                                    last_face_index
                                ),
                                int(
                                    face_index
                                ),
                            )
                        )
                    )

                    if (
                        transition_reason == "uv-seam"
                        and seam_pair == state[
                            "last_seam_pair"
                        ]
                        and state[
                            "seam_mouse"
                        ] is not None
                    ):
                        self._seam_suppressed_count += 1

                        print(
                            "BLENDGIMP: "
                            "Direct paint seam chatter suppressed "
                            f"depth={raw_depth} track={track_key} "
                            f"face {last_face_index} -> {face_index}"
                        )

                        return False

                    self._topology_split_count += 1
                    self._last_topology_split_reason = transition_reason

                    print(
                        "BLENDGIMP: "
                        "Direct paint topology split "
                        f"depth={raw_depth} track={track_key} "
                        f"face {last_face_index} -> "
                        f"{face_index}: {transition_reason}"
                    )

                    if transition_reason == "uv-seam":
                        state[
                            "last_seam_pair"
                        ] = seam_pair

                        state[
                            "seam_mouse"
                        ] = current_mouse

                        state[
                            "seam_time"
                        ] = now

                    state[
                        "segment_index"
                    ] = self._begin_segment()

        self._segments[
            state[
                "segment_index"
            ]
        ].append(
            point
        )

        state[
            "last_point"
        ] = point

        state[
            "last_face_index"
        ] = int(
            face_index
        )

        if int(
            raw_depth
        ) == 0:
            self._last_point = point
            self._last_face_index = int(
                face_index
            )
            self._last_crossed_seam_pair = state[
                "last_seam_pair"
            ]
            self._seam_hysteresis_mouse = state[
                "seam_mouse"
            ]
            self._seam_hysteresis_time = state[
                "seam_time"
            ]

        return True

    def _block_missing_surface_tracks(
        self,
        seen_tracks
    ):
        for track_key, state in self._surface_tracks.items():
            if track_key in seen_tracks:
                continue

            self._block_surface_track(
                track_key,
                int(
                    state.get(
                        "raw_depth",
                        -1
                    )
                ),
                footprint=False
            )

    def _event_window_position(
        self,
        event
    ):
        return (
            float(
                event.mouse_x
            ),
            float(
                event.mouse_y
            ),
        )

    def _release_seam_hysteresis_if_needed(
        self,
        current_mouse,
        now
    ):
        if self._seam_hysteresis_mouse is None:
            return

        elapsed = (
            float(
                now
            )
            - self._seam_hysteresis_time
        )

        distance = math.hypot(
            current_mouse[0]
            - self._seam_hysteresis_mouse[0],
            current_mouse[1]
            - self._seam_hysteresis_mouse[1],
        )

        if (
            elapsed > SEAM_CHATTER_TIME_WINDOW
            or distance > SEAM_CHATTER_SCREEN_DISTANCE
        ):
            self._seam_hysteresis_mouse = None
            self._seam_hysteresis_time = 0.0

    def _sample_interpolated(
        self,
        context,
        event
    ):
        """
        Insert intermediate surface raycasts for sparse pointer movement.

        Texture-space spacing is derived from the active GIMP brush diameter
        and GIMP brush spacing. A screen-space ceiling also guarantees that UV
        compression cannot allow very large jumps over the 3D surface.
        """

        current_mouse = self._event_window_position(
            event
        )

        if self._last_mouse_position is None:

            self._last_mouse_position = (
                current_mouse
            )

            return (
                1
                if self._sample(
                    context,
                    event
                )
                else 0
            )

        previous_mouse = (
            self._last_mouse_position
        )

        endpoint_sample = _raycast_uv_point(
            context,
            self._window_region,
            event,
            self.image_width,
            self.image_height,
            self._front_faces_only,
            self._normal_angle_enabled,
            self._normal_angle_limit,
            raycast_object=self._raycast_object,
            raycast_mesh=self._raycast_mesh,
            raycast_bvh=self._raycast_bvh,
            uv_layer_name=self._raycast_uv_layer_name
        )

        if (
            endpoint_sample is None
            or self._last_point is None
        ):

            self._last_mouse_position = (
                current_mouse
            )

            return (
                1
                if self._sample(
                    context,
                    event
                )
                else 0
            )

        endpoint_x = float(
            endpoint_sample[
                0
            ]
        )

        endpoint_y = float(
            endpoint_sample[
                1
            ]
        )

        texture_distance = math.hypot(
            endpoint_x
            - self._last_point[
                0
            ],
            endpoint_y
            - self._last_point[
                1
            ],
        )

        screen_distance = math.hypot(
            current_mouse[
                0
            ]
            - previous_mouse[
                0
            ],
            current_mouse[
                1
            ]
            - previous_mouse[
                1
            ],
        )

        spacing_fraction = (
            _normalize_gimp_brush_spacing(
                self._brush_spacing
            )
        )

        # Half nominal brush-stamp spacing gives enough geometric samples to
        # follow curvature and seam transitions. GIMP still performs its own
        # normal paintbrush interpolation between these control points.
        texture_step = max(
            INTERPOLATION_MIN_TEXTURE_STEP,
            float(
                self._brush_size
            )
            * spacing_fraction
            * 0.5
        )

        texture_steps = max(
            1,
            int(
                math.ceil(
                    texture_distance
                    / texture_step
                )
            )
        )

        screen_steps = max(
            1,
            int(
                math.ceil(
                    screen_distance
                    / INTERPOLATION_MAX_SCREEN_STEP
                )
            )
        )

        step_count = min(
            INTERPOLATION_MAX_STEPS,
            max(
                texture_steps,
                screen_steps
            )
        )

        added_count = 0

        for step_index in range(
            1,
            step_count
            + 1
        ):

            t = (
                float(
                    step_index
                )
                / float(
                    step_count
                )
            )

            sample_event = SimpleNamespace(
                mouse_x=(
                    previous_mouse[
                        0
                    ]
                    + (
                        current_mouse[
                            0
                        ]
                        - previous_mouse[
                            0
                        ]
                    )
                    * t
                ),
                mouse_y=(
                    previous_mouse[
                        1
                    ]
                    + (
                        current_mouse[
                            1
                        ]
                        - previous_mouse[
                            1
                        ]
                    )
                    * t
                ),
            )

            if self._sample(
                context,
                sample_event
            ):
                added_count += 1

        self._last_mouse_position = (
            current_mouse
        )

        inserted = max(
            0,
            added_count
            - 1
        )

        if inserted:

            self._interpolated_sample_count += (
                inserted
            )

        return added_count


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

        active_segment_indices = {
            int(
                state[
                    "segment_index"
                ]
            )
            for state in self._surface_tracks.values()
            if (
                state.get(
                    "segment_index"
                ) is not None
                and not bool(
                    state.get(
                        "blocked",
                        False
                    )
                )
            )
        }

        segment_payloads = []
        transmitted_segments = []

        for segment_index, segment in enumerate(
            self._segments
        ):

            if not segment:
                continue

            segment_is_closed = (
                segment_index
                not in active_segment_indices
            )

            if (
                not force
                and not segment_is_closed
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

            segment_payloads.append(
                coordinates
            )

            transmitted_segments.append(
                (
                    segment_index,
                    segment,
                    points,
                )
            )

        if not segment_payloads:
            return 0

        response = (
            connection_manager.paint_stroke_segments_chunk(
                self.image_id,
                self._layer_id,
                self._stroke_id,
                segment_payloads
            )
        )

        transmitted = int(
            response.get(
                "point_count",
                sum(
                    len(
                        coordinates
                    )
                    // 2
                    for coordinates in segment_payloads
                )
            )
        )

        transmitted_segment_count = int(
            response.get(
                "segment_count",
                len(
                    segment_payloads
                )
            )
        )

        self._streamed_chunks += 1
        self._streamed_segments += transmitted_segment_count
        self._streamed_points += transmitted

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

        self._brush_spacing = float(
            response.get(
                "brush_spacing",
                self._brush_spacing
            )
        )

        for segment_index, segment, points in transmitted_segments:
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
                f"chunks={self._streamed_chunks}; "
                f"segments={transmitted_segment_count} "
                f"(total={self._streamed_segments})"
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

        self._brush_spacing = float(
            response.get(
                "brush_spacing",
                self._brush_spacing
            )
        )

        self._last_chunk_time = time.monotonic()
        self._last_viewport_refresh_time = 0.0
        self._streamed_chunks = 0
        self._streamed_segments = 0
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

        self._projection_mode = str(
            getattr(
                scene,
                "blendgimp_direct_paint_projection_mesh",
                "AUTO"
            )
        ).upper()

        try:
            projection = _resolve_projection_mesh(
                context,
                obj,
                self._projection_mode
            )

            self._source_object = obj
            self._source_mesh = obj.data
            self._raycast_object = projection[
                "object"
            ]
            self._raycast_mesh = projection[
                "mesh"
            ]
            self._depsgraph = projection[
                "depsgraph"
            ]
            self._raycast_uv_layer_name = projection[
                "uv_layer_name"
            ]
            self._projection_mesh_kind = projection[
                "kind"
            ]
            self._projection_fallback_count = (
                1
                if projection[
                    "fallback"
                ]
                else 0
            )
            self._projection_fallback_reason = projection[
                "fallback_reason"
            ]
            self._raycast_bvh = _build_mesh_bvh(
                self._raycast_mesh
            )

            if self._raycast_bvh is None:
                raise RuntimeError(
                    "projection mesh has no ray-castable polygons"
                )

        except Exception as exc:
            self.report(
                {"ERROR"},
                f"Could not prepare direct-paint projection mesh: {exc}"
            )
            return {"CANCELLED"}

        self._enabled_modifier_count = sum(
            1
            for modifier in obj.modifiers
            if bool(
                modifier.show_viewport
            )
        )
        self._source_vertex_count = len(
            self._source_mesh.vertices
        )
        self._source_polygon_count = len(
            self._source_mesh.polygons
        )
        self._projection_vertex_count = len(
            self._raycast_mesh.vertices
        )
        self._projection_polygon_count = len(
            self._raycast_mesh.polygons
        )
        self._evaluated_topology_changed = (
            self._source_vertex_count
            != self._projection_vertex_count
            or self._source_polygon_count
            != self._projection_polygon_count
        )

        if self._projection_fallback_count:
            print(
                "BLENDGIMP: "
                "Direct paint evaluated mesh fallback to original: "
                f"{self._projection_fallback_reason}"
            )

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

        self._brush_spacing = float(
            brush_state.get(
                "brush_spacing",
                0.10
            )
        )

        self._front_faces_only = bool(
            getattr(
                scene,
                "blendgimp_direct_paint_front_faces_only",
                True
            )
        )

        self._normal_angle_enabled = bool(
            getattr(
                scene,
                "blendgimp_direct_paint_normal_angle_enabled",
                False
            )
        )

        self._normal_angle_limit = float(
            getattr(
                scene,
                "blendgimp_direct_paint_normal_angle_limit",
                75.0
            )
        )

        self._occlusion_mode = str(
            getattr(
                scene,
                "blendgimp_direct_paint_occlusion_mode",
                "VISIBLE"
            )
        )

        self._paint_through = (
            self._occlusion_mode
            == "THROUGH"
        )

        self._footprint_protection = bool(
            getattr(
                scene,
                "blendgimp_direct_paint_footprint_protection",
                True
            )
        )

        self._footprint_sample_count = max(
            4,
            min(
                16,
                int(
                    getattr(
                        scene,
                        "blendgimp_direct_paint_footprint_samples",
                        8
                    )
                )
            )
        )

        self._footprint_safe_ratio = max(
            0.5,
            min(
                1.0,
                float(
                    getattr(
                        scene,
                        "blendgimp_direct_paint_footprint_safe_ratio",
                        0.75
                    )
                )
            )
        )

        self._uv_face_adjacency = _build_uv_face_adjacency(
            self._raycast_mesh,
            self._raycast_uv_layer_name
        )

        self._mesh_face_adjacency = _build_mesh_face_adjacency(
            self._raycast_mesh
        )

        self._geometry_rejections = {
            "backface": 0,
            "normal-angle": 0,
        }

        self._surface_tracks = {}
        self._raw_depth_track_map = {}
        self._next_surface_track_id = 0
        self._surface_sample_serial = 0
        self._surface_track_created_count = 0
        self._surface_track_reassignment_count = 0
        self._footprint_track_break_count = 0
        self._occlusion_check_count = 0
        self._paint_through_point_count = 0
        self._footprint_check_count = 0
        self._footprint_ray_count = 0
        self._footprint_rejected_count = 0
        self._footprint_adaptive_accepted_count = 0
        self._footprint_silhouette_rejected_count = 0
        self._footprint_boundary_rejected_count = 0
        self._footprint_hysteresis_held_count = 0
        self._footprint_hysteresis_resumed_count = 0
        self._footprint_radius_total = 0.0
        self._footprint_radius_sample_count = 0

        self._painting = False
        self._segments = []
        self._segment_overlap = []
        self._last_point = None
        self._last_face_index = None
        self._last_mouse_position = None
        self._interpolated_sample_count = 0
        self._topology_split_count = 0
        self._last_topology_split_reason = ""
        self._last_crossed_seam_pair = None
        self._seam_hysteresis_mouse = None
        self._seam_hysteresis_time = 0.0
        self._seam_suppressed_count = 0
        self._stroke_id = None
        self._last_chunk_time = 0.0
        self._last_viewport_refresh_time = 0.0
        self._streamed_chunks = 0
        self._streamed_segments = 0
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
            f"brush={self._brush_name}, size={self._brush_size:.1f}, "
            f"front_faces_only={self._front_faces_only}, "
            f"normal_angle_enabled={self._normal_angle_enabled}, "
            f"normal_angle_limit={self._normal_angle_limit:.1f}, "
            f"occlusion_mode={self._occlusion_mode.lower()}, "
            f"footprint_protection={self._footprint_protection}, "
            f"footprint_samples={self._footprint_sample_count}, "
            f"footprint_safe_ratio={self._footprint_safe_ratio:.3f}, "
            f"projection_mesh={self._projection_mesh_kind}, "
            f"projection_mode={self._projection_mode.lower()}, "
            f"projection_uv={self._raycast_uv_layer_name}, "
            f"modifiers={self._enabled_modifier_count}, "
            f"source_faces={self._source_polygon_count}, "
            f"projection_faces={self._projection_polygon_count}, "
            f"topology_changed={str(self._evaluated_topology_changed).lower()}, "
            "projection_index_space=mesh-bvh, "
            "stable_surface_tracks=True"
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
                self._last_mouse_position = None
                self._interpolated_sample_count = 0
                self._topology_split_count = 0
                self._last_topology_split_reason = ""
                self._last_crossed_seam_pair = None
                self._seam_hysteresis_mouse = None
                self._seam_hysteresis_time = 0.0
                self._seam_suppressed_count = 0
                self._geometry_rejections = {
                    "backface": 0,
                    "normal-angle": 0,
                }
                self._surface_tracks = {}
                self._raw_depth_track_map = {}
                self._next_surface_track_id = 0
                self._surface_sample_serial = 0
                self._surface_track_created_count = 0
                self._surface_track_reassignment_count = 0
                self._footprint_track_break_count = 0
                self._occlusion_check_count = 0
                self._paint_through_point_count = 0
                self._footprint_check_count = 0
                self._footprint_ray_count = 0
                self._footprint_rejected_count = 0
                self._footprint_adaptive_accepted_count = 0
                self._footprint_silhouette_rejected_count = 0
                self._footprint_boundary_rejected_count = 0
                self._footprint_hysteresis_held_count = 0
                self._footprint_hysteresis_resumed_count = 0
                self._footprint_radius_total = 0.0
                self._footprint_radius_sample_count = 0
                self._stroke_id = None
                self._last_chunk_time = 0.0
                self._last_viewport_refresh_time = 0.0
                self._streamed_chunks = 0
                self._streamed_segments = 0
                self._streamed_points = 0

                point_added = (
                    self._sample_interpolated(
                        context,
                        event
                    )
                    > 0
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

                point_added = (
                    self._sample_interpolated(
                        context,
                        event
                    )
                    > 0
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

                    backface_rejected = int(
                        self._geometry_rejections.get(
                            "backface",
                            0
                        )
                    )

                    angle_rejected = int(
                        self._geometry_rejections.get(
                            "normal-angle",
                            0
                        )
                    )

                    geometry_rejected = (
                        backface_rejected
                        + angle_rejected
                    )

                    average_footprint_radius = (
                        self._footprint_radius_total
                        / self._footprint_radius_sample_count
                        if self._footprint_radius_sample_count > 0
                        else 0.0
                    )

                    self._set_status(
                        context,
                        (
                            f"Completed live GIMP stroke: "
                            f"{self._streamed_points} point(s), "
                            f"{self._streamed_chunks} chunk(s), "
                            f"{self._streamed_segments} segment(s), "
                            f"{self._topology_split_count} UV split(s), "
                            f"{self._seam_suppressed_count} seam suppression(s), "
                            f"{self._interpolated_sample_count} interpolated, "
                            f"{geometry_rejected} geometry rejection(s), "
                            f"{self._paint_through_point_count} through point(s), "
                            f"{self._footprint_rejected_count} footprint rejection(s)"
                        )
                    )

                    print(
                        "BLENDGIMP: "
                        "Direct GIMP live 3D stroke completed: "
                        f"{self._streamed_points} point(s), "
                        f"{self._streamed_chunks} chunk(s), "
                        f"segments={self._streamed_segments}, "
                        f"uv_splits={self._topology_split_count}, "
                        f"seam_suppressed={self._seam_suppressed_count}, "
                        f"interpolated={self._interpolated_sample_count}, "
                        f"geometry_rejected={geometry_rejected}, "
                        f"backface_rejected={backface_rejected}, "
                        f"angle_rejected={angle_rejected}, "
                        f"occlusion_mode={self._occlusion_mode.lower()}, "
                        f"occlusion_checks={self._occlusion_check_count}, "
                        f"paint_through_points={self._paint_through_point_count}, "
                        f"footprint_protection={str(self._footprint_protection).lower()}, "
                        f"footprint_samples={self._footprint_sample_count}, "
                        f"footprint_safe_ratio={self._footprint_safe_ratio:.3f}, "
                        f"footprint_checks={self._footprint_check_count}, "
                        f"footprint_rays={self._footprint_ray_count}, "
                        f"footprint_rejected={self._footprint_rejected_count}, "
                        f"footprint_adaptive_accepted={self._footprint_adaptive_accepted_count}, "
                        f"silhouette_rejected={self._footprint_silhouette_rejected_count}, "
                        f"boundary_rejected={self._footprint_boundary_rejected_count}, "
                        f"hysteresis_held={self._footprint_hysteresis_held_count}, "
                        f"hysteresis_resumed={self._footprint_hysteresis_resumed_count}, "
                        f"footprint_breaks={self._footprint_track_break_count}, "
                        f"footprint_radius={average_footprint_radius:.1f}px, "
                        f"surface_tracks={self._surface_track_created_count}, "
                        f"track_reassignments={self._surface_track_reassignment_count}, "
                        f"projection_mesh={self._projection_mesh_kind}, "
                        f"projection_mode={self._projection_mode.lower()}, "
                        f"projection_fallbacks={self._projection_fallback_count}, "
                        f"modifiers={self._enabled_modifier_count}, "
                        f"source_faces={self._source_polygon_count}, "
                        f"projection_faces={self._projection_polygon_count}, "
                        f"topology_changed={str(self._evaluated_topology_changed).lower()}, "
                        "projection_index_space=mesh-bvh, "
                        f"brush={self._brush_name}, "
                        f"spacing={self._brush_spacing:.3f}"
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

            added_samples = (
                self._sample_interpolated(
                    context,
                    event
                )
            )

            if added_samples > 0:

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
