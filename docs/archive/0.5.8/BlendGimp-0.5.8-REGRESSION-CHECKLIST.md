# BlendGimp 0.5.8 — Regression Checklist

## Identity

- [ ] Manifest reports 0.5.8
- [ ] Blender IPC reports 0.5.8
- [ ] GIMP component reports 0.5.8
- [ ] Build reports `7.2-selection-foundation`

## Color Tag UI regression

- [ ] Set layer to Red → stack row visibly shows `[Red]`
- [ ] Set another layer to Green → row visibly shows `[Green]`
- [ ] Set another layer to Orange → row visibly shows `[Orange]`
- [ ] switch active layers → badges remain on the correct rows
- [ ] Refresh / GET_IMAGE_LAYERS → badges remain correct
- [ ] test Blue, Yellow, Brown, Violet and Gray
- [ ] set tag to None → badge disappears
- [ ] no tag changes raster pixels or paint ownership

## Phase 7.2 Selection Foundation

- [ ] Rectangle button enters selection mode instead of painting
- [ ] click-drag creates native GIMP Rectangle selection
- [ ] Rectangle overlay follows drag and committed selection
- [ ] Ellipse click-drag creates native GIMP Ellipse selection
- [ ] Ellipse overlay follows drag and committed selection
- [ ] Selection UI reports synchronized bounds
- [ ] Select All selects the complete image
- [ ] Deselect clears selection and overlay
- [ ] Invert inverts GIMP selection
- [ ] Refresh Selection reads authoritative state from GIMP
- [ ] switching Active Image prevents old selection overlay leaking to the new image
- [ ] Refresh on the new image restores its own selection state

## Selection + painting

- [ ] Texture Paint stroke inside selection changes pixels
- [ ] Texture Paint stroke outside selection is clipped
- [ ] Object Paint stroke inside selection changes pixels
- [ ] Object Paint projected points outside selection are clipped by GIMP
- [ ] layer-mask painting respects the selection
- [ ] Auto Sync resumes normally after selection interactions
- [ ] hybrid-progressive Texture Paint still produces progressive updates on a slow stroke

## Phase 7.1 protected regression

- [ ] layer add/delete/rename/duplicate/reorder/move/group still works
- [ ] Layer/Mask edit target still works
- [ ] Object Paint onto layer mask still works
- [ ] mask enable/show/apply/delete still works
- [ ] Merge Visible rebinds the resulting layer
- [ ] Flatten rebinds the resulting layer
- [ ] Duplicate Group still works
- [ ] Content lock blocks painting
- [ ] Position / Visibility / Alpha lock states round-trip

## Frozen Phase 6 regression

- [ ] Texture Paint 2D live preview works
- [ ] GIMP authoritative end-of-stroke pixels replace preview
- [ ] Object Paint live dirty updates work
- [ ] modifier-aware projection works
- [ ] seam protection works
- [ ] footprint protection works
- [ ] active image switching has no wrong-layer warning
- [ ] foreground/background shared color state still works
- [ ] Paintbrush/Pencil/Eraser/Airbrush/Fill/Gradient/Smudge/Clone/Heal still function
- [ ] XCF save/open workflow remains intact
