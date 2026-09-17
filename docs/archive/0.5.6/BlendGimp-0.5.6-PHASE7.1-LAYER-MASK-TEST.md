# BlendGimp 0.5.6 — Phase 7.1 Layer Mask Runtime Test

## Install identity

```text
BlendGimp: 0.5.6
Build: 7.1-layer-mask-foundation
GIMP component: 0.5.6
GIMP runtime: 3.2.4
```

## Primary mask test

- [ ] Create a BlendGimp texture.
- [ ] Add/select a raster layer.
- [ ] Add a White layer mask.
- [ ] Layer snapshot shows a mask and the panel displays Layer/Mask controls.
- [ ] New mask opens in Mask edit mode.
- [ ] Texture Paint modifies the mask, not the layer RGB pixels.
- [ ] Object Paint modifies the same mask.
- [ ] Switch to Layer editing; subsequent paint modifies layer pixels.
- [ ] Switch back to Mask; ownership/buffer rebinds without warnings.
- [ ] Disable mask; layer appears as if the mask is not applied.
- [ ] Re-enable mask; masked result returns.
- [ ] Apply Mask permanently on a disposable layer.
- [ ] Delete Mask discards it on another disposable layer.

## Mask initialization types

Test as practical:

- [ ] White
- [ ] Black
- [ ] Layer Alpha
- [ ] Transfer Alpha
- [ ] Selection
- [ ] Grayscale Copy

## Regression gate

- [ ] Active Image switching remains clean.
- [ ] Add/Delete/Duplicate/Rename/Reorder layers still work.
- [ ] Visibility / opacity / blend modes / locks still work.
- [ ] Groups and Merge Down still work.
- [ ] Paintbrush/Pencil/Eraser/Airbrush still work.
- [ ] Fill/Gradient/Smudge/Clone/Heal still work on normal layers.
- [ ] Object Paint dirty-region updates remain live.
- [ ] Texture Paint remains asynchronous.
- [ ] Slow 3–5 second Texture Paint stroke produces progressive authoritative updates.
- [ ] No `Layer ID ... does not belong to image ID ...` warning.
- [ ] No layer/mask target mismatch while streaming.
