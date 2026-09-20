"""Where measured models live, and why there is only one answer to that.

Three things are measured once and read by several services: the per-symbol
cost model, the controlled edge study's cache, and the pre-registration
registry that fixes a strategy's sample-size target before the data can argue
about it. All three are worthless if each container keeps its own copy.

On 2026-09-20 two of them did exactly that. `symbol_costs` resolved to
`~/.claude/matrix_models`, which rides the `matrix_claude_config` volume and is
genuinely shared; `promotion` and `edge_study` were written later against
`/var/lib/matrix/models`, which is not a volume at all. The consequences were
not subtle: `reflection` could not see the edge cache, so after every restart
it read "no measurement" as "no edge" and demoted the one strategy with a
confirmed edge; and the pre-registration registry, whose entire guarantee is
that a target is written once and never moves, was being written once *per
container*.

So the path is resolved here and nowhere else.
"""

from __future__ import annotations

import os
from pathlib import Path


def model_dir() -> Path:
    """Directory for measured models, shared across services.

    `MATRIX_MODEL_DIR` overrides. Otherwise `$CLAUDE_CONFIG_DIR/matrix_models`
    (or `~/.claude/matrix_models`), which is the `matrix_claude_config` volume
    mounted at `/root/.claude` in every service that needs it.
    """
    override = os.environ.get("MATRIX_MODEL_DIR")
    if override:
        return Path(override)
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return Path(base) / "matrix_models"


def model_path(name: str) -> Path:
    """Full path for one model file, e.g. `model_path("edge_cache.json")`."""
    return model_dir() / name


__all__ = ["model_dir", "model_path"]
