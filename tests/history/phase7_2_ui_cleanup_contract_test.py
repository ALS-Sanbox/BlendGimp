from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BLENDER = ROOT / "blender" / "blendgimp"
TEX = (BLENDER / "ui" / "texture_editor.py").read_text(encoding="utf-8")
PAINT = (BLENDER / "ui" / "paint_tools.py").read_text(encoding="utf-8")
MANIFEST = (BLENDER / "blender_manifest.toml").read_text(encoding="utf-8")
IPC = (BLENDER / "ipc" / "connection.py").read_text(encoding="utf-8")
GIMP = (ROOT / "gimp" / "blendgimp" / "blendgimp.py").read_text(encoding="utf-8")

checks = {
    "version": 'version = "0.5.20"' in MANIFEST,
    "ipc version": 'BLENDGIMP_VERSION = "0.5.20"' in IPC,
    "gimp version": 'BLENDGIMP_VERSION = "0.5.20"' in GIMP,
    "build id": 'BUILD_ID = "7.2-brush-preview-gpu-float-hotfix"' in PAINT,
    "native editor auto enrollment": "def _ensure_default_native_areas(context):" in TEX,
    "texture auto enrollment": '(MODE_TEXTURE, "IMAGE_EDITOR")' in TEX,
    "object auto enrollment": '(MODE_OBJECT, "VIEW_3D")' in TEX,
    "header launcher not appended": "IMAGE_HT_header.append(_draw_blendgimp_header)" not in TEX and "VIEW3D_HT_header.append(_draw_blendgimp_header)" not in TEX,
    "launcher panels not registered": "    BLENDGIMP_PT_area_launcher_image,\n" not in TEX and "    BLENDGIMP_PT_area_launcher_view3d,\n" not in TEX,
    "legacy use area internal": 'bl_label = "Use Area as BlendGimp (Legacy)"' in TEX and 'bl_options = {"INTERNAL"}' in TEX,
    "no return area production button": 'layout.operator("blendgimp.disable_area", text="Return Area to Previous Editor"' not in TEX,
    "object panel renamed": 'class BLENDGIMP_PT_object_area' in TEX and 'bl_label = "BlendGimp"' in TEX,
    "swap operator": 'class BLENDGIMP_OT_swap_fg_bg' in PAINT,
    "swap registered": '    BLENDGIMP_OT_swap_fg_bg,' in PAINT,
    "swap ui": 'colors.operator("blendgimp.swap_fg_bg", text="", icon="ARROW_LEFTRIGHT")' in PAINT,
    "atomic swap guard": '_STATE_SYNCING = True' in PAINT and 'scene.blendgimp_foreground_color = bg' in PAINT and 'scene.blendgimp_background_color = fg' in PAINT,
    "single authoritative push": '_push_scene_brush_state(scene, quiet=False, force=True)' in PAINT,
    "selection contour preserved": '_SELECTION_OUTLINE_CACHE' in TEX,
    "by color retained": 'select_by_color(' in TEX,
    "phase 6 router retained": 'automatic pointer routing armed' in TEX,
}

failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit("FAILED: " + ", ".join(failed))
print(f"BlendGimp 0.5.20 UI cleanup contract: {len(checks)}/{len(checks)} PASS")
