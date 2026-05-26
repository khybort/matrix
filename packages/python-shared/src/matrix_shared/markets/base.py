"""MarketAdapter ABC + adjacent value objects and protocols.

Each market (crypto, bist, …) implements `MarketAdapter` to declare its
universe, trading hours, fee model, settlement rules, and factories for
the pipeline-level adapters (ingestor, executor). Pipeline services
depend on this interface, never on a hard-coded market.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class FeeModel:
    """Per-side trading fees and slippage assumptions for a symbol.

    All values are in basis points (1 bp = 0.01%) unless suffixed `_usd`.
    Paper-trade and live executors both consume this.
    """

    maker_bps: Decimal
    taker_bps: Decimal
    fixed_usd: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")


class ExecutionAdapter(ABC):
    """Order placement / position tracking for one market.

    Concrete implementations live in `services/execution/` (live broker
    connectors) or are produced by the paper-trade engine. Wired in Phase D.
    """

    @abstractmethod
    async def place_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        **kw: Any,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def cancel_order(self, *, order_id: str) -> bool: ...

    @abstractmethod
    async def positions(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def equity(self) -> Decimal: ...

    @abstractmethod
    async def health(self) -> bool: ...


class IngestorAdapter(ABC):
    """Long-running ingest task for one market."""

    @abstractmethod
    async def run(self) -> None: ...


class MarketAdapter(ABC):
    """Pluggable market — one trading venue family.

    Subclasses must set `name` and `asset_class` as class variables. They
    are auto-registered into the global registry when their module is
    imported (see `matrix_shared.markets.__init__`).
    """

    name: ClassVar[str]
    asset_class: ClassVar[str]

    # ------------------------------------------------------------------
    # Universe & symbology
    # ------------------------------------------------------------------
    @abstractmethod
    async def universe(self, db: AsyncSession) -> list[str]:
        """Active tradable symbols for this market."""

    @abstractmethod
    def claims_symbol(self, symbol: str) -> bool:
        """True iff this market owns the given symbol.

        Must be sync and fast — called by `infer_market(symbol)` in hot
        paths. Implementations may consult a cached frozenset; absent a
        cache, fall back to a regex/heuristic.
        """

    # ------------------------------------------------------------------
    # Trading rules
    # ------------------------------------------------------------------
    @abstractmethod
    def is_session_open(self, ts: datetime | None = None) -> bool: ...

    @abstractmethod
    def fees(self, symbol: str) -> FeeModel: ...

    @abstractmethod
    def allows_short(self) -> bool: ...

    @abstractmethod
    def settlement_days(self) -> int: ...

    # ------------------------------------------------------------------
    # Pipeline factories — filled in by Phase B/D wiring
    # ------------------------------------------------------------------
    def make_ingestor(self, cfg: Any) -> IngestorAdapter:
        raise NotImplementedError(f"{self.name}: ingestor not wired")

    def make_executor(self, cfg: Any, *, paper: bool) -> ExecutionAdapter:
        raise NotImplementedError(
            f"{self.name}: executor not wired (paper={paper})"
        )

    # ------------------------------------------------------------------
    # Quote source — used by paper engine, dashboards, agents
    # ------------------------------------------------------------------
    @abstractmethod
    async def latest_price(self, db: AsyncSession, symbol: str) -> Decimal | None: ...

    # ------------------------------------------------------------------
    # Repr / debugging
    # ------------------------------------------------------------------
    def __repr__(self) -> str:  # pragma: no cover
        return f"<MarketAdapter {self.name}>"
