from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BLENDER = ROOT / "blender" / "blendgimp"
PAINT = (BLENDER / "ui" / "paint_tools.py").read_text(encoding="utf-8")
MANIFEST = (BLENDER / "blender_manifest.toml").read_text(encoding="utf-8")
IPC = (BLENDER / "ipc" / "connection.py").read_text(encoding="utf-8")
GIMP = (ROOT / "gimp" / "blendgimp" / "blendgimp.py").read_text(encoding="utf-8")

checks = {
    "version": 'version = "0.5.20"' in MANIFEST,
    "ipc version": 'BLENDGIMP_VERSION = "0.5.20"' in IPC,
    "gimp version": 'BLENDGIMP_VERSION = "0.5.20"' in GIMP,
    "build id": 'BUILD_ID = "7.2-brush-preview-gpu-float-hotfix"' in PAINT,
    "preview redraw helper": "def _tag_texture_preview_redraw():" in PAINT,
    "brush property redraw": "_tag_texture_preview_redraw()" in PAINT,
    "cursor point state": "self._cursor_preview_point = None" in PAINT,
    "cursor event tracking": "def _update_cursor_preview(self, event):" in PAINT,
    "image-pixel brush conversion": "def _brush_screen_radii" in PAINT and "blendgimp_brush_size" in PAINT and "region.view2d.view_to_region" in PAINT,
    "high contrast cursor": "def _draw_brush_cursor" in PAINT and '(0.0, 0.0, 0.0, 0.92)' in PAINT and '(1.0, 1.0, 1.0, 0.98)' in PAINT,
    "hardness cue": "blendgimp_brush_hardness" in PAINT and "rx * hardness" in PAINT,
    "filled dab preview": "def _stroke_preview_vertices" in PAINT and 'batch_for_shader(shader, "TRIS"' in PAINT,
    "pressure preview": "self._preview_pressures" in PAINT and "pressure = max(0.05" in PAINT,
    "continuous interpolation": "distance / spacing" in PAINT and "TEXTURE_PREVIEW_MAX_DABS" in PAINT,
    "airbrush footprint": '"AIRBRUSH", "SMUDGE", "CLONE", "HEAL"' in PAINT,
    "gradient preview retained": 'active_tool == "GRADIENT"' in PAINT and '"LINE_STRIP"' in PAINT,
    "progressive trim pressures": "del pressures[:removed]" in PAINT,
    "authoritative progressive dirty path retained": "2D progressive GIMP update applied" in PAINT and "_apply_dirty_result" in PAINT,
    "async worker retained": 'self._worker.submit("BEGIN"' in PAINT and 'self._worker.submit(\n            "CHUNK"' in PAINT,
    "brush opacity preview": "blendgimp_brush_opacity" in PAINT and "float(fg[3]) * 0.95" in PAINT,
}

failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit("FAILED: " + ", ".join(failed))
print(f"BlendGimp 0.5.20 live Texture preview contract: {len(checks)}/{len(checks)} PASS")
