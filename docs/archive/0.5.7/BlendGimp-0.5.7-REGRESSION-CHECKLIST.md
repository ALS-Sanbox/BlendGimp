# BlendGimp 0.5.7 — Regression Checklist

## Release identity

- [ ] Manifest reports 0.5.7
- [ ] Blender IPC reports 0.5.7
- [ ] GIMP component reports 0.5.7
- [ ] Build reports `7.1-advanced-layer-ops`

## Advanced layers

- [ ] Set each GIMP color tag and confirm UI state refreshes
- [ ] Clear color tag back to None
- [ ] Lock Pixels
- [ ] Lock Position
- [ ] Lock Visibility
- [ ] Lock Alpha
- [ ] Duplicate a group with children
- [ ] Reorder duplicated group
- [ ] Merge Visible with at least two visible layers
- [ ] Confirm hidden layer survives Merge Visible
- [ ] Paint the merged result
- [ ] Flatten Image with a hidden layer present
- [ ] Confirm hidden layer is discarded
- [ ] Paint the flattened result

## Layer masks

- [ ] Add White mask
- [ ] Add Black mask
- [ ] Switch Layer ↔ Mask
- [ ] Paint Mask in Texture Paint
- [ ] Paint Mask in Object Paint
- [ ] Toggle mask enabled
- [ ] Toggle Mask View
- [ ] Apply mask
- [ ] Delete/discard mask

## Frozen production workflow

- [ ] Start GIMP performs automatic detection
- [ ] Native Active Image selector switches between two BlendGimp images
- [ ] No `Layer ID ... does not belong to image ID ...` warning
- [ ] Add / Delete / Rename / Duplicate / Reorder / Move layer
- [ ] Create Group / Merge Down
- [ ] Opacity / Blend Mode / Visibility
- [ ] Paintbrush / Pencil / Eraser / Airbrush
- [ ] Fill / Gradient / Smudge / Clone / Heal
- [ ] Texture Paint automatic routing
- [ ] Object Paint automatic routing
- [ ] Slow Texture Paint stroke produces a progressive authoritative update
- [ ] Object Paint dirty-region publication and automatic viewport redraw
- [ ] Modifier-aware projection
- [ ] Seam / footprint protection
- [ ] Auto Sync handoff
- [ ] Headless clean shutdown/recovery
