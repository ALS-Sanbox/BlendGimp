from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PAINT = (ROOT / "blender" / "blendgimp" / "ui" / "paint_tools.py").read_text(encoding="utf-8")
MANIFEST = (ROOT / "blender" / "blendgimp" / "blender_manifest.toml").read_text(encoding="utf-8")
IPC = (ROOT / "blender" / "blendgimp" / "ipc" / "connection.py").read_text(encoding="utf-8")
GIMP = (ROOT / "gimp" / "blendgimp" / "blendgimp.py").read_text(encoding="utf-8")

checks = {
    "version": 'version = "0.5.20"' in MANIFEST,
    "build id": 'BUILD_ID = "7.2-brush-preview-gpu-float-hotfix"' in PAINT,
    "ipc version": 'BLENDGIMP_VERSION = "0.5.20"' in IPC,
    "gimp version": 'BLENDGIMP_VERSION = "0.5.20"' in GIMP,
    "float buffer upload": 'gpu.types.Buffer("FLOAT", len(float_rgba), float_rgba)' in PAINT,
    "rgba16f texture": 'format="RGBA16F"' in PAINT,
    "normalized bytes": 'value / 255.0 for value in rgba' in PAINT,
    "cached normalized data": 'entry["float_rgba"] = float_rgba' in PAINT,
    "no ubyte gpu upload": 'gpu.types.Buffer("UBYTE", len(rgba), rgba)' not in PAINT,
    "gpu success diagnostic": 'GPU GIMP brush preview texture ready' in PAINT,
    "gimp preview transport retained": 'message_type == "GET_BRUSH_PREVIEW"' in GIMP,
    "babl mask fix retained": 'Babl.format("Y u8")' in GIMP,
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit("FAIL: " + ", ".join(failed))
print(f"BlendGimp 0.5.20 brush-preview GPU FLOAT hotfix contract: {len(checks)}/{len(checks)} PASS")
