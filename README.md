# BlendGimp

**BlendGimp** is a Blender extension that integrates **GIMP 3.2+** directly into Blender as a native-feeling texture painting and editing system.

The goal is to combine Blender's 3D viewport, UV tools, materials, and scene workflow with GIMP's mature 2D image editing and brush engine — without requiring artists to constantly switch between Blender and the normal GIMP interface.

BlendGimp is being developed toward a workflow similar to Substance Painter, while keeping GIMP as the authoritative image and layer engine.

---

## Current Status

BlendGimp is under active development.

Current development focus:

> **Phase 6 — BlendGimp Texture Editor**

The project currently supports communication between Blender and a persistent GIMP engine, layer management, image synchronization, direct 3D painting, dirty-region updates, modifier-aware projection, and a growing Blender-native texture editing interface.

---

## Project Goals

BlendGimp is designed around a simple division of responsibility:

### Blender owns

- 3D viewport
- Meshes
- UVs
- Materials
- Texture assignment
- 3D paint interaction
- Artist-facing interface
- Texture canvas display

### GIMP owns

- Image data
- Layers
- Groups
- Brush engine
- Paint operations
- Blend modes
- Layer locks
- Image editing operations
- Undo for texture edits

The two applications communicate continuously so texture changes can appear inside Blender with minimal delay.

---

# Features

## GIMP Engine Integration

BlendGimp can launch and communicate with GIMP through a persistent background engine.

Supported engine features include:

- GIMP 3.2+ detection
- Headless GIMP operation
- Persistent background process
- Automatic connection
- TCP communication over localhost
- HELLO / READY handshake
- Runtime version detection
- Automatic reconnect support
- Engine start
- Engine stop
- Engine restart
- Heartbeat / connection checks

GIMP can run without opening its normal graphical interface.

---

## Image Management

BlendGimp can communicate directly with open GIMP images.

Current functionality includes:

- Discover GIMP images
- Create images
- Read image size
- Select active images
- Pull image pixels into Blender
- Binary RGBA transport
- Dirty-region pixel updates
- Blender image creation
- Automatic material texture assignment

Large images can be updated using dirty rectangles instead of repeatedly transferring the entire image.

---

## Layer System

BlendGimp exposes GIMP's layer system inside Blender.

Supported operations include:

- View image layers
- Select active layer
- Add layer
- Delete layer
- Rename layer
- Duplicate layer
- Reorder layer
- Move layer
- Move layers into groups
- Move layers out of groups
- Create layer groups
- Merge layer down
- Toggle visibility
- Set opacity
- Set blend mode
- Lock layer position
- Lock alpha
- Lock content

GIMP remains the authoritative source for the actual layer stack.

---

# Direct 3D Painting

BlendGimp supports painting directly onto a model in Blender while the texture changes are written into GIMP.

The painting system includes:

- Direct Blender-to-GIMP strokes
- Dedicated GIMP paint layers
- One GIMP undo operation per Blender stroke
- Live viewport feedback
- Stroke chunk streaming
- UV projection
- Seam protection
- UV split detection
- Paint-through support
- Backface rejection
- Surface-angle rejection
- Geometry rejection
- Brush footprint protection

Painting updates can be streamed back into Blender while the mouse or stylus is still held down.

---

# Modifier-Aware Projection

BlendGimp supports painting against evaluated Blender geometry.

This allows painting to work with models using modifiers while preserving their original UV mapping where possible.

Features include:

- Evaluated modifier geometry
- Mesh-local BVH projection
- Original-mesh fallback
- Automatic UV compatibility checks
- Evaluated Mesh mode
- Original Mesh mode
- Topology-change detection
- Projection diagnostics
- Seam handling
- Paint-through support
- Live viewport updates

---

# Automatic Synchronization

BlendGimp can automatically detect image changes inside GIMP.

The synchronization system supports:

- Image revision tracking
- Dirty-region detection
- Binary dirty-pixel transport
- Automatic Blender texture refresh
- GIMP-to-Blender updates
- Blender-to-GIMP paint updates
- Auto Sync ownership
- Live paint feedback

This allows Blender's material preview and 3D viewport to stay synchronized with the GIMP image.

---

# Phase 6 — BlendGimp Texture Editor

Phase 6 is focused on building the full artist-facing texture editing environment inside Blender.

The long-term goal is to make it possible to create and edit textures without opening the normal GIMP interface.

## Texture Canvas

Planned and in-development functionality includes:

- Blender Image Editor based texture canvas
- Pan
- Zoom
- Fit image
- 100% view
- Checkerboard transparency
- Texture navigation

---

## UV Tools

The texture editor is being designed to provide:

- UV overlay
- UV overlay opacity
- UV island visibility
- Active-face highlighting
- Mesh-aware texture display

---

## GIMP Painting Tools

The texture editor is intended to expose GIMP tools directly inside Blender.

Target tools include:

- Paintbrush
- Pencil
- Eraser
- Airbrush
- Smudge
- Clone
- Heal
- Bucket Fill
- Gradient

---

## Brush Controls

BlendGimp is being developed around a shared 2D and 3D brush state.

Target brush controls include:

- Brush selection
- Brush size
- Opacity
- Hardness
- Spacing
- Angle
- Dynamics
- Foreground color
- Background color

Brush settings should remain synchronized between texture painting and direct 3D painting.

---

## Automatic Pointer Routing

BlendGimp is also being developed so artists do not need to repeatedly enter and exit special paint modes.

The intended workflow is:

- Hover over the 3D viewport → use object painting
- Hover over the texture editor → use texture painting
- Hover over Blender UI → normal Blender interaction

This is intended to make BlendGimp feel like part of Blender rather than a separate painting tool.

---

# Development Roadmap

## Phase 1 — GIMP Communication

Completed.

- GIMP detection
- TCP communication
- Persistent GIMP component
- HELLO / READY protocol
- Image discovery
- Layer discovery

---

## Phase 2 — Blender / GIMP Image Bridge

Completed.

- Image transfer
- Binary RGBA transport
- Blender image creation
- Material assignment
- Image refreshing
- Dirty-region updates

---

## Phase 3 — Layer and Painting Integration

Completed.

- Layer controls
- Layer CRUD
- Groups
- Blend modes
- Layer locks
- Direct Blender-to-GIMP painting
- Live viewport feedback
- Stroke undo grouping

---

## Phase 4 — Modifier-Aware Projection

Completed.

- Evaluated geometry projection
- Original mesh fallback
- UV compatibility detection
- Projection diagnostics
- Topology-change detection

---

## Phase 5 — GIMP Engine and Headless Workflow

Completed.

- Headless GIMP engine
- Persistent background operation
- Engine restart
- Reconnect handling
- Stability testing
- Extended soak testing

---

## Phase 6 — BlendGimp Texture Editor

**Current development phase.**

Primary goals:

- Texture canvas
- UV overlay
- Texture painting
- GIMP tool integration
- Shared 2D / 3D brush state
- Native layer interface
- Foreground / background colors
- Automatic pointer routing
- Pressure-sensitive stylus support
- Blender-native texture workflow

Tablet tilt is currently outside the active development target because the development tablet does not support tilt input.

---

## Future Development

Future phases are expected to focus on:

- Performance optimization
- Larger texture workflows
- Better GPU-side updates
- Multi-texture material workflows
- Improved model-to-texture ownership
- Multiple material channels
- Advanced brush management
- Texture set workflows
- Better save/export management
- Additional GIMP filters and operations
- Production stabilization

---

# Architecture

```text
┌────────────────────────────────────────────┐
│                  Blender                   │
│                                            │
│  3D Viewport                               │
│  Texture Editor                            │
│  UV System                                 │
│  Materials                                 │
│  BlendGimp UI                              │
│         │                                  │
│         │ BlendGimp Protocol               │
└─────────┼──────────────────────────────────┘
          │
          │ TCP / Binary Pixel Transport
          │
┌─────────▼──────────────────────────────────┐
│                   GIMP                     │
│                                            │
│  Headless Engine                           │
│  Layer Stack                               │
│  Brush Engine                              │
│  Image Operations                          │
│  Undo System                               │
│  Pixel Storage                             │
│                                            │
└────────────────────────────────────────────┘
```

---

# Requirements

Current development environment:

- **Blender 5.2**
- **GIMP 3.2+**
- Windows 10 / 11
- Python provided by Blender and GIMP

Current development testing is performed primarily with:

- Blender 5.2
- GIMP 3.2.4

---

# Repository Structure

```text
BlendGimp/
│
├── blender/
│   └── blendgimp/
│       ├── __init__.py
│       ├── blender_manifest.toml
│       ├── ui/
│       └── ...
│
├── gimp/
│   └── plugin/
│       └── blendgimp/
│           └── blendgimp.py
│
└── README.md
```

The Blender and GIMP components work together as one system.

---

# Development Philosophy

BlendGimp is not intended to simply open GIMP from Blender.

The objective is deeper integration.

An artist should ultimately be able to:

1. Select a model.
2. Create or select a texture.
3. Paint directly on the model.
4. Paint directly on the 2D texture.
5. Edit GIMP layers.
6. Change brushes and colors.
7. Use GIMP image-editing tools.
8. See updates immediately on the model.
9. Save or export the finished textures.

All without leaving Blender's normal working environment.

---

# Project Status

BlendGimp is currently an experimental development project and should not yet be considered production-ready.

Major systems are working, but interfaces, protocols, file layouts, and workflows may change as development continues.

Bug reports, testing, and development feedback are welcome.

---

# Repository

**GitHub**

https://github.com/ALS-Sanbox/BlendGimp

---

# License

A final project license has not yet been defined.

Before distributing BlendGimp publicly, an appropriate open-source license should be selected and added to the repository.

---

# BlendGimp

**Blender for 3D.  
GIMP for textures.  
One integrated workflow.**