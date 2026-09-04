# BlendGimp

![Blender](https://img.shields.io/badge/Blender-Extension-orange?logo=blender\&logoColor=white)
![GIMP](https://img.shields.io/badge/GIMP-3.x-5C5543?logo=gimp\&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.x-blue?logo=python\&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows\&logoColor=white)
![Development](https://img.shields.io/badge/Status-Active%20Development-brightgreen)

**GIMP-powered texture painting inside Blender.**

BlendGimp is a Blender extension that connects Blender directly to GIMP, allowing GIMP to operate as a persistent background texture-processing engine while Blender remains the primary 3D workspace.

The goal is to combine Blender's 3D workflow with GIMP's mature image-editing ecosystem to create a powerful integrated texture-authoring environment.

> **Current milestone:** Phase 5.3 — Headless GIMP Engine Stabilization ✅ Complete

---

## What is BlendGimp?

Traditional Blender ↔ GIMP workflows often look like this:

```text
Export Texture
      ↓
Open GIMP
      ↓
Edit Texture
      ↓
Save
      ↓
Reload in Blender
      ↓
Repeat
```

BlendGimp is designed to replace that with:

```text
Paint in Blender
      ↓
BlendGimp sends the changes
      ↓
GIMP processes the texture
      ↓
Only changed pixels are returned
      ↓
Blender viewport updates automatically
```

GIMP can run entirely in the background, allowing the user to remain inside Blender.

---

# Key Features

### 🎨 Direct 3D Painting

Paint directly on the model inside Blender while BlendGimp sends projected strokes to GIMP.

Current support includes:

* Live paint streaming
* Stroke chunking
* Stroke interpolation
* Paint-through
* UV seam protection
* Brush footprint protection
* Undo grouping
* Live viewport feedback

---

### 🔄 Automatic Texture Synchronization

BlendGimp keeps Blender and GIMP texture data synchronized automatically.

Features include:

* GIMP → Blender synchronization
* Blender → GIMP synchronization
* Image revision tracking
* Dirty-region detection
* Partial texture updates
* Binary RGBA transfer
* Auto Sync ownership

Small texture edits do not require retransmitting the entire image.

---

### 🧩 Modifier-Aware Projection

BlendGimp can project paint against Blender's evaluated geometry.

Supported modes:

```text
Evaluated Mesh
Original Mesh
```

Features include:

* Modifier-aware geometry
* Mesh-local BVH projection
* Topology-change detection
* UV compatibility detection
* Automatic fallback to original geometry
* Projection diagnostics

This helps paint follow the geometry visible in the viewport even when modifiers are active.

---

### 🖌️ UV Seam Handling

BlendGimp includes dedicated seam protection designed to reduce artifacts when painting across UV islands.

Current systems include:

* UV seam detection
* Seam hysteresis
* Stroke interpolation
* Brush footprint protection
* Projection filtering
* Paint-through compatibility

---

### ⚡ Dirty-Region Synchronization

Instead of repeatedly transferring full textures, BlendGimp can identify which region of an image changed.

The current image-state system tracks information including:

```text
image revision
fingerprint changes
native changes
damage regions
```

Only the necessary pixel data can then be returned to Blender.

---

### 🖥️ Headless GIMP Engine

GIMP can operate as a persistent background process.

BlendGimp currently supports:

* Automatic GIMP detection
* Automatic engine startup
* Headless operation
* Persistent GIMP process
* TCP connection
* Protocol handshake
* Runtime version reporting
* Process monitoring
* Automatic restart
* Recovery after engine termination

The extended headless-engine soak test has been successfully completed.

---

# Architecture

```text
┌─────────────────────────────────┐
│             Blender             │
│                                 │
│       BlendGimp Extension       │
│                                 │
│   • 3D Paint Capture            │
│   • Projection                  │
│   • Texture Synchronization     │
│   • Viewport Feedback           │
│   • Process Management          │
│   • Blender UI                  │
└───────────────┬─────────────────┘
                │
                │ TCP
                ▼
┌─────────────────────────────────┐
│              GIMP               │
│                                 │
│       BlendGimp Component       │
│                                 │
│   • Image Processing            │
│   • Paint Operations            │
│   • Dirty Region Tracking       │
│   • Image State Detection       │
│   • GIMP / GEGL Integration     │
└─────────────────────────────────┘
```

---

# Current Build

## Working

| Feature                        | Status |
| ------------------------------ | :----: |
| Blender extension registration |    ✅   |
| GIMP executable detection      |    ✅   |
| Automatic GIMP startup         |    ✅   |
| Blender ↔ GIMP handshake       |    ✅   |
| Persistent TCP connection      |    ✅   |
| Headless GIMP engine           |    ✅   |
| Automatic engine restart       |    ✅   |
| Engine recovery                |    ✅   |
| Image synchronization          |    ✅   |
| Binary RGBA transfer           |    ✅   |
| Dirty-region updates           |    ✅   |
| Image revision tracking        |    ✅   |
| Direct 3D paint streaming      |    ✅   |
| Stroke chunking                |    ✅   |
| Stroke interpolation           |    ✅   |
| UV seam handling               |    ✅   |
| Seam hysteresis                |    ✅   |
| Paint-through                  |    ✅   |
| Modifier-aware projection      |    ✅   |
| Evaluated mesh projection      |    ✅   |
| Original mesh fallback         |    ✅   |
| Viewport feedback              |    ✅   |
| Undo grouping                  |    ✅   |
| Extended engine soak test      |    ✅   |

---

## In Development

| Feature                       | Status |
| ----------------------------- | :----: |
| Blender-native texture editor |   🚧   |
| Expanded GIMP tool access     |   🚧   |
| GIMP layer integration        |   🚧   |
| Advanced texture workflow     |   🚧   |
| Performance optimization      |   🚧   |

---

# Roadmap

## ✅ Foundation

Core Blender/GIMP communication.

* Extension registration
* GIMP process management
* TCP transport
* Protocol handshake
* Persistent connection
* Image transfer

**Status: Complete**

---

## ✅ Direct 3D Painting Foundation

Send Blender paint operations directly into GIMP.

* 3D surface projection
* Stroke streaming
* Interpolation
* Paint-through
* Seam protection
* Viewport feedback
* Undo grouping

**Status: Complete**

---

## ✅ Modifier-Aware Projection

Support painting on Blender's evaluated geometry.

* Evaluated mesh BVH
* Original mesh mode
* UV-safe fallback
* Topology diagnostics
* Projection diagnostics

**Status: Complete**

---

## ✅ Headless GIMP Engine Stabilization

Move GIMP from a visible external application toward a transparent texture-processing backend.

* Headless launch
* Persistent engine
* Process monitoring
* Automatic restart
* Connection recovery
* Session recovery
* Extended soak testing

**Status: Complete**

---

## 🚧 Blender-Native Texture Editor

Build the primary texture-editing environment inside Blender.

Planned functionality includes:

* Texture workspace
* Layer panel
* Brush controls
* Image tools
* Selection tools
* Mask controls
* GIMP-backed operations

**Status: In Development**

---

## 🔜 GIMP Ecosystem Integration

Expose more of GIMP without requiring users to leave Blender.

Planned integration includes:

* GIMP brushes
* Layers
* Masks
* Filters
* GEGL operations
* Selections
* Transform tools
* GIMP plug-ins

---

## 🔜 Advanced Texture Authoring

Expand BlendGimp into a more complete material and texture-authoring environment.

Planned areas include:

* Multiple texture channels
* Material texture sets
* Base Color
* Roughness
* Metallic
* Normal maps
* Emission
* Masks
* Channel visualization
* Faster large-texture workflows

---

## 🔜 Performance & Native Optimization

Continue reducing the difference between BlendGimp and a native Blender painting system.

Focus areas include:

* Large texture performance
* Synchronization latency
* Memory usage
* Dirty-region optimization
* Stroke throughput
* Background processing
* Recovery reliability
* Long-running stability

---

# Long-Term Goal

BlendGimp is intended to become more than a synchronization plug-in.

The target experience is:

```text
Launch Blender
      ↓
Open BlendGimp
      ↓
Select a material or texture
      ↓
Paint directly on the 3D model
      ↓
Manage layers and tools inside Blender
      ↓
GIMP performs processing in the background
      ↓
Results appear immediately in Blender
```

The user should not need to constantly switch between Blender and GIMP.

---

# Why GIMP?

GIMP already contains a mature image-processing ecosystem.

### Blender

Provides:

* 3D modeling
* UV mapping
* Materials
* Modifiers
* Geometry
* Rendering
* 3D viewport

### GIMP

Provides:

* Layers
* Masks
* Brushes
* Filters
* Selections
* GEGL
* Image manipulation
* Plug-ins

### BlendGimp

Connects the two.

---

# Substance Painter Alternative

One of the long-term goals of BlendGimp is to provide an open and extensible texture-authoring workflow capable of covering many tasks normally performed using dedicated applications such as Substance Painter.

BlendGimp is not intended to clone Substance Painter.

Instead, it combines:

**Blender's 3D environment**

with

**GIMP's image-processing ecosystem**

to create a different open texture-authoring workflow.

---

# Requirements

BlendGimp is currently under active development.

Current development environment:

```text
Blender
GIMP 3.x
Python 3.x
Windows 10 / Windows 11
```

Development testing currently includes GIMP **3.2.x**.

Cross-platform support is planned as the project matures.

---

# Installation

> ⚠️ BlendGimp is currently a development project. Installation procedures may change between builds.

Clone the repository:

```bash
git clone https://github.com/ALS-Sanbox/BlendGimp.git
```

Enter the repository:

```bash
cd BlendGimp
```

Development builds can then be packaged and installed as a Blender extension.

The matching BlendGimp GIMP-side component must also be available to the GIMP runtime.

More complete installation instructions will be added as the project approaches its first public release.

---

# Development & Testing

BlendGimp currently uses a combination of automated and manual validation.

Testing includes:

* Python compile checks
* AST/static contract validation
* Projection tests
* Projection fallback tests
* Package integrity tests
* Engine startup testing
* Engine shutdown testing
* Engine restart testing
* Connection recovery
* Texture synchronization
* Dirty-region synchronization
* Direct paint streaming
* Modifier-aware projection
* Extended headless-engine soak testing

---

# Project Status

```text
BlendGimp
│
├── Communication Foundation        ✅
├── Texture Synchronization         ✅
├── Direct 3D Painting              ✅
├── Seam Protection                 ✅
├── Modifier-Aware Projection       ✅
├── Headless GIMP Engine            ✅
├── Engine Stabilization            ✅
├── Blender Texture Editor          🚧
├── GIMP Tool Integration           🔜
├── Advanced Texture Authoring      🔜
└── Native Performance Optimization 🔜
```

---

# Repository

**GitHub**

https://github.com/ALS-Sanbox/BlendGimp

---

# Contributing

BlendGimp is under active development.

Bug reports, testing feedback, feature suggestions, and code contributions are welcome.

When submitting a bug report, please include:

* Blender version
* GIMP version
* Operating system
* BlendGimp commit/build
* Steps to reproduce
* Blender console output
* BlendGimp/GIMP logs when available

---

# License

No open-source license has currently been assigned to BlendGimp.

Unless a license is added to this repository, the source code should be considered **All Rights Reserved**.

---

# Afro Lion Studios

BlendGimp is developed as part of the tools and game-development work of **Afro Lion Studios**.

---

## Development Milestone

### Phase 5.3 — Headless GIMP Engine Stabilization

**✅ COMPLETE**

The persistent headless GIMP engine has completed its extended soak testing and the project is ready to move forward into the next development milestone.
