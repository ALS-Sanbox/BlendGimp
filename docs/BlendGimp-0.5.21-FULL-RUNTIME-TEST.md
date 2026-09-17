# BlendGimp 0.5.21 — Full Runtime Acceptance Test

Run this once after installing **both matching 0.5.21 packages**. This replaces intermediate checkpoint testing for the cleanup pass.

## 1. Startup and version match

- Start Blender 5.2 with BlendGimp 0.5.21 installed.
- Start GIMP from BlendGimp in Headless mode.
- Confirm the console reports Blender 0.5.21 and GIMP component 0.5.21.
- Confirm the HELLO / READY handshake succeeds and protocol = 1.
- Confirm there is no visible **BlendGimp Area** launcher, mode-switch control, Return Area control, or F3 legacy area operator.
- Confirm a normal Image Editor and 3D View are automatically enrolled when used.

## 2. Image/material ownership

- Create a 2048×2048 `BaseColor` image from BlendGimp.
- Confirm it is created in GIMP, mirrored into Blender, assigned to the active material, and Auto Sync starts.
- Create a second BlendGimp image and switch Active Image between the two.
- Confirm material/image ownership follows the selected BlendGimp image without binding the wrong model/image.
- Save an XCF and reopen an XCF from Image → BlendGimp XCF.

## 3. Layer stack and hierarchy

- Add, rename, duplicate, delete, and reorder a raster layer.
- Create a layer group, move layers into/out of it, collapse/expand it, and duplicate the group.
- Toggle visibility and opacity.
- Test content, position, and alpha locks.
- Change blend mode.
- Apply at least two color tags and confirm the visual swatches display.
- Run Merge Visible on a disposable test stack.
- Run Flatten on a disposable test image.

## 4. Layer masks

- Add a White mask and paint on the mask.
- Switch editing target between Layer and Mask and confirm the correct drawable receives paint.
- Toggle mask enable/show behavior.
- Delete a mask without applying it.
- Add another mask and Apply it.
- Confirm the active-layer buffer reloads correctly after each target change.

## 5. Texture Paint tools and live preview

Use Texture Paint in the Image Editor.

- Paintbrush: confirm the real GIMP brush silhouette appears under the cursor and immediate preview follows the stroke.
- Choose `2. Star`, Confetti, Sponge, or another clearly non-circular brush and confirm the cursor/stroke preview uses the actual mask shape.
- Press `[` and `]` while the mouse stays over the texture; confirm the preview resizes immediately.
- Press Shift+`[` and Shift+`]`; confirm 10 px steps.
- Test Pencil, Eraser, Airbrush, Smudge, Clone, and Heal.
- For Clone/Heal, verify source capture and subsequent painting.
- Test Fill and Gradient.
- Change FG and BG to different colors, press the swap button repeatedly, and confirm Paintbrush/Fill/Gradient use the swapped state.
- Draw one deliberate 3–5 second Paintbrush stroke and confirm at least one progressive GIMP dirty update arrives before stroke completion.

## 6. Object Paint

- Paint on the 3D object with the same active GIMP brush.
- Test a small and large brush.
- Paint near UV seams and geometry boundaries.
- Confirm seam/footprint protection prevents unwanted jumps or bleed.
- Test evaluated modifier projection on an object with a compatible modifier.
- Switch between Texture Paint and Object Paint by pointer/editor use without manually entering a BlendGimp-specific area mode.
- Confirm both modes continue sharing GIMP brush/color state.

## 7. Selections

- Rectangle and Ellipse Replace.
- Shift = Add.
- Ctrl = Subtract.
- Shift+Ctrl = Intersect.
- While composing a selection, confirm the existing committed selection remains visible.
- Free Select: close with double-click and with Enter.
- Fuzzy Select on several regions.
- Select by Color repeatedly on solid areas and anti-aliased edges.
- Confirm irregular/disconnected selections show actual contours rather than only a bounding rectangle.
- Test All, None, Invert, Grow, Shrink, Feather, and Border.
- Paint/fill with an active selection and verify GIMP enforces the selected region.

## 8. Auto Sync / ownership regression

- Paint in Texture Paint, then Object Paint, then modify a layer in the Layers panel.
- Switch Active Image during idle state.
- Confirm no stale owner remains and no old image/layer receives new paint.
- Toggle Auto Sync off/on and confirm recovery.
- Save the `.blend`, close Blender cleanly, reopen it, reconnect GIMP, and continue painting.

## 9. Stability pass

- Perform at least 10 minutes of mixed operations: brush changes, size changes, selections, layer changes, Texture/Object Paint switching, and image switching.
- Confirm there is no traceback, dead modal owner, UI freeze, wrong-image paint, or GIMP component version mismatch.

## Acceptance result

Mark **0.5.21 PASS** only if all sections above complete without a functional regression. If something fails, capture the Blender console log from startup through the failure and note the exact test section/step.
