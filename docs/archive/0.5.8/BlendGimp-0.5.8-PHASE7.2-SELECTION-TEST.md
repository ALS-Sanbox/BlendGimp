# BlendGimp 0.5.8 — Phase 7.2 Selection + Color Tag UI Runtime Test

## Test identity

```text
BlendGimp: 0.5.8
Build: 7.2-selection-foundation
GIMP component: 0.5.8
Blender: 5.2
GIMP: 3.2.4
```

## A. Color Tag UI test

1. Create a BlendGimp texture and add at least three layers.
2. Set the first layer **Red**.
   - Expected: console receives `LAYER_COLOR_TAG_SET` and the compact layer row visibly becomes `LayerName [Red]`.
3. Set second layer **Green**, third **Orange**.
4. Switch repeatedly between layers.
   - Expected: the tag badges remain attached to the correct layer rows.
5. Trigger any operation that refreshes `GET_IMAGE_LAYERS`.
   - Expected: badges survive the round-trip.
6. Test Blue, Yellow, Brown, Violet and Gray.
7. Set one tagged layer back to **None**.
   - Expected: its `[Color]` badge disappears.

This directly tests the issue observed in 0.5.7 where GIMP accepted `GREEN`, `RED`, and `ORANGE` tags but the Blender stack had no visual tag indicator.

## B. Rectangle selection

1. Switch BlendGimp Area to **Texture Paint**.
2. Open the new **GIMP Selection** box.
3. Press **Rectangle**.
4. Move onto the texture and click-drag a clearly visible region.
5. Expected console sequence includes:

```text
SELECT_RECTANGLE
SELECTION_CHANGED
BLENDGIMP: GIMP selection created shape=RECTANGLE ...
```

6. Expected UI:
   - white/dark selection boundary is visible on canvas
   - Selection panel reports `Active: Rectangle`
   - synchronized pixel bounds are shown

## C. Selection clips Texture Paint

1. Keep the Rectangle selection active.
2. Paint a stroke crossing both inside and outside the selection.
3. Release LMB.
4. Expected: authoritative GIMP result changes only the selected portion.
5. Use one slow 3–5 second stroke to keep the hybrid-progressive regression covered.

## D. Ellipse selection

1. Press **Ellipse**.
2. Click-drag a new region.
3. Expected: native GIMP ellipse replaces the previous selection and the overlay is elliptical.
4. Paint through it and verify clipping.

## E. Object Paint selection clipping

1. Keep a visible selection active.
2. Switch to **Object Paint**.
3. Paint across UV areas both inside and outside that selection.
4. Expected: Blender projection still sends normal GIMP stroke points, but GIMP clips resulting raster changes to its active selection.
5. No image/layer ownership mismatch warning should appear.

## F. Select All / Invert / Deselect

1. In Texture Paint, press **All**.
   - Expected: full-image boundary and active selection.
2. Create a Rectangle, then press **Invert**.
   - Expected: GIMP inverts the mask. The 0.5.8 foundation displays synchronized selection bounds for inverted/complex masks rather than claiming an exact marching-ant contour.
3. Press **Deselect**.
   - Expected: active selection becomes false and overlay disappears.

## G. Active Image isolation

1. Create a second BlendGimp texture.
2. Create a selection on image A.
3. Switch Active Image to image B.
4. Expected: A's overlay does not remain visible on B.
5. Press Selection **Refresh** on B.
6. Expected: B's authoritative GIMP selection state is loaded independently.

## Pass condition

0.5.8 passes when native GIMP selection commands work, the canvas interaction/overlay is usable, painting is selection-clipped, Active Image switching does not leak selection state, and Color Tags now have an obvious Blender-side visual change.
