# BlendGimp 0.5.11

**Phase:** 7.2 — GIMP Selections  
**Build:** `7.2-group-collapse-hotfix`  
**Runtime target:** Blender 5.2 + GIMP 3.2.4

This is a focused runtime hotfix over 0.5.10. The ellipse/inverse dashed-outline behavior is preserved as accepted.

## Fix

- Registers the missing `Scene.blendgimp_collapsed_group_ids` UI-state property.
- Adds a defensive custom-property fallback for unusual extension hot-reload states.
- Makes unregister tolerant of the broken 0.5.10 state where the property was never registered.
- Group disclosure arrows now persist their local collapsed/expanded state and hide/show child rows without changing the GIMP hierarchy.
- Preserves color-tag swatches, dashed selection outlines, inverse dual-boundary display, and frozen Phase 6 painting/sync/projection behavior.

## Runtime focus

Create a group, move at least two layers into it, then click the arrow after the eye repeatedly. Children should disappear/reappear while remaining in the GIMP group.
