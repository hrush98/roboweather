from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from weather_trader.discovery.decision_cache import (
    DecisionCacheContract,
    DecisionCacheLockedError,
    ExecutableDecisionCache,
    benchmark_decision_grain,
    quote_ready_timestamp,
)
from weather_trader.tape.replay import PostReadyCheckpointBookProvider


def _research(rows: list[tuple]) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        create table prediction_snapshots (
            id integer primary key,
            timestamp text not null,
            station text not null,
            market_date text not null,
            decision_time_utc text not null,
            decision_time_local text not null,
            latest_obs_time_utc text not null,
            obs_age_minutes real not null,
            obs_delay_bucket text not null,
            strategy_bucket text not null,
            selected_market_id text,
            selected_bucket text,
            selected_side text not null,
            selected_edge real,
            selected_fair_yes real,
            selected_fair_no real,
            selected_yes_ask real,
            selected_no_ask real,
            high_conviction integer not null,
            model_name text not null,
            market_family text not null,
            raw_json text not null
        );
        """
    )
    connection.executemany(
        "insert into prediction_snapshots values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return connection


def _row(
    snapshot_id: int,
    timestamp: str,
    *,
    model: str,
    market: str = "market-1",
    side: str = "BUY_YES",
) -> tuple:
    return (
        snapshot_id,
        timestamp,
        "KATL",
        "2026-01-02",
        "2026-01-02T19:59:00+00:00",
        "2026-01-02T13:00:00-07:00",
        "2026-01-02T19:50:00+00:00",
        9.0,
        "10m",
        "HIGH_CONVICTION",
        market,
        "<=90F",
        side,
        0.60,
        0.80,
        0.20,
        0.20,
        0.80,
        1,
        model,
        "HIGH_TEMP",
        f'{{"snapshot":{snapshot_id}}}',
    )


def _tape(*markets: str) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("create table tape_tokens(market_id text,outcome text,token_id text)")
    for market in markets:
        connection.execute(
            "insert into tape_tokens values(?,?,?)",
            (market, "YES", f"token-{market}"),
        )
    return connection


def _checkpoint_tape() -> sqlite3.Connection:
    connection = _tape("market-1")
    connection.executescript(
        """
        create table tape_book_checkpoints (
            checkpoint_id text primary key, session_id text, token_id text,
            event_id text, event_offset integer, captured_at_utc text,
            reconstruction_hash text, coverage_state text, raw_json text
        );
        create table tape_coverage_intervals (
            id integer primary key, session_id text, token_id text, state text,
            started_at_utc text, ended_at_utc text
        );
        insert into tape_book_checkpoints values (
            'checkpoint-1','session-1','token-market-1','event-1',1,
            '2026-01-02T20:01:10+00:00','checkpoint-hash','VALID',
            '{"bids":[[0.19,100.0]],"asks":[[0.20,500.0]]}'
        );
        insert into tape_coverage_intervals values (
            7,'session-1','token-market-1','VALID',
            '2026-01-02T19:59:00+00:00','2026-01-02T20:02:00+00:00'
        );
        """
    )
    return connection


class CountingProvider:
    def __init__(self, *, rejection: str | None = None, crash_on_call: int | None = None) -> None:
        self.calls = 0
        self.rejection = rejection
        self.crash_on_call = crash_on_call

    def book_at(self, token_id, ready, *, pre_signal_seconds):
        self.calls += 1
        if self.crash_on_call == self.calls:
            raise RuntimeError("simulated interruption")
        if self.rejection:
            return None, self.rejection
        return {
            "bids": {0.19: 100.0},
            "asks": {0.20: 500.0, 0.30: 500.0},
            "checkpoint_age_s": 0.5,
            "session_id": "session-1",
            "coverage_interval_id": 7,
            "checkpoint_event_id": "event-1",
            "checkpoint_captured_at_utc": ready.isoformat(),
            "checkpoint_reconstruction_hash": "checkpoint-hash",
            "partition_ids": ("partition-1",),
            "reconstruction_hash": f"book-{token_id}-{ready.isoformat()}",
        }, None


def _factory(provider: CountingProvider):
    def build(tape, tokens):
        return provider

    return build


def test_quote_ready_uses_causal_availability_ceiling() -> None:
    contract = DecisionCacheContract(availability_bucket_seconds=60, latency_ms=250)
    ready = quote_ready_timestamp("2026-01-02T20:00:01.100000+00:00", contract)

    assert ready == datetime(2026, 1, 2, 20, 1, 0, 250000, tzinfo=timezone.utc)
    assert ready > datetime(2026, 1, 2, 20, 0, 1, 100000, tzinfo=timezone.utc)


def test_post_ready_checkpoint_provider_is_causal_bounded_and_gap_safe() -> None:
    tape = _checkpoint_tape()
    ready = datetime(2026, 1, 2, 20, 1, 0, 250000, tzinfo=timezone.utc)
    provider = PostReadyCheckpointBookProvider(
        tape,
        {"token-market-1"},
        maximum_execution_delay_seconds=30.0,
    )

    book, reason = provider.book_at("token-market-1", ready, pre_signal_seconds=60)

    assert reason is None
    assert book is not None
    assert min(book["asks"]) == 0.20
    assert book["execution_delay_ms_after_ready"] == pytest.approx(9_750.0)
    tape.execute("update tape_coverage_intervals set started_at_utc='2026-01-02T20:00:30+00:00'")
    rejected, rejection = provider.book_at("token-market-1", ready, pre_signal_seconds=60)
    assert rejected is None
    assert rejection == "no_continuous_valid_interval_through_execution"


def test_decision_grain_benchmark_measures_exact_replay_reduction() -> None:
    research = _research([
        _row(1, "2026-01-02T20:00:01.100000+00:00", model="model-a"),
        _row(2, "2026-01-02T20:00:02.900000+00:00", model="model-b"),
    ])

    result = benchmark_decision_grain(
        research,
        _tape("market-1"),
        contract=DecisionCacheContract(),
        source_start_date="2026-01-01",
    )

    assert result["raw_model_rows"] == 2
    assert result["legacy_exact_timestamp_decisions"] == 2
    assert result["bucketed_executable_decisions"] == 1
    assert result["bucketed_provider_calls"] == 1
    assert result["minimum_replay_reduction_factor"] == 2.0


def test_duplicate_model_rows_share_one_replay_and_warm_refresh_is_noop(tmp_path: Path) -> None:
    research = _research([
        _row(1, "2026-01-02T20:00:01.100000+00:00", model="model-a"),
        _row(2, "2026-01-02T20:00:02.900000+00:00", model="model-b"),
    ])
    tape = _tape("market-1")
    provider = CountingProvider()
    contract = DecisionCacheContract()

    with ExecutableDecisionCache(tmp_path / "cache.sqlite") as cache:
        first = cache.refresh(
            research,
            tape,
            contract=contract,
            source_start_date="2026-01-01",
            book_provider_factory=_factory(provider),
        )
        second = cache.refresh(
            research,
            tape,
            contract=contract,
            source_start_date="2026-01-01",
            book_provider_factory=_factory(provider),
        )

        assert cache.table_counts()["model_decision_mappings"] == 2
        assert cache.table_counts()["executable_decisions"] == 1
        assert provider.calls == 1
        assert first["diagnostics"]["REPLAY_CALLS"] == 1
        assert second["diagnostics"].get("REPLAY_CALLS", 0) == 0
        assert second["diagnostics"]["PENDING_DECISIONS"] == 0


def test_rejections_are_cached_and_replay_version_creates_new_identity(tmp_path: Path) -> None:
    research = _research([_row(1, "2026-01-02T20:00:01+00:00", model="model-a")])
    tape = _tape("market-1")
    rejected_provider = CountingProvider(rejection="no_continuous_valid_interval")
    contract = DecisionCacheContract()

    with ExecutableDecisionCache(tmp_path / "cache.sqlite") as cache:
        first = cache.refresh(
            research,
            tape,
            contract=contract,
            source_start_date="2026-01-01",
            book_provider_factory=_factory(rejected_provider),
        )
        repeated = cache.refresh(
            research,
            tape,
            contract=contract,
            source_start_date="2026-01-01",
            book_provider_factory=_factory(rejected_provider),
        )
        changed_provider = CountingProvider()
        changed = cache.refresh(
            research,
            tape,
            contract=replace(contract, replay_version="causal_l2_replay_v2"),
            source_start_date="2026-01-01",
            book_provider_factory=_factory(changed_provider),
        )

        assert first["diagnostics"]["REJECTION:TAPE:no_continuous_valid_interval"] == 1
        assert repeated["diagnostics"].get("REPLAY_CALLS", 0) == 0
        assert rejected_provider.calls == 1
        assert changed_provider.calls == 1
        assert changed["diagnostics"]["DECISION_SUCCESS"] == 1
        assert cache.table_counts()["executable_decisions"] == 2


def test_direct_replay_verifier_requires_exact_cached_hashes(tmp_path: Path) -> None:
    research = _research([
        _row(1, "2026-01-02T20:00:01+00:00", model="model-a", market="market-1"),
        _row(2, "2026-01-02T20:01:01+00:00", model="model-b", market="market-2"),
    ])
    tape = _tape("market-1", "market-2")
    contract = DecisionCacheContract()

    with ExecutableDecisionCache(tmp_path / "cache.sqlite") as cache:
        cache.refresh(
            research,
            tape,
            contract=contract,
            source_start_date="2026-01-01",
            book_provider_factory=_factory(CountingProvider()),
        )
        verifier_provider = CountingProvider()
        passed = cache.verify_direct_replay(
            tape,
            contract=contract,
            sample_size=10,
            book_provider_factory=_factory(verifier_provider),
        )
        cache.connection.execute(
            "update executable_decisions set result_hash='corrupt' where decision_id=(select min(decision_id) from executable_decisions)"
        )
        failed = cache.verify_direct_replay(
            tape,
            contract=contract,
            sample_size=10,
            book_provider_factory=_factory(CountingProvider()),
        )

    assert passed["status"] == "PASS"
    assert passed["sampled_decisions"] == passed["exact_matches"] == 2
    assert verifier_provider.calls == 2
    assert failed["status"] == "FAIL"
    assert len(failed["mismatches"]) == 1


def test_interrupted_replay_resumes_from_committed_batch_with_same_hashes(tmp_path: Path) -> None:
    rows = [
        _row(1, "2026-01-02T20:00:01+00:00", model="model-a", market="market-1"),
        _row(2, "2026-01-02T20:01:01+00:00", model="model-a", market="market-2"),
        _row(3, "2026-01-02T20:02:01+00:00", model="model-a", market="market-3"),
    ]
    research = _research(rows)
    tape = _tape("market-1", "market-2", "market-3")
    contract = DecisionCacheContract()

    resumed_path = tmp_path / "resumed.sqlite"
    with ExecutableDecisionCache(resumed_path) as cache:
        crashing = CountingProvider(crash_on_call=2)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            cache.refresh(
                research,
                tape,
                contract=contract,
                source_start_date="2026-01-01",
                replay_batch_size=1,
                book_provider_factory=_factory(crashing),
            )
        assert [row["status"] for row in cache.decision_rows(contract.contract_hash)].count("SUCCESS") == 1

        resumed = CountingProvider()
        cache.refresh(
            research,
            tape,
            contract=contract,
            source_start_date="2026-01-01",
            replay_batch_size=1,
            book_provider_factory=_factory(resumed),
        )
        resumed_hashes = [row["result_hash"] for row in cache.decision_rows(contract.contract_hash)]
        assert resumed.calls == 2

    with ExecutableDecisionCache(tmp_path / "uninterrupted.sqlite") as cache:
        uninterrupted = CountingProvider()
        cache.refresh(
            research,
            tape,
            contract=contract,
            source_start_date="2026-01-01",
            replay_batch_size=1,
            book_provider_factory=_factory(uninterrupted),
        )
        uninterrupted_hashes = [row["result_hash"] for row in cache.decision_rows(contract.contract_hash)]

    assert resumed_hashes == uninterrupted_hashes


def test_uncataloged_token_is_cached_without_calling_tape(tmp_path: Path) -> None:
    research = _research([_row(1, "2026-01-02T20:00:01+00:00", model="model-a")])
    provider = CountingProvider()

    with ExecutableDecisionCache(tmp_path / "cache.sqlite") as cache:
        result = cache.refresh(
            research,
            _tape(),
            contract=DecisionCacheContract(),
            source_start_date="2026-01-01",
            book_provider_factory=_factory(provider),
        )

        decision = cache.decision_rows(result["contract_hash"])[0]
        assert decision["status"] == "REJECTED"
        assert decision["rejection_reason"] == "TOKEN_NOT_CATALOGED"
        assert provider.calls == 0


def test_cache_writer_lock_rejects_a_second_writer(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite"
    with ExecutableDecisionCache(path) as first, ExecutableDecisionCache(path) as second:
        with first.writer_lock():
            with pytest.raises(DecisionCacheLockedError):
                with second.writer_lock():
                    pass


def test_schema_v1_migrates_checkpoint_execution_columns(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite"
    with ExecutableDecisionCache(path):
        pass
    connection = sqlite3.connect(path)
    connection.execute("update cache_metadata set value='1' where key='schema_version'")
    connection.execute("alter table executable_decisions drop column execution_timestamp_utc")
    connection.execute("alter table executable_decisions drop column execution_delay_ms_after_ready")
    connection.commit()
    connection.close()

    with ExecutableDecisionCache(path) as migrated:
        columns = {
            str(row[1])
            for row in migrated.connection.execute("pragma table_info(executable_decisions)")
        }
        version = migrated.connection.execute(
            "select value from cache_metadata where key='schema_version'"
        ).fetchone()[0]

    assert {"execution_timestamp_utc", "execution_delay_ms_after_ready"} <= columns
    assert version == "2"


def test_research_outcome_enrichment_is_separate_and_idempotent(tmp_path: Path) -> None:
    research = _research([_row(1, "2026-01-02T20:00:01+00:00", model="model-a")])
    research.executescript(
        """
        create table station_date_outcomes (
            station text, market_date text, final_high_tmpf real,
            final_low_tmpf real, source text, resolved_at text
        );
        insert into station_date_outcomes values (
            'KATL','2026-01-02',85.0,30.0,'IEM_ASOS','2026-01-03T12:00:00+00:00'
        );
        """
    )
    contract = DecisionCacheContract()
    with ExecutableDecisionCache(tmp_path / "cache.sqlite") as cache:
        cache.refresh(
            research,
            _tape("market-1"),
            contract=contract,
            source_start_date="2026-01-01",
            book_provider_factory=_factory(CountingProvider()),
        )
        first = cache.enrich_research_outcomes(
            research,
            contract_hash=contract.contract_hash,
            outcome_watermark="2026-01-04T00:00:00+00:00",
        )
        repeated = cache.enrich_research_outcomes(
            research,
            contract_hash=contract.contract_hash,
            outcome_watermark="2026-01-04T00:00:00+00:00",
        )
        enrichment = cache.connection.execute(
            "select status,value_json from decision_enrichments"
        ).fetchone()

    assert first == repeated
    assert first["available"] == 1
    assert enrichment["status"] == "AVAILABLE"
    assert '"label":1' in enrichment["value_json"]
