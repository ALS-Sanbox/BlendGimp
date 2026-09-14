# BlendGimp 0.5.3 — Image & Material Usability Update

## Objective

Continue the Phase 7.0 production-UI cleanup without changing the frozen Phase 6 painting architecture.

This patch focuses on the **Image & Material** workflow shown in the main BlendGimp panel.

## Completed

- [x] Replaced the permanent inline **New BlendGimp Texture** form with one compact **Create Image** button.
- [x] **Create Image** now opens a Blender property dialog containing:
  - Name
  - Width / Height
  - Format
  - Background
  - Background color when Solid is selected
  - Initial layer name
- [x] Dialog confirmation is labeled **Create**.
- [x] Added compact **Active Material** presentation.
- [x] Added compact **Active Image** presentation.
- [x] Added production actions:
  - **Create Image**
  - **Refresh From GIMP**
  - **Assign to Material**
- [x] Removed **Open XCF**, **Save All XCF**, **Refresh GIMP Images**, and **Disconnect** from the Image & Material production card.
- [x] Added **Image Editor → Image → BlendGimp XCF** submenu.
- [x] Moved XCF file commands into that Image menu:
  - Open XCF
  - Save XCF
  - Save XCF As
  - Save All XCF
  - Refresh GIMP Image List
- [x] XCF menu actions resolve the active BlendGimp image automatically.
- [x] Existing material/image ownership protection remains in place.
- [x] Existing engine Start/Reconnect/Stop behavior from 0.5.2 remains unchanged.

## Resulting production card

```text
Image & Material
────────────────────────────────
Active Material   [ Material ]
Active Image      [ Texture ]

[ Create Image ] [ Refresh From GIMP ]
[          Assign to Material          ]
```

The texture creation settings appear only when **Create Image** is pressed.

## XCF file workflow

XCF files are image documents, so their user-facing file commands now live with Blender's Image Editor image commands rather than permanently occupying the production sidebar.

```text
Image Editor
  Image
    BlendGimp XCF
      Open XCF...
      Save XCF
      Save XCF As...
      Save All XCF
      Refresh GIMP Image List
```

## Production UI rule

```text
Preferences
    = setup / configuration / diagnostics

Image menu
    = image document file operations

BlendGimp production panel
    = active material / active image / everyday authoring actions
```

## Protected baseline

This release must not change:

- GIMP-authoritative raster editing
- Auto Sync ownership
- dirty-region publication
- Texture Paint / Object Paint architecture
- shared brush state
- automatic pointer routing
- modifier-aware projection
- seam / footprint protection
- GIMP layer operations
- XCF persistence and dirty-exit recovery
