# BlendGimp 0.5.19 Runtime Test — Brush Preview Babl Hotfix

1. Confirm Blender and the GIMP component both report 0.5.19.
2. Load GIMP brushes and select a non-circular brush such as `2. Star`.
3. Confirm `GET_BRUSH_PREVIEW` returns `BRUSH_PREVIEW`, not `ERROR`.
4. Confirm Blender logs `Real GIMP brush preview cached` with mask dimensions.
5. Hover over the Texture Editor and confirm the real brush silhouette appears inside the size ring.
6. Press `[` and `]`; confirm the silhouette resizes under the stationary cursor.
7. Paint a short stroke and confirm the first-frame log contains `mask=<width>x<height>`, not `mask=fallback-circle`.
8. Regression-check normal Texture Paint and Object Paint.
