# BlendGimp 0.5.5 — Layers UI + Active Image Ownership Fix

## Objective

Finish the next Phase 7.0 production-UI pass while repairing the stale active-layer ownership state discovered during 0.5.4 testing.

## Ownership fix

- [x] Flush pending Blender layer edits before changing Active Image ownership.
- [x] Release Texture/Object Paint refresh owners during image changes.
- [x] Clear stale reusable active-layer buffer image/layer IDs.
- [x] Rebind even when the Texture Editor already names the selected image but the buffer owner is different.
- [x] Resolve the selected image's active raster layer again.
- [x] Reload the active-layer working buffer from that image/layer.
- [x] Retarget normal Auto Sync to the explicitly selected Active Image.
- [x] Refuse live layer-buffer patches when the operation image does not match the buffer-owner image.
- [x] Selecting an ordinary Blender image retires previous GIMP paint ownership instead of leaving stale ownership armed behind the unlinked image.
- [x] Add clear ownership transition diagnostics.

## Layers UI pass

- [x] Keep existing GIMP layer backend/protocol unchanged.
- [x] Visibility control is first in each stack row.
- [x] Layer/group name and hierarchy are immediately visible.
- [x] Active selection uses the compact row state.
- [x] Opacity is visible on every row.
- [x] Add Layer and Create Group are in a compact toolbar.
- [x] Duplicate/Delete are contextual to the selected layer.
- [x] Up/Down reorder controls are contextual to legal positions.
- [x] Selected layer Blend and Opacity are consolidated in one property box.
- [x] Content/Position/Alpha locks remain available.
- [x] Rename, Move and Merge Down remain available.
- [x] Refresh remains in the Layers header.

## Protected baseline

0.5.5 must preserve the frozen 0.5.0 Phase 6 paint/sync architecture and all functionality accepted through 0.5.4.
