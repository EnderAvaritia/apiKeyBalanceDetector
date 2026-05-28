#!/usr/bin/env python3
"""
API Key 余额检测器
支持: DeepSeek / 硅基流动 / 月之暗面 / 智谱AI / OpenRouter
"""

import sys
import time
from datetime import datetime
from pathlib import Path

import requests


# ============================================================
# 提供商配置
# ============================================================

PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "url": "https://api.deepseek.com/user/balance",
        "auth_type": "Bearer",
        "key_prefix": "sk-",
        "parse": lambda data: {
            "available": data.get("is_available", False),
            "balances": [
                {
                    "currency": b["currency"],
                    "total": b["total_balance"],
                    "granted": b["granted_balance"],
                    "topped_up": b["topped_up_balance"],
                }
                for b in data.get("balance_infos", [])
            ],
        },
    },
    "siliconflow": {
        "label": "硅基流动 (SiliconFlow)",
        "url": "https://api.siliconflow.com/v1/user/info",
        "auth_type": "Bearer",
        "key_prefix": "sk-",
        "parse": lambda data: {
            "available": data.get("status", False),
            "balances": [
                {
                    "currency": "CNY",
                    "total": data.get("data", {}).get("totalBalance", "N/A"),
                    "charge": data.get("data", {}).get("chargeBalance", "N/A"),
                    "balance": data.get("data", {}).get("balance", "N/A"),
                }
            ],
        },
    },
    "moonshot": {
        "label": "月之暗面 (Moonshot/Kimi)",
        "url": "https://api.moonshot.cn/v1/users/me/balance",
        "auth_type": "Bearer",
        "key_prefix": "moonshot-",
        "parse": lambda data: {
            "available": True,
            "balances": [
                {
                    "available_balance": data.get("available_balance", "N/A"),
                    "voucher_balance": data.get("voucher_balance", "N/A"),
                    "cash_balance": data.get("cash_balance", "N/A"),
                }
            ],
        },
    },
    "zhipu": {
        "label": "智谱AI (ZhipuAI)",
        "url": "https://bigmodel.cn/api/monitor/usage/quota/limit",
        "auth_type": "Token",  # 智谱直接用 key，不加 Bearer 前缀
        "key_prefix": "",
        "parse": lambda data: {
            "available": data.get("success", False),
            "balances": [
                {
                    "quota": d.get("quotaName", "N/A"),
                    "used": f"{d.get('currentValue', 'N/A')} / {d.get('limitValue', 'N/A')}",
                    "remain": d.get("remainValue", "N/A"),
                    "reset": f"{d.get('resetTime', 'N/A')}小时后",
                }
                for d in data.get("data", [])
            ],
        },
    },
    "openrouter": {
        "label": "OpenRouter",
        "url": "https://openrouter.ai/api/v1/credits",
        "auth_type": "Bearer",
        "key_prefix": "sk-or-",
        "note": "需要 Management Key（非普通 API Key），在 OpenRouter 后台创建",
        "parse": lambda data: {
            "available": True,
            "balances": [
                {
                    "total_purchased": data.get("data", {}).get("total_purchased", "N/A"),
                    "total_used": data.get("data", {}).get("total_used", "N/A"),
                }
            ],
        },
    },
}


# ============================================================
# Key 自动识别（根据前缀）
# ============================================================

PREFIX_MAP = {}
for pid, cfg in PROVIDERS.items():
    prefix = cfg["key_prefix"]
    if prefix:
        PREFIX_MAP.setdefault(prefix, []).append(pid)


def detect_provider(api_key: str) -> str | None:
    """根据 key 前缀猜测提供商。前缀越精确匹配优先级越高。"""
    exact_prefixes = sorted(PREFIX_MAP.keys(), key=len, reverse=True)
    for prefix in exact_prefixes:
        if api_key.startswith(prefix):
            candidates = PREFIX_MAP[prefix]
            if len(candidates) == 1:
                return candidates[0]
            # sk- 有歧义: deepseek vs siliconflow → 先试 deepseek
            if "deepseek" in candidates:
                return "deepseek"
            return candidates[0]
    return None


# ============================================================
# 查询逻辑
# ============================================================

def query_balance(provider_id: str, api_key: str) -> dict:
    """查询单个 Key 的余额，返回结果 dict。"""
    cfg = PROVIDERS[provider_id]
    url = cfg["url"]
    headers = {"Content-Type": "application/json"}

    if cfg["auth_type"] == "Bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        headers["Authorization"] = api_key

    start = time.time()
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        elapsed = round(time.time() - start, 2)
        if resp.status_code == 200:
            data = resp.json()
            parsed = cfg["parse"](data)
            return {
                "provider": cfg["label"],
                "provider_id": provider_id,
                "key": api_key,
                "status": "ok",
                "elapsed": elapsed,
                **parsed,
            }
        else:
            return {
                "provider": cfg["label"],
                "provider_id": provider_id,
                "key": api_key,
                "status": "error",
                "code": resp.status_code,
                "message": resp.text[:200],
                "elapsed": elapsed,
            }
    except requests.exceptions.Timeout:
        return {
            "provider": cfg["label"],
            "provider_id": provider_id,
            "key": api_key,
            "status": "error",
            "message": "请求超时",
        }
    except requests.exceptions.RequestException as e:
        return {
            "provider": cfg["label"],
            "provider_id": provider_id,
            "key": api_key,
            "status": "error",
            "message": str(e),
        }

# ============================================================
# 结果排序 & 分组
# ============================================================

def _extract_balance_value(result: dict) -> float:
    """从结果中提取数值型余额用于排序。失败或未知返回 -1。"""
    if result["status"] != "ok":
        return -1.0
    bls = result.get("balances", [])
    if not bls:
        return -1.0
    b = bls[0]
    pid = result.get("provider_id", "")
    try:
        if pid == "deepseek":
            return float(b.get("total", "0"))
        elif pid == "siliconflow":
            return float(b.get("total", "0"))
        elif pid == "moonshot":
            return float(b.get("available_balance", "0"))
        elif pid == "zhipu":
            return float(b.get("remain", "0"))
        elif pid == "openrouter":
            purchased = float(b.get("total_purchased", "0"))
            used = float(b.get("total_used", "0"))
            return purchased - used
        # fallback: 尝试第一个数值字段
        for v in b.values():
            try:
                return float(v)
            except (ValueError, TypeError):
                continue
        return -1.0
    except (ValueError, TypeError):
        return -1.0


def _is_key_usable(result: dict) -> bool:
    """
    判断 Key 是否真正可用：
    - API 查询必须成功
    - 余额必须 > 0
    """
    if result["status"] != "ok":
        return False
    balance = _extract_balance_value(result)
    if balance <= 0:
        return False
    return True


def _one_line_summary(result: dict) -> str:
    """生成一行简洁的余额摘要。会附加 ❌ 表示不可用（余额为 0 或查询失败）。"""
    if result["status"] != "ok":
        return "查询失败"

    bls = result.get("balances", [])
    if not bls:
        return "无数据"
    b = bls[0]
    pid = result.get("provider_id", "")

    usable = _is_key_usable(result)
    badge = "  ✅" if usable else "  ❌"

    if pid == "deepseek":
        return f"余额: ¥{b['total']}{badge}"
    elif pid == "siliconflow":
        return f"余额: ¥{b['total']}{badge}"
    elif pid == "moonshot":
        return f"可用: ¥{b['available_balance']}{badge}"
    elif pid == "zhipu":
        return f"剩余: {b.get('remain', 'N/A')}{badge}"
    elif pid == "openrouter":
        remaining = float(b.get("total_purchased", 0)) - float(b.get("total_used", 0))
        return f"剩余: ${remaining:.2f}{badge}"
    else:
        # fallback: 拼接显示
        parts = " | ".join(f"{k}: {v}" for k, v in b.items())
        return parts[:60]


def _sort_and_group(results: list[dict]) -> list[tuple[str, list[dict]]]:
    """
    按 provider 分组，组内按余额从高到低排序。
    返回 [(provider_label, [result, ...]), ...]
    """
    groups: dict[str, list[dict]] = {}
    for r in results:
        groups.setdefault(r["provider"], []).append(r)

    # 每组内按余额降序
    for label in groups:
        groups[label].sort(key=_extract_balance_value, reverse=True)

    # 确定 provider 显示顺序：正常 provider 在前，失败/未知在后
    normal_order = ["DeepSeek", "硅基流动 (SiliconFlow)", "月之暗面 (Moonshot/Kimi)", "智谱AI (ZhipuAI)", "OpenRouter"]
    ordered = []
    tail = []
    for label in normal_order:
        if label in groups:
            ordered.append((label, groups[label]))
    for label in list(groups.keys()):
        if label not in normal_order:
            tail.append((label, groups[label]))

    return ordered + tail


# ============================================================
# 结果格式化
# ============================================================

def format_summary(results: list[dict]) -> str:
    """生成汇总文本（控制台 + 文件用），按余额排序 + 服务商分组 + 速复制区。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok_count = sum(1 for r in results if r["status"] == "ok")
    err_count = sum(1 for r in results if r["status"] == "error")
    W = 54

    lines = []

    # ── 头部 ──
    lines.append("═" * W)
    lines.append(f"{'API Key 余额检测报告':^{W}}")
    lines.append("═" * W)
    lines.append(f"  生成: {now}")
    lines.append(f"  Key 总数: {len(results)}  |  成功: {ok_count}  |  失败: {err_count}")
    lines.append("─" * W)
    lines.append("")

    # ── 按服务商分组输出 ──
    grouped = _sort_and_group(results)
    for gi, (provider_label, items) in enumerate(grouped):
        if gi > 0:
            lines.append("")
        is_error_group = provider_label in ("未知", "查询失败")
        if is_error_group:
            lines.append(f"── {provider_label} ─{'─' * (W - 6 - len(provider_label))}")
        else:
            lines.append(f"── {provider_label} ─{'─' * (W - 6 - len(provider_label))}")

        for idx, r in enumerate(items, 1):
            masked = r["key"]
            if r["status"] == "ok":
                summary = _one_line_summary(r)
                elapsed = r.get("elapsed", "?")
                lines.append(f"  #{idx:<2d}  {masked:<24s}  {summary:<28s}  {elapsed}s")
            else:
                err_msg = r.get("message", "未知错误")
                # 截短错误信息
                if len(err_msg) > 40:
                    err_msg = err_msg[:37] + "..."
                lines.append(f"  #{idx:<2d}  {masked:<24s}  ❌ {err_msg}")

    # ── 速复制区（按服务商分组，可用/不可用分隔） ──
    lines.append("")
    lines.append("═" * W)
    lines.append("")
    lines.append(f"{'═ API Key 速复制区 ═':^{W}}")
    lines.append(f"{'（完整 Key，按服务商分组，组内按余额排序）':^{W}}")
    lines.append("")

    for gi, (provider_label, items) in enumerate(grouped):
        if gi > 0:
            lines.append("")
        lines.append(f"── {provider_label} ─{'─' * (W - 6 - len(provider_label))}")

        ok_items = [r for r in items if _is_key_usable(r)]
        err_items = [r for r in items if not _is_key_usable(r)]

        for r in ok_items:
            lines.append(r["key"])
        if ok_items and err_items:
            lines.append(f"{'───── 以下 Key 不可用 ─────':^{W}}")
        for r in err_items:
            lines.append(r["key"])

    lines.append("")
    lines.append("═" * W)
    lines.append(f"{'---  以上 Key 可直接选中复制  ---':^{W}}")
    lines.append("═" * W)

    return "\n".join(lines)


def format_summary_md(results: list[dict]) -> str:
    """生成 Markdown 格式报告，更好的可读性。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok_count = sum(1 for r in results if r["status"] == "ok")
    err_count = sum(1 for r in results if r["status"] == "error")
    grouped = _sort_and_group(results)

    md = []

    # ── 头部 ──
    md.append("# API Key 余额检测报告")
    md.append("")
    md.append(f"**生成时间**: {now}")
    md.append(f"**Key 总数**: {len(results)} | **成功**: {ok_count} | **失败**: {err_count}")
    md.append("")
    md.append("---")
    md.append("")

    # ── 按服务商分组输出 ──
    for provider_label, items in grouped:
        md.append(f"## {provider_label}")
        md.append("")

        # 查询详情表格
        md.append("| # | Key | 余额状态 | 耗时 |")
        md.append("|---|-----|---------|:----:|")
        for idx, r in enumerate(items, 1):
            masked = r["key"]
            if r["status"] == "ok":
                summary = _one_line_summary(r)
                elapsed = f"{r.get('elapsed', '?')}s"
                md.append(f"| {idx} | `{masked}` | {summary} | {elapsed} |")
            else:
                err_msg = r.get("message", "未知错误")
                if len(err_msg) > 50:
                    err_msg = err_msg[:47] + "..."
                md.append(f"| {idx} | `{masked}` | ❌ {err_msg} | - |")
        md.append("")

    # ── 速复制区（按服务商分组，可用/不可用分隔） ──
    md.append("---")
    md.append("")
    md.append("## 📋 API Key 速复制区")
    md.append("")
    md.append("> 完整 Key，按服务商分组，组内按余额从高到低排序。直接复制所需区块即可。")
    md.append("")

    for provider_label, items in grouped:
        md.append(f"### {provider_label}")
        md.append("")

        ok_items = [r for r in items if _is_key_usable(r)]
        err_items = [r for r in items if not _is_key_usable(r)]

        md.append("```")
        for r in ok_items:
            md.append(r["key"])
        if ok_items and err_items:
            md.append("")
            md.append("# ---- 以下 Key 不可用 ----")
            md.append("")
        for r in err_items:
            md.append(r["key"])
        md.append("```")
        md.append("")

    return "\n".join(md)


# ============================================================
# 配置文件读写
# ============================================================

CONFIG_FILE = "keys.txt"

def load_keys_from_file(filepath: str) -> list[tuple[str, str]]:
    """
    从文件读取 Key，每行格式: provider:api_key
    空行和 # 开头的行被忽略。
    """
    entries = []
    path = Path(filepath)
    if not path.exists():
        print(f"⚠  ️ 找不到配置文件: {filepath}")
        return entries

    for line in path.read_text(encoding="utf-8").strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if ":" in line:
            provider, _, key = line.partition(":")
            provider = provider.strip().lower()
            key = key.strip()
            if key:
                entries.append((provider, key))
        else:
            # 没写 provider，自动检测
            entries.append(("auto", line.strip()))

    return entries


def save_keys_to_file(entries: list[tuple[str, str]]):
    """将 Key 保存到配置文件。"""
    path = Path(CONFIG_FILE)
    lines = [
        "# API Key 配置文件",
        f"# 格式: provider:api_key",
        f"# 支持: {', '.join(PROVIDERS.keys())}",
        f"# 不写 provider 则自动识别",
        f"# 更新于: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for provider, key in entries:
        lines.append(f"{provider}:{key}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"✔  Key 已保存到 {CONFIG_FILE}")


# ============================================================
# 交互模式
# ============================================================

def interactive_input() -> list[tuple[str, str]]:
    """交互式输入 Key。"""
    print("🔑 请输入 API Key（每行一个），输入空行结束：")
    print("   格式1: provider:api_key（推荐）")
    print("   格式2: api_key（自动识别提供商）")
    print(f"   支持的提供商: {', '.join(PROVIDERS.keys())}")
    print("   ─" * 18)

    entries = []
    while True:
        try:
            line = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            break
        if line.startswith("#"):
            continue

        if ":" in line:
            provider, _, key = line.partition(":")
            entries.append((provider.strip().lower(), key.strip()))
        else:
            entries.append(("auto", line.strip()))

    if entries:
        save = input(f"\n  是否保存到 {CONFIG_FILE}? (y/N): ").strip().lower()
        if save == "y":
            save_keys_to_file(entries)

    return entries


# ============================================================
# Main
# ============================================================

def main():
    entries: list[tuple[str, str]] = []

    # 1) 尝试从命令行参数读取
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        entries = load_keys_from_file(filepath)
    else:
        # 2) 尝试从默认配置文件读取
        entries = load_keys_from_file(CONFIG_FILE)

    # 3) 如果都没有，进入交互模式
    if not entries:
        entries = interactive_input()

    if not entries:
        print("❌ 没有可用的 API Key，退出。")
        sys.exit(1)

    # ============================================================
    # 解析并去重
    # ============================================================
    queries: list[tuple[str, str]] = []  # (provider_id, api_key)
    seen_keys = set()

    for provider, key in entries:
        if key in seen_keys:
            continue
        seen_keys.add(key)

        if provider == "auto":
            detected = detect_provider(key)
            if detected:
                queries.append((detected, key))
            else:
                # 自动识别失败，标记未知
                queries.append(("unknown", key))
        elif provider in PROVIDERS:
            queries.append((provider, key))
        else:
            print(f"⚠  未知提供商 '{provider}'，跳过 {key}")
            print(f"   支持的: {', '.join(PROVIDERS.keys())}")

    if not queries:
        print("❌ 没有可查询的 Key，退出。")
        sys.exit(1)

    # ============================================================
    # 查询
    # ============================================================
    print(f"\n🔍 开始查询 {len(queries)} 个 Key ...\n")

    results = []
    for provider_id, api_key in queries:
        if provider_id == "unknown":
            results.append({
                "provider": "未知",
                "provider_id": "unknown",
                "key": api_key,
                "status": "error",
                "message": "无法自动识别提供商，请在 key 前加上 provider: 前缀",
            })
            continue

        note = PROVIDERS[provider_id].get("note", "")
        if note:
            print(f"  ⓘ  {PROVIDERS[provider_id]['label']}: {note}")

        print(f"  → 查询 {PROVIDERS[provider_id]['label']} ... ", end="", flush=True)
        result = query_balance(provider_id, api_key)
        status_icon = "✔" if result["status"] == "ok" else "✘"
        print(f"{status_icon} ({result.get('elapsed', '?')}s)")
        results.append(result)

    # ============================================================
    # 输出
    # ============================================================
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # 文本版（控制台 + .txt）
    report_txt = format_summary(results)
    print("\n" + report_txt)

    txt_file = f"balance_report_{timestamp}.txt"
    Path(txt_file).write_text(report_txt, encoding="utf-8")
    print(f"\n📄 文本报告: {txt_file}")

    # Markdown 版（.md）
    report_md = format_summary_md(results)
    md_file = f"balance_report_{timestamp}.md"
    Path(md_file).write_text(report_md, encoding="utf-8")
    print(f"📝 Markdown 报告: {md_file}")


if __name__ == "__main__":
    main()
