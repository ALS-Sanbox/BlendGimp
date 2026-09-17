# BlendGimp Development Roadmap — 0.5.6

```text
Phase 1–5                              ✅ COMPLETE
Phase 6                                ✅ COMPLETE / FROZEN — 0.5.0
Phase 7.0 Production UI / Usability    ✅ ACCEPTED — 0.5.5
Phase 7.1 Advanced Layers & Masks      🚧 CURRENT — 0.5.6
Phase 7.2 Selections                   ⏳ NEXT
Phase 7.3 Filters / GEGL               ⏳ FUTURE
Phase 7.4 Live Filter Preview          ⏳ FUTURE
Phase 7.5 Procedure Discovery          ⏳ FUTURE
Phase 7.6 Automatic Procedure UI       ⏳ FUTURE
Phase 7.7 GIMP Plug-ins                ⏳ FUTURE
Phase 7.8 Texture Undo / Redo          ⏳ FUTURE
```

## Phase 7.1 — Advanced Layers & Masks

### 7.1.1 Native Layer Mask Foundation — 0.5.6

- [x] Mask metadata in layer snapshots
- [x] Add layer mask
- [x] White / Black initialization
- [x] Layer Alpha / Transfer Alpha initialization
- [x] Selection initialization
- [x] Grayscale Copy initialization
- [x] Layer vs Mask edit target
- [x] Mask enable/disable
- [x] Mask-only display toggle
- [x] Apply mask
- [x] Delete/discard mask
- [x] GIMP-authoritative paint tools redirect to active mask drawable
- [x] Fill and Gradient redirect to active mask drawable
- [x] Active raw working buffer follows Layer/Mask target
- [x] Pending paint is flushed before target changes
- [ ] Blender 5.2 + GIMP 3.2.4 runtime acceptance

### Following Advanced Layer work

After 0.5.6 acceptance:

- [ ] Additional mask presentation/thumbnail polish
- [ ] Alpha-mask workflow decisions where distinct from layer masks
- [ ] Layer color labels
- [ ] Merge Visible
- [ ] Flatten Image
- [ ] Duplicate-group workflow polish
- [ ] Additional lock/state polish as required

## Phase 7.2 — Selections

Rectangle, Ellipse, Free Select, Color Select, Grow, Shrink, Feather, Invert, Select All, and Deselect.
