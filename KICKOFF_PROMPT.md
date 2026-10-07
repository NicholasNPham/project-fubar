Read CLAUDE.md and stac_navigation.md in full before doing anything. Then read PROJECT-DALYN's src/stac.py, src/exceptions.py, src/logger.py and main.py from https://github.com/NicholasNPham/PROJECT-DALYN. Its StacSession is the base for this project's STAC code.

This is a new, empty project. Do not write code yet. Give me:

1. A short summary of what you're reusing from DALYN, what you're dropping (upload, matrix dialog, save, name checks), and what's new (Case Number search, Images tab filter, Details tab read).
2. The module plan following the structure in CLAUDE.md, with the main functions in each module and what they raise.
3. The exact per-image flow you'll implement, step by step, with the wait condition for each step.
4. Every open TODO in stac_navigation.md that blocks implementation, and exactly what HTML or manual test you need from me for each. Two I already know are open:
   - After filtering the Images tab to one tile, does the tile need a click before Details shows it?
   - Does the Details tab stay open between Image Id searches, or flip back?
   If a TODO doesn't block the first version, say so and design around it (e.g. always click the tile, always re-select Details).
5. A probe mode: a `--probe` flag that signs in, searches one case number, opens the Images tab, filters one Image Id, opens Details, and dumps the Details fields (labels and values, no defendant names) plus whether the tile needed a click. I'll run it on my machine against the two known test images (15031454 corrupted, 10680257 good) so we can confirm the selectors before the full build.

Build order once I approve: excel_io + checker + their tests first (no browser needed), then stac.py probe mode, then the full run. Small steps, and wait for my go-ahead between them.
