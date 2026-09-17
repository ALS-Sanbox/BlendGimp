from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEX = (ROOT / "blender/blendgimp/ui/texture_editor.py").read_text(encoding="utf-8")
CONN = (ROOT / "blender/blendgimp/ipc/connection.py").read_text(encoding="utf-8")
GIMP = (ROOT / "gimp/blendgimp/blendgimp.py").read_text(encoding="utf-8")
MANIFEST = (ROOT / "blender/blendgimp/blender_manifest.toml").read_text(encoding="utf-8")
PAINT = (ROOT / "blender/blendgimp/ui/paint_tools.py").read_text(encoding="utf-8")

checks = {
    "version": 'version = "0.5.20"' in MANIFEST,
    "build id": 'BUILD_ID = "7.2-brush-preview-gpu-float-hotfix"' in PAINT,
    "gimp component version": 'BLENDGIMP_VERSION = "0.5.20"' in GIMP,
    "selection combine enum": 'SELECTION_OPERATION_ITEMS' in TEX and '"INTERSECT"' in TEX,
    "selection combine property": 'blendgimp_selection_operation' in TEX,
    "free select operator": 'class BLENDGIMP_OT_selection_free' in TEX,
    "free select gimp function": 'def gimp_select_polygon' in GIMP,
    "free select direct GI signature": 'image.select_polygon(channel_op, flat)' in GIMP,
    "free select ipc": '"type": "SELECT_POLYGON"' in CONN,
    "free points state": 'blendgimp_selection_points_json' in TEX,
    "free inverse preserve": 'old_shape in {"RECTANGLE", "ELLIPSE", "FREE"}' in TEX,
    "fuzzy operator": '"FUZZY", "Fuzzy Select"' in TEX,
    "fuzzy gimp": 'def gimp_select_fuzzy' in GIMP and 'select_contiguous_color' in GIMP,
    "fuzzy ipc": '"type": "SELECT_FUZZY"' in CONN,
    "by color operator": '"COLOR", "Select by Color"' in TEX,
    "by color gimp": 'def gimp_select_by_color' in GIMP and 'image.select_color' in GIMP,
    "by color ipc": '"type": "SELECT_BY_COLOR"' in CONN,
    "threshold property": 'blendgimp_selection_threshold' in TEX,
    "sample merged property": 'blendgimp_selection_sample_merged' in TEX,
    "sample transparent property": 'blendgimp_selection_sample_transparent' in TEX,
    "grow": 'Gimp.Selection.grow' in GIMP and '"GROW"' in TEX,
    "shrink": 'Gimp.Selection.shrink' in GIMP and '"SHRINK"' in TEX,
    "feather": 'Gimp.Selection.feather' in GIMP and '"FEATHER"' in TEX,
    "border": 'Gimp.Selection.border' in GIMP and '"BORDER"' in TEX,
    "modify ipc": 'def modify_selection' in CONN,
    "radius property": 'blendgimp_selection_radius' in TEX,
    "replace add subtract intersect GIMP": all(x in GIMP for x in ['"REPLACE"', '"ADD"', '"SUBTRACT"', '"INTERSECT"']),
    "rectangle operation IPC": 'def select_rectangle(self, image_id, x, y, width, height, operation="REPLACE")' in CONN,
    "ellipse operation IPC": 'def select_ellipse(self, image_id, x, y, width, height, operation="REPLACE")' in CONN,
    "free preview runtime": '_SELECTION_FREE_PREVIEW' in TEX,
    "dotted overlay preserved": '_selection_dashed_segments' in TEX,
    "group collapse preserved": 'blendgimp_collapsed_group_ids' in (ROOT / "blender/blendgimp/ui/main_panel.py").read_text(encoding="utf-8"),
}
failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(("PASS" if ok else "FAIL"), name)
print(f"BlendGimp 0.5.20 expanded selections contract: {len(checks)-len(failed)}/{len(checks)} PASS")
if failed:
    raise SystemExit("FAILED: " + ", ".join(failed))
