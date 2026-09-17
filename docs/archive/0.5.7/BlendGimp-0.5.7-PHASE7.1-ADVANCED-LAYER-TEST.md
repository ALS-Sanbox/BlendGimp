# BlendGimp 0.5.7 — Phase 7.1 Advanced Layer Runtime Test

```text
BlendGimp: 0.5.7
Build: 7.1-advanced-layer-ops
GIMP component: 0.5.7
Target runtime: Blender 5.2 + GIMP 3.2.4
```

## Primary test

1. Create a 2048×2048 BlendGimp image.
2. Add three layers: `Top`, `Middle`, `Hidden`.
3. Hide `Hidden`.
4. Assign Red to `Top`, Blue to `Middle`, and verify each color tag persists after layer refresh.
5. Toggle Pixels, Position, Visibility, and Alpha locks and confirm each state persists.
6. Create a group, move `Top` and `Middle` into it, duplicate the group, and confirm the duplicate remains a group with child layers.
7. Undo/rebuild if necessary, then run Merge Visible with multiple visible layers and verify hidden content is not merged.
8. Paint the merged layer in Texture Paint and Object Paint.
9. Rebuild a stack with one hidden layer and run Flatten Image. Confirm only one flattened layer remains and the hidden layer was discarded.
10. Paint the flattened result.
11. Add a layer mask to the result and verify Layer/Mask target switching still works.
12. While Mask is active, paint one Texture Paint stroke and one Object Paint stroke.
13. Switch Active Images if a second texture exists and verify correct image/layer rebinding.

## Pass indicators

Expected console operations include:

```text
SET_LAYER_COLOR_TAG
SET_LAYER_LOCK ... VISIBILITY=
DUPLICATE_GROUP
MERGE_VISIBLE_LAYERS
FLATTEN_IMAGE
Active Layer Buffer loaded ... target=LAYER
```

There should be no wrong-image/wrong-layer ownership warnings or unhandled GIMP protocol errors.
