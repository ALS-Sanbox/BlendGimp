# BlendGimp 0.5.16 Runtime Test

## 1. Native editor workflow

1. Install the 0.5.16 Blender extension and matching GIMP component.
2. Start GIMP from BlendGimp. Confirm the handshake reports component `0.5.16`.
3. Open an Image Editor and a 3D View.
4. Confirm there is **no BlendGimp Area launcher**, no Texture/Object mode-switch row, and no Return Area button.
5. Confirm the normal BlendGimp panels appear after the automatic enrollment tick.
6. Move the pointer over the Image Editor canvas and confirm Texture Paint auto-routing still arms.
7. Move to the 3D View and confirm Object Paint auto-routing still works in the accepted workflow.

Expected console cue on a fresh screen:

```text
BLENDGIMP: Native editor auto-enrolled mode=TEXTURE area=IMAGE_EDITOR
BLENDGIMP: Native editor auto-enrolled mode=OBJECT area=VIEW_3D
```

## 2. FG / BG swap

1. Choose clearly different foreground and background colors.
2. Press the new swap button between **FG** and **BG**.
3. Verify the two visible color controls exchange values immediately.
4. Paint once with Paintbrush and verify the former BG color is now used as FG.
5. Swap again and verify the original FG is restored.
6. Test Fill using Foreground and Background.
7. Test an FG/BG Gradient and confirm the endpoints reverse after swapping.

Expected console cue:

```text
BLENDGIMP: Foreground / Background colors swapped
```

## 3. Regression smoke test

- Rectangle/Ellipse/Free/Fuzzy/By Color selection
- Shift Add / Ctrl Subtract / Shift+Ctrl Intersect
- one long Texture Paint stroke with progressive update
- one Object Paint stroke
- layer group collapse/expand
- color tag swatch

No stale-layer, routing-owner, selection, or IPC errors should appear.
