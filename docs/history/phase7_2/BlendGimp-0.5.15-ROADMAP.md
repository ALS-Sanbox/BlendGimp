# BlendGimp Development Roadmap — 0.5.15

## Current milestone

Phase 7.2 — GIMP Selections.

0.5.15 is a runtime-feedback hotfix over 0.5.13. It removes the blank combine-mode button strip, moves temporary selection composition to GIMP-style modifier keys, keeps the committed selection visible during composition, and makes Select by Color contour generation non-blocking/best-effort.

## Runtime acceptance gate

Before adding more selection features, verify:

- Shift/Ctrl/Shift+Ctrl temporary combine modes
- visible existing-selection composition preview
- Select by Color reliability with disconnected areas
- Fuzzy/By Color exact-ish dotted mask contours
- Free Select double-click regression

After this gate, continue the remaining Phase 7.2 selection polish rather than changing the frozen Phase 6 painting architecture.
