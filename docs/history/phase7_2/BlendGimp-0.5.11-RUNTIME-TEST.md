# BlendGimp 0.5.11 Runtime Test

1. Create a GIMP group and put at least two raster layers in it.
2. Confirm the group row has the Eye followed immediately by a down-arrow.
3. Click the arrow. The children must disappear and the arrow must become right-facing.
4. Click it again. The children must return with the same GIMP hierarchy and order.
5. Repeat collapse/expand several times and after selecting a child layer.
6. Confirm no `AttributeError: Scene has no attribute blendgimp_collapsed_group_ids` occurs.
7. Quick regression: Ellipse -> Invert -> Invert still preserves the ellipse and dashed outline.
8. Quick regression: Color Tag swatches still render.
