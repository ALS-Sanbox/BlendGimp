# BlendGimp 0.5.18 Regression Checklist

- [ ] GIMP 3.2.4 handshake reports BlendGimp component 0.5.18.
- [ ] Non-circular GIMP brush mask appears in the Texture Editor cursor.
- [ ] Live temporary stroke uses the cached GIMP mask instead of generic circles.
- [ ] `[` / `]` resize the brush under the stationary mouse.
- [ ] Shift + bracket changes size by 10 px.
- [ ] Authoritative GIMP dirty pixels replace the temporary preview progressively.
- [ ] Fallback circle still paints if a brush mask preview is unavailable.
- [ ] Texture Paint and Object Paint retain frozen Phase 6 ownership/sync behavior.
- [ ] FG/BG swap, selections, layer masks, hierarchy, and Auto Sync still work.
