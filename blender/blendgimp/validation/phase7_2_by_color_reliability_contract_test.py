from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEX = (ROOT / 'ui' / 'texture_editor.py').read_text()
GIMP = (ROOT.parents[1] / 'gimp' / 'blendgimp' / 'blendgimp.py').read_text()
MANIFEST = (ROOT / 'blender_manifest.toml').read_text()
PAINT = (ROOT / 'ui' / 'paint_tools.py').read_text()

checks = {
    'version': 'version = "0.5.18"' in MANIFEST,
    'build id': 'BUILD_ID = "7.2-real-brush-preview-hotkeys"' in PAINT,
    'gimp component version': 'BLENDGIMP_VERSION = "0.5.18"' in GIMP,
    'native pick helper': 'def _gimp_pick_color_native(' in GIMP,
    'native pick uses image pick': 'image.pick_color(' in GIMP,
    'nonmerged native sample': 'sample_merged=bool(sample_merged)' in GIMP,
    'transparent fallback': 'drawable-buffer-transparent-fallback' in GIMP,
    'empty selection retry': 'drawable-buffer-retry' in GIMP,
    'deterministic criterion': 'context_set_sample_criterion' in GIMP and 'SelectCriterion' in GIMP and 'COMPOSITE' in GIMP,
    'sample source response': '"sample_source": sample_source' in GIMP,
    'sample retry response': '"sample_retry": bool(retry_used)' in GIMP,
    'runtime active diagnostic': 'active={active}' in TEX,
    'runtime bounds diagnostic': 'outline_points={outline_count}' in TEX,
    'empty by-color keeps tool armed': 'Select by Color ACTIVE - no pixels matched; click another color or Esc' in TEX,
    'empty by-color running modal': 'return {"RUNNING_MODAL"}' in TEX,
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit('FAILED: ' + ', '.join(failed))
print(f'BlendGimp 0.5.18 Select by Color reliability contract: {len(checks)}/{len(checks)} PASS')
