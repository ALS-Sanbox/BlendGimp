# BlendGimp 0.5.21 — Phase 7.2 Code Cleanup Complete

This release is a **stabilization and cleanup build** based on the accepted 0.5.20 runtime baseline. It intentionally does not add a new artist feature set. The goal is to leave the Phase 7.2 codebase cleaner and easier to extend before Phase 7.3 Filters / GEGL work begins.

## What changed

- Removed the retired user-facing **BlendGimp Area** operators, launcher panels, header code, and obsolete `blendgimp_area_last_mode` Scene property instead of merely hiding them.
- Kept the private area-scoped routing metadata required by the accepted automatic pointer/modal ownership architecture.
- Removed dead helpers and unused constants/imports found by source analysis.
- Centralized Blender-side version/build/protocol metadata in `core/build_info.py`.
- Consolidated Texture Editor transient-state reset logic so register/unregister share one cleanup path.
- Kept the accepted 0.5.20 real GIMP brush preview, GPU FLOAT upload, `[` / `]` brush-size hotkeys, FG/BG swap, selection system, layers/masks, direct Object Paint, and progressive Texture Paint paths intact.
- Removed validation scripts and build tooling from the **runtime Blender Extension ZIP**. Release tests remain in the full source tree under `tests/`.
- Archived old Phase 7.2 development notes under `docs/history/phase7_2/` so the active documentation only describes the current build.

## Architecture preserved

GIMP remains authoritative for raster pixels and 2D layer state. Blender remains authoritative for 3D geometry, UVs, materials, viewport interaction, and the immediate non-authoritative preview layer. The protected Phase 6 painting/sync/projection ownership model was not redesigned in this cleanup.

## Release validation

The release contract verifies version synchronization, removal of retired UI code, all major Phase 7.0–7.2 commands, mask/layer operations, selection tools, real brush preview, asynchronous/progressive painting, direct-paint ownership, modifier-aware projection, seam/footprint protection, Python parsing, duplicate class checks, and production package cleanliness.

The final Blender/GIMP runtime acceptance test is in `docs/BlendGimp-0.5.21-FULL-RUNTIME-TEST.md`.
