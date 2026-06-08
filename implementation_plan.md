# 日常防守队伍数据汇总与翻译方案 (GitHub Action)

根据你的需求，我设计了如下的自动化工作流架构。该工作流将每天从 B2 拉取最新的盲打数据，然后兵分两路：一路供给 BOT 消化，另一路转化为包含中文角色名的阅读报告。

## 整体架构设计

### 1. 触发机制
- 使用 GitHub Action 定时任务（Cron `0 12 * * *`，对应北京时间每天 **20:00** 运行）。
- 提供 `workflow_dispatch` 手动触发选项，以便调试或紧急汇总。

### 2. 环境与依赖
- **语言**: Python 3.10+
- **库**: `b2sdk` (与 B2 官方交互，比手动拼接 API 更稳)，`requests` (用于获取花名册)。
- **前置准备**: 你需要在 GitHub 仓库中配置 3 个 Secrets：
  - `B2_APP_KEY_ID`: 对应 `application_key_id`
  - `B2_APP_KEY`: 对应 `application_key`
  - `B2_BUCKET_NAME`: 填入 `pcrjjc`

### 3. 数据处理脚本 (`.github/scripts/aggregate_b2.py`)
**A. 下载与聚合**
- 连接 B2，列出 `upload/` 前缀下的文件。
- 筛选**最后修改时间在过去 24 小时内**的 `defense_groups` 和 `strong_defenses` 文件，并下载到本地临时文件夹。
- **3队组合数据 (`defense_groups`)**：遍历所有 JSON 中的 `groups` 数组，根据 `group_signature` 进行合并。合并逻辑为：累加 `encounter_count`，取最小的 `first_seen_at` 和最大的 `last_seen_at`。
- **防守强度数据 (`strong_defenses`)**：遍历所有 JSON 中的 `strong_defenses` 数组。
  > **数据释疑：**经过代码溯源，数据库中记录的 `defense_win_rate` 实际上是**预设进攻队伍的胜率**（即进攻成功率）。
  > 因此，防守方越**强**，这个胜率数值就越**低**。
  > 合并逻辑为：以 `group_signature` 为键，合并多台机器人的同阵容测试次数 `tested_count` 和 `defense_win_count`，然后重新计算总的进攻方胜率。

**B. 角色站位排序与翻译**
- **加载站位表 (`SEARCH_AREA_WIDTH`)**：
  1. 优先尝试从 `https://raw.githubusercontent.com/niconiconiiiiiiiii/data-sync-tool/refs/heads/main/CN_pcr_data.py` 获取角色站位值。
  2. 若获取失败或该角色不存在，则回退从 `https://raw.githubusercontent.com/niconiconiiiiiiiii/data-sync-tool/refs/heads/main/JP_pcr_data.py` 获取。
- **加载别名表 (`CHARA_NICKNAME`)**：
  从 `https://raw.githubusercontent.com/cc004/autopcr/refs/heads/main/autopcr/util/pcr_data.py` 获取。
- **排序与转换规则应用**: 
  - 遍历防守数组，解析 `teams_json` 里的 6 位数字 ID。
  - 对于每一队内的 5 名角色，**首先按照 `SEARCH_AREA_WIDTH`（站位值从小到大）进行排序**，如果站位相同则按 ID 排序（对齐 Python 和 Go 版本的逻辑）。
  - 取 6位 ID 的前 4 位（`unit_id // 100`）去别名表中查询中文名称。若查不到则直接保留 6 位 ID 原样。
  - 将排序、翻译好的 5 个角色名合并为空格分隔的字符串输出到人类报表。

**C. 输出生成**
1. **机器版 (`aggregated_defense_groups.json`)**: 包含版本号 `schema_version: 1` 和聚合后的 3队防守池（供 BOT 自动更新暗牌推断库）。
2. **人类版 (以 `YYYY-MM-DD` 命名的每日报表)**: 
   - **模块一：3队组合常见防守 (`YYYY-MM-DD_艾防.csv`)**
     采用 **CSV 逗号分隔文件** 按遭遇次数 (`encounter_count`) **降序**排列。可以直接用 Excel 打开，方便鼠标拖拽纯净复制队伍成员，不含乱七八糟的格式符号。
     表头字段：`遭遇频次, 队伍1, 队伍2, 队伍3`
     内容示例：`15, "初音 咲恋 狗拳 羊驼 黑骑", "空花 望 妹法 似似花 圣母", "布丁 妹弓 充电宝 瞎子 雪"`

   - **模块二：高强度防守 (`YYYY-MM-DD_人类高质量防守.csv`)**
     采用 **CSV 逗号分隔文件** 按 `defense_win_rate`（进攻方胜率）**升序**排列（胜率越低，说明防守越难啃）。自动过滤掉进攻方胜率超过 `70%` 的弱防守，以及测试总次数少于 10 次的记录。
     表头字段：`进攻方胜率, 被模拟次数, 防守队伍构成`
     内容示例：`10.5%, 152, "羊驼 裁缝 扇子 深月 咲恋"`

### 4. 数据发布路由
- **人类版**：脚本运行后，生成的这两个 CSV 报表将通过 Github Action 直接推送到本仓库的 `reports/` 目录下。
- **BOT版**：通过 `softprops/action-gh-release` 将 `aggregated_defense_groups.json` 作为附件挂载到一个滚动更新的 `shared-data-latest` Release 标签上。BOT 配置 `download_url` 直接指向该 Release 的固定下载直链，实现提供给 BOT 自动更新暗牌库的纯净 JSON 数据，以及供人类阅读参考的高质量防守和常见防守组合。

## 常见问题解答 (FAQ)

> **Q1: 上传的数据中为什么会有 `group_signature` 和 `teams_json` 这 2 个高度相似的信息？**
> 
> **A**: 它们承担着完全不同的职责：
> - **`group_signature` (用于去重和聚合的唯一索引)**：它是通过把队伍内角色强行排序、然后把 3 支队伍也强行按字符串字典序排好后拼接出来的一串文本（如 `100101,100201|...|...`）。不管你在游戏里把防守队怎么互换位置，它们生成的 `group_signature` 永远是一样的。它被用作数据库的 **主键 (Primary Key)**，用于高效地进行 `GROUP BY` 把相同的防守合并、累加遭遇次数。
> - **`teams_json` (用于复原队伍架构的真实数据)**：它是一个完整的二维 JSON 数组（如 `[[100101, ...], [100501, ...], ...]`），严格保留了“第一队是谁、第二队是谁”。有了它，机器人在下载共享数据时，直接解析 JSON 就能原封不动地还原出阵型，而不需要去痛苦地拆解 `group_signature` 字符串。

## User Review Required
> [!IMPORTANT]
> **预期的结果展示**：一旦运行成功，你将能在仓库的 `reports/` 里看到类似每日战报的 Markdown 文档；同时 BOT 的 `jjc_config.toml` 里只需要把 `download_url` 指向 GitHub Release 的最新资产链接即可自动吃到聚合后的全国大数据。

如果你对这套流程、文件放置位置以及转换规则没有异议，请点击审批通过。我将直接为你编写 YAML 流程文件和配套的 Python 脚本。
