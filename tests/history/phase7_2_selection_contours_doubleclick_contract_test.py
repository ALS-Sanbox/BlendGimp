from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEX = (ROOT / 'blender/blendgimp/ui/texture_editor.py').read_text(encoding='utf-8')
GIMP = (ROOT / 'gimp/blendgimp/blendgimp.py').read_text(encoding='utf-8')
MANIFEST = (ROOT / 'blender/blendgimp/blender_manifest.toml').read_text(encoding='utf-8')
PAINT = (ROOT / 'blender/blendgimp/ui/paint_tools.py').read_text(encoding='utf-8')

checks = {
    'version': 'version = "0.5.20"' in MANIFEST,
    'build': 'BUILD_ID = "7.2-brush-preview-gpu-float-hotfix"' in PAINT,
    'gimp version': 'BLENDGIMP_VERSION = "0.5.20"' in GIMP,
    'mask shape enum': '("MASK", "Mask Contour"' in TEX,
    'runtime outline cache': '_SELECTION_OUTLINE_CACHE = {}' in TEX,
    'gimp selection buffer': 'selection = image.get_selection()' in GIMP and 'selection.get_buffer()' in GIMP,
    'grayscale mask read': '"Y u8"' in GIMP,
    'outline extraction': 'def _gimp_selection_outline_points' in GIMP,
    'outline attach': 'def _gimp_attach_selection_outline' in GIMP,
    'fuzzy exact outline': '_gimp_attach_selection_outline(image, result)' in GIMP,
    'point overlay': 'def _draw_selection_mask_points' in TEX and 'batch_for_shader(shader, "POINTS"' in TEX,
    'manual double click': 'manual_double' in TEX and 'Free Select manual double-click commit' in TEX,
    'native double click retained': 'Free Select native double-click commit' in TEX,
    'double click timing': '<= 0.40' in TEX,
}
failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(('PASS' if ok else 'FAIL'), name)
if failed:
    raise SystemExit(f'{len(checks)-len(failed)}/{len(checks)} PASS; failed: {failed}')
print(f'BlendGimp 0.5.20 selection contour/double-click contract: {len(checks)}/{len(checks)} PASS')
