# Editable system diagrams

Open either `.drawio` file in diagrams.net or the draw.io desktop application. Shapes, text, connectors, and container boundaries are individual editable objects; no diagram is a flattened image.

- `outbound-control.drawio` recreates the previous privacy/budget flow and makes this release's review of every export explicit.
- `system-architecture.drawio` shows terminal/gateway separation, Docker-managed state, native Apple GPU inference, and the two permitted host folders.

The colors and rounded rectangles/diamonds use a consistent process/decision style. Regenerate the XML with `python3 scripts/build_diagrams.py`; manual diagram changes will be overwritten if the generator is rerun. Optional `--preview-dir PATH` creates PNGs for review when Pillow and DejaVu Sans are installed.
