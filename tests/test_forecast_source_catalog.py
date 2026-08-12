from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from scripts.forecast_source_catalog import (
    latest_cycle,
    plan_operational_requests,
)
from weather_trader.forecasting.source_catalog import (
    ArtifactRequest,
    BoundedCollector,
    FetchResult,
    ForecastSourceCatalog,
    MarketTarget,
    RequestsFetcher,
    SOURCE_CONTRACTS,
    SOURCE_VINTAGE_CONTRACT_VERSION,
    contract_for,
    manifest_requests,
)


UTC = timezone.utc


class FakeFetcher:
    def __init__(self, payloads: dict[str, tuple[bytes, str, dict[str, str]]]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    def fetch(self, request: ArtifactRequest, timeout_seconds: float) -> FetchResult:
        self.calls.append(request.source_key)
        payload, observed, headers = self.payloads[request.source_key]
        return FetchResult(
            content=payload,
            final_url=request.url,
            headers=headers,
            observed_at_utc=observed,
        )


def test_source_contracts_have_stable_distinct_fingerprints() -> None:
    assert SOURCE_VINTAGE_CONTRACT_VERSION == "forecast_source_vintage_v1"
    fingerprints = {item.fingerprint for item in SOURCE_CONTRACTS}
    assert len(fingerprints) == len(SOURCE_CONTRACTS)
    assert contract_for("weathernext_2").availability_rule == "PROVIDER_INGESTION_TIME"
    assert contract_for("nbm_v5").availability_rule == "FIRST_SUCCESSFUL_OBSERVATION"
    assert contract_for("rrfs").operational_version == "UNFROZEN"


def test_noaa_artifact_is_not_backdated_by_last_modified(tmp_path: Path) -> None:
    request = ArtifactRequest(
        source_id="nbm_v5",
        source_key="nbm/run/station/f001",
        url="https://example.test/nbm",
        station="KATL",
        market_date="2026-08-12",
        cycle_at_utc="2026-08-12T12:00:00+00:00",
    )
    fetcher = FakeFetcher(
        {
            request.source_key: (
                b"GRIB-nbm-bytes",
                "2026-08-12T14:05:00+00:00",
                {"Last-Modified": "Wed, 12 Aug 2026 13:00:00 GMT", "ETag": "v1"},
            )
        }
    )
    with ForecastSourceCatalog(tmp_path / "catalog.sqlite", tmp_path / "raw") as catalog:
        summary = BoundedCollector(catalog, fetcher=fetcher).collect(
            [request], max_artifacts=1, max_bytes=1000
        )
        assert summary["status"] == "COMPLETE"
        before = catalog.replay_visible(
            "nbm_v5", as_of_utc="2026-08-12T14:04:59+00:00"
        )
        visible = catalog.replay_visible(
            "nbm_v5", as_of_utc="2026-08-12T14:05:00+00:00"
        )
        assert before == []
        assert len(visible) == 1
        assert visible[0]["causal_available_at_utc"] == "2026-08-12T14:05:00+00:00"
        assert visible[0]["last_modified_at_utc"] == "2026-08-12T13:00:00+00:00"
        assert Path(visible[0]["raw_path"]).read_bytes() == b"GRIB-nbm-bytes"


def test_weathernext_uses_provider_ingestion_time_for_replay(tmp_path: Path) -> None:
    request = ArtifactRequest(
        source_id="weathernext_2",
        source_key="wn2/2026081200/member-set/station",
        url="https://example.test/wn2",
        station="KATL",
        market_date="2026-08-13",
        cycle_at_utc="2026-08-12T00:00:00+00:00",
        provider_available_at_utc="2026-08-12T07:42:00+00:00",
    )
    fetcher = FakeFetcher(
        {
            request.source_key: (
                b"weather-next",
                "2026-08-15T00:00:00+00:00",
                {},
            )
        }
    )
    with ForecastSourceCatalog(tmp_path / "catalog.sqlite", tmp_path / "raw") as catalog:
        summary = BoundedCollector(catalog, fetcher=fetcher).collect(
            [request], max_artifacts=1, max_bytes=1000
        )
        assert summary["status"] == "COMPLETE"
        visible = catalog.replay_visible(
            "weathernext_2",
            as_of_utc="2026-08-12T07:42:00+00:00",
            station="KATL",
            market_date=date(2026, 8, 13),
        )
        assert len(visible) == 1
        assert visible[0]["first_observed_at_utc"] == "2026-08-15T00:00:00+00:00"
        assert visible[0]["causal_available_at_utc"] == "2026-08-12T07:42:00+00:00"


def test_weathernext_missing_ingestion_time_fails_closed(tmp_path: Path) -> None:
    request = ArtifactRequest(
        source_id="weathernext_2",
        source_key="wn2/missing-ingestion",
        url="https://example.test/wn2",
    )
    fetcher = FakeFetcher(
        {request.source_key: (b"bytes", "2026-08-12T08:00:00+00:00", {})}
    )
    with ForecastSourceCatalog(tmp_path / "catalog.sqlite", tmp_path / "raw") as catalog:
        summary = BoundedCollector(catalog, fetcher=fetcher).collect(
            [request], max_artifacts=1, max_bytes=1000
        )
        assert summary["status"] == "FAILED"
        assert catalog.replay_visible(
            "weathernext_2", as_of_utc="2026-08-13T00:00:00+00:00"
        ) == []
        assert list((tmp_path / "raw").rglob("*.bin")) == []


def test_content_versions_are_immutable_and_identical_content_deduplicates(tmp_path: Path) -> None:
    request = ArtifactRequest(
        source_id="iem_metar",
        source_key="iem/KATL/2026-08-12/14",
        url="https://example.test/iem",
    )
    with ForecastSourceCatalog(tmp_path / "catalog.sqlite", tmp_path / "raw") as catalog:
        first = BoundedCollector(
            catalog,
            fetcher=FakeFetcher(
                {request.source_key: (b"station,valid\\nATL,now\\n", "2026-08-12T14:00:00+00:00", {})}
            ),
        ).collect([request], max_artifacts=1, max_bytes=100)
        duplicate = BoundedCollector(
            catalog,
            fetcher=FakeFetcher(
                {request.source_key: (b"station,valid\\nATL,now\\n", "2026-08-12T14:10:00+00:00", {})}
            ),
        ).collect([request], max_artifacts=1, max_bytes=100)
        revised = BoundedCollector(
            catalog,
            fetcher=FakeFetcher(
                {request.source_key: (b"station,valid\\nATL,later\\n", "2026-08-12T14:20:00+00:00", {})}
            ),
        ).collect([request], max_artifacts=1, max_bytes=100)
        assert first["new_artifacts"] == 1
        assert duplicate["new_artifacts"] == 0
        assert revised["new_artifacts"] == 1
        rows = catalog.replay_visible(
            "iem_metar", as_of_utc="2026-08-12T14:30:00+00:00"
        )
        assert len(rows) == 2
        assert len({row["content_sha256"] for row in rows}) == 2
        coverage = {
            row["source_id"]: row for row in catalog.coverage()["sources"]
        }
        assert coverage["iem_metar"]["revised_keys"] == 1


def test_collector_enforces_byte_bound_before_persisting(tmp_path: Path) -> None:
    request = ArtifactRequest(
        source_id="hrrr_v4",
        source_key="large",
        url="https://example.test/hrrr",
    )
    with ForecastSourceCatalog(tmp_path / "catalog.sqlite", tmp_path / "raw") as catalog:
        summary = BoundedCollector(
            catalog,
            fetcher=FakeFetcher(
                {request.source_key: (b"GRIB" + b"x" * 97, "2026-08-12T14:00:00+00:00", {})}
            ),
        ).collect([request], max_artifacts=1, max_bytes=100)
        assert summary["status"] == "FAILED"
        assert summary["stopped_reason"] == "MAX_BYTES"
        assert catalog.replay_visible(
            "hrrr_v4", as_of_utc="2026-08-13T00:00:00+00:00"
        ) == []


def test_operational_plan_is_listing_bounded_and_selects_expected_fields() -> None:
    target = MarketTarget(
        station="KATL",
        market_date=date(2026, 8, 13),
        first_supported_listing_at_utc="2026-08-12T10:00:00+00:00",
        listing_source="test",
        latitude=33.6367,
        longitude=-84.4281,
    )
    requests = plan_operational_requests(
        [target],
        sources={"nbm_v5", "hrrr_v4", "iem_metar"},
        as_of_utc=datetime(2026, 8, 12, 18, tzinfo=UTC),
        lead_stride_hours=6,
    )
    by_source: dict[str, list[ArtifactRequest]] = {}
    for item in requests:
        by_source.setdefault(item.source_id, []).append(item)
    assert by_source["nbm_v5"]
    assert by_source["hrrr_v4"]
    assert "var_TMAX" in by_source["nbm_v5"][0].params
    assert "var_DSWRF" in by_source["hrrr_v4"][0].params
    assert all(
        item.metadata["first_supported_listing_at_utc"]
        == target.first_supported_listing_at_utc
        for item in requests
    )

    future_target = MarketTarget(
        **{
            **target.__dict__,
            "first_supported_listing_at_utc": "2026-08-12T19:00:00+00:00",
        }
    )
    assert plan_operational_requests(
        [future_target],
        sources={"nbm_v5"},
        as_of_utc=datetime(2026, 8, 12, 18, tzinfo=UTC),
        lead_stride_hours=6,
    ) == []


def test_manifest_requires_weathernext_ingestion_time(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"artifacts":[{"source_id":"weathernext_2","source_key":"x","url":"https://x"}]}'
    )
    with pytest.raises(ValueError, match="provider_available"):
        manifest_requests(manifest)

    manifest.write_text(
        '{"artifacts":[{"source_id":"weathernext_2","source_key":"x",'
        '"url":"https://x","provider_available_at_utc":"2026-08-12T07:30:00Z"}]}'
    )
    requests = manifest_requests(manifest)
    assert requests[0].provider_available_at_utc == "2026-08-12T07:30:00Z"



def test_requests_fetcher_retries_rate_limit_without_losing_observed_payload() -> None:
    class Response:
        def __init__(self, status_code: int, content: bytes) -> None:
            self.status_code = status_code
            self.content = content
            self.url = "https://example.test/iem"
            self.headers = {"Retry-After": "0"}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

    class Session:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.responses = [
                Response(429, b"limited"),
                Response(200, b"station,valid\\nATL,now\\n"),
            ]

        def get(self, *args: object, **kwargs: object) -> Response:
            return self.responses.pop(0)

    session = Session()
    fetcher = RequestsFetcher(
        session, max_retries=1, iem_min_interval_seconds=0.0  # type: ignore[arg-type]
    )
    result = fetcher.fetch(
        ArtifactRequest("iem_metar", "key", "https://example.test/iem"),
        timeout_seconds=1.0,
    )
    assert result.content.startswith(b"station,valid")
    assert session.responses == []


def test_latest_cycle_respects_release_lag() -> None:
    assert latest_cycle(
        datetime(2026, 8, 12, 13, 0, tzinfo=UTC),
        cycle_hours=6,
        release_lag=__import__("datetime").timedelta(hours=2),
    ) == datetime(2026, 8, 12, 6, 0, tzinfo=UTC)
