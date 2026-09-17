from pathlib import Path
import ast
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
BLENDER = ROOT / "blender" / "blendgimp"
GIMP_FILE = ROOT / "gimp" / "blendgimp" / "blendgimp.py"

FILES = {
    "manifest": (BLENDER / "blender_manifest.toml").read_text(encoding="utf-8"),
    "build": (BLENDER / "core" / "build_info.py").read_text(encoding="utf-8"),
    "init": (BLENDER / "__init__.py").read_text(encoding="utf-8"),
    "prefs": (BLENDER / "ui" / "preferences.py").read_text(encoding="utf-8"),
    "main": (BLENDER / "ui" / "main_panel.py").read_text(encoding="utf-8"),
    "paint": (BLENDER / "ui" / "paint_tools.py").read_text(encoding="utf-8"),
    "tex": (BLENDER / "ui" / "texture_editor.py").read_text(encoding="utf-8"),
    "ipc": (BLENDER / "ipc" / "connection.py").read_text(encoding="utf-8"),
    "stroke": (BLENDER / "painting" / "stroke_tool.py").read_text(encoding="utf-8"),
    "gimp": GIMP_FILE.read_text(encoding="utf-8"),
}

checks = {}

def check(name, condition):
    checks[name] = bool(condition)

# Release metadata / centralization.
check("manifest version", 'version = "0.5.21"' in FILES["manifest"])
check("central version", 'VERSION = "0.5.21"' in FILES["build"])
check("central build id", 'BUILD_ID = "7.2-code-cleanup-complete"' in FILES["build"])
check("gimp component version", 'BLENDGIMP_VERSION = "0.5.21"' in FILES["gimp"])
check("prefs imports build info", 'from ..core.build_info import VERSION as BLENDGIMP_VERSION, BUILD_ID' in FILES["prefs"])
check("paint imports build info", 'from ..core.build_info import BUILD_ID, DISPLAY_NAME' in FILES["paint"])
check("ipc imports build info", 'from ..core.build_info import VERSION as BLENDGIMP_VERSION, PROTOCOL_VERSION' in FILES["ipc"])
check("main imports display name", 'from ..core.build_info import DISPLAY_NAME' in FILES["main"])
check("root registration uses display name", 'print(f"BLENDGIMP: {DISPLAY_NAME} registered successfully")' in FILES["init"])

# Retired BlendGimp Area UI is physically gone, not merely hidden.
for token in (
    'bl_idname = "blendgimp.use_area"',
    'bl_idname = "blendgimp.switch_area_mode"',
    'bl_idname = "blendgimp.disable_area"',
    'class BLENDGIMP_OT_use_area',
    'class BLENDGIMP_OT_switch_area_mode',
    'class BLENDGIMP_OT_disable_area',
    'def _draw_blendgimp_header',
    'class _BLENDGIMP_PT_area_launcher_base',
    'BLENDGIMP_PT_area_launcher_image',
    'BLENDGIMP_PT_area_launcher_view3d',
    'blendgimp_area_last_mode',
):
    check(f"retired UI absent: {token}", token not in FILES["tex"])
check("native editor auto enrollment", 'def _ensure_default_native_areas(context):' in FILES["tex"])
check("texture/object native types", '(MODE_TEXTURE, "IMAGE_EDITOR"), (MODE_OBJECT, "VIEW_3D")' in FILES["tex"])
check("runtime reset helper", 'def _reset_texture_editor_runtime_state()' in FILES["tex"])
check("runtime reset clears contours", '_SELECTION_OUTLINE_CACHE.clear()' in FILES["tex"])
check("status label cleaned", 'name="BlendGimp Texture Status"' in FILES["tex"])

# 7.0/7.1 image/layer/mask architecture remains present.
for command in (
    "CREATE_IMAGE",
    "GET_IMAGE_LAYERS",
    "ADD_LAYER",
    "DELETE_LAYER",
    "RENAME_LAYER",
    "DUPLICATE_LAYER",
    "REORDER_LAYER",
    "CREATE_GROUP",
    "MERGE_VISIBLE_LAYERS",
    "FLATTEN_IMAGE",
    "DUPLICATE_GROUP",
    "SET_LAYER_LOCK",
    "SET_LAYER_MODE",
    "ADD_LAYER_MASK",
    "SET_LAYER_MASK_EDIT",
    "SET_LAYER_MASK_APPLY",
    "SET_LAYER_MASK_SHOW",
    "REMOVE_LAYER_MASK",
):
    check(f"GIMP command retained: {command}", f'message_type == "{command}"' in FILES["gimp"])
check("group collapse state", 'blendgimp_collapsed_group_ids' in FILES["main"])
check("color tag swatches", 'Color Tag visual swatches loaded' in FILES["main"])

# Selection system remains complete.
check("selection combine hotkeys", 'def _selection_operation_from_event(event):' in FILES["tex"])
check("selection intersect", 'return "INTERSECT"' in FILES["tex"])
check("committed selection stays visible", 'def _draw_committed_selection(' in FILES["tex"])
check("free selection", 'class BLENDGIMP_OT_selection_free' in FILES["tex"] and 'def gimp_select_polygon' in FILES["gimp"])
check("fuzzy selection", 'select_contiguous_color' in FILES["gimp"] and '"SELECT_FUZZY"' in FILES["ipc"])
check("by color selection", 'def gimp_select_by_color' in FILES["gimp"] and '"SELECT_BY_COLOR"' in FILES["ipc"])
check("by color native pick", 'def _gimp_pick_color_native(' in FILES["gimp"] and 'image.pick_color(' in FILES["gimp"])
check("by color criterion pinned", 'context_set_sample_criterion' in FILES["gimp"] and 'COMPOSITE' in FILES["gimp"])
check("selection contour cache", '_SELECTION_OUTLINE_CACHE = {}' in FILES["tex"])
check("selection outline extraction", 'def _gimp_selection_outline_points' in FILES["gimp"])
for op in ("grow", "shrink", "feather", "border"):
    check(f"selection {op}", f'Gimp.Selection.{op}' in FILES["gimp"])
check("free double click fallback", 'Free Select manual double-click commit' in FILES["tex"])

# Artist-facing paint cleanup remains intact.
check("FG/BG swap operator", 'class BLENDGIMP_OT_swap_fg_bg' in FILES["paint"])
check("FG/BG swap UI", 'colors.operator("blendgimp.swap_fg_bg", text="", icon="ARROW_LEFTRIGHT")' in FILES["paint"])
check("real brush preview command", '"type": "GET_BRUSH_PREVIEW"' in FILES["ipc"] and 'message_type == "GET_BRUSH_PREVIEW"' in FILES["gimp"])
check("Babl brush mask", 'Babl.format("Y u8")' in FILES["gimp"])
check("float GPU upload", 'gpu.types.Buffer("FLOAT"' in FILES["paint"] and 'format="RGBA16F"' in FILES["paint"])
check("real brush stamp draw", 'def _draw_real_brush_dabs' in FILES["paint"])
check("brush-size hotkeys", 'event.type in {"LEFT_BRACKET", "RIGHT_BRACKET"}' in FILES["paint"])
check("brush preview fallback", 'fallback-circle' in FILES["paint"])
check("progressive 2D path", '2D progressive GIMP update applied' in FILES["paint"])
check("async worker path", 'self._worker.submit("BEGIN"' in FILES["paint"])

# Frozen paint transport / ownership invariants.
for token in (
    "GET_IMAGE_PIXELS_BINARY",
    "GET_IMAGE_DIRTY_PIXELS_BINARY",
    "GET_LAYER_PIXELS_BINARY",
    "SET_LAYER_PIXELS_BINARY",
    "BEGIN_PAINT_STROKE",
    "PAINT_STROKE_CHUNK",
    "END_PAINT_STROKE",
):
    check(f"IPC transport retained: {token}", token in FILES["ipc"] and token in FILES["gimp"])
check("named refresh ownership", 'owners = _DIRECT_PAINT_REFRESH_RUNTIME.setdefault("owners", {})' in FILES["ipc"])
check("modifier-aware projection", 'projection_mesh=evaluated' in FILES["stroke"] or 'projection_mesh' in FILES["stroke"])
check("footprint protection", 'footprint_protection' in FILES["stroke"])
check("seam protection", 'seam_suppressed' in FILES["stroke"])

# Production payload cleanliness.
check("validation removed from runtime tree", not (BLENDER / "validation").exists())
check("build script excludes validation", "'validation'" not in (BLENDER / "buildaddon.bat").read_text(encoding="utf-8"))

# Every Python file parses. Also reject duplicate top-level class names per module.
parse_failures = []
duplicate_classes = []
for path in list(BLENDER.rglob("*.py")) + [GIMP_FILE]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        parse_failures.append(f"{path.relative_to(ROOT)}:{exc.lineno}:{exc.msg}")
        continue
    names = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    dup = sorted({name for name in names if names.count(name) > 1})
    if dup:
        duplicate_classes.append(f"{path.relative_to(ROOT)}:{','.join(dup)}")
check("all Python parses", not parse_failures)
check("no duplicate top-level classes", not duplicate_classes)

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
print(f"\nBlendGimp 0.5.21 release contract: {len(checks)-len(failed)}/{len(checks)} PASS")
if parse_failures:
    print("Parse failures:", *parse_failures, sep="\n  ")
if duplicate_classes:
    print("Duplicate classes:", *duplicate_classes, sep="\n  ")
if failed:
    print("Failed checks:", *failed, sep="\n  ")
    raise SystemExit(1)
