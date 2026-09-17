from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEX = (ROOT / 'ui' / 'texture_editor.py').read_text()
GIMP = (ROOT.parents[1] / 'gimp' / 'blendgimp' / 'blendgimp.py').read_text()
MANIFEST = (ROOT / 'blender_manifest.toml').read_text()
PAINT = (ROOT / 'ui' / 'paint_tools.py').read_text()

checks = {
    'version': 'version = "0.5.20"' in MANIFEST,
    'build id': 'BUILD_ID = "7.2-brush-preview-gpu-float-hotfix"' in PAINT,
    'gimp hotkey helper': 'def _selection_operation_from_event(event):' in TEX,
    'shift add': 'if shift:' in TEX and 'return "ADD"' in TEX,
    'ctrl subtract': 'if ctrl:' in TEX and 'return "SUBTRACT"' in TEX,
    'shift ctrl intersect': 'if shift and ctrl:' in TEX and 'return "INTERSECT"' in TEX,
    'no visible expanded combine enum': 'combine.prop(scene, "blendgimp_selection_operation"' not in TEX,
    'hotkey legend': 'Combine Hotkeys (GIMP-style)' in TEX,
    'drag hotkey capture': 'self._operation = _selection_operation_from_event(event)' in TEX,
    'free hotkey capture': '_SELECTION_FREE_PREVIEW["operation"] = operation' in TEX,
    'point hotkey capture': 'operation = _selection_operation_from_event(event)' in TEX,
    'composition preview committed selection': 'preview_operation in {"PENDING", "ADD", "SUBTRACT", "INTERSECT"}' in TEX,
    'committed selection helper': 'def _draw_committed_selection(' in TEX,
    'by color contour best effort': 'Best-effort contour metadata; never fail the underlying selection.' in GIMP,
    'bounded contour scan': 'sample_budget = 300000.0' in GIMP,
    'color selection timeout': 'timeout=15.0' in GIMP,
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit('FAILED: ' + ', '.join(failed))
print(f'BlendGimp 0.5.20 selection hotkeys/compose preview contract: {len(checks)}/{len(checks)} PASS')
