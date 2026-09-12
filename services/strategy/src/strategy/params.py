"""Bind `strategy_configs.params` (JSON) to a strategy class constructor.

Before this module, the dispatcher instantiated every deterministic strategy
with `cls(symbols=...)` and ignored the DB row — reflection/labs mutations
were written to `strategy_configs` and never read (docs/AUTONOMY_PLAN.md §5).

Rules:
  * Only kwargs the constructor actually declares are passed; unknown keys
    are logged once and dropped (so a stale param never crashes a tick).
  * Values are coerced to the type of the ctor default (Decimal / int /
    float / bool). `None` defaults (e.g. `tp_pct: Decimal | None`) coerce
    to Decimal.
  * `PARAM_ALIASES` maps the historical proposal key names used by
    reflection (`PARAM_TUNERS`) / migrations onto ctor kwarg names.
  * The instance's `version` is overwritten with the config row's version so
    emitted PredictionDrafts are keyed to the params that produced them —
    without this, reflection's per-version metrics see n=0 after the first
    apply and learning silently stops.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from loguru import logger

# proposal/DB key → constructor kwarg
PARAM_ALIASES: dict[str, str] = {
    "price_band_pct": "band_pct",
    "horizon_seconds": "horizon_s",
}

# Keys that are legitimately in params but never constructor inputs.
_IGNORED_KEYS: frozenset[str] = frozenset({
    "weights", "signal_threshold", "explore_epsilon",  # matrix_agent-only
    "symbols",  # dispatcher-owned
})

_warned: set[tuple[str, str]] = set()


def _coerce(value: Any, default: Any, annotation: Any) -> Any:
    if value is None:
        return None
    target = type(default) if default is not None else None
    if target is None:
        # `x: Decimal | None = None` — infer from annotation text
        ann = str(annotation)
        if "Decimal" in ann:
            target = Decimal
        elif "int" in ann:
            target = int
        elif "float" in ann:
            target = float
        elif "bool" in ann:
            target = bool
    try:
        if target is Decimal:
            return Decimal(str(value))
        if target is bool:
            return str(value).strip().lower() in ("1", "true", "yes")
        if target is int:
            return int(Decimal(str(value)))
        if target is float:
            return float(value)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"cannot coerce {value!r} to {target}") from None
    return value


def build_kwargs(cls: type, params: dict[str, Any] | None) -> dict[str, Any]:
    """Filter + alias + coerce `params` into kwargs accepted by `cls.__init__`."""
    if not params:
        return {}
    sig = inspect.signature(cls.__init__)
    accepted = {
        name: p for name, p in sig.parameters.items()
        if name != "self" and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    }
    out: dict[str, Any] = {}
    for raw_key, value in params.items():
        if raw_key in _IGNORED_KEYS:
            continue
        key = PARAM_ALIASES.get(raw_key, raw_key)
        if key == "symbols":
            continue
        p = accepted.get(key)
        if p is None:
            mark = (getattr(cls, "id", cls.__name__), raw_key)
            if mark not in _warned:
                _warned.add(mark)
                logger.warning(
                    f"{mark[0]}: param {raw_key!r} not a constructor kwarg; ignored"
                )
            continue
        try:
            out[key] = _coerce(value, p.default if p.default is not p.empty else None, p.annotation)
        except ValueError as e:
            logger.warning(f"{getattr(cls, 'id', cls.__name__)}: {e}; using default")
    return out


def instantiate(
    cls: type,
    *,
    symbols: Sequence[str] | None = None,
    version: int | None = None,
    params: dict[str, Any] | None = None,
):
    """Build a strategy instance bound to a strategy_configs row."""
    kwargs = build_kwargs(cls, params)
    sig = inspect.signature(cls.__init__)
    if symbols is not None and "symbols" in sig.parameters:
        kwargs["symbols"] = list(symbols)
    strat = cls(**kwargs)
    if version is not None:
        strat.version = int(version)
    return strat
