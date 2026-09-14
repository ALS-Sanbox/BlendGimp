# BlendGimp 0.5.3

**Phase 7.0 — Image & Material Usability Update**  
**Frozen functional baseline:** 0.5.0 — Phase 6 COMPLETE / FROZEN  
**Previous usability patch:** 0.5.2 — Start GIMP auto-detect  
**Next major feature phase:** Phase 7.1 — Advanced Layers & Masks

BlendGimp is a Blender extension that uses GIMP as a persistent, normally headless raster engine while Blender remains the artist-facing 2D/3D texturing environment.

## What changed in 0.5.3

0.5.3 continues the production-UI cleanup started in 0.5.1/0.5.2. No new raster tool or paint architecture is introduced.

### Image & Material card

The old always-expanded texture creation form and image-management controls were replaced by a compact production workflow:

```text
Image & Material
────────────────────────────────
Active Material   [ Material ]
Active Image      [ Texture ]

[ Create Image ] [ Refresh From GIMP ]
[          Assign to Material          ]
```

### Create Image dialog

Pressing **Create Image** opens a Blender dialog instead of permanently occupying sidebar space. It contains:

- Name
- Width / Height
- RGBA / RGB
- Transparent / Solid background
- Solid background color when required
- Initial layer name

The dialog confirms with **Create** and then uses the existing frozen texture-creation pipeline.

### XCF file commands moved to the Image menu

The main production panel no longer carries dedicated Open/Save XCF buttons.

Use:

```text
Image Editor → Image → BlendGimp XCF
```

for:

- Open XCF...
- Save XCF
- Save XCF As...
- Save All XCF
- Refresh GIMP Image List

This keeps document file operations with Blender's image workflow while leaving the BlendGimp panel focused on everyday texture work.

### Previous 0.5.2 usability behavior retained

- Start GIMP works without manually pressing Detect GIMP first.
- Start automatically detects GIMP if the cached executable is missing.
- Start uses Blender's accent treatment.
- Stop uses Blender's alert/red treatment.
- Headless/Visible mode, Detect GIMP, Check Process, Auto Paint, and diagnostics remain in Preferences.

## Release identity

```text
BlendGimp: 0.5.3
Build: 7.0-image-material-ui
GIMP component: 0.5.3
GIMP runtime target: 3.2.4
IPC protocol: 1
Phase 6 baseline: 0.5.0 COMPLETE / FROZEN
```

## Preserved Phase 6 functionality

- Persistent Blender ↔ GIMP connection
- Headless GIMP with visible/debug fallback
- dirty-region / binary RGBA synchronization
- Blender-owned texture creation
- XCF open/save/save-as/save-all and dirty-exit recovery
- Texture Paint and Object Paint
- Paintbrush / Pencil / Eraser / Airbrush
- Fill / Gradient / Smudge / Clone / Heal
- GIMP Dynamics
- shared 2D/3D brush state
- automatic pointer routing
- layer CRUD, groups, blend modes, locks, opacity, visibility, merge down
- modifier-aware projection
- seam / silhouette / boundary / footprint protection

## Package layout

```text
blender/blendgimp/   Blender extension source
gimp/blendgimp/      GIMP-side persistent component
docs/                Current release, roadmap and regression docs
docs/archive/        Frozen 0.5.0 Phase 6 reference documents
packages/            Installable Blender and GIMP ZIPs
VERSION.md           Canonical 0.5.3 identity
CHECKSUMS.sha256     Package hashes
```

## Next

After 0.5.3 is visually/runtime accepted in Blender, continue the remaining production-panel cleanup and then begin **Phase 7.1 — Advanced Layers & Masks**.

## Repository

https://github.com/ALS-Sanbox/BlendGimp
