from datetime import datetime, timezone

import pytest

from aggregation import build_aggregation, snapshot_kind_from_name


NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def _group(signature, observed_date, count):
    return {
        "group_signature": signature,
        "observed_date": observed_date,
        "teams_json": "[[1,2,3,4,5],[6,7,8,9,10],[11,12,13,14,15]]",
        "team_count": 3,
        "encounter_count": count,
        "first_seen_at": 100,
        "last_seen_at": 200,
    }


def _snapshot(bot_id, uploaded_at, *, groups=None, strengths=None):
    kind = "defense_groups" if groups is not None else "strong_defenses"
    payload = {
        "schema_version": 1,
        "bot_id": bot_id,
        "exported_at": "2026-06-12T03:00:00Z",
        "window_start": "2026-05-14",
        "window_end": "2026-06-12",
    }
    if groups is not None:
        payload["groups"] = groups
    else:
        payload["strong_defenses"] = strengths or []
    return {
        "kind": kind,
        "upload_timestamp": uploaded_at,
        "payload": payload,
    }


def test_build_aggregation_uses_latest_snapshot_per_bot_and_kind():
    snapshots = [
        _snapshot("bot-a", 1000, groups=[_group("same", "2026-06-11", 99)]),
        _snapshot("bot-a", 2000, groups=[_group("same", "2026-06-11", 2)]),
        _snapshot("bot-b", 1500, groups=[_group("same", "2026-06-11", 3)]),
    ]

    result = build_aggregation(snapshots, NOW)

    assert result["machine_output"]["groups"][0]["encounter_count"] == 5
    assert result["machine_output"]["total_bots"] == 2
    assert result["machine_output"]["source_uploads"] == 2


def test_build_aggregation_keeps_dates_separate_and_filters_to_beijing_windows():
    snapshots = [
        _snapshot(
            "bot-a",
            1000,
            groups=[
                _group("same", "2026-06-11", 2),
                _group("same", "2026-06-10", 4),
                _group("start", "2026-05-14", 1),
                _group("too-old", "2026-05-13", 8),
                _group("future", "2026-06-13", 9),
            ],
        )
    ]

    result = build_aggregation(snapshots, NOW)
    machine = result["machine_output"]

    assert machine["window_start"] == "2026-05-14"
    assert machine["window_end"] == "2026-06-12"
    assert [(item["group_signature"], item["observed_date"]) for item in machine["groups"]] == [
        ("same", "2026-06-10"),
        ("same", "2026-06-11"),
        ("start", "2026-05-14"),
    ]
    assert [item["observed_date"] for item in result["daily_groups"]] == ["2026-06-11"]
    assert result["report_date"] == "2026-06-11"


def test_build_aggregation_rejects_empty_snapshot_collection():
    with pytest.raises(ValueError, match="没有有效快照"):
        build_aggregation([], NOW)


def test_snapshot_kind_only_accepts_new_fixed_snapshot_paths():
    assert (
        snapshot_kind_from_name("upload/snapshots/bot-a/defense_groups.json")
        == "defense_groups"
    )
    assert (
        snapshot_kind_from_name("upload/snapshots/bot-a/strong_defenses.json")
        == "strong_defenses"
    )
    assert snapshot_kind_from_name("upload/defense_groups_bot-a_2026-06-12.json") is None


def test_invalid_latest_snapshot_does_not_fall_back_to_older_version():
    old_snapshot = _snapshot(
        "bot-a",
        1000,
        groups=[_group("old", "2026-06-11", 9)],
    )
    invalid_latest = _snapshot("bot-a", 2000, groups=[])
    invalid_latest["payload"]["groups"] = None

    with pytest.raises(ValueError, match="没有有效快照"):
        build_aggregation([old_snapshot, invalid_latest], NOW)


def test_strength_only_snapshots_do_not_publish_empty_prediction_package():
    strength_only = _snapshot(
        "bot-a",
        1000,
        strengths=[
            {
                "group_signature": "group",
                "pool_hash": "pool",
                "observed_at": 1781179200,
                "tested_count": 10,
                "defense_win_count": 5,
                "teams_json": "[[1,2,3,4,5]]",
            }
        ],
    )

    with pytest.raises(ValueError, match="没有有效防守快照"):
        build_aggregation([strength_only], NOW)
