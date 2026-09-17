# BlendGimp 0.5.5 — Regression Checklist

## Release identity

- [ ] Blender manifest reports 0.5.5
- [ ] Blender IPC reports 0.5.5
- [ ] GIMP component reports 0.5.5
- [ ] Build reports `7.0-layers-ui-ownership-fix`
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
- [ ] Active Image is a Blender-native Image datablock selector
- [ ] Active Image dropdown lists loaded Blender images
- [ ] Native Open action can load a Blender image datablock
- [ ] Selecting a BlendGimp-backed image pins the Texture Editor target
- [ ] Selecting an ordinary Blender image shows an explicit not-linked state
- [ ] Create Image automatically becomes the Active Image selection
- [ ] Open XCF automatically becomes the Active Image selection
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

## 0.5.5 Active Image ownership

- [ ] Create at least two BlendGimp-backed images
- [ ] Switch Active Image from image A to image B and back
- [ ] Console reports `Active Image ownership rebound` with the correct image/layer IDs
- [ ] Auto Sync retargets to the selected Active Image
- [ ] Texture Paint edits the selected image only
- [ ] Object Paint edits the selected image only
- [ ] No `Layer ID ... does not belong to image ID ...` warning appears
- [ ] Selecting an ordinary Blender image disarms old GIMP layer-buffer ownership
- [ ] Switching back to a BlendGimp image restores the correct layer buffer

## 0.5.5 Layers panel

- [ ] Layer rows show visibility, name/group identity and opacity
- [ ] Selected row is visually clear
- [ ] Add Layer works from the compact toolbar
- [ ] Create Group works from the compact toolbar
- [ ] Duplicate and Delete target the selected layer
- [ ] Up/Down reorder controls work when legal
- [ ] Blend Mode dialog targets the selected layer
- [ ] Opacity dialog targets the selected layer
- [ ] Pixels/Position/Alpha locks work
- [ ] Rename works
- [ ] Move works
- [ ] Merge Down works when legal
- [ ] Nested group/layer hierarchy remains readable

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
python validation/phase7_0_layers_ui_ownership_fix_contract_test.py
```

Expected: all checks pass before packaging.
