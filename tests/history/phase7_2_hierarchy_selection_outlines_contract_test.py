from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
main=(ROOT/'blender/blendgimp/ui/main_panel.py').read_text(encoding='utf-8')
paint=(ROOT/'blender/blendgimp/ui/paint_tools.py').read_text(encoding='utf-8')
tex=(ROOT/'blender/blendgimp/ui/texture_editor.py').read_text(encoding='utf-8')
manifest=(ROOT/'blender/blendgimp/blender_manifest.toml').read_text(encoding='utf-8')
gimp=(ROOT/'gimp/blendgimp/blendgimp.py').read_text(encoding='utf-8')
checks={
'version': 'version = "0.5.20"' in manifest,
'build': '7.2-brush-preview-gpu-float-hotfix' in paint,
'gimp version': 'BLENDGIMP_VERSION = "0.5.20"' in gimp,
'collapse operator': 'blendgimp.toggle_group_collapse' in main,
'collapse prop': 'blendgimp_collapsed_group_ids' in main,
'disclosure icons': 'TRIA_RIGHT' in main and 'TRIA_DOWN' in main,
'dashed helper': '_selection_dashed_segments' in tex,
'inverse outer': 'if inverted:' in tex and 'float(width), float(height)' in tex,
'invert preserve shape': 'old_shape in {"RECTANGLE", "ELLIPSE", "FREE"}' in tex,
'inverted prop': 'blendgimp_selection_inverted' in tex,
}
failed=[k for k,v in checks.items() if not v]
for k,v in checks.items(): print(('PASS' if v else 'FAIL'), k)
if failed: raise SystemExit('FAILED: '+', '.join(failed))
print(f'{len(checks)}/{len(checks)} PASS')
