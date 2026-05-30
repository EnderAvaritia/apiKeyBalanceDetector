# API Key 余额检测器

多提供商 API Key 余额查询工具。输入 key，输出余额，汇总到文件。

## 支持范围

| 提供商 | 查询方式 | 说明 |
|--------|---------|------|
| **DeepSeek** | `GET /user/balance` | 返回总额/充值/赠送余额 |
| **硅基流动 (SiliconFlow)** | `GET /v1/user/info` | 返回总余额/充值余额/可用余额 |
| **月之暗面 (Moonshot/Kimi)** | `GET /v1/users/me/balance` | 返回可用余额/代金券/现金 |
| **智谱AI (ZhipuAI)** | `GET /api/monitor/usage/quota/limit` | 返回额度用量/剩余/重置时间 |
| **OpenRouter** | `GET /api/v1/credits` | 需 Management Key（后台创建） |

> **不支持**: OpenAI / Groq / 通义千问 — 这些提供商没有通过 API Key 查询余额的公开接口。

## 环境要求

- Python 3.8+
- `requests` 库（如未安装：`pip install requests`）

## 快速开始

### 1. 准备配置文件

创建 `keys.txt`，每行一个 key，格式 `provider:api_key`：

```txt
# keys.txt
deepseek:sk-your-deepseek-key
siliconflow:sk-your-siliconflow-key
moonshot:moonshot-your-moonshot-key
zhipu:your-zhipu-key-here
openrouter:sk-or-your-openrouter-mgmt-key
```

> 也可以只写 key（不写 `provider:`），脚本会自动识别：
> - `moonshot-` 开头 → 月之暗面
> - `sk-or-` 开头 → OpenRouter
> - `sk-` 开头 → DeepSeek（和硅基流动共用前缀，建议显式指定 `siliconflow:`）

### 2. 运行

```bash
python balance_checker.py
```

### 其他运行方式

**交互模式** — 直接运行，按提示粘贴 key：
```bash
python balance_checker.py
```

**指定配置文件路径**：
```bash
python balance_checker.py my_keys.txt
```

## 安全说明

> 报告中 **检测结果详情区** 的 Key 会自动脱敏（保留前 6 位 + `***` + 后 4 位），
> 仅在 **速复制区** 展示完整 Key，方便直接复制使用。

## 输出示例

```
══════════════════════════════════════════════════════
                    API Key 余额检测报告
══════════════════════════════════════════════════════
  生成: 2026-05-29 14:30:22
  Key 总数: 5  |  成功: 4  |  失败: 1
──────────────────────────────────────────────────────

── DeepSeek ──────────────────────────────────────────
  #1   sk-abc***-xyz              余额: ¥8.66  ✅         0.42s
  #2   sk-def***-key              余额: ¥0.00  ❌         0.35s
  #3   sk-ghi***-key              ❌ Unauthorized

── 硅基流动 (SiliconFlow) ────────────────────────────────
  #1   sk-jkl***-key              余额: ¥88.88  ✅         0.31s

══════════════════════════════════════════════════════

                  ═ API Key 速复制区 ═
              （完整 Key，按服务商分组，组内按余额排序）

── DeepSeek ──────────────────────────────────────────
sk-abc-test-key-xyz
                 ───── 以下 Key 不可用 ─────
sk-def-zero-bal-key
sk-ghi-failed-key

── 硅基流动 (SiliconFlow) ────────────────────────────────
sk-jkl-all-good-key

══════════════════════════════════════════════════════
               ---  以上 Key 可直接选中复制  ---
══════════════════════════════════════════════════════
```

同时会在当前目录生成两份报告（存放于 `reports/` 目录下）：

| 文件 | 格式 | 说明 |
|------|------|------|
| `reports/balance_report_*.txt` | 纯文本 | 控制台同款，终端友好 |
| `reports/balance_report_*.md` | Markdown | 表格排版 + SVG 趋势图，GitHub/IDE 预览更清晰 |
| `reports/balance_chart_*.svg` | SVG | 余额趋势折线图，自动嵌入 MD 报告 |
| `balance_history.json` | JSON | 历史记录数据库（自动维护，每次运行追加） |

## 余额历史追踪

每次运行会自动记录每个 Key 的余额快照到 `balance_history.json`，并在报告中显示变化趋势。

**文本报告** — 增加"余额变化追踪"区，含火花条趋势图：
```
──────────────────────────────────────────────────────
                    余额变化追踪
──────────────────────────────────────────────────────

  sk-abc***-xyz           📈 ¥8.50 → ¥8.66（+0.16）      ▁▃▆█
  sk-def***-key           持平（¥0.00）
  sk-jkl***-key           首次查询
```

**Markdown 报告** — 增加变化表格 + SVG 折线图（零依赖，纯 Python 生成）：

```markdown
## 📊 余额变化追踪
| Key | Provider | 变化趋势 | 变化 |
|---|---|---|---|
| sk-abc***-xyz | DeepSeek | ▁▃▆█ | 📈 ¥8.50 → ¥8.66（+0.16） |

## 📈 余额趋势图
### DeepSeek
<img src="reports/balance_chart_20260531_010230_DeepSeek.svg" />
```

### 历史查看器

```bash
python history_viewer.py                  # 摘要总览
python history_viewer.py --records        # 逐条查看全部记录
python history_viewer.py --stats          # 详细统计（平均/变化/趋势）
python history_viewer.py --chart          # 生成 SVG 趋势图
python history_viewer.py --key <id>       # 按 Key 过滤
python history_viewer.py --provider <名>  # 按提供商过滤
python history_viewer.py --days <N>       # 最近 N 天
python history_viewer.py --output <file>  # 输出到文件
```

### 回填旧报告

如果 `balance_history.json` 为空（或缺少部分数据），可以从之前生成的报告文件中回填：

```bash
python backfill_history.py               # 回填所有 balance_report_*.txt
python backfill_history.py --dry-run      # 先预览，不写入
```

## 可用性判定

判定 Key 是否可用的规则：

| 条件 | 结果 | 显示 |
|------|------|------|
| 查询成功 + 余额 > 0 | ✅ 可用 | 出现在可用区域 |
| 查询成功 + 余额 = 0 | ❌ 不可用 | 分隔线下方 |
| 查询成功 + 余额 < 0 | ❌ 不可用 | 分隔线下方 |
| 查询失败（401/超时等） | ❌ 不可用 | 分隔线下方 |

## 文件结构

```
.
├── balance_checker.py              # 主程序
├── history_viewer.py               # 历史查看器
├── backfill_history.py             # 旧报告回填脚本
├── keys.txt                        # 配置文件（需自行创建）
├── keys.example.txt                # 配置文件示例
├── balance_history.json            # 历史记录数据库（自动生成）
├── reports/                        # 报告输出目录
│   ├── balance_report_*.txt        # 纯文本报告（自动生成）
│   ├── balance_report_*.md         # Markdown 报告（自动生成）
│   └── balance_chart_*.svg         # 趋势图（自动生成）
└── ...
```
