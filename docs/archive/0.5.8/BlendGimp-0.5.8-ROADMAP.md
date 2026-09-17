# BlendGimp Development Roadmap — 0.5.8

## Status

```text
Phase 6   Texture Editor / Paint Engine     ✅ FROZEN
Phase 7.0 Production UI / Usability         ✅ CORE COMPLETE
Phase 7.1 Advanced Layers & Masks           ✅ CORE COMPLETE
Phase 7.2 GIMP Selections                   🚧 CURRENT — 0.5.8
Phase 7.3 GEGL / Filters                    ⏳ NEXT
Phase 7.4 GIMP Procedure / Plug-in Access   ⏳ PLANNED
Phase 8   PBR Texture Sets                  ⏳ PLANNED
```

## 7.2.1 Selection Foundation — 0.5.8

- [x] GIMP-authoritative selection state query
- [x] Rectangle Select
- [x] Ellipse Select
- [x] Select All
- [x] Deselect
- [x] Invert
- [x] click-drag selection interaction in BlendGimp Texture Editor
- [x] pause automatic paint routing while selection interaction owns the canvas
- [x] synchronized image-specific selection bounds
- [x] persistent Blender Image Editor selection overlay
- [x] active image selection state isolation
- [x] selection controls in BlendGimp Texture Editor sidebar
- [x] preserve GIMP-authoritative painting so selections clip paint naturally
- [x] visible Color Tag badges in layer/group rows
- [ ] runtime acceptance

## 7.2.2 Expanded Selection Tools

After the 0.5.8 foundation is accepted:

- [ ] Free Select / Polygon Select
- [ ] Select by Color
- [ ] Contiguous / Fuzzy Select
- [ ] Grow
- [ ] Shrink
- [ ] Feather
- [ ] Sharpen
- [ ] Border
- [ ] selection add/subtract/intersect modes
- [ ] exact arbitrary selection contour / marching-ants overlay
- [ ] selection to layer mask / layer mask to selection shortcuts
- [ ] selection save/load channels

## Phase 7.1 carried regression items

0.5.8 also carries the final non-blocking Layer UI checks:

- [x] Color Tag protocol round-trip existed in 0.5.7
- [x] 0.5.8 adds visible Color Tag stack badges
- [ ] runtime verify Red/Orange/Yellow/Green/Blue/Violet/Brown/Gray/None badges
- [ ] runtime verify Position lock enforcement
- [ ] runtime verify Visibility lock enforcement
- [ ] visually confirm Alpha lock preserves existing alpha
