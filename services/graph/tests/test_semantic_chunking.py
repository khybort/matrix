"""Unit tests for semantic chunking and multi-chunk merge."""

from __future__ import annotations

from graph.chunking import ChunkConfig, plan_semantic_chunks
from graph.parsing import Entity, Relation, merge_extractions


def test_short_body_stays_single_chunk():
    cfg = ChunkConfig(
        enabled=True,
        target_chars=1200,
        max_chars=1800,
        min_chars=200,
        sim_threshold=0.25,
        max_chunks=6,
    )
    text = "Bitcoin rallied after BlackRock filed for an ETF."
    chunks = plan_semantic_chunks(text, cfg)
    assert len(chunks) == 1
    assert chunks[0].text == text


def test_long_body_splits_into_multiple_chunks():
    cfg = ChunkConfig(
        enabled=True,
        target_chars=250,
        max_chars=320,
        min_chars=80,
        sim_threshold=0.25,
        max_chunks=6,
    )
    crypto = (
        "Bitcoin and Ethereum rallied sharply after institutional buyers returned. "
        "Traders cited ETF inflows and stablecoin demand as primary drivers. "
        "Solana and XRP followed with double-digit weekly gains across major venues. "
    ) * 2
    macro = (
        "Meanwhile the Federal Reserve kept rates unchanged and warned on inflation. "
        "Treasury yields climbed while the dollar strengthened against major peers. "
        "Equity markets slipped as investors rotated into defensive sectors. "
    ) * 2
    text = crypto + "\n\n" + macro
    chunks = plan_semantic_chunks(text, cfg)
    assert len(chunks) >= 2
    assert all(len(c.text) <= cfg.max_chars for c in chunks)


def test_merge_extractions_dedupes_entities_and_relations():
    ents_a = [
        Entity(type="Asset", canonical="BTC", display="Bitcoin"),
        Entity(type="Company", canonical="BlackRock", display="BlackRock"),
    ]
    rels_a = [
        Relation(
            source_type="Company",
            source_canonical="BlackRock",
            edge_type="OWNS",
            target_type="Asset",
            target_canonical="BTC",
        )
    ]
    ents_b = [
        Entity(type="Asset", canonical="BTC", display="Bitcoin"),
        Entity(type="Company", canonical="BlackRock", display="BlackRock"),
    ]
    merged_e, merged_r = merge_extractions([(ents_a, rels_a), (ents_b, [])])
    assert len(merged_e) == 2
    assert len(merged_r) == 1
