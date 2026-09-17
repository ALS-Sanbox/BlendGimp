# BlendGimp 0.5.17 Runtime Test — Live Texture Preview

## 1. Version and baseline

1. Install the 0.5.17 Blender extension and matching GIMP component.
2. Start GIMP from BlendGimp.
3. Confirm the handshake reports GIMP component `0.5.17`.
4. Create or open a synchronized 2048×2048 test texture.
5. Confirm the native Image Editor and 3D View workflow from 0.5.16 still auto-enrolls without a BlendGimp Area launcher.

## 2. Brush footprint cursor

1. Select Paintbrush.
2. Move the pointer over the Image Editor texture without pressing LMB.
3. Confirm a high-contrast circular brush footprint follows the pointer.
4. Change Size from roughly 20 px to 100 px without moving the pointer.
5. Confirm the footprint resizes immediately.
6. Zoom the Image Editor in and out and confirm the on-screen footprint scales with the texture zoom while representing the same image-pixel diameter.
7. Change Hardness between a soft value and 1.0. Confirm the inner hardness ring updates/disappears appropriately.
8. Move the pointer outside the image canvas; the footprint should not remain over empty editor space.

## 3. Immediate live stroke preview

1. Choose a clearly visible FG color and set Opacity around 50–75%.
2. Press and hold LMB and draw a slow line.
3. Confirm colored, brush-sized dabs appear immediately under the pointer before GIMP dirty pixels arrive.
4. Draw a fast curve. Confirm the temporary preview remains continuous rather than becoming a thin center line.
5. Hold a 3–5 second stroke. Confirm real GIMP progressive updates replace/trim the temporary preview while LMB remains down.
6. Release LMB. Confirm the final texture matches the GIMP-authored result and the temporary tail disappears.

Expected first-preview console cue:

```text
BLENDGIMP: 2D live brush preview first frame drawn points=... brush_px=...
```

A long stroke should still produce the existing progressive cue:

```text
BLENDGIMP: 2D progressive GIMP update applied ... preview_tail_points=...
```

## 4. Tool smoke test

Verify visible footprint/live feedback for:

- Paintbrush
- Pencil
- Eraser
- Airbrush
- Smudge
- Clone
- Heal

Verify Gradient still uses its drag-line preview and Fill still behaves as a point operation.

## 5. Regression smoke test

- FG/BG swap then paint
- Rectangle/Ellipse/Free/Fuzzy/By Color selection
- Shift Add / Ctrl Subtract / Shift+Ctrl Intersect
- one Object Paint stroke
- layer group collapse/expand
- layer mask paint

There should be no stale-layer, routing-owner, preview-handler, selection, or IPC traceback.
