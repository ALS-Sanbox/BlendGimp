# BlendGimp Roadmap Snapshot — 0.5.17

## Accepted foundation

- Phase 6 frozen raster/sync/projection architecture
- Phase 7.0 native workflow usability
- Phase 7.1 advanced layers and masks
- Phase 7.2 selection core, exact irregular selection contours, modifier-combine workflow, and Select by Color reliability
- 0.5.16 removal of the artist-facing BlendGimp Area workflow and FG/BG swap

## Current runtime candidate — 0.5.17

Texture Editor live feedback:

- persistent brush-size footprint
- hardness cue
- immediate brush-sized GPU stroke preview
- pressure-scaled preview dabs
- progressive replacement by authoritative GIMP pixels
- no change to GIMP raster ownership

## Next after runtime acceptance

Continue code/UI cleanup, then Phase 7.3 GIMP Filters / GEGL integration. Exact cached GIMP brush-mask preview is optional polish and should not block Phase 7.3 unless the current circular preview proves insufficient in normal use.
