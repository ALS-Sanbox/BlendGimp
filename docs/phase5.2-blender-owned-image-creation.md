# Phase 5.2 — Blender-Owned Image Creation

## Implementation status

The Phase 5.2 source implements the replacement for the obsolete
`Demo.xcf`/`BlenderTest.xcf` workflow. A normal headless session no longer
requires a document to be created or opened manually in GIMP.

## New texture workflow

While connected, the Blender panel exposes **New BlendGimp Texture** with:

- Texture name
- Width and height (default 2048 × 2048)
- RGBA or RGB format
- Transparent or solid background
- Solid background color
- Initial layer name

Pressing **Create** sends `CREATE_IMAGE`. GIMP creates an RGB-base image and a
compatible RGB/RGBA initial layer, initializes its pixels, inserts and selects
the layer, and returns `IMAGE_CREATED` with the runtime image ID, layer ID,
dimensions, format, requested name, and synchronization token.

Blender then:

1. Refreshes the normal GIMP image list.
2. Transfers the new composite through the existing direct RGBA path.
3. Creates and names the paired Blender Image datablock.
4. Records the GIMP image/layer IDs and session synchronization token.
5. Assigns the image to the active material when an eligible object exists.
6. Starts Auto Sync at the current GIMP image revision.

All existing painting, layer, dirty-region, binary RGBA, projection, undo, and
recovery protocols remain unchanged.

## Runtime validation

Validate on Windows with GIMP 3.2.4:

1. Start the default Headless engine and wait for **Connected**.
2. Create a 2048 × 2048 RGBA texture with a transparent background.
3. Confirm Blender reports both returned IDs, creates the Blender image, and
   shows **Auto Sync: ON** for the new GIMP image.
4. Paint with Direct GIMP Brush 3D Paint and confirm dirty pixels update the
   Blender viewport.
5. Repeat with an RGBA solid background and verify the chosen color.
6. Repeat with RGB and a solid background. RGB plus Transparent must remain
   unavailable because an RGB layer has no alpha channel.
7. Restart the engine and confirm the old runtime IDs are released rather than
   polled. Create a replacement texture to establish the new session pair.

Phase 5.3 begins after this runtime sequence passes.

## Headless engine restart pairing fix

GIMP runtime image IDs are process-local and may be reused after the headless
engine restarts. BlendGimp therefore treats a non-empty `sync_token` returned
by GIMP as authoritative when pairing a GIMP image to a persistent Blender
Image datablock. Runtime image-ID fallback is used only for legacy responses
that do not contain a token.

This prevents a newly created image (for example 2048x2048 RGBA) from being
bound to a stale Blender Image left from an earlier GIMP process (for example
1024x1024 RGBA), which would otherwise make `Image.pixels.foreach_set()` reject
the incoming component count.
