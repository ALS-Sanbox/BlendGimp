# BlendGimp 0.5.3 — Regression Checklist

## Release identity

- [ ] Blender manifest reports 0.5.3
- [ ] Blender IPC reports 0.5.3
- [ ] GIMP component reports 0.5.3
- [ ] Build reports `7.0-image-material-ui`
- [ ] Protocol remains 1

## Preferences / UI

- [ ] BlendGimp Preferences opens
- [ ] Headless / Visible-Debug is selectable in Preferences
- [ ] Detect GIMP is in Preferences and not the normal production panel
- [ ] Check GIMP Process is in Preferences and not the normal production panel
- [ ] Auto Paint is in Preferences and not the paint-session header
- [ ] Advanced / Diagnostics is in Preferences
- [ ] Changing Auto Paint affects Texture/Object Paint routing
- [ ] Changing engine mode is used on next start/restart
- [ ] Main panel shows compact status and a Preferences shortcut
- [ ] Image & Material production controls remain available
- [ ] Image & Material card shows Active Material and Active Image
- [ ] Create Image opens a properties dialog instead of an always-expanded form
- [ ] Create dialog confirms with Create
- [ ] Refresh From GIMP targets the active BlendGimp image
- [ ] Assign to Material targets the active BlendGimp image while preserving ownership rules
- [ ] Open XCF is no longer a production-panel button
- [ ] Save XCF / Save All XCF are no longer production-panel buttons
- [ ] Image Editor → Image → BlendGimp XCF submenu is present
- [ ] Open XCF from the Image menu opens the file selector
- [ ] Save XCF / Save XCF As work from the Image menu
- [ ] Fresh install: Start GIMP is enabled before manual detection
- [ ] Fresh install: one Start GIMP click detects GIMP and begins launch
- [ ] Cached missing/stale executable: Start GIMP re-runs detection automatically
- [ ] Manual Detect / Re-detect GIMP still works in Preferences
- [ ] Start GIMP uses the Blender accent/active treatment
- [ ] Stop uses the Blender alert/red treatment
- [ ] Reconnect remains a neutral button and is enabled only when useful

## Frozen Phase 6 functional baseline

- [ ] Create texture
- [ ] Open XCF
- [ ] Save / Save As / Save All
- [ ] Auto Sync
- [ ] Paintbrush
- [ ] Pencil
- [ ] Eraser
- [ ] Airbrush
- [ ] Fill
- [ ] Gradient
- [ ] Smudge
- [ ] Clone
- [ ] Heal
- [ ] GIMP dynamics
- [ ] Texture Paint hybrid progressive updates
- [ ] Object Paint live refresh
- [ ] Layer add/delete/duplicate/rename/reorder/move/group/merge
- [ ] Layer visibility / opacity / modes / locks
- [ ] Automatic pointer routing
- [ ] Shared brush state
- [ ] Pressure behavior
- [ ] Modifier-aware projection
- [ ] Seam / footprint / silhouette / boundary protections
- [ ] Dirty-region bulk-buffer publication
- [ ] Headless restart/recovery
- [ ] Clean shutdown and dirty-image protection

## Static contract

Run:

```text
python validation/phase7_0_image_material_contract_test.py
```

Expected: all checks pass before packaging.
