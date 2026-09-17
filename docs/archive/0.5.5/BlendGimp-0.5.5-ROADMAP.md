# BlendGimp Development Roadmap — 0.5.5

## Current status

```text
Phase 1 — Extension Foundation              ✅ COMPLETE
Phase 2 — Live Texture Synchronization      ✅ CORE COMPLETE
Phase 3 — Direct 3D Painting                ✅ COMPLETE
Phase 4 — Production-Aware Projection       ✅ COMPLETE
Phase 5 — Headless GIMP Engine              ✅ COMPLETE
Phase 6 — BlendGimp Texture Editor          ✅ COMPLETE / FROZEN (0.5.0)
Phase 7.0 — Usability / Production UI       🔧 0.5.5 RUNTIME ACCEPTANCE
Phase 7.1 — Advanced Layers & Masks         ▶ NEXT
Phase 7.2 — Selections                      ⏳
Phase 7.3 — Filters / GEGL                  ⏳
Phase 7.4 — Live Filter Preview             ⏳
Phase 7.5 — Procedure Discovery / Auto UI   ⏳
Phase 7.6 — GIMP Plug-in Integration        ⏳
Phase 7.7 — Texture Undo / Redo             ⏳
Phase 8 — PBR Texture Sets                  ⏳ FUTURE
Phase 9 — Baking / Smart Features           ⏳ FUTURE
Phase 10 — UDIM / Professional Workflow     ⏳ FUTURE
Phase 11 — Project / Recovery System        ⏳ FUTURE
```

## Current release

- BlendGimp **0.5.5**
- Build `7.0-layers-ui-ownership-fix`
- GIMP component **0.5.5**
- GIMP runtime target **3.2.4**
- IPC protocol **1**
- Functional baseline **0.5.0 / Phase 6 frozen**

## Phase 7.0 — Usability / Production UI — 0.5.5 PATCH

- [x] Real BlendGimp Preferences
- [x] Engine mode moved out of production panels
- [x] Detect GIMP moved to Preferences
- [x] Process check moved to Preferences
- [x] Auto Paint moved to Preferences
- [x] Advanced diagnostics moved to Preferences
- [x] Engine recovery preference moved to Preferences
- [x] Compact production engine status
- [x] Start GIMP automatically detects GIMP when needed
- [x] Manual Detect remains Preferences-only troubleshooting
- [x] Start uses active/accent styling; Stop uses alert/red styling
- [x] Contextual recovery actions only
- [x] Image & Material production grouping
- [x] Compact Active Material / Active Image card
- [x] Active Image converted to Blender-native Image datablock selector
- [x] BlendGimp-backed image selection pins the Texture Editor target
- [x] Ordinary Blender images remain selectable and are clearly marked unlinked
- [x] Create/Open XCF populate the Active Image selector automatically
- [x] Create Image moved to a modal properties dialog
- [x] XCF Open/Save moved to Image Editor → Image → BlendGimp XCF
- [x] Refresh From GIMP and Assign to Material kept as everyday production actions
- [x] Active Image switching now atomically rebinds layer-buffer and Auto Sync ownership
- [x] Live layer-buffer refresh refuses mismatched image ownership
- [x] Layers panel reorganized into compact stack + action toolbar + selected-layer properties
- [x] Existing layer CRUD/groups/modes/locks/merge functionality preserved
- [x] Frozen Phase 6 regression validation

## Phase 7.1 — Advanced Layers & Masks — NEXT

- [ ] Create layer mask
- [ ] Delete layer mask
- [ ] Apply layer mask
- [ ] Enable / disable layer mask
- [ ] Edit layer vs edit mask targeting
- [ ] Mask visibility state
- [ ] Mask thumbnail/state representation where practical
- [ ] Expanded lock controls
- [ ] Layer color labels
- [ ] Merge visible
- [ ] Flatten image
- [ ] Duplicate groups
- [ ] Dirty-region refresh after mask/layer operations
- [ ] Preserve one authoritative GIMP undo operation/group per action

## Phase 7.2 — Selections

- [ ] Rectangle
- [ ] Ellipse
- [ ] Free Select
- [ ] Color Select
- [ ] Select All / Deselect
- [ ] Invert
- [ ] Grow / Shrink
- [ ] Feather
- [ ] Selection visualization where practical

## Phase 7.3 — Filters / GEGL

- [ ] Operation discovery
- [ ] Parameter handling
- [ ] Blur
- [ ] Noise
- [ ] Distort
- [ ] Light & Shadow
- [ ] Artistic
- [ ] Edge Detect
- [ ] Enhance
- [ ] Color
- [ ] Generic GEGL operations

## Phase 7.4 — Live Filter Preview

- [ ] Temporary preview state
- [ ] 2D texture preview
- [ ] 3D material preview
- [ ] Apply
- [ ] Cancel
- [ ] Clear undo ownership

## Phase 7.5 — Procedure Discovery / Automatic UI

- [ ] Procedure name/documentation
- [ ] Arguments and types
- [ ] Defaults
- [ ] Ranges
- [ ] Enumerations
- [ ] Safe generic Blender UI generation

## Phase 7.6 — GIMP Plug-ins

- [ ] Native BlendGimp UI path for compatible procedures
- [ ] Temporary visible GIMP path for custom GTK/UI plug-ins
- [ ] Return to normal headless workflow
- [ ] Automatic texture refresh after completion

## Phase 7.7 — Texture Undo / Redo

- [ ] GIMP texture undo
- [ ] GIMP texture redo
- [ ] Dirty-region return
- [ ] Automatic 2D/3D refresh
- [ ] Keep Blender scene undo separate

## Phase 8 — PBR Texture Sets

- [ ] Texture Set object
- [ ] Base Color / Roughness / Metallic / Normal / Height / AO / Emission / Opacity
- [ ] Custom channels
- [ ] Automatic Principled BSDF wiring
- [ ] Multi-channel painting

## Long-term rule

GIMP owns authoritative raster editing. Blender owns geometry, UVs, materials, projection, viewport interaction, and the artist-facing interface. Configuration belongs in Preferences; production authoring belongs in BlendGimp panels.
