# BlendGimp 0.5.9 — Regression Checklist

## Identity
- [ ] Blender manifest reports 0.5.9
- [ ] Blender IPC reports 0.5.9
- [ ] GIMP component reports 0.5.9
- [ ] Build reports `7.2-ui-cues-performance`

## UI corrections
- [ ] Blue/Green/Yellow/Orange/Brown/Red/Violet/Gray tags show colored swatches
- [ ] Color Tag text fallback remains visible
- [ ] Rectangle button depresses while armed
- [ ] Ellipse button depresses while armed
- [ ] Explicit selection ACTIVE status appears
- [ ] Esc/RMB clears the active selection-tool cue

## Selection responsiveness
- [ ] UI events pass through before a canvas drag begins
- [ ] Live drag preview stays responsive
- [ ] Drag preview does not update Scene RNA every mousemove
- [ ] Preview redraw is canvas-local and throttled to about 60 Hz
- [ ] Rectangle selection completes correctly
- [ ] Ellipse selection completes correctly

## Phase 7.2 behavior
- [ ] Select All
- [ ] Deselect
- [ ] Invert
- [ ] Selection overlay
- [ ] Texture Paint clipped by selection
- [ ] Object Paint clipped by selection
- [ ] Selection state does not leak between Active Images

## Protected regression
- [ ] Active Image ownership remains correct
- [ ] Layer/mask ownership remains correct
- [ ] Object Paint on mask remains correct
- [ ] Merge/Flatten ownership rebinding remains correct
- [ ] Auto Sync and hybrid-progressive Texture Paint remain correct
