# BlendGimp Development Roadmap — 0.5.7

```text
Phase 1–5                              ✅ COMPLETE
Phase 6                                ✅ COMPLETE / FROZEN — 0.5.0
Phase 7.0 Production UI / Usability    ✅ ACCEPTED — 0.5.5
Phase 7.1 Advanced Layers & Masks      🚧 CURRENT — 0.5.7
Phase 7.2 Selections                   ⏳ NEXT
Phase 7.3 Filters / GEGL               ⏳ FUTURE
Phase 7.4 Live Filter Preview          ⏳ FUTURE
Phase 7.5 Procedure Discovery          ⏳ FUTURE
Phase 7.6 Automatic Procedure UI       ⏳ FUTURE
Phase 7.7 GIMP Plug-ins                ⏳ FUTURE
Phase 7.8 Texture Undo / Redo          ⏳ FUTURE
```

## Phase 7.1 — Advanced Layers & Masks

### 7.1.1 Native Layer Mask Foundation — 0.5.6 — ✅ ACCEPTED

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
- [x] GIMP-authoritative mask painting in Texture Paint
- [x] Hybrid-progressive regression confirmed (`progressive_dirty_updates > 0`)
- [x] Runtime layer/mask target switching accepted
- [ ] Object Paint-on-mask explicit regression check carried forward

### 7.1.2 Advanced Layer Operations — 0.5.7 — 🚧 RUNTIME TEST

- [x] Native GIMP color-tag metadata
- [x] Set layer/group color tag
- [x] Merge Visible
- [x] Flatten Image
- [x] Dedicated Duplicate Group path
- [x] Visibility lock
- [x] Pixels lock
- [x] Position lock
- [x] Alpha lock
- [x] Compact production-panel integration
- [x] Destructive-operation confirmation
- [x] Paint ownership retired before destructive operations
- [x] Resulting composite republished after destructive operations
- [x] Resulting active layer buffer rebound
- [x] Mask status console text encoding cleanup
- [ ] Blender 5.2 + GIMP 3.2.4 runtime acceptance

### Phase 7.1 completion gate

Phase 7.1 can be frozen after 0.5.7 runtime acceptance confirms:

- Merge Visible produces the expected layer stack and remains paintable.
- Flatten Image produces one paintable result and discards hidden layers.
- Group duplication preserves a valid group/hierarchy.
- Color tags round-trip through GIMP metadata.
- All four lock states round-trip through GIMP metadata.
- Layer masks remain functional.
- Active Image ownership remains correct.
- Texture Paint and Object Paint remain regression-free.

## Phase 7.2 — Selections — NEXT

Planned: Rectangle, Ellipse, Free Select/Polygon, Color Select, Contiguous Color Select, Grow, Shrink, Feather, Invert, Select All, and Deselect.
