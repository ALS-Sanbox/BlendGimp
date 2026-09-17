from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BLENDER = ROOT / "blender" / "blendgimp"
PAINT = (BLENDER / "ui" / "paint_tools.py").read_text(encoding="utf-8")
IPC = (BLENDER / "ipc" / "connection.py").read_text(encoding="utf-8")
GIMP = (ROOT / "gimp" / "blendgimp" / "blendgimp.py").read_text(encoding="utf-8")
MANIFEST = (BLENDER / "blender_manifest.toml").read_text(encoding="utf-8")

checks = {
    "version": 'version = "0.5.20"' in MANIFEST,
    "build id": 'BUILD_ID = "7.2-brush-preview-gpu-float-hotfix"' in PAINT,
    "ipc version": 'BLENDGIMP_VERSION = "0.5.20"' in IPC,
    "gimp version": 'BLENDGIMP_VERSION = "0.5.20"' in GIMP,
    "gimp preview function": "def gimp_get_brush_preview" in GIMP,
    "babl namespace required": 'gi.require_version("Babl", "0.1")' in GIMP,
    "babl imported": "Gegl, Babl" in GIMP,
    "explicit gimp mask format": 'mask_format = Babl.format("Y u8")' in GIMP,
    "real gimp mask api": "brush.get_mask(max_size, max_size, mask_format)" in GIMP,
    "nullable format regression removed": "brush.get_mask(max_size, max_size, None)" not in GIMP,
    "gimp mask transport": '"mask_b64": base64.b64encode(raw).decode("ascii")' in GIMP,
    "gimp mask checksum": '"mask_sha256"' in GIMP,
    "gimp preview command": 'message_type == "GET_BRUSH_PREVIEW"' in GIMP,
    "ipc preview command": '"type": "GET_BRUSH_PREVIEW"' in IPC,
    "ipc preview response": 'response.get("type") != "BRUSH_PREVIEW"' in IPC,
    "brush preview cache": "_BRUSH_PREVIEW_CACHE = {}" in PAINT,
    "cache refresh": "def _refresh_active_brush_preview" in PAINT,
    "draw callback nonblocking": "draw callbacks never block on IPC" in PAINT,
    "gpu mask texture": 'gpu.types.GPUTexture((width, height), format="RGBA16F"' in PAINT and 'gpu.types.Buffer("FLOAT"' in PAINT,
    "image color shader": 'gpu.shader.from_builtin("IMAGE_COLOR")' in PAINT,
    "real dab draw": "def _draw_real_brush_dabs" in PAINT,
    "mask aspect": "def _brush_mask_radii" in PAINT,
    "brush rotation": "def _rotated_stamp_geometry" in PAINT and "blendgimp_brush_angle" in PAINT,
    "gimp spacing preview": "blendgimp_brush_spacing_percent" in PAINT and "diameter_screen * spacing_fraction" in PAINT,
    "cursor real stamp": "self._draw_real_brush_dabs([cursor_point], [1.0]" in PAINT,
    "stroke real stamp": "if not self._draw_real_brush_dabs(points, pressures, rx, ry, color)" in PAINT,
    "fallback circle retained": "Fallback circular dabs when the GIMP brush mask is unavailable" in PAINT,
    "left bracket hotkey": 'event.type in {"LEFT_BRACKET", "RIGHT_BRACKET"}' in PAINT,
    "normal size step": "step = 10.0 if bool(getattr(event, \"shift\", False)) else 1.0" in PAINT,
    "size property immediate": "scene.blendgimp_brush_size = target" in PAINT,
    "hotkey status": "([ / ] resize; Shift = 10px)" in PAINT,
    "hotkey ui hint": "[ / ] resize under cursor" in PAINT,
    "progressive authoritative path retained": "2D progressive GIMP update applied" in PAINT,
    "async worker retained": 'self._worker.submit("BEGIN"' in PAINT,
}

failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit("FAILED: " + ", ".join(failed))
print(f"BlendGimp 0.5.20 real brush preview/hotkeys contract: {len(checks)}/{len(checks)} PASS")
