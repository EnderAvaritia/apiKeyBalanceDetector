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

## 输出示例

```
══════════════════════════════════════════════════════
                    API Key 余额检测报告
══════════════════════════════════════════════════════
  生成: 2026-05-29 14:30:22
  Key 总数: 5  |  成功: 4  |  失败: 1
──────────────────────────────────────────────────────

── DeepSeek ──────────────────────────────────────────
  #1   sk-abc-test-key-xyz        余额: ¥8.66  ✅         0.42s
  #2   sk-def-zero-bal-key        余额: ¥0.00  ❌         0.35s
  #3   sk-ghi-failed-key          ❌ Unauthorized

── 硅基流动 (SiliconFlow) ────────────────────────────────
  #1   sk-jkl-all-good-key        余额: ¥88.88  ✅         0.31s

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

同时会在当前目录生成两份报告：

| 文件 | 格式 | 说明 |
|------|------|------|
| `balance_report_20260529_143022.txt` | 纯文本 | 控制台同款，终端友好 |
| `balance_report_20260529_143022.md` | Markdown | 表格排版，GitHub/IDE 预览更清晰 |

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
├── keys.txt                        # 配置文件（需自行创建）
├── keys.example.txt                # 配置文件示例
├── balance_report_*.txt            # 纯文本报告（自动生成）
└── balance_report_*.md             # Markdown 报告（自动生成）
```
