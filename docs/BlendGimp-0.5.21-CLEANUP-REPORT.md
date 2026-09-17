# BlendGimp 0.5.21 — Code Cleanup Report

## Completed cleanup

### Retired workflow removal

The old user-facing **BlendGimp Area** workflow is now physically removed from the Texture Editor module:

- removed `blendgimp.use_area`
- removed `blendgimp.switch_area_mode`
- removed `blendgimp.disable_area`
- removed retired header drawing code
- removed retired launcher panels
- removed obsolete `blendgimp_area_last_mode`
- removed dead mode-switch/object-area helper code that existed only for those retired controls

Private Blender-area routing metadata remains because it is the ownership mechanism used by automatic Texture/Object Paint routing. It is no longer exposed as an artist workflow.

### Code organization

- Added `core/build_info.py` as the Blender-side source for version, build ID, phase label, display name, and protocol version.
- Updated the entry point, Preferences, main panel, paint tools, and IPC layer to use centralized release metadata.
- Added one Texture Editor runtime-reset helper used by both registration and unregistration.
- Removed source-level dead functions, dead constants, and an unused import found by AST/name-use analysis.

### Runtime-package cleanup

The Blender Extension ZIP now contains only runtime code/assets plus the manifest. Development validation files and the local build batch file are excluded from the installed extension payload.

### Documentation cleanup

Historical Phase 7.2 roadmaps, regression notes, and runtime notes were moved to `docs/history/phase7_2/`. Current docs now focus on 0.5.21 and the one final end-to-end runtime test.

## Intentionally unchanged

The following accepted systems were protected during cleanup:

- persistent GIMP IPC/session ownership
- binary full/dirty pixel transport
- asynchronous Texture Paint
- progressive authoritative dirty-pixel replacement
- real GIMP brush masks and GPU preview
- Object Paint projection, seams, footprint protection, occlusion, and modifier-aware geometry
- layer/group/mask operations
- color tags and group collapse
- Rectangle/Ellipse/Free/Fuzzy/By Color selections
- Add/Subtract/Intersect selection modifiers
- selection contours, Grow/Shrink/Feather/Border
- FG/BG swap
- image/material ownership and active-image switching

## Validation status

Static/source/release validation is complete. The remaining acceptance step is the single full Blender + GIMP runtime test documented in `BlendGimp-0.5.21-FULL-RUNTIME-TEST.md`.
