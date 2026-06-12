import ast
import csv
import io
import json
import os
import sys
from pathlib import Path

import requests
from b2sdk.v2 import B2Api, InMemoryAccountInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aggregation import build_aggregation, snapshot_kind_from_name


def fetch_chara_names():
    url = (
        "https://raw.githubusercontent.com/Ice-Cirno/HoshinoBot/"
        "master/hoshino/modules/priconne/pcr_data.py"
    )
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        code = response.text
        start_index = code.find("CHARA_NICKNAME")
        dict_start = code.find("{", start_index)
        if start_index < 0 or dict_start < 0:
            return {}
        depth = 0
        for index in range(dict_start, len(code)):
            if code[index] == "{":
                depth += 1
            elif code[index] == "}":
                depth -= 1
                if depth == 0:
                    return ast.literal_eval(code[dict_start : index + 1])
    except Exception as exc:
        print(f"角色别名加载失败，将使用角色 ID：{exc}")
    return {}


def translate_team(team, nicknames):
    names = []
    for raw_unit_id in team:
        unit_id = int(raw_unit_id)
        aliases = nicknames.get(unit_id // 100)
        names.append(str(aliases[0]) if aliases else str(unit_id))
    return " ".join(names)


def translate_teams_json(teams_json, nicknames):
    try:
        teams = json.loads(teams_json)
        translated = [translate_team(team, nicknames) if team else "(暗牌)" for team in teams]
        while len(translated) < 3:
            translated.append("(暗牌)")
        return translated[:3]
    except Exception:
        return ["", "", ""]


def translate_single_team_json(teams_json, nicknames):
    try:
        teams = json.loads(teams_json)
        return translate_team(teams[0], nicknames) if teams and teams[0] else ""
    except Exception:
        return ""


def download_json(bucket, file_version):
    buffer = io.BytesIO()
    bucket.download_file_by_id(file_version.id_).save(buffer)
    buffer.seek(0)
    return json.loads(buffer.read().decode("utf-8"))


def load_snapshots(bucket):
    snapshots = []
    for file_version, _ in bucket.ls(
        folder_to_list="upload/snapshots",
        latest_only=False,
        recursive=True,
    ):
        kind = snapshot_kind_from_name(file_version.file_name)
        if kind is None:
            continue
        bot_id = file_version.file_name.split("/")[2]
        if getattr(file_version, "action", "upload") != "upload":
            snapshots.append(
                {
                    "kind": kind,
                    "upload_timestamp": file_version.upload_timestamp,
                    "payload": {"bot_id": bot_id},
                }
            )
            continue
        try:
            payload = download_json(bucket, file_version)
        except Exception as exc:
            print(f"跳过无法读取的快照 {file_version.file_name}: {exc}")
            payload = {"bot_id": bot_id}
        snapshots.append(
            {
                "kind": kind,
                "upload_timestamp": file_version.upload_timestamp,
                "payload": payload,
            }
        )
    return snapshots


def write_machine_output(machine_output):
    output_path = REPO_ROOT / "aggregated_defense_groups.json"
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(machine_output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    json.loads(temporary_path.read_text(encoding="utf-8"))
    temporary_path.replace(output_path)
    print(
        f"导出 {output_path.name}：{len(machine_output['groups'])} 条，"
        f"{machine_output['total_bots']} 台 Bot"
    )


def write_daily_reports(result, nicknames):
    reports_dir = REPO_ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    report_date = result["report_date"]

    defense_path = reports_dir / f"{report_date}_艾防.csv"
    groups = sorted(
        result["daily_groups"],
        key=lambda item: int(item.get("encounter_count", 0)),
        reverse=True,
    )
    with defense_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["遭遇频次", "队伍1", "队伍2", "队伍3"])
        for group in groups:
            writer.writerow(
                [
                    group["encounter_count"],
                    *translate_teams_json(group["teams_json"], nicknames),
                ]
            )

    strength_path = reports_dir / f"{report_date}_人类高质量防守.csv"
    filtered_strengths = []
    for sample in result["daily_strong_defenses"]:
        tested_count = int(sample.get("tested_count", 0))
        if tested_count < 10:
            continue
        calculated_win_rate = int(sample.get("defense_win_count", 0)) / tested_count
        if calculated_win_rate > 0.70:
            continue
        filtered_strengths.append((calculated_win_rate, sample))
    filtered_strengths.sort(key=lambda item: item[0])
    with strength_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["进攻方胜率", "被模拟次数", "防守队伍构成"])
        for rate, sample in filtered_strengths:
            writer.writerow(
                [
                    f"{rate * 100:.1f}%",
                    sample["tested_count"],
                    translate_single_team_json(sample["teams_json"], nicknames),
                ]
            )

    print(f"导出昨日日报：{defense_path.name}、{strength_path.name}")


def aggregate_data():
    app_key_id = os.environ.get("B2_APP_KEY_ID")
    app_key = os.environ.get("B2_APP_KEY")
    bucket_name = os.environ.get("B2_BUCKET_NAME")
    if not all([app_key_id, app_key, bucket_name]):
        raise RuntimeError(
            "缺少 B2 环境变量：B2_APP_KEY_ID、B2_APP_KEY、B2_BUCKET_NAME"
        )

    info = InMemoryAccountInfo()
    b2_api = B2Api(info)
    b2_api.authorize_account("production", app_key_id, app_key)
    bucket = b2_api.get_bucket_by_name(bucket_name)

    snapshots = load_snapshots(bucket)
    result = build_aggregation(snapshots)
    nicknames = fetch_chara_names()
    write_machine_output(result["machine_output"])
    write_daily_reports(result, nicknames)


if __name__ == "__main__":
    aggregate_data()
