# BlendGimp 0.5.15 — Focused Runtime Test

Use Blender 5.2 with the matching GIMP 3.2.4 BlendGimp component 0.5.15.

## 1. Version handshake

Confirm Blender reports 0.5.15 / `7.2-by-color-reliability` and the GIMP handshake reports component 0.5.15.

## 2. GIMP-style combine hotkeys

Create one visible ellipse selection. Then test all temporary modes without clicking a Combine Mode button:

1. Arm Rectangle, hold **Shift before the first canvas click**, drag a second region, release. Existing selection must remain visible during the drag and the result must be Add.
2. Arm Rectangle, hold **Ctrl before the first canvas click**, drag over part of the selection. Existing selection must remain visible and the result must be Subtract.
3. Arm Rectangle, hold **Shift+Ctrl before the first canvas click**, drag across the selection. Existing selection must remain visible and the result must be Intersect.
4. Repeat one Add/Subtract test with Free Select; the old outline must remain visible while polygon points are placed.

Expected console examples:

```text
BLENDGIMP: Selection tool armed shape=RECTANGLE operation=GIMP-HOTKEY
BLENDGIMP: GIMP selection created shape=RECTANGLE operation=ADD ...
BLENDGIMP: Free Select combine hotkey operation=SUBTRACT
```

## 3. Fuzzy and Select by Color

Use an image with more than one distinct painted color.

- Fuzzy Select an irregular contiguous area and confirm a dotted contour follows the selected area.
- Select by Color a color that appears in multiple disconnected places and confirm the command completes and dotted contours appear on the matching islands.
- Repeat By Color with Shift to Add to an existing selection.

The command must not fail just because contour extraction is expensive or unavailable; GIMP selection creation is authoritative.

## 4. Regression

Confirm Rectangle, Ellipse, Invert, Free Select double-click, group collapse, Color Tag swatches, Texture Paint, and Object Paint still work.


## 0.5.15 Select by Color reliability focus

1. Create obvious red, green, and blue painted regions plus at least one transparent region.
2. Arm **Select by Color** and click each solid color several times at edge and center pixels.
3. Confirm the dotted contour appears every time and the console reports `active=True`, non-zero bounds, and `sample=gimp-pick-color`.
4. Click a transparent pixel with Sample Transparent enabled. Confirm either a valid transparent-color selection or a clear no-match status; the tool must remain armed instead of returning to paint.
5. Repeat with Sample Merged enabled.
6. Test Shift/Add, Ctrl/Subtract, and Shift+Ctrl/Intersect with Select by Color.
7. Confirm no `ERROR`, no wrong-layer ownership warning, and normal painting resumes after the selection operation.
