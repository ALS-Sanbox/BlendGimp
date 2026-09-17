# BlendGimp 0.5.6 — Regression Checklist

## Identity

- [ ] Manifest reports 0.5.6
- [ ] Blender IPC reports 0.5.6
- [ ] GIMP component reports 0.5.6
- [ ] Build reports `7.1-layer-mask-foundation`

## Layer masks

- [ ] Add Mask dialog works
- [ ] Layer/Mask target switches cleanly
- [ ] Mask painting works in Texture Paint
- [ ] Mask painting works in Object Paint
- [ ] Enabled toggle works
- [ ] Mask View state works
- [ ] Apply Mask works
- [ ] Delete Mask works
- [ ] Active buffer logs `target=MASK` when editing mask

## Existing layers

- [ ] Add
- [ ] Delete
- [ ] Duplicate
- [ ] Rename
- [ ] Reorder
- [ ] Move
- [ ] Visibility
- [ ] Opacity
- [ ] Blend modes
- [ ] Content/position/alpha locks
- [ ] Merge Down
- [ ] Groups

## Frozen Phase 6

- [ ] Create Texture
- [ ] Active Image switching
- [ ] Auto Sync
- [ ] Paintbrush / Pencil / Eraser / Airbrush
- [ ] Fill / Gradient / Smudge / Clone / Heal
- [ ] Texture Paint asynchronous input
- [ ] Object Paint live viewport redraw
- [ ] Modifier-aware projection
- [ ] Seam/footprint protection
- [ ] One GIMP undo group per stroke/operation
- [ ] Clean headless-engine shutdown/recovery
- [ ] 3–5 second Texture Paint stroke produces progressive authoritative updates
