from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "ui" / "main_panel.py").read_text(encoding="utf-8")
MANIFEST = (ROOT / "blender_manifest.toml").read_text(encoding="utf-8")
PAINT = (ROOT / "ui" / "paint_tools.py").read_text(encoding="utf-8")

checks = {
    "version": 'version = "0.5.20"' in MANIFEST,
    "build": '7.2-brush-preview-gpu-float-hotfix' in PAINT,
    "collapse property registered": 'bpy.types.Scene.blendgimp_collapsed_group_ids = (' in MAIN,
    "collapse property string": 'name="Collapsed BlendGimp Group IDs"' in MAIN,
    "collapse setter defensive": 'scene["_blendgimp_collapsed_group_ids"] = encoded' in MAIN,
    "collapse getter defensive": 'scene.get("_blendgimp_collapsed_group_ids", "")' in MAIN,
    "unregister guarded": 'hasattr(bpy.types.Scene, "blendgimp_collapsed_group_ids")' in MAIN,
    "disclosure operator": 'blendgimp.toggle_group_collapse' in MAIN,
    "down arrow": 'icon="TRIA_RIGHT" if collapsed else "TRIA_DOWN"' in MAIN,
    "collapsed children filtered": 'if children and layer_id not in collapsed_ids:' in MAIN,
}
failed = [name for name, ok in checks.items() if not ok]
print(f"BlendGimp 0.5.20 group-collapse hotfix contract: {len(checks)-len(failed)}/{len(checks)} PASS")
if failed:
    for name in failed:
        print("FAIL:", name)
    raise SystemExit(1)
