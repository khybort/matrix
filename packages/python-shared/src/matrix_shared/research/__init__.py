"""matrix_research — the pre-registered hypothesis-testing harness.

How-to: docs/wiki/research-harness.md. Ledger: docs/research/ledger.jsonl.

Pure standard library (no numpy/pandas, no DB) so it imports in every image
and on the host; study builders may use whatever they like.
"""

from matrix_shared.research.episodes import (
    Episode,
    LookaheadError,
    WindowError,
    non_overlapping,
)
from matrix_shared.research.ledger import Ledger, LedgerError
from matrix_shared.research.protocol import (
    Cell,
    CellResult,
    FrozenCellError,
    HoldoutError,
    NotCommitted,
    ProtocolError,
    Spec,
    SpecMismatch,
    Study,
    SubprocessGit,
    register,
)
from matrix_shared.research.stats import bhy_qvalues, clustered_t, t_sf

__all__ = [
    "Cell",
    "CellResult",
    "Episode",
    "FrozenCellError",
    "HoldoutError",
    "Ledger",
    "LedgerError",
    "LookaheadError",
    "NotCommitted",
    "ProtocolError",
    "Spec",
    "SpecMismatch",
    "Study",
    "SubprocessGit",
    "WindowError",
    "bhy_qvalues",
    "clustered_t",
    "non_overlapping",
    "register",
    "t_sf",
]
