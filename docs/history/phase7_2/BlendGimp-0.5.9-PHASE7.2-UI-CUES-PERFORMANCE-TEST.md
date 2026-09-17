# BlendGimp 0.5.9 — Phase 7.2 UI Cues + Performance Test

## 1. Version / startup

Confirm Blender and the GIMP component both report `0.5.9` and build `7.2-ui-cues-performance`. The console should report that Color Tag visual swatches loaded.

## 2. Color Tag visual test

1. Create three layers.
2. Set Blue, Green, and Yellow tags.
3. Confirm each tagged layer row shows an actual colored swatch and keeps the `[Blue]`, `[Green]`, or `[Yellow]` text suffix.
4. Select each layer and confirm the Color Tag property row shows the matching swatch.
5. Set one tag back to None and confirm the swatch/suffix disappear.

## 3. Selection button cue test

1. Enter Texture Paint.
2. Click Rectangle.
3. Before drawing, confirm Rectangle is visibly depressed and the panel says `Rectangle Select ACTIVE - drag on canvas`.
4. Press Esc and confirm the visual active cue clears.
5. Repeat for Ellipse.

## 4. Selection responsiveness test

1. Click Rectangle, then move the pointer around Blender UI regions before starting the drag. UI hover/navigation should remain responsive.
2. Drag a large selection slowly around the canvas for several seconds. The outline should track smoothly without the sidebar stuttering heavily.
3. Repeat with Ellipse.
4. Confirm the console prints `preview=local-60hz scene_rna=commit-only` when arming a selection tool.

## 5. Selection behavior regression

Verify Rectangle, Ellipse, All, Deselect, Invert, selection-clipped Texture Paint, selection-clipped Object Paint, and image-specific selection ownership still work.

## Pass condition

0.5.9 passes when color tags are visibly distinguishable in the Layers panel, Rectangle/Ellipse show a clear selected/armed state, selection mode no longer makes unrelated Blender UI interaction feel modal, live selection dragging is materially smoother, and the 0.5.8 GIMP selection behavior remains intact.
