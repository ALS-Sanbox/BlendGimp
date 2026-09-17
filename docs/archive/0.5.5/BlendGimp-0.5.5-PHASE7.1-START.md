# BlendGimp 0.5.5 — Phase 7.1 Start Plan

## Baseline

```text
BlendGimp: 0.5.5
Build: 7.0-layers-ui-ownership-fix
Phase 6 functional baseline: 0.5.0 COMPLETE / FROZEN
Phase 7.0 usability pass: 0.5.5 runtime acceptance required before Phase 7.1
GIMP component: 0.5.5
GIMP runtime target: 3.2.4
IPC protocol: 1
```

## Next target — Phase 7.1 Advanced Layers & Masks

The first full GIMP ecosystem feature should extend the already mature layer transport rather than creating a new raster path.

### 7.1.1 Layer Mask foundation

First implementation target:

- Add mask to selected raster layer
- Remove mask
- Apply mask
- Enable/disable mask
- Switch edit target between layer pixels and mask pixels
- Return authoritative dirty pixels after mask-affecting operations
- Preserve active image/layer ownership
- Preserve Auto Sync ownership
- Preserve current layer selection and compact layer UI behavior
- One GIMP undo operation/group per explicit mask action

### UI direction

The production layer panel should visually distinguish:

```text
Layer
└── Layer Mask
```

Mask controls belong with Layers, not Preferences, because they are texture-authoring operations.

### Regression gate

Every 7.1 build must preserve:

- 0.5.5 Preferences behavior
- one-click Start GIMP automatic detection/launch behavior
- Start accent / Stop alert visual treatment
- Headless/Visible mode preference
- Detect/Check/Diagnostics remaining out of production panels
- Auto Paint preference behavior
- Create/Open/Save XCF
- Auto Sync ownership
- all nine Phase 6 tools
- hybrid progressive Texture Paint
- live Object Paint redraw
- active GIMP layer targeting
- pointer routing
- brush/dynamics state
- pressure behavior
- modifier-aware projection
- seam/footprint/silhouette/boundary protection
- dirty-region bulk publication
- clean engine shutdown/recovery
