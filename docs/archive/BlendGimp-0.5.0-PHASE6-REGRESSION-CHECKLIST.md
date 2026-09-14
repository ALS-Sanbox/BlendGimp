# BlendGimp 0.5.0 — Frozen Phase 6 Regression Checklist

Use this checklist before accepting any Phase 7 build.

## Engine / persistence
- [ ] Detect GIMP
- [ ] Start headless engine
- [ ] HELLO/READY protocol 1 handshake
- [ ] Blender reports BlendGimp component 0.5.0
- [ ] Reconnect after engine restart
- [ ] Clean shutdown
- [ ] Create texture
- [ ] Save XCF
- [ ] Save As XCF
- [ ] Save All XCF
- [ ] Reopen native XCF
- [ ] Dirty document exit behavior

## Texture Paint
- [ ] Paintbrush
- [ ] Pencil
- [ ] Eraser
- [ ] Airbrush
- [ ] Fill
- [ ] Gradient
- [ ] Smudge
- [ ] Clone source + destination
- [ ] Heal source + destination
- [ ] Hybrid progressive updates while LMB/stylus remains down
- [ ] No blocking GIMP IPC in mouse-move path
- [ ] Bulk pixel publication

## Object Paint
- [ ] Paintbrush and streamed tools
- [ ] Projected Fill/Gradient where supported by current UI
- [ ] Clone/Heal source targeting
- [ ] Modifier-aware evaluated/original projection
- [ ] Seam protection
- [ ] Footprint protection
- [ ] Boundary/silhouette protection
- [ ] Occlusion and paint-through behavior
- [ ] Automatic GPU invalidation/redraw

## Shared state / UI
- [ ] Active image remains correct
- [ ] Active GIMP layer remains correct
- [ ] Brush resource
- [ ] Dynamics
- [ ] Size/opacity/hardness/spacing/angle/aspect
- [ ] Foreground/background color
- [ ] Pressure behavior
- [ ] Automatic pointer routing: texture / model / UI
- [ ] Live tool switching
- [ ] ESC cancels active operation without destroying routing state

## Canvas / UV
- [ ] Fit Image
- [ ] 100%
- [ ] Transparency/presentation
- [ ] UV overlay
- [ ] UV opacity
- [ ] Island visibility
- [ ] Edge style
- [ ] Active-face highlight

## Layers
- [ ] Select active layer
- [ ] Add/delete/rename/duplicate
- [ ] Reorder/move
- [ ] Groups
- [ ] Visibility/opacity
- [ ] Blend mode
- [ ] Locks
- [ ] Merge down

## Pass condition

Phase 7 changes are accepted only when failures are either fixed or deliberately documented as an intentional change from the 0.5.0 frozen baseline.
