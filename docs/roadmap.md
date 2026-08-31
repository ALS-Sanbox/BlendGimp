# BlendGimp Development Roadmap

## Project Vision

**BlendGimp** is a Blender-integrated 3D texturing system powered by GIMP.

The goal is not to embed the normal GIMP application window inside Blender. Instead, BlendGimp will use GIMP as the primary 2D image-processing and painting engine while Blender provides the 3D model, UV data, material system, geometry information, projection, baking, and viewport.

The long-term goal is to create a workflow capable of replacing much of the need for external applications such as Substance Painter.

---

# Core Project Goal

Create a Blender texture workspace where an artist can:

- Paint directly on a 3D model.
- Paint directly on a 2D texture.
- Use GIMP brushes and image-processing tools.
- Use real GIMP layers and layer masks.
- See texture changes update automatically in Blender.
- See 3D painting automatically update the GIMP-backed texture.
- Paint multiple PBR channels.
- Use smart materials and geometry-aware masks.
- Bake mesh information directly from Blender.
- Use GIMP plug-ins and filters.
- Work with UDIM texture sets.
- Keep the entire texturing workflow inside Blender whenever possible.

---

# Core Architecture

BlendGimp should follow this responsibility model:

```text
Blender
│
├── 3D Models
├── UV Maps
├── Materials
├── Shader Nodes
├── Ray Casting
├── Geometry Information
├── Texture Baking
├── 3D Viewport
└── BlendGimp UI
        │
        ▼
BlendGimp Integration Layer
│
├── Tool State
├── Texture Sets
├── Material Channels
├── Stroke Translation
├── UV Projection
├── Dirty Texture Regions
├── Shared Memory
├── Smart Materials
├── Masks
└── GIMP Procedure Interface
        │
        ▼
GIMP
│
├── Raster Painting
├── Brushes
├── Layers
├── Layer Masks
├── Blend Modes
├── Selections
├── Filters
├── GEGL
├── PDB Procedures
└── GIMP Plug-ins
```

---

# Important Architectural Rules

## 1. GIMP Owns Texture Editing

GIMP should be treated as the authoritative editor for raster texture information.

Blender should display synchronized representations of the GIMP texture rather than maintaining a separate independent editing history.

---

## 2. Blender Owns 3D Information

Blender remains responsible for:

- Meshes
- UVs
- Materials
- Geometry
- Scene information
- Projection
- Ray casting
- Mesh-map generation
- Shader preview
- Rendering

---

## 3. Do Not Build a Disposable Bridge Prototype

There will be no separate bridge-prototype development stage.

The communication layer between Blender and GIMP will be implemented only as needed while building the actual BlendGimp Texture Editor.

Every major piece of infrastructure should contribute directly to production code.

---

## 4. GIMP and Blender Should Remain Loosely Coupled

Communication should happen through a defined BlendGimp protocol rather than tightly binding Blender Python directly to GIMP internals.

This makes future changes easier and allows native code to replace performance-sensitive portions later.

---

# Development Stages

---

# Stage 1 — BlendGimp Texture Editor

## Objective

Create a functional GIMP-powered texture workspace directly inside Blender.

This should be the first usable version of BlendGimp.

---

## Blender Workspace

Create a BlendGimp workspace containing:

```text
┌─────────────────────────────────────────────────────────────┐
│ BlendGimp                                                   │
├──────────────┬─────────────────────────────┬────────────────┤
│ Tools        │                             │ Layers         │
│              │                             │                │
│ Brush        │       Texture Canvas        │ Paint          │
│ Pencil       │                             │ Dirt           │
│ Eraser       │       UV Overlay            │ Base           │
│ Smudge       │                             │                │
│ Clone        │                             │                │
│ Heal         │                             │                │
│ Fill         │                             │                │
├──────────────┴─────────────────────────────┴────────────────┤
│ Brush Settings | Color | Material Channel | Texture Set     │
└─────────────────────────────────────────────────────────────┘
```

---

## Required Features

### GIMP Connection

- Detect supported GIMP installation.
- Start/connect to BlendGimp's GIMP component.
- Maintain a persistent GIMP session.
- Detect connection loss.
- Automatically reconnect when possible.
- Display BlendGimp connection state.

Possible states:

```text
BlendGimp
● Connected

BlendGimp
○ GIMP Offline

BlendGimp
◐ Connecting
```

---

## Texture Canvas

Create a texture canvas based on Blender's Image Editor.

Required functionality:

- Pan
- Zoom
- Fit image
- 100% zoom
- UV overlay
- UV overlay opacity
- UV island visibility
- Active-face highlighting
- Checkerboard transparency display

---

## Initial GIMP Tools

Support:

- Paintbrush
- Pencil
- Eraser
- Airbrush
- Smudge
- Clone
- Heal
- Fill
- Gradient

---

## Brush Controls

Expose:

- Brush
- Brush size
- Opacity
- Hardness
- Spacing
- Aspect ratio
- Angle
- Dynamics
- Foreground color
- Background color

---

## Tablet Support

Capture:

- Pressure
- Tilt
- Stroke movement
- Button state

Prepare the input system so the same brush settings can later be used in the 3D View.

---

## Layers

Initial GIMP layer functionality:

- Add layer
- Delete layer
- Duplicate layer
- Rename layer
- Reorder layer
- Hide/show layer
- Opacity
- Blend mode
- Lock layer
- Lock alpha
- Merge down
- Layer groups

---

## Texture Synchronization

Implement live synchronization:

```text
GIMP texture changed
        ↓
Detect changed image region
        ↓
Transfer changed pixels
        ↓
Update Blender image
        ↓
Refresh material
        ↓
Refresh 3D viewport
```

Avoid full-image transfers whenever possible.

Track dirty rectangles.

Example:

```text
Changed Region

X: 420
Y: 215
Width: 128
Height: 94
```

---

## UV Overlay

Blender sends UV information to BlendGimp.

Overlay should update when:

- UVs move
- UVs scale
- UVs rotate
- Mesh changes
- Active UV map changes

UV overlay must never permanently modify the texture.

---

## Stage 1 Completion Criteria

Stage 1 is complete when:

- GIMP connects automatically.
- A Blender material texture can be opened in BlendGimp.
- GIMP brushes can paint the image.
- GIMP layers work.
- Texture changes appear automatically on the Blender model.
- UVs can be displayed over the texture.
- Texture changes do not require manual saving/reloading.
- Basic tablet pressure works.

---

# Stage 2 — Direct 3D Painting

## Objective

Allow the user to paint directly on Blender geometry using GIMP's paint engine.

The same selected GIMP brush should work in both the 2D Texture Editor and the 3D Viewport.

---

## 3D Stroke Pipeline

```text
Mouse / Stylus
      ↓
Blender 3D View
      ↓
Ray Cast
      ↓
Mesh Intersection
      ↓
Polygon
      ↓
Barycentric Coordinates
      ↓
UV Coordinates
      ↓
Texture Coordinates
      ↓
GIMP Stroke
      ↓
Texture Update
      ↓
Blender Viewport Update
```

---

## Shared Tool State

Switching between 2D and 3D painting should preserve:

- Brush
- Color
- Size
- Opacity
- Pressure
- Dynamics
- Active texture
- Active material channel
- Active layer

---

## Stroke Interpolation

Prevent gaps during fast stylus movement.

Create intermediate paint points based on:

- Brush size
- Brush spacing
- Cursor velocity
- Tablet pressure

---

## UV Seam Handling

Detect UV discontinuities.

Do not allow a stroke crossing a seam to create a line across unrelated areas of the texture.

Example:

```text
Stroke on model
───────────────>

UV Island 1      UV Island 2

──────●●●          ●●●──────
```

Create separate GIMP stroke segments for disconnected UV regions.

---

## Geometry Controls

Add:

- Front faces only
- Paint through geometry
- Backface protection
- Occlusion checking
- Normal-angle limits
- Face selection masking
- Material masking
- Object masking

---

## Symmetry

Support:

- X symmetry
- Y symmetry
- Z symmetry

Later support radial symmetry.

---

## Brush Cursor

3D View should display:

- Brush radius
- Brush falloff
- Active color
- Surface projection
- Pressure feedback

---

## Stage 2 Completion Criteria

Stage 2 is complete when:

- The user can paint directly on a Blender object.
- Painting uses the active GIMP brush.
- Pressure works.
- 3D strokes update the GIMP texture.
- 2D texture updates immediately.
- UV seams are handled correctly.
- Occlusion controls work.
- Painting feels responsive enough for normal production use.

---

# Stage 3 — Full GIMP Ecosystem Integration

## Objective

Expose most commonly needed GIMP capabilities directly through BlendGimp.

The user should rarely need to open the traditional GIMP interface.

---

# GIMP Layers

Expand support to include:

- Layer groups
- Layer masks
- Alpha masks
- Lock position
- Lock pixels
- Lock alpha
- Blend modes
- Layer color labels
- Merge visible
- Flatten
- Duplicate groups

---

# Selections

Add:

- Rectangle selection
- Ellipse selection
- Free selection
- Color selection
- Grow selection
- Shrink selection
- Feather
- Invert selection
- Select all
- Deselect

---

# Filters

Expose GIMP/GEGL filters.

Categories may include:

- Blur
- Noise
- Distort
- Light and Shadow
- Artistic
- Edge Detect
- Enhance
- Color
- Generic

---

# Live Filter Preview

Filter parameters should preview directly on the 3D model.

```text
Filter Parameter Changed
        ↓
GIMP Preview
        ↓
Texture Composite
        ↓
Blender Texture
        ↓
3D Viewport
```

---

# GIMP Procedure Discovery

Use GIMP's procedure system to discover available operations.

BlendGimp should inspect:

- Procedure name
- Arguments
- Argument types
- Default values
- Ranges
- Enumerations

Then generate Blender controls automatically when practical.

---

# Plug-in Support

Provide two modes.

### Native BlendGimp Interface

For plug-ins whose parameters can be represented through GIMP procedures.

### Open GIMP Plug-in Window

For plug-ins that depend on custom GTK interfaces.

When the operation finishes, BlendGimp automatically refreshes the texture.

---

# Undo Integration

Texture undo should be controlled by GIMP.

```text
BlendGimp Undo
      ↓
GIMP Undo
      ↓
Changed region returned
      ↓
Blender texture refreshed
```

Blender continues to own undo for:

- Mesh edits
- Objects
- Materials
- UV edits
- Scene changes

---

## Stage 3 Completion Criteria

Stage 3 is complete when:

- Layers and masks are production-ready.
- Common filters work.
- Filter previews appear on the 3D model.
- Common GIMP plug-ins can be executed.
- GIMP procedures can be discovered dynamically.
- Undo/redo remains synchronized.

---

# Stage 4 — Substance-Style PBR Texturing

## Objective

Turn BlendGimp from an image editor into a complete material-authoring environment.

---

# Texture Sets

BlendGimp should understand materials rather than individual images.

Example:

```text
Texture Set

Worn Steel
│
├── Base Color
├── Roughness
├── Metallic
├── Normal
├── Height
├── Ambient Occlusion
├── Emission
└── Opacity
```

---

# Material Channels

Initial required channels:

- Base Color
- Roughness
- Metallic
- Normal
- Height
- Ambient Occlusion
- Emission
- Opacity

Additional channels may be user-defined.

---

# Automatic Shader Nodes

BlendGimp should automatically create and maintain Blender shader nodes.

Example:

```text
Base Color
    └────► Principled BSDF / Base Color

Roughness
    └────► Principled BSDF / Roughness

Metallic
    └────► Principled BSDF / Metallic

Normal
    └────► Normal Map
              └────► Principled BSDF / Normal

Emission
    └────► Principled BSDF / Emission
```

---

# Multi-Channel Painting

One brush stroke should be capable of modifying several channels.

Example:

```text
Rust Brush

Base Color
Orange/Brown

Roughness
0.85

Metallic
0.10

Normal
Rust Pitting

Height
-0.02
```

---

# Mesh Map Baking

Create a dedicated Bake Mesh Maps panel.

```text
BLENDGIMP — BAKE MESH MAPS

Resolution
[4096]

☑ Normal
☑ World Space Normal
☑ Ambient Occlusion
☑ Curvature
☑ Position
☑ Thickness
☑ Material ID
☑ Object ID

High Poly
[ Character_High ]

Low Poly
[ Character_Low ]

Cage
[ Automatic ]

Anti-Aliasing
[ 4x ]

[ BAKE ALL ]
```

---

# Required Mesh Maps

Prioritize:

- Normal
- World-space normal
- Ambient occlusion
- Curvature
- Position
- Thickness
- Object ID
- Material ID

---

# Bake Dependency Tracking

BlendGimp should know when a baked map becomes outdated.

Examples:

```text
⚠ Curvature Outdated
Mesh geometry changed

⚠ AO Outdated
High-poly source changed
```

---

# Smart Masks

Allow masks based on:

- Curvature
- AO
- Position
- Surface normals
- Material ID
- Object ID
- Vertex groups
- Face selection
- UV island
- Height
- Procedural noise

---

# Smart Materials

Create reusable BlendGimp materials.

Example:

```text
SMART MATERIAL
Worn Painted Steel

Paint
├── Base Paint
├── Color Variation
├── Scratches
└── Dirt

Edge Wear
└── Curvature Mask

Dirt
├── AO Mask
└── Position Mask

Metal
└── Exposed Metal Mask
```

---

# BlendGimp Material Format

Possible extension:

```text
.bgmat
```

A BlendGimp Material should store:

- Layer structure
- Material channels
- Blend modes
- GIMP filters
- Masks
- Generators
- Procedural parameters
- Brush settings
- Channel values
- Texture references

---

# Material Library

Suggested categories:

```text
METAL
├── Worn Steel
├── Painted Steel
├── Rusted Iron
├── Aluminum
└── Chrome

WOOD
├── Oak
├── Painted Wood
├── Weathered Wood
└── Wet Wood

FABRIC
├── Cotton
├── Denim
├── Canvas
└── Leather

STONE
├── Granite
├── Concrete
├── Brick
└── Marble
```

---

# Projection Painting

Support image projection directly onto the model.

Workflow:

```text
Reference Photograph
        ↓
Projection Camera
        ↓
3D Model
        ↓
BlendGimp Layer
        ↓
Clone / Heal / Color Correct
```

GIMP handles image cleanup.

Blender handles 3D projection.

---

# Geometry-Generated Masks

Allow direct creation of GIMP masks from Blender.

```text
Create Mask From

Selected Faces
Vertex Group
UV Island
Material Slot
Object
Facing Direction
Ambient Occlusion
Curvature
Position
```

---

# Stage 4 Completion Criteria

Stage 4 is complete when:

- Materials contain multiple PBR channels.
- Channels automatically connect to Blender shaders.
- Brushes can affect multiple channels.
- Blender can bake required mesh maps.
- Smart masks work.
- Smart materials can be saved and reused.
- Projection painting works.

At this point, BlendGimp should cover a substantial portion of a Substance Painter-style workflow.

---

# Stage 5 — UDIM, Performance and Native Integration

## Objective

Make BlendGimp suitable for large professional projects and high-resolution textures.

---

# UDIM Support

Support standard UDIM layouts.

Example:

```text
1001 1002 1003 1004
1011 1012 1013 1014
1021 1022 1023 1024
```

---

## UDIM 3D Painting

BlendGimp should automatically determine which tile receives each stroke.

```text
3D Stroke
    ↓
UV Coordinate
    ↓
UDIM Calculation
    ↓
Correct Texture Tile
    ↓
GIMP Stroke
```

---

# UDIM Canvas

Allow multiple tiles to be displayed.

```text
┌─────────┬─────────┬─────────┐
│ 1001    │ 1002    │ 1003    │
│         │         │         │
├─────────┼─────────┼─────────┤
│ 1011    │ 1012    │ 1013    │
│         │         │         │
└─────────┴─────────┴─────────┘
```

---

# Shared Memory

Move large texture data through shared memory rather than repeatedly serializing image data through Python.

Target architecture:

```text
GIMP GEGL Buffer
        ↓
Shared Memory
        ↓
BlendGimp Native Layer
        ↓
Blender Image/GPU Texture
```

---

# Dirty Region Updates

Only changed texture areas should update.

Do not update the entire image for every brush stroke.

Example:

```text
8K Texture

Full Image
8192 × 8192

Changed Area
122 × 87

Only transfer
122 × 87 pixels
```

---

# Native BlendGimp Module

If Python performance becomes limiting, introduce a small native C/C++ component.

Native responsibilities:

- Shared memory
- Pixel transfer
- Dirty rectangle processing
- GPU texture updates
- High-frequency stylus processing
- Large texture management

Python remains responsible for:

- UI
- Tool management
- Materials
- Plug-in discovery
- Project settings
- Smart materials
- Workflow

---

# Native Blender Editor

Only consider creating a dedicated Blender editor type if extending the Image Editor becomes too limiting.

Possible future editor:

```text
SPACE_BLENDGIMP
```

This should not be attempted until the existing Blender extension proves that a native editor is necessary.

---

# Performance Targets

Target usable interaction at:

- 1K textures
- 2K textures
- 4K textures

Professional target:

- 8K textures

Advanced target:

- 16K textures
- Multiple UDIMs

---

# Stage 5 Completion Criteria

Stage 5 is complete when:

- UDIM painting works.
- Large textures remain responsive.
- Shared memory is operational if required.
- Dirty-region GPU updates work.
- 8K textures are practical.
- Multiple texture sets remain manageable.
- Native code handles performance-sensitive operations where required.

---

# Future Development

The following features should be considered after the core roadmap is functional.

---

## Procedural Generators

Possible generators:

- Edge wear
- Dirt
- Dust
- Rust
- Scratches
- Water streaks
- Oil
- Grunge
- Cavity
- Moss
- Snow
- Mud

---

## Generator Stack Example

```text
Painted Metal

Base Paint
│
├── Noise Variation
│
├── Edge Wear
│    └── Curvature
│
├── Dirt
│    ├── AO
│    └── Position
│
├── Scratches
│
└── Exposed Metal
```

---

# Non-Destructive Effects

Investigate a non-destructive effect stack.

Example:

```text
Layer
│
├── Blur
├── Color Correction
├── Noise
└── Sharpen
```

Effects should remain editable when possible.

---

# Material Preset Browser

Add:

- Search
- Categories
- Favorites
- Tags
- Preview spheres
- User libraries

---

# Brush Library

Expose:

- GIMP brushes
- User brushes
- Favorites
- Categories
- Recent brushes

---

# Asset Packaging

Potential BlendGimp asset formats:

```text
.bgmat     BlendGimp Material
.bgbrush   BlendGimp Brush
.bgmask    BlendGimp Smart Mask
.bgproj    BlendGimp Texture Project
```

These formats are conceptual and may change.

---

# Texture Project Management

A BlendGimp project should eventually track:

```text
Project
│
├── Blender Objects
├── Materials
├── Texture Sets
├── GIMP Documents
├── UDIM Tiles
├── Mesh Maps
├── Smart Materials
├── Masks
└── External Assets
```

---

# Save Strategy

Avoid embedding unnecessarily large texture data directly inside `.blend` files.

Prefer a project structure such as:

```text
MyCharacter/
│
├── character.blend
│
├── BlendGimp/
│   │
│   ├── project.json
│   │
│   ├── materials/
│   │
│   ├── textures/
│   │
│   ├── gimp/
│   │
│   ├── bakes/
│   │
│   └── cache/
│
└── exports/
```

---

# Crash Recovery

BlendGimp should eventually maintain automatic recovery data.

Track:

- Active texture
- Active layer
- Unsaved GIMP documents
- Texture-set state
- Material assignments

Recovery should not depend entirely on Blender's `.blend` recovery system.

---

# Version Compatibility

Initial development target:

```text
Blender 5.x
GIMP 3.2+
```

Avoid supporting old Blender or GIMP versions until the architecture is stable.

---

# Development Priorities

Use the following priority system.

## P0 — Required

The project cannot reach its main goal without these.

- Live texture synchronization
- GIMP painting
- 3D painting
- GIMP layers
- Material channels
- Baking
- Smart masks
- Smart materials
- Shader integration

---

## P1 — Important

Strongly improves production usability.

- Projection painting
- Multi-channel brushes
- Filter previews
- Plug-in integration
- UDIM
- Shared memory
- Material library

---

## P2 — Enhancement

Useful after the main workflow is reliable.

- Procedural generators
- Asset browser
- Custom material marketplace/library
- Advanced symmetry
- Specialized baking options
- Custom native Blender editor

---

# Project Rules

## Rule 1

Do not recreate functionality that Blender already provides well.

Use Blender for:

- Geometry
- Rendering
- UV data
- Baking
- Materials
- Viewport interaction

---

## Rule 2

Do not recreate functionality that GIMP already provides well.

Use GIMP for:

- Raster painting
- Brushes
- Layers
- Masks
- Filters
- Image processing
- Plug-ins

---

## Rule 3

BlendGimp exists to connect those capabilities and add the missing 3D texturing workflow.

---

## Rule 4

Performance optimization should be based on measured bottlenecks.

Do not move systems to C/C++ simply because native code is faster.

Prototype behavior in Python first unless Python prevents the feature from functioning correctly.

---

## Rule 5

Avoid temporary architecture that will need to be discarded.

Every major system should move the project toward the final BlendGimp workflow.

---

# Major Technical Risks

Track these carefully during development.

### Risk: Texture Transfer Performance

Mitigation:

- Dirty rectangles
- Shared memory
- Partial texture updates
- Native extension if required

---

### Risk: UV Seam Painting

Mitigation:

- Detect UV discontinuities
- Split strokes
- Add padding/bleeding
- Test complex production meshes

---

### Risk: GIMP Plug-in Compatibility

Mitigation:

Support plug-ins through multiple methods:

1. Direct BlendGimp-generated interface
2. Procedure call
3. External GIMP interface fallback

---

### Risk: Blender API Limitations

Mitigation:

Use Blender's Python API first.

Only modify Blender's native source when there is a demonstrated limitation.

---

### Risk: Undo Synchronization

Mitigation:

GIMP owns texture-edit undo.

Blender owns scene and geometry undo.

---

### Risk: Large Texture Memory Usage

Mitigation:

- Tile images
- Shared buffers
- Cache controls
- Lazy loading
- UDIM-aware memory management

---

# Recommended Repository Structure

```text
BlendGimp/
│
├── blender/
│   │
│   ├── addon/
│   ├── ui/
│   ├── painting/
│   ├── materials/
│   ├── texture_sets/
│   ├── baking/
│   ├── masks/
│   ├── udim/
│   └── ipc/
│
├── gimp/
│   │
│   ├── plugin/
│   ├── painting/
│   ├── layers/
│   ├── procedures/
│   └── ipc/
│
├── native/
│   │
│   ├── shared_memory/
│   └── texture_update/
│
├── protocol/
│
├── tests/
│   │
│   ├── blender/
│   ├── gimp/
│   └── integration/
│
├── docs/
│
├── examples/
│
└── README.md
```

The `native` folder can remain empty until necessary.

---

# Testing Strategy

Maintain several standardized test assets.

## Simple Test

```text
Cube
Single material
Single UV map
1024 × 1024 texture
```

Used for:

- Basic painting
- Synchronization
- Layers
- Filters

---

## Seam Test

Mesh containing:

- Multiple UV islands
- Rotated islands
- Mirrored islands
- Narrow seams

Used specifically for stroke testing.

---

## Character Test

Character containing:

- Multiple materials
- Complex UVs
- Multiple texture sets

Used for production workflow testing.

---

## UDIM Test

Character or environment using:

```text
1001
1002
1003
1004
1011
1012
```

---

## High Resolution Test

Use:

```text
4096 × 4096
8192 × 8192
```

to measure:

- Brush latency
- Update latency
- RAM use
- GPU upload time

---

# Performance Metrics

Track actual numbers during development.

Example:

```text
Brush latency:
18 ms

GIMP processing:
5 ms

Texture transfer:
7 ms

GPU update:
4 ms

Total:
34 ms
```

This will show exactly where native optimization is required.

---

# Milestone Checklist

## Milestone A — 2D BlendGimp

- [ ] Blender extension loads
- [ ] GIMP connection works
- [ ] Texture opens
- [ ] GIMP brush paints
- [ ] Blender updates automatically
- [ ] UV overlay works
- [ ] GIMP layers work
- [ ] Tablet pressure works

---

## Milestone B — 3D Paint

- [ ] Viewport strokes captured
- [ ] Ray casting works
- [ ] UV coordinates resolved
- [ ] GIMP receives strokes
- [ ] Surface updates live
- [ ] Seam detection works
- [ ] Occlusion works
- [ ] Pressure works in 3D

---

## Milestone C — GIMP Environment

- [ ] Layer masks
- [ ] Layer groups
- [ ] Filters
- [ ] Live filter preview
- [ ] Selection tools
- [ ] Procedure discovery
- [ ] Plug-in support
- [ ] Undo synchronization

---

## Milestone D — PBR Workflow

- [ ] Texture sets
- [ ] Base Color
- [ ] Roughness
- [ ] Metallic
- [ ] Normal
- [ ] Height
- [ ] AO
- [ ] Emission
- [ ] Multi-channel brushes
- [ ] Automatic shader setup

---

## Milestone E — Substance-Style Features

- [ ] Normal bake
- [ ] AO bake
- [ ] Curvature bake
- [ ] Position bake
- [ ] World normal bake
- [ ] Smart masks
- [ ] Smart materials
- [ ] Material presets
- [ ] Projection painting

---

## Milestone F — Professional Workflow

- [ ] UDIM support
- [ ] Multiple texture sets
- [ ] Dirty-region optimization
- [ ] Shared memory if necessary
- [ ] 8K testing
- [ ] Native acceleration if necessary
- [ ] Crash recovery
- [ ] Stable project format

---

# Definition of BlendGimp 1.0

BlendGimp 1.0 should allow an artist to:

1. Open Blender.
2. Select a model.
3. Enter the BlendGimp workspace.
4. Create or select a PBR texture set.
5. Bake required mesh maps.
6. Apply a smart material.
7. Paint directly on the model using GIMP brushes.
8. Paint or edit the texture in 2D.
9. Use layers and masks.
10. Use GIMP filters and plug-ins.
11. Paint multiple PBR channels.
12. See every change immediately on the model.
13. Save the Blender project.
14. Return later without rebuilding the texturing setup.
15. Export finished textures when needed.

The artist should not need to manually export the model to another texturing application during this workflow.

---

# Long-Term Vision

BlendGimp should eventually feel less like:

```text
GIMP connected to Blender
```

and more like:

```text
Blender has gained an advanced professional texture-painting environment.
```

The defining combination is:

```text
GIMP
Powerful 2D Art Ecosystem

        +

Blender
Powerful 3D Ecosystem

        +

BlendGimp
3D Texturing, Materials, Baking,
Smart Masks, Smart Materials,
Projection and Live Synchronization
```

The goal is not to clone Substance Painter feature-for-feature.

The goal is to create a Blender-native texturing workflow that uses the strengths of GIMP and Blender together and removes the need to constantly move assets between separate applications.

---

# Current Project Direction

**Current development priority: Stage 1 — BlendGimp Texture Editor**

Do not create a separate bridge prototype.

Build the minimum GIMP communication infrastructure directly into the real BlendGimp editor and expand forward from there.

The first major success state is:

```text
Blender 3D View
       +
BlendGimp Texture Editor
       +
GIMP Brush Engine
       +
GIMP Layers
       +
UV Overlay
       +
Live Material Updates
```

Once that works reliably, development moves directly into 3D painting.