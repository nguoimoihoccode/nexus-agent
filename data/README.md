# Nexus Runtime Data

This directory is the local MVP runtime workspace for quant data.

- `staging/`: normalized OpenBB/OHLCV staging datasets.
- `qlib/`: Qlib provider datasets.
- `artifacts/`: experiment metadata, reports, models, and generated artifacts.

The directory structure is tracked for convenience, but generated data inside
these folders is ignored by Git. Delete the generated contents to reset local
quant state. Existing Docker named volumes are not migrated automatically; copy
anything you still need into this workspace before switching to bind mounts.
