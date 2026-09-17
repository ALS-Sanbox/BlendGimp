# BlendGimp Development Roadmap — 0.5.9

## Current position

Phase 6 — frozen painting/sync/projection baseline: COMPLETE  
Phase 7.0 — usability / production UI: COMPLETE  
Phase 7.1 — advanced layers & masks: CORE COMPLETE  
Phase 7.2 — GIMP selections: IN PROGRESS

## 7.2.1 Selection Foundation

Implemented: Rectangle Select, Ellipse Select, Select All, Deselect, Invert, GIMP-authoritative selection state, image-specific ownership, canvas overlay, and selection-clipped paint operations.

## 7.2.2 UI Cues + Performance — 0.5.9

- Actual Color Tag swatches in the compact Layers stack
- Immediate layer-panel redraw after Color Tag changes
- Rectangle/Ellipse depressed-state cue while armed
- Explicit selection-tool ACTIVE status
- Selection modal passes unrelated Blender UI events through
- Live drag preview uses transient runtime state instead of per-event Scene RNA writes
- Canvas-only preview redraw capped near 60 Hz
- Cached GPU selection shader

## Next Phase 7.2 feature set

After 0.5.9 runtime acceptance: Free Select, Add/Subtract/Intersect modes, Fuzzy Select / Select by Color, Grow, Shrink, Feather, Border, and selection-to-mask workflows.
