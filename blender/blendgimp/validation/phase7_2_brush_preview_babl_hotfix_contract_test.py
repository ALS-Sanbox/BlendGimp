from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GIMP = (ROOT / "gimp" / "blendgimp" / "blendgimp.py").read_text(encoding="utf-8")
MANIFEST = (ROOT / "blender" / "blendgimp" / "blender_manifest.toml").read_text(encoding="utf-8")
PAINT = (ROOT / "blender" / "blendgimp" / "ui" / "paint_tools.py").read_text(encoding="utf-8")
IPC = (ROOT / "blender" / "blendgimp" / "ipc" / "connection.py").read_text(encoding="utf-8")

checks = {
    "version": 'version = "0.5.20"' in MANIFEST,
    "build id": 'BUILD_ID = "7.2-brush-preview-gpu-float-hotfix"' in PAINT,
    "ipc version": 'BLENDGIMP_VERSION = "0.5.20"' in IPC,
    "gimp version": 'BLENDGIMP_VERSION = "0.5.20"' in GIMP,
    "Babl GI namespace": 'gi.require_version("Babl", "0.1")' in GIMP,
    "Babl import": 'from gi.repository import Gimp, GLib, Gio, Gegl, Babl' in GIMP,
    "Y u8 Babl format": 'mask_format = Babl.format("Y u8")' in GIMP,
    "explicit format passed": 'brush.get_mask(max_size, max_size, mask_format)' in GIMP,
    "None format removed": 'brush.get_mask(max_size, max_size, None)' not in GIMP,
    "preview IPC retained": 'message_type == "GET_BRUSH_PREVIEW"' in GIMP,
    "preview fallback retained": 'fallback-circle' in PAINT,
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise AssertionError("Failed: " + ", ".join(failed))
print(f"BlendGimp 0.5.20 brush-preview Babl hotfix contract: {len(checks)}/{len(checks)} PASS")
