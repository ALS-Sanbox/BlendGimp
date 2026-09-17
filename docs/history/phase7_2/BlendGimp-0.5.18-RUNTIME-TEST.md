# BlendGimp 0.5.18 Runtime Test — Real Brush Preview + Hotkeys

## 1. Version handshake

1. Install the 0.5.18 Blender extension and matching 0.5.18 GIMP component.
2. Start GIMP from BlendGimp.
3. Confirm the Blender console reports `GIMP BlendGimp component = 0.5.18`.
4. Create/open a synchronized texture and hover the Image Editor.

Expected when the active brush is first read:

```text
BLENDGIMP: Real GIMP brush preview cached brush=... mask=...x... native=...x...
```

The GIMP console should also show a successful `GET_BRUSH_PREVIEW` request.

## 2. Real brush silhouette

1. Choose a clearly non-circular GIMP brush such as a star, splatter, or textured brush.
2. Hover without pressing LMB.
3. Confirm the real brush silhouette is visible inside the high-contrast size ring.
4. Switch between at least three visually different brushes. Each should update to its own mask.
5. Paint a slow stroke. Confirm the temporary live preview uses repeated copies of that brush shape rather than solid circular dabs.
6. Hold a 3–5 second stroke. Confirm progressive GIMP pixels replace the temporary preview without a jump to a different stroke path.

Expected first-frame cue:

```text
BLENDGIMP: 2D live brush preview first frame drawn points=... brush_px=... mask=...x...
```

If GIMP cannot expose a brush mask, `mask=fallback-circle` is allowed only for that brush and painting must still work.

## 3. Brush-size hotkeys

1. Leave the mouse stationary over the texture.
2. Press `]` several times. The ring and brush silhouette should grow immediately without moving the mouse.
3. Press `[` several times. They should shrink immediately.
4. Press `Shift + ]` once and confirm roughly +10 px.
5. Press `Shift + [` once and confirm roughly -10 px.
6. Paint and confirm the authoritative GIMP result uses the new size.

Expected console cue:

```text
BLENDGIMP: Texture Paint brush size hotkey 48.0->49.0px key=RIGHT_BRACKET step=1
```

## 4. Brush/tool smoke test

Verify the visible real-mask footprint and live preview with Paintbrush, Pencil, Eraser, Airbrush, Smudge, Clone, and Heal. Gradient should retain its drag-line preview and Fill should remain a point operation.

## 5. Regression smoke test

- FG/BG swap then paint.
- Rectangle/Ellipse/Free/Fuzzy/By Color selection.
- Shift Add / Ctrl Subtract / Shift+Ctrl Intersect.
- one Object Paint stroke.
- layer group collapse/expand.
- layer mask paint.

There should be no routing-owner, preview-handler, GPU texture, brush-preview IPC, stale-layer, or selection traceback.
