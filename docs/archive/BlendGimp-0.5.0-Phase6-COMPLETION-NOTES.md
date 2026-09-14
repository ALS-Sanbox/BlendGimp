# BlendGimp 0.5.0 — Phase 6 Completion Notes

**Status:** COMPLETE / FROZEN  
**Frozen build:** `6.5.4-phase6-frozen`  
**Date frozen:** 2026-09-14  
**Next phase:** Phase 7 — Full GIMP Ecosystem Integration

## Completion statement

Phase 6 is accepted as the frozen artist-facing texture editor baseline. A user can create and reopen GIMP-backed textures from Blender, paint in 2D or on the 3D model, use the completed Phase 6 GIMP-backed tool set, manage layers, share tool state between 2D/3D contexts, and work without opening the normal GIMP interface for routine texture operations.

## Frozen Phase 6 systems

- Blender-native Texture Paint canvas based on the Image Editor
- Object Paint in the 3D View
- Automatic pointer-based routing between 2D paint, 3D paint, and Blender UI
- GIMP-backed Paintbrush, Pencil, Eraser, Airbrush, Fill, Gradient, Smudge, Clone, and Heal
- GIMP brush/dynamics integration and shared tool state
- Layer targeting and layer management
- Fit Image / 100% view controls
- Transparency/presentation controls
- UV overlay, islands, edge presentation, opacity, and active-face highlight
- Asynchronous 2D input architecture with hybrid progressive GIMP updates
- Cached full-image float buffer and bulk `Image.pixels.foreach_set()` publication
- Direct 3D projection with evaluated/original mesh support
- Seam, footprint, silhouette, boundary, occlusion, and paint-through protections
- GIMP undo grouping per stroke/operation where applicable
- Native XCF save/reopen workflow
- Headless GIMP lifecycle, reconnect, shutdown, and recovery behavior

## Protected architecture for Phase 7

Phase 7 must not casually replace the working Phase 6 paint path.

```text
Texture Paint
  immediate local feedback
        +
  asynchronous GIMP work
        +
  progressive dirty-region pulls
        +
  cached bulk Blender publication

Object Paint
  Blender projection
        +
  batched GIMP operations
        +
  dirty-region pulls
        +
  cached bulk Blender publication
        +
  GPU invalidation / VIEW_3D redraw
```

Keep these regression protections:

- No blocking GIMP IPC from `MOUSEMOVE`.
- No return to per-row image pixel writes.
- Keep bulk `foreach_set()` publication.
- Preserve Auto Sync refresh ownership handoff.
- Preserve selected GIMP layer targeting.
- Preserve one authoritative operation/undo grouping behavior.
- Preserve modifier-aware projection and surface protections.
- Preserve automatic contextual pointer routing.
- Preserve XCF persistence and headless engine recovery.

## Tablet scope

Pressure capture/application is part of the accepted Phase 6 baseline. Tablet tilt is intentionally not required to freeze Phase 6 for the current hardware baseline.

## Version rule

All active release-facing identifiers in this package use **BlendGimp 0.5.0**. Internal phase numbers and the GIMP/IPC dependency versions remain separate identifiers and should not be interpreted as BlendGimp package versions.
