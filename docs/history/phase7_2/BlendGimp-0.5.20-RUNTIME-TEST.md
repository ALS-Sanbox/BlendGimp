# BlendGimp 0.5.20 Runtime Test — Real Brush GPU Preview

1. Install both matching 0.5.20 packages and start GIMP from BlendGimp.
2. Confirm the handshake reports `GIMP BlendGimp component = 0.5.20`.
3. Create/open a texture and switch to Texture Paint.
4. Select `2. Star` or another clearly non-circular GIMP brush.
5. Confirm the console reports `Received BRUSH_PREVIEW` and `Real GIMP brush preview cached ...`.
6. Confirm the console reports `GPU GIMP brush preview texture ready ... format=RGBA16F` and does **not** report `Only Buffer of format FLOAT is currently supported`.
7. Hover the texture. The real brush silhouette should appear inside the outer size ring.
8. Press `[` and `]` without moving the mouse. The silhouette/ring should resize immediately.
9. Draw a short stroke. The temporary GPU stroke should use the same real brush silhouette; final pixels still come from GIMP.
10. Switch back to a round hardness brush and repeat once to verify cached mask switching.
