# BlendGimp 0.5.10 Runtime Test

1. Create a group and move two or more raster layers into it. Confirm children are indented and the group row shows a disclosure arrow.
2. Collapse the group: children disappear from the Blender layer stack only. Expand it: children return and remain in the same GIMP hierarchy.
3. Draw a Rectangle selection. Confirm a dashed outline remains visible while painting.
4. Draw an Ellipse selection. Confirm the ellipse remains dashed and visible while painting.
5. Invert the ellipse. Confirm both the ellipse boundary and the full image boundary are dashed. Paint and verify GIMP clips to the inverse region.
6. Press Invert again. Confirm the original ellipse returns, not a rectangular bounds box. Repeat several times.
7. Repeat Rectangle -> Invert -> Invert.
8. Confirm Color Tag swatches from 0.5.9 remain visible.
