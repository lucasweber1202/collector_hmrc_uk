"""Run one standalone UK inflation predictor collector.

Every repository owns one publisher/source family. Source-specific extraction
lives in scripts.extract; persistence, PIT semantics and logging stay identical
across the predictor fleet.
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
import traceback
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.engine import Engine

from scripts.availability import attribute_release, upsert_availability
from scripts.config import LOG_LEVEL, missing_environment, unresolved_credentials
from scripts.db import build_engine
from scripts.extract import DATASETS, collect_one, open_client, resolve
from scripts.init_db import init_db
from scripts.metadata import upsert_metadata
from scripts.run_logs import insert_run_log
from scripts.snapshots import upsert_snapshots
from scripts.time_series import WriteResult, upsert_time_series

logger = logging.getLogger("main")
TRANSACTIONAL_DIALECTS = frozenset({"postgresql", "sqlite"})


def _setup_logging(level: str) -> io.StringIO:
    buffer = io.StringIO()
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(formatter)
    buffer_handler = logging.StreamHandler(stream=buffer)
    buffer_handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.ERROR)
    root.addHandler(stream_handler)
    root.addHandler(buffer_handler)
    logging.getLogger("main").setLevel(level.upper())
    logging.getLogger("scripts").setLevel(level.upper())
    return buffer


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect this publisher's UK inflation predictor data sets."
    )
    parser.add_argument("--log-level", default=LOG_LEVEL)
    parser.add_argument(
        "--source-id",
        action="append",
        choices=sorted(DATASETS),
        help="Collect only this data set; repeatable. Defaults to every data set.",
    )
    return parser.parse_args(argv)


def _availability_rows(data: Any, result: WriteResult, collected_at: datetime) -> list[dict[str, Any]]:
    """Build immutable PIT rows for the vintages written in this run.

    A later revision of an already stored reference period must never reuse the
    original release timestamp. Without explicit revision-release evidence, the
    safe availability instant is when this collector first saw the revised value.
    """
    snapshot_by_key = {
        (observation.series_id, observation.reference_date): observation.snapshot_id
        for observation in data.observations
    }
    rows: list[dict[str, Any]] = []
    for series_id, reference_date, vintage_date in result.written_keys:
        key = (series_id, reference_date, vintage_date)
        if key in result.revised_keys:
            available_at, basis, release_date = collected_at, "first_seen", None
        else:
            available_at, basis, release_date = attribute_release(
                reference_date,
                data.releases,
                data.min_lag_days,
                data.max_lag_days,
                data.inferred_lag_days,
            )
            if basis not in {"official_timestamp", "official_date", "archived_release"} and series_id in result.preexisting_series:
                available_at, basis, release_date = collected_at, "first_seen", None
        rows.append(
            {
                "series_id": series_id,
                "reference_date": reference_date,
                "vintage_date": vintage_date,
                "release_date": release_date,
                "available_at": available_at,
                "availability_basis": basis,
                "source_snapshot_id": snapshot_by_key[(series_id, reference_date)],
            }
        )
    return rows


def collect_source(engine: Engine, source_ids: list[str] | None = None) -> None:
    """Collect and persist each selected data set in its own transaction.

    One repository owns several data sets from the same publisher. They share
    nothing but this persistence path, so a layout change at one DEFRA page
    must not stop the others from updating: each is collected and committed
    independently and the failures are re-raised together at the end.
    """
    selected = resolve(source_ids)
    if engine.dialect.name not in TRANSACTIONAL_DIALECTS:
        logger.warning(
            "%s commits statements independently; interrupted Databricks runs are repaired "
            "idempotently by the next run",
            engine.dialect.name,
        )
    failures: list[tuple[str, Exception]] = []
    with open_client() as client:
        for source_id in selected:
            try:
                data = collect_one(client, source_id)
                collected_at = datetime.now(UTC)
                with engine.begin() as conn:
                    snapshots_written = upsert_snapshots(conn, data.snapshots)
                    result = upsert_time_series(conn, data.observations, collected_at)
                    availability_rows = _availability_rows(data, result, collected_at)
                    availability_written = upsert_availability(
                        conn, availability_rows, collected_at
                    )
                    metadata_inserted, metadata_updated = upsert_metadata(
                        conn, data.catalog, collected_at
                    )
            except Exception as exc:
                logger.exception("Data set %s failed", source_id)
                failures.append((source_id, exc))
                continue
            logger.info(
                "result %s: new_observations=%d new_vintages=%d availability=%d snapshots=%d "
                "metadata_inserted=%d metadata_updated=%d",
                source_id,
                result.new_observations,
                result.new_vintages,
                availability_written,
                snapshots_written,
                metadata_inserted,
                metadata_updated,
            )
    if failures:
        names = ", ".join(source_id for source_id, _ in failures)
        raise RuntimeError(
            f"{len(failures)} of {len(selected)} data set(s) failed: {names}"
        ) from failures[0][1]


def main(source_ids: list[str] | None = None) -> int:
    missing = missing_environment()
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    for name in unresolved_credentials():
        logger.warning("%s is unset; it must resolve from the runtime context", name)
    engine = build_engine()
    try:
        init_db(engine)
        collect_source(engine, source_ids)
    finally:
        engine.dispose()
    return 0


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    log_buffer = _setup_logging(args.log_level)
    started_at = datetime.now(UTC)
    status = "success"
    traceback_text: str | None = None
    return_code = 0
    try:
        return_code = main(args.source_id)
    except Exception:
        status = "error"
        traceback_text = traceback.format_exc()
        logger.exception("Pipeline failed")
        return_code = 1
    finally:
        finished_at = datetime.now(UTC)
        log_engine = None
        try:
            log_engine = build_engine()
            init_db(log_engine)
            insert_run_log(
                log_engine,
                started_at,
                finished_at,
                status,
                log_buffer.getvalue(),
                traceback_text,
            )
        except Exception:
            logger.exception("Could not persist run log")
            return_code = 1
        finally:
            if log_engine is not None:
                log_engine.dispose()
    return return_code


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
