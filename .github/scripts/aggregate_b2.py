import os
import json
import csv
import time
import ast
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import requests
from b2sdk.v2 import InMemoryAccountInfo, B2Api

# ========== 1. 角色名翻译加载 ==========
def fetch_chara_names():
    chara_nickname = {}
    urls = [
        "https://raw.githubusercontent.com/cc004/autopcr/refs/heads/main/autopcr/util/pcr_data.py",
        "https://raw.githubusercontent.com/Ice-Cirno/HoshinoBot/master/hoshino/modules/priconne/pcr_data.py",
    ]
    for url in urls:
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                # 简单用 ast 解析 dict
                code = resp.text
                start_idx = code.find("CHARA_NICKNAME")
                if start_idx != -1:
                    dict_start = code.find("{", start_idx)
                    # 寻找结束的 }
                    stack = 0
                    dict_end = -1
                    for i in range(dict_start, len(code)):
                        if code[i] == '{': stack += 1
                        elif code[i] == '}':
                            stack -= 1
                            if stack == 0:
                                dict_end = i + 1
                                break
                    if dict_end != -1:
                        dict_str = code[dict_start:dict_end]
                        chara_nickname = ast.literal_eval(dict_str)
                        print(f"成功加载角色别名库，共 {len(chara_nickname)} 个角色")
                        break
        except Exception as e:
            print(f"加载别名库失败: {e}")
    return chara_nickname

CHARA_NICKNAME = fetch_chara_names()

def fetch_search_area_width():
    search_area_width = {}
    urls = [
        "https://raw.githubusercontent.com/niconiconiiiiiiiii/data-sync-tool/refs/heads/main/CN_pcr_data.py",
        "https://raw.githubusercontent.com/niconiconiiiiiiiii/data-sync-tool/refs/heads/main/JP_pcr_data.py"
    ]
    for url in urls:
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                code = resp.text
                start_idx = code.find("SEARCH_AREA_WIDTH")
                if start_idx != -1:
                    dict_start = code.find("{", start_idx)
                    stack = 0
                    dict_end = -1
                    for i in range(dict_start, len(code)):
                        if code[i] == '{': stack += 1
                        elif code[i] == '}':
                            stack -= 1
                            if stack == 0:
                                dict_end = i + 1
                                break
                    if dict_end != -1:
                        dict_str = code[dict_start:dict_end]
                        search_area_width = ast.literal_eval(dict_str)
                        print(f"成功从 {url} 加载站位库，共 {len(search_area_width)} 个角色站位")
                        break
        except Exception as e:
            print(f"加载站位库失败: {e}")
    return search_area_width

SEARCH_AREA_WIDTH = fetch_search_area_width()

def translate_team(team_list):
    # 根据 SEARCH_AREA_WIDTH(6位ID) 排序，无数据的放最后(9999)，相同站位按ID升序
    sorted_team = sorted(
        team_list,
        key=lambda uid: (SEARCH_AREA_WIDTH.get(int(uid), 9999) if str(uid).isdigit() else 9999, int(uid) if str(uid).isdigit() else 0)
    )
    names = []
    for uid in sorted_team:
        try:
            unit_id = int(uid)
            base_id = unit_id // 100
            if base_id in CHARA_NICKNAME and CHARA_NICKNAME[base_id]:
                val = CHARA_NICKNAME[base_id]
                names.append(val[0] if isinstance(val, list) else val)
            else:
                names.append(str(unit_id))
        except:
            names.append(str(uid))
    return " ".join(names)

def translate_teams_json(teams_json_str):
    try:
        teams = json.loads(teams_json_str)
        translated = []
        for team in teams:
            if not team:
                translated.append("(暗牌)")
            else:
                translated.append(translate_team(team))
        # 补齐3队（针对3队组合表）
        while len(translated) < 3:
            translated.append("(暗牌)")
        return translated
    except:
        return ["", "", ""]

def translate_single_team_json(teams_json_str):
    try:
        teams = json.loads(teams_json_str)
        if teams and len(teams) > 0 and teams[0]:
            return translate_team(teams[0])
    except:
        pass
    return ""


# ========== 2. 从 B2 获取数据 ==========
def aggregate_data():
    app_key_id = os.environ.get("B2_APP_KEY_ID")
    app_key = os.environ.get("B2_APP_KEY")
    bucket_name = os.environ.get("B2_BUCKET_NAME")

    if not all([app_key_id, app_key, bucket_name]):
        print("缺少 B2 相关的环境变量 (B2_APP_KEY_ID, B2_APP_KEY, B2_BUCKET_NAME)")
        return

    info = InMemoryAccountInfo()
    b2_api = B2Api(info)
    b2_api.authorize_account("production", app_key_id, app_key)
    bucket = b2_api.get_bucket_by_name(bucket_name)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    cutoff_ts = cutoff.timestamp() * 1000  # B2 uses milliseconds

    groups_merged = {}
    strong_merged = {}

    print(f"拉取过去 24 小时的上传记录 (cutoff: {cutoff})")
    
    # 获取 upload/ 目录下的所有文件
    for file_version, _ in bucket.ls("upload/"):
        if file_version.upload_timestamp > cutoff_ts:
            filename = file_version.file_name
            try:
                import io
                downloaded_file = bucket.download_file_by_id(file_version.id_)
                buf = io.BytesIO()
                downloaded_file.save(buf)
                content = buf.getvalue().decode('utf-8')
                data = json.loads(content)

                # 处理 3队防守组合
                if "defense_groups_" in filename and "groups" in data:
                    for g in data["groups"]:
                        sig = g["group_signature"]
                        if sig not in groups_merged:
                            groups_merged[sig] = g
                        else:
                            groups_merged[sig]["encounter_count"] += g["encounter_count"]
                            groups_merged[sig]["first_seen_at"] = min(groups_merged[sig]["first_seen_at"], g.get("first_seen_at", 0))
                            groups_merged[sig]["last_seen_at"] = max(groups_merged[sig]["last_seen_at"], g.get("last_seen_at", 0))

                # 处理 单队高强度防守
                elif "strong_defenses_" in filename and "strong_defenses" in data:
                    for s in data["strong_defenses"]:
                        sig = s["group_signature"]
                        if sig not in strong_merged:
                            strong_merged[sig] = s
                        else:
                            strong_merged[sig]["tested_count"] += s["tested_count"]
                            strong_merged[sig]["defense_win_count"] += s["defense_win_count"]
            except Exception as e:
                print(f"处理文件 {filename} 时出错: {e}")

    # ========== 3. 处理并写入机器版 JSON ==========
    aggregated_groups_list = list(groups_merged.values())
    machine_output = {
        "schema_version": 1,
        "aggregated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "groups": aggregated_groups_list
    }
    with open("aggregated_defense_groups.json", "w", encoding="utf-8") as f:
        json.dump(machine_output, f, ensure_ascii=False, indent=2)
    print(f"导出 aggregated_defense_groups.json，共 {len(aggregated_groups_list)} 组合")

    # ========== 4. 处理并写入人类版 CSV ==========
    os.makedirs("reports", exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")

    # A. 艾防.csv (按 encounter_count 降序)
    groups_sorted = sorted(aggregated_groups_list, key=lambda x: x["encounter_count"], reverse=True)
    ai_defense_path = f"reports/{today_str}_艾防.csv"
    with open(ai_defense_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["遭遇频次", "队伍1", "队伍2", "队伍3"])
        for g in groups_sorted:
            t1, t2, t3 = translate_teams_json(g["teams_json"])
            writer.writerow([g["encounter_count"], t1, t2, t3])
    print(f"导出 {ai_defense_path}，共 {len(groups_sorted)} 条")

    # B. 人类高质量防守.csv (重新计算胜率，按胜率升序，过滤 胜率>70% 或 tested_count<10)
    strong_list = list(strong_merged.values())
    filtered_strong = []
    for s in strong_list:
        tested = s["tested_count"]
        if tested < 10:
            continue
        # 重新计算进攻方胜率
        attack_win_rate = s["defense_win_count"] / tested
        if attack_win_rate > 0.70:
            continue
        s["calculated_win_rate"] = attack_win_rate
        filtered_strong.append(s)

    filtered_strong.sort(key=lambda x: x["calculated_win_rate"])
    high_quality_path = f"reports/{today_str}_人类高质量防守.csv"
    with open(high_quality_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["进攻方胜率", "候选队伍数", "防守队伍构成"])
        for s in filtered_strong:
            rate_str = f"{s['calculated_win_rate'] * 100:.1f}%"
            team_str = translate_single_team_json(s["teams_json"])
            writer.writerow([rate_str, s["tested_count"], team_str])
    print(f"导出 {high_quality_path}，共 {len(filtered_strong)} 条")


if __name__ == "__main__":
    aggregate_data()
