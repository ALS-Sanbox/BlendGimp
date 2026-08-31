# BlendGimp

**GIMP-powered texture painting and material authoring for Blender**

BlendGimp is an open-source project that aims to combine the strengths of **Blender** and **GIMP** into a unified 3D texturing workflow.

Instead of recreating an entire 2D painting ecosystem inside Blender, BlendGimp uses **GIMP as the raster painting and image-processing engine** while Blender remains responsible for geometry, UVs, materials, projection, baking, and the 3D viewport.

The long-term goal is to make texture creation feel like a native part of Blender while gaining access to GIMP's brushes, layers, masks, filters, GEGL operations, and plug-in ecosystem.

> **Project Status:** Early Development — `v0.1.0`
>
> BlendGimp is currently under active development and is **not yet production ready**.

---

## Vision

The goal is for an artist to eventually be able to:

* Paint directly on a 3D model using GIMP's painting engine.
* Paint directly on the corresponding 2D texture.
* Use GIMP brushes from inside the Blender workflow.
* Work with real GIMP layers and layer groups.
* Use GIMP masks, filters, selections, and plug-ins.
* See texture changes immediately on the Blender model.
* Send 3D paint strokes from Blender back to the GIMP texture.
* Paint multiple PBR channels.
* Bake mesh maps directly from Blender.
* Create smart masks and reusable smart materials.
* Work with UDIM texture sets.
* Avoid constantly exporting assets between Blender and a separate texturing application.

The objective is not to clone Substance Painter feature-for-feature.

The objective is to create a powerful Blender-native texturing workflow by connecting:

```text
GIMP
2D Painting + Image Processing
             │
             ▼
        BlendGimp
             │
             ▼
Blender
3D Geometry + Materials + Baking
```

---

# Current Development Status

Development is currently focused on the first major milestone:

## Stage 1 — BlendGimp Texture Editor

The communication foundation between Blender and GIMP is operational.

### Currently Implemented

#### Blender Extension

* [x] Blender extension registration
* [x] BlendGimp Blender UI panel
* [x] GIMP installation detection
* [x] GIMP process launching
* [x] GIMP process status checking
* [x] Connect/disconnect controls
* [x] BlendGimp protocol handshake
* [x] GIMP runtime version reporting

#### Blender ↔ GIMP Communication

* [x] Persistent local TCP communication
* [x] JSON-based BlendGimp protocol
* [x] Protocol version validation
* [x] Request/response IDs
* [x] Ping/status communication
* [x] Retrieve open GIMP images
* [x] Retrieve image layer hierarchy

#### GIMP Layer Management

* [x] Select active layer
* [x] Show/hide layers
* [x] Change layer opacity
* [x] Add layers
* [x] Delete layers
* [x] Rename layers
* [x] Duplicate layers
* [x] Reorder layers
* [x] Move layers between groups
* [x] Create layer groups
* [x] Merge layer down
* [x] Lock layer content
* [x] Lock layer position
* [x] Lock layer alpha
* [x] Change GIMP blend modes

### Current Next Priorities

* [ ] Complete the GIMP-powered texture canvas
* [ ] Transfer texture pixels between GIMP and Blender
* [ ] Automatic Blender image refresh
* [ ] Live material/viewport updates
* [ ] Dirty-region texture synchronization
* [ ] UV overlay
* [ ] GIMP brush integration
* [ ] Brush controls
* [ ] Tablet pressure support

---

# Architecture

BlendGimp follows a strict division of responsibility.

## Blender Owns 3D

Blender remains responsible for:

* Meshes
* Objects
* UV maps
* Materials
* Shader nodes
* Geometry information
* Ray casting
* Projection
* Texture baking
* 3D viewport interaction
* Rendering

## GIMP Owns Raster Editing

GIMP remains responsible for:

* Raster painting
* Brushes
* Layers
* Layer groups
* Layer masks
* Selections
* Blend modes
* Filters
* GEGL operations
* Image processing
* GIMP plug-ins

## BlendGimp Connects Them

BlendGimp provides the integration layer responsible for:

* Communication
* Texture synchronization
* Tool state
* Stroke translation
* UV projection
* Material channels
* Texture sets
* Smart masks
* Smart materials
* Dirty texture regions
* GIMP procedure integration

Conceptually:

```text
Blender
│
├── Geometry
├── UV Maps
├── Materials
├── Shader Nodes
├── Ray Casting
├── Baking
└── 3D Viewport
        │
        ▼
BlendGimp
│
├── IPC Protocol
├── Tool State
├── Texture Sync
├── Stroke Translation
├── Texture Sets
├── Material Channels
├── Smart Masks
└── GIMP Procedure Interface
        │
        ▼
GIMP
│
├── Brushes
├── Raster Painting
├── Layers
├── Masks
├── Selections
├── Filters
├── GEGL
└── Plug-ins
```

---

# Communication Protocol

The current development implementation uses a local TCP connection between Blender and the BlendGimp GIMP component.

```text
Host: 127.0.0.1
Port: 8765
Protocol Version: 1
```

Messages are exchanged as newline-delimited JSON.

Example:

```text
Blender
   │
   │ HELLO
   ▼
GIMP
   │
   │ READY
   ▼
Blender
```

Current protocol operations include:

```text
HELLO
PING
STATUS

GET_IMAGES
GET_IMAGE_LAYERS

SET_ACTIVE_LAYER
SET_LAYER_VISIBILITY
SET_LAYER_OPACITY

ADD_LAYER
DELETE_LAYER
RENAME_LAYER
DUPLICATE_LAYER
REORDER_LAYER
MOVE_LAYER

CREATE_GROUP
MERGE_LAYER_DOWN

SET_LAYER_LOCK
SET_LAYER_MODE
```

The same protocol will later carry commands for painting, texture synchronization, filters, selections, masks, and other GIMP operations.

---

# Requirements

Current development targets:

```text
Blender 5.x
GIMP 3.2+
Python
```

The Blender extension currently declares:

```text
Minimum Blender Version: 5.0.0
BlendGimp Version: 0.1.0
```

Development is currently primarily being tested on Windows.

Support for additional operating systems can be expanded once the core architecture stabilizes.

---

# Development Installation

BlendGimp is still in early development, so installation is currently intended for developers rather than end users.

Clone the repository:

```bash
git clone https://github.com/ALS-Sanbox/BlendGimp.git
```

Enter the project:

```bash
cd BlendGimp
```

BlendGimp contains two primary components:

```text
Blender Extension
        +
GIMP Component
```

Both must be installed in their respective applications for the current development build to communicate.

Formal packaged installation instructions will be added once the extension layout and GIMP component are stabilized.

---

# Development Roadmap

## Stage 1 — BlendGimp Texture Editor

Create the first usable GIMP-powered texture environment inside Blender.

Major goals:

* GIMP connection
* Texture canvas
* GIMP brushes
* Layer management
* Brush settings
* Tablet support
* UV overlay
* Live texture synchronization

---

## Stage 2 — Direct 3D Painting

Allow GIMP-powered painting directly on Blender geometry.

The planned stroke pipeline is:

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
Blender Viewport
```

Major goals include:

* 3D brush cursor
* Ray casting
* UV coordinate resolution
* Stroke interpolation
* UV seam handling
* Occlusion
* Front-face controls
* Symmetry
* Tablet pressure

---

## Stage 3 — Full GIMP Ecosystem Integration

Expand GIMP functionality available through Blender.

Planned features:

* Layer masks
* Advanced layer groups
* Selections
* GIMP/GEGL filters
* Live filter preview
* GIMP procedure discovery
* Plug-in integration
* Undo/redo synchronization

The objective is for common GIMP operations to be accessible without constantly switching to the traditional GIMP interface.

---

## Stage 4 — PBR Material Authoring

Turn BlendGimp into a complete material texturing environment.

Planned material channels include:

```text
Texture Set
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

Planned features:

* Texture sets
* Automatic Blender shader setup
* Multi-channel painting
* Mesh-map baking
* Curvature
* Ambient Occlusion
* Position
* Thickness
* Material ID
* Object ID
* Smart masks
* Smart materials
* Projection painting

---

## Stage 5 — UDIM and Performance

Prepare BlendGimp for large professional projects.

Planned features:

* UDIM support
* Multi-tile painting
* Dirty rectangle updates
* Shared memory
* Partial GPU texture updates
* High-resolution texture optimization
* Optional native C/C++ acceleration
* 8K+ texture testing
* Crash recovery

Native code will only be introduced where profiling demonstrates that Python is a real bottleneck.

---

# Planned Repository Structure

As the project grows, the repository will move toward:

```text
BlendGimp/
│
├── blender/
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
│   ├── plugin/
│   ├── painting/
│   ├── layers/
│   ├── procedures/
│   └── ipc/
│
├── native/
│   ├── shared_memory/
│   └── texture_update/
│
├── protocol/
├── tests/
│   ├── blender/
│   ├── gimp/
│   └── integration/
│
├── docs/
├── examples/
└── README.md
```

The native component will remain optional until performance measurements justify its introduction.

---

# Design Principles

## Do Not Rebuild Blender

BlendGimp should use Blender's existing strengths for:

* Geometry
* UVs
* Rendering
* Baking
* Materials
* Shader nodes
* Viewport interaction

## Do Not Rebuild GIMP

BlendGimp should use GIMP's existing strengths for:

* Painting
* Brushes
* Layers
* Masks
* Filters
* Selections
* Image processing
* Plug-ins

## Keep Blender and GIMP Loosely Coupled

Communication should occur through the defined BlendGimp protocol rather than tightly coupling Blender Python to GIMP internals.

This allows either side of the system to evolve independently.

## Avoid Disposable Architecture

Infrastructure should contribute directly toward the final application.

BlendGimp intentionally does not have a separate throwaway "bridge prototype" stage.

## Optimize From Measurements

Performance work will be driven by profiling.

Potential optimizations include:

```text
Dirty Rectangles
      ↓
Partial Pixel Transfer
      ↓
Shared Memory
      ↓
Native Acceleration
```

Native code will only be introduced where necessary.

---

# Testing Strategy

Standardized test projects will be maintained as development progresses.

### Simple Test

```text
Cube
Single material
Single UV map
1024 × 1024 texture
```

Used for basic painting, synchronization, layers, and filters.

### Seam Test

Used to test:

* Multiple UV islands
* Rotated islands
* Mirrored islands
* Narrow seams
* Stroke splitting

### Character Test

Used for:

* Complex UV layouts
* Multiple materials
* Multiple texture sets
* Production workflows

### UDIM Test

Example tile arrangement:

```text
1001 1002 1003 1004
1011 1012 1013 1014
```

### High Resolution Test

Performance testing will include:

```text
4096 × 4096
8192 × 8192
```

Measurements will include:

* Brush latency
* GIMP processing time
* Texture transfer time
* GPU update time
* RAM usage

---

# BlendGimp 1.0 Goal

BlendGimp 1.0 should allow an artist to:

1. Open Blender.
2. Select a model.
3. Enter the BlendGimp workspace.
4. Create or select a PBR texture set.
5. Bake required mesh maps.
6. Apply a smart material.
7. Paint directly on the model using GIMP brushes.
8. Edit the texture in 2D.
9. Use GIMP layers and masks.
10. Use GIMP filters and plug-ins.
11. Paint multiple PBR channels.
12. See changes immediately on the model.
13. Save the Blender project.
14. Return later without rebuilding the texturing setup.
15. Export finished textures when needed.

The artist should not need to manually export the model to another texturing application during this workflow.

---

# Contributing

BlendGimp is currently in active architectural development.

Bug reports, testing, research, and code contributions will become increasingly useful as the core texture synchronization and painting systems stabilize.

When contributing code:

* Keep Blender-specific functionality on the Blender side.
* Keep GIMP image-processing functionality on the GIMP side.
* Use the BlendGimp protocol for communication between them.
* Avoid adding temporary systems that conflict with the long-term architecture.
* Test existing functionality before submitting changes.
* Keep commits focused and descriptive.

---

# License

BlendGimp is licensed under:

**GNU General Public License v3.0 or later**

See the `LICENSE` file for details.

---

# Repository

**GitHub:**
https://github.com/ALS-Sanbox/BlendGimp

---

# Project

**BlendGimp**

GIMP-powered texture painting for Blender.

Developed by **Afro Lion Studios**.

---

> Blender provides the 3D ecosystem.
> GIMP provides the 2D art ecosystem.
> BlendGimp brings them together.
