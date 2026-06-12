from copy import deepcopy
from datetime import datetime, timedelta, timezone


BEIJING_TZ = timezone(timedelta(hours=8))
SCHEMA_VERSION = 1
WINDOW_DAYS = 30


def snapshot_kind_from_name(file_name):
    parts = str(file_name).split("/")
    if len(parts) != 4 or parts[:2] != ["upload", "snapshots"] or not parts[2]:
        return None
    if parts[3] == "defense_groups.json":
        return "defense_groups"
    if parts[3] == "strong_defenses.json":
        return "strong_defenses"
    return None


def _window(now_utc):
    now_beijing = now_utc.astimezone(BEIJING_TZ)
    window_end = now_beijing.date()
    window_start = window_end - timedelta(days=WINDOW_DAYS - 1)
    report_date = window_end - timedelta(days=1)
    return window_start, window_end, report_date


def _latest_snapshots(snapshots):
    latest = {}
    for snapshot in snapshots:
        payload = snapshot.get("payload")
        kind = snapshot.get("kind")
        if not isinstance(payload, dict) or kind not in {"defense_groups", "strong_defenses"}:
            continue
        bot_id = str(payload.get("bot_id", "")).strip()
        if not bot_id:
            continue
        try:
            uploaded_at = int(snapshot.get("upload_timestamp", 0))
        except (TypeError, ValueError):
            continue
        key = (bot_id, kind)
        current = latest.get(key)
        if current is None or uploaded_at > current["upload_timestamp"]:
            latest[key] = {
                "upload_timestamp": uploaded_at,
                "payload": payload,
            }
    valid = {}
    for key, snapshot in latest.items():
        payload = snapshot["payload"]
        kind = key[1]
        expected_field = "groups" if kind == "defense_groups" else "strong_defenses"
        if payload.get("schema_version") != SCHEMA_VERSION:
            continue
        if not isinstance(payload.get(expected_field), list):
            continue
        valid[key] = snapshot
    return valid


def _merge_groups(latest, window_start, window_end):
    merged = {}
    bots = set()
    source_uploads = 0
    for (bot_id, kind), snapshot in latest.items():
        if kind != "defense_groups":
            continue
        bots.add(bot_id)
        source_uploads += 1
        for group in snapshot["payload"]["groups"]:
            observed_date = str(group.get("observed_date", ""))
            if not window_start.isoformat() <= observed_date <= window_end.isoformat():
                continue
            signature = str(group.get("group_signature", ""))
            if not signature:
                continue
            key = (signature, observed_date)
            if key not in merged:
                merged[key] = deepcopy(group)
                continue
            current = merged[key]
            current["encounter_count"] = int(current.get("encounter_count", 0)) + int(
                group.get("encounter_count", 0)
            )
            current["first_seen_at"] = min(
                int(current.get("first_seen_at", 0)),
                int(group.get("first_seen_at", 0)),
            )
            current["last_seen_at"] = max(
                int(current.get("last_seen_at", 0)),
                int(group.get("last_seen_at", 0)),
            )
    groups = [merged[key] for key in sorted(merged)]
    return groups, bots, source_uploads


def _merge_daily_strengths(latest, report_date):
    day_start = datetime.combine(report_date, datetime.min.time(), tzinfo=BEIJING_TZ)
    day_end = day_start + timedelta(days=1)
    start_ts = int(day_start.timestamp())
    end_ts = int(day_end.timestamp())
    merged = {}
    for (_, kind), snapshot in latest.items():
        if kind != "strong_defenses":
            continue
        for sample in snapshot["payload"]["strong_defenses"]:
            observed_at = int(sample.get("observed_at", 0))
            if not start_ts <= observed_at < end_ts:
                continue
            key = (
                str(sample.get("group_signature", "")),
                str(sample.get("pool_hash", "")),
            )
            if not all(key):
                continue
            if key not in merged:
                merged[key] = deepcopy(sample)
                continue
            current = merged[key]
            current["tested_count"] = int(current.get("tested_count", 0)) + int(
                sample.get("tested_count", 0)
            )
            current["defense_win_count"] = int(
                current.get("defense_win_count", 0)
            ) + int(sample.get("defense_win_count", 0))
    return [merged[key] for key in sorted(merged)]


def build_aggregation(snapshots, now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    latest = _latest_snapshots(snapshots)
    if not latest:
        raise ValueError("B2 中没有有效快照，停止发布")
    if not any(kind == "defense_groups" for _, kind in latest):
        raise ValueError("B2 中没有有效防守快照，停止发布")

    window_start, window_end, report_date = _window(now_utc)
    groups, bots, source_uploads = _merge_groups(latest, window_start, window_end)
    daily_groups = [
        group for group in groups if group["observed_date"] == report_date.isoformat()
    ]
    daily_strengths = _merge_daily_strengths(latest, report_date)
    machine_output = {
        "schema_version": SCHEMA_VERSION,
        "aggregated_at": now_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "total_bots": len(bots),
        "source_uploads": source_uploads,
        "groups": groups,
    }
    return {
        "machine_output": machine_output,
        "report_date": report_date.isoformat(),
        "daily_groups": daily_groups,
        "daily_strong_defenses": daily_strengths,
    }
