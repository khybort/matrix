"""Wallet model schema test for Phase C asset_class split.

DB-free: inspects the SQLAlchemy mapper to verify the column,
default, and composite unique constraint exist. A live alembic
migration test against Postgres is run in the matrix integration
suite (`make acceptance`).
"""

from __future__ import annotations

from sqlalchemy import UniqueConstraint

from matrix_shared.models import Wallet


def test_wallet_has_asset_class_column() -> None:
    cols = {c.name: c for c in Wallet.__table__.columns}
    assert "asset_class" in cols, "Wallet.asset_class column missing"
    col = cols["asset_class"]
    assert not col.nullable, "asset_class must be NOT NULL"
    assert col.default is not None and col.default.arg == "crypto", (
        "asset_class default must be 'crypto' so legacy rows backfill cleanly"
    )


def test_wallet_name_no_longer_globally_unique() -> None:
    """Phase C widens the unique constraint — same name allowed across markets."""
    name_col = Wallet.__table__.columns["name"]
    assert not name_col.unique, (
        "wallets.name must NOT be globally unique after Phase C; "
        "use (name, asset_class) composite instead"
    )


def test_wallet_has_composite_unique_constraint() -> None:
    constraints = [
        c for c in Wallet.__table__.constraints if isinstance(c, UniqueConstraint)
    ]
    composite = next(
        (c for c in constraints if c.name == "wallets_name_asset_class_key"),
        None,
    )
    assert composite is not None, "composite unique constraint missing"
    cols = {c.name for c in composite.columns}
    assert cols == {"name", "asset_class"}
