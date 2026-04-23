# Brand submission (deferred)

This integration does NOT currently ship brand assets. The
`tapelectric` domain will show as a generic icon in the HA UI until:

1. We receive explicit written permission from Tap Electric B.V. to
   use their logo, wordmark, and color palette; OR
2. We commission original community artwork (generic EV plug
   iconography) that makes no visual reference to Tap.

When either condition is met, submit a PR to
[`github.com/home-assistant/brands`](https://github.com/home-assistant/brands)
with:

- `icon.png` (256 × 256, transparent)
- `logo.png`
- `dark_icon.png`
- `dark_logo.png`

Until then: a generic icon is acceptable and legally safe.

## Why this matters

The Home Assistant brands repository is public and indexed. Uploading
a third-party logo without explicit permission creates a trademark
infringement paper trail that makes this integration harder, not
easier, to ever get officially blessed. Err on the side of waiting.

If/when Bart receives written permission from Tap Electric B.V., drop
the four PNGs into this directory and submit the brands PR. Nothing
else in this repository needs to change — the `domain: "tapelectric"`
and display name `"Tap Electric Charger (Community)"` remain stable.
