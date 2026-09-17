#!/usr/bin/env python3
from pathlib import Path
import ast
import sys

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]).resolve()
checks = []


def check(name, cond):
    ok = bool(cond)
    checks.append((name, ok))
    print(("PASS" if ok else "FAIL") + ": " + name)
    return ok


def text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


manifest = text("blender_manifest.toml")
init = text("__init__.py")
main = text("ui/main_panel.py")
paint = text("ui/paint_tools.py")
tex = text("ui/texture_editor.py")
prefs = text("ui/preferences.py")
stroke = text("painting/stroke_tool.py")
ipc = text("ipc/connection.py")
mgr = text("core/gimp_manager.py")
tool = text("core/tool_state.py")

# 0.5.4 release identity
check("manifest version 0.5.4", 'version = "0.5.4"' in manifest)
check("IPC version 0.5.4", 'BLENDGIMP_VERSION = "0.5.4"' in ipc)
check("usability build id", 'BUILD_ID = "7.0-active-image-datablock"' in paint)
check("entry point identifies 0.5.4", '0.5.4 — Phase 7.0 Usability / Production UI' in init)
check("main panel identifies production UI", 'BlendGimp 0.5.4 — Production UI' in main)

# Phase 7.0 preferences / clutter relocation
check("AddonPreferences exists", 'class BLENDGIMP_Preferences(bpy.types.AddonPreferences)' in prefs)
check("engine mode is a preference", 'engine_mode: bpy.props.EnumProperty' in prefs)
check("Auto Paint is a preference", 'auto_paint: bpy.props.BoolProperty' in prefs)
check("automatic recovery is a preference", 'automatic_recovery: bpy.props.BoolProperty' in prefs)
check("Detect GIMP remains in Preferences", '"blendgimp.detect_gimp"' in prefs)
check("Check GIMP Process remains in Preferences", '"blendgimp.check_gimp"' in prefs)
check("Advanced diagnostics moved to Preferences", 'show_advanced_diagnostics: bpy.props.BoolProperty' in prefs)
check("Preferences registered first", 'preferences.register()' in init and 'preferences.apply_preferences_to_all_scenes()' in init)
check("Preferences open shortcut", 'bl_idname = "blendgimp.open_preferences"' in prefs)

panel_start = main.index("class BLENDGIMP_PT_main_panel")
panel_end = main.index("# ============================================================\n# CLASSES", panel_start)
panel_draw = main[panel_start:panel_end]
check("production panel has no Detect GIMP button", '"blendgimp.detect_gimp"' not in panel_draw)
check("production panel has no Check Process button", '"blendgimp.check_gimp"' not in panel_draw)
check("production panel does not edit engine mode", '"blendgimp_engine_mode",\n            text="Mode"' not in panel_draw)
check("production panel has compact engine status", 'text="Engine"' in panel_draw and 'blendgimp.open_preferences' in panel_draw)
check("production panel renamed Image & Material", 'text="Image & Material"' in panel_draw)
check("compact panel shows Active Material", 'text="Active Material"' in panel_draw)
check("compact panel shows Active Image", 'text="Active Image"' in panel_draw)
check("Active Image uses native datablock selector", 'template_ID(' in panel_draw and '"blendgimp_active_image"' in panel_draw)
check("Active Image native selector can open Blender images", 'open="image.open"' in panel_draw)
check("Active Image pointer property exists", 'blendgimp_active_image = (' in main and 'bpy.props.PointerProperty(' in main and 'type=bpy.types.Image' in main)
check("Active Image selection update callback exists", 'def _active_image_datablock_update' in main and 'update=_active_image_datablock_update' in main)
check("GIMP-backed selection pins texture editor", 'texture_editor._set_active_texture(scene, image_id)' in main and 'blendgimp_texture_editor_follow_active = False' in main)
check("ordinary Blender image is explicitly marked unlinked", 'Selected image is not linked to BlendGimp' in panel_draw)
check("manual image selection is strongest resolver signal", 'strongest explicit user choice' in main and 'return _image_gimp_id(selected_image)' in main)
check("new textures populate native image selector", '_set_active_image_datablock(scene, blender_image)' in main)
check("compact panel has Create Image", 'text="Create Image"' in panel_draw)
check("compact panel has Refresh From GIMP", 'text="Refresh From GIMP"' in panel_draw)
check("compact panel has Assign to Material", 'text="Assign to Material"' in panel_draw)
check("Open XCF removed from production panel", '"blendgimp.open_xcf"' not in panel_draw)
check("Save All XCF removed from production panel", '"blendgimp.save_all_images"' not in panel_draw)
check("Refresh image-list removed from production panel", '"blendgimp.get_images"' not in panel_draw)
check("Disconnect removed from Image & Material panel", '"blendgimp.disconnect"' not in panel_draw)
check("Create Image uses properties dialog", 'invoke_props_dialog(' in main and 'title="Create BlendGimp Image"' in main)
check("Create dialog confirm says Create", 'confirm_text="Create"' in main)
check("Image menu XCF submenu exists", 'class BLENDGIMP_MT_image_xcf' in main)
check("Image menu hook registered", 'IMAGE_MT_image.append(draw_blendgimp_image_menu)' in main)
check("Image menu hook unregistered", 'IMAGE_MT_image.remove(draw_blendgimp_image_menu)' in main)
check("Image menu invokes file selectors", 'layout.operator_context = "INVOKE_DEFAULT"' in main)
check("Image menu has Open XCF", 'text="Open XCF..."' in main)
check("Image menu has Save XCF", 'text="Save XCF"' in main and 'text="Save XCF As..."' in main)
check("Image menu has Save All XCF", 'text="Save All XCF"' in main)
check("Start GIMP available without prior detection", 'start_cell.operator(' in panel_draw and '"blendgimp.launch_gimp"' in panel_draw)
check("Start GIMP uses active accent treatment", 'depress=True' in panel_draw)
check("Stop GIMP uses alert/red treatment", 'stop_cell.alert = True' in panel_draw)
check("Reconnect remains neutral contextual action", '"blendgimp.connect"' in panel_draw and 'reconnect_cell.enabled' in panel_draw)
check("Start performs automatic detection", 'detect_gimp_for_scene(scene)' in main and 'if not gimp_path or not os.path.isfile(gimp_path)' in main)
check("manual detection shares automatic helper", 'detected, gimp_path, version = detect_gimp_for_scene(scene)' in main)
check("Preferences explain automatic detection", 'Detection also runs automatically when Start GIMP is pressed.' in prefs)
check("Preferences Start is always rendered", 'start_cell.operator(' in prefs and '"blendgimp.launch_gimp"' in prefs)
check("Preferences Stop uses alert/red treatment", 'stop_cell.alert = True' in prefs)

summary_start = paint.index("def _draw_session_summary")
summary_end = paint.index("def _draw_layer_controls", summary_start)
summary_draw = paint[summary_start:summary_end]
check("Auto Paint removed from paint session panel", 'text="Auto Paint"' not in summary_draw)
check("old paint diagnostics panel removed", 'def _draw_diagnostics' not in paint)

# Frozen Phase 6 texture canvas / UV presentation remains present
check("Fit Image", 'bl_idname = "blendgimp.texture_view_fit"' in tex)
check("100 percent", 'bl_idname = "blendgimp.texture_view_100"' in tex)
check("presentation controls", 'blendgimp_texture_editor_display_channels' in tex)
check("UV islands", 'blendgimp_texture_editor_show_islands' in tex)
check("UV opacity", 'blendgimp_texture_editor_uv_opacity' in tex)
check("active-face highlight", 'blendgimp_texture_editor_active_face_highlight' in tex)

# Frozen Phase 6 layers remain present
for name, marker in [
    ("active layer", 'bl_idname = "blendgimp.set_active_layer"'),
    ("visibility", 'bl_idname = "blendgimp.set_layer_visibility"'),
    ("opacity", 'bl_idname = "blendgimp.set_layer_opacity"'),
    ("rename", 'bl_idname = "blendgimp.rename_layer"'),
    ("duplicate", 'bl_idname = "blendgimp.duplicate_layer"'),
    ("delete", 'bl_idname = "blendgimp.delete_layer"'),
    ("reorder", 'bl_idname = "blendgimp.reorder_layer"'),
    ("move", 'bl_idname = "blendgimp.move_layer"'),
    ("group", 'bl_idname = "blendgimp.create_group"'),
    ("merge down", 'bl_idname = "blendgimp.merge_layer_down"'),
    ("blend mode", 'bl_idname = "blendgimp.set_layer_mode"'),
]:
    check("layer " + name, marker in main)
check("layer locks", 'blendgimp.set_layer_lock' in main)

# Frozen Phase 6 tools remain present
for label, marker in [
    ("Paintbrush", "PAINTBRUSH"),
    ("Pencil", "PENCIL"),
    ("Eraser", "ERASER"),
    ("Airbrush", "AIRBRUSH"),
    ("Fill", "FILL"),
    ("Gradient", "GRADIENT"),
    ("Smudge", "SMUDGE"),
    ("Clone", "CLONE"),
    ("Heal", "HEAL"),
]:
    check(label + " tool", marker in tool and marker in paint)
check("GIMP dynamics", 'def get_dynamics(' in ipc and 'def set_dynamics(' in ipc and 'def set_dynamics_enabled(' in ipc)

# Routing/input and pressure baseline remains present
check("routing generation registry", '_ROUTING_GENERATIONS' in tex)
check("Auto Paint preference drives router", 'blendgimp_preferences.auto_paint_enabled' in tex)
check("Texture live tool switch", 'Texture Paint live tool switch' in paint)
check("Object live tool switch", 'Object Paint live tool switch' in stroke)
check("2D pressure capture", 'getattr(event, "pressure"' in paint)
check("3D pressure capture", 'getattr(event, "pressure"' in stroke)

# Frozen performance / projection architecture remains present
check("2D async worker", 'async' in paint.lower() and 'worker' in paint.lower())
check("bulk foreach_set", 'foreach_set' in main or 'foreach_set' in paint)
check("Object GPU invalidation", 'gpu_invalidated' in main)
check("Auto Sync ownership 2D", 'acquired Auto Sync refresh ownership' in paint)
check("Auto Sync ownership 3D", 'Object Paint acquired Auto Sync refresh ownership' in stroke)
check("stroke protocol", 'BEGIN_PAINT_STROKE' in ipc and 'END_PAINT_STROKE' in ipc)
check("modifier-aware projection", 'projection_mesh' in stroke and 'projection_fallbacks' in stroke and 'topology_changed' in stroke)
check("footprint protection", 'footprint_protection' in stroke and 'footprint_safe_ratio' in stroke)
check("seam protection", 'seam_suppressed' in stroke and 'uv_splits' in stroke)

# Persistence / engine lifecycle remains present
check("Create image", 'CREATE_IMAGE' in ipc and 'bl_idname = "blendgimp.create_image"' in main)
check("Save XCF", 'bl_idname = "blendgimp.save_image"' in main)
check("Save As XCF", 'bl_idname = "blendgimp.save_image_as"' in main)
check("Save All XCF", 'bl_idname = "blendgimp.save_all_images"' in main)
check("Open XCF", 'OPEN_XCF' in ipc or 'open_xcf' in ipc.lower())
check("dirty exit recovery", '_save_dirty_images_for_exit' in main)
check("headless mode", 'ENGINE_MODE_HEADLESS' in mgr)
check("visible debug fallback", 'ENGINE_MODE_VISIBLE_DEBUG' in mgr)
check("protocol remains v1", 'PROTOCOL_VERSION = 1' in ipc)
check("no legacy row writes", '.pixels[' not in paint and '.pixels[' not in stroke)

syntax_ok = True
for p in ROOT.rglob("*.py"):
    if "__pycache__" in p.parts:
        continue
    try:
        ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
    except Exception as exc:
        syntax_ok = False
        print("SYNTAX:", p, exc)
check("all Python parses", syntax_ok)
check("no cache files", not any(p.suffix == ".pyc" or p.name == "__pycache__" for p in ROOT.rglob("*")))

failed = [name for name, ok in checks if not ok]
print(f"\nRESULT: {len(checks) - len(failed)}/{len(checks)} PASS")
if failed:
    print("FAILED:")
    for name in failed:
        print(" -", name)
    raise SystemExit(1)
