# Provider artwork

The eleven provider logos are vendored from `@lobehub/icons-static-svg` 1.95.0,
published by [LobeHub](https://github.com/lobehub/lobe-icons) under the included
MIT license. Brand names and marks belong to their respective owners; they
identify supported integrations, without implying endorsement.

`manifest.json` records the archive's SHA-512 integrity and each original and
bundled asset's SHA-256. To reproduce the assets, run
`python scripts/vendor-provider-icons.py` from the repository root.

The importer reads only explicit archive members, rejects executable elements,
event handlers, styles, entities, and external references, and removes the
upstream root layout style. `currentColor` becomes a fixed dark ink for the
light logo tiles. No package scripts run. The client selects from a fixed map
and loads these files through same-origin `<img>` elements, never injected SVG
markup or remote logo URLs. No CDN requests, API keys, or image proxy are used.

`custom.svg` is the project's own generic connection symbol.
