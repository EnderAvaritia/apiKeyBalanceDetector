#!/usr/bin/env python3
"""
API Key 余额检测器
支持: DeepSeek / 硅基流动 / 月之暗面 / 智谱AI / OpenRouter
"""

import json
import sys
import time
from collections import defaultdict
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

def mask_key(key: str, keep_head: int = 6, keep_tail: int = 4) -> str:
    """
    对 API Key 做脱敏处理，只保留首尾若干字符，中间用 *** 替代。
    短 Key（长度 ≤ keep_head + keep_tail）则只保留首尾各一半字符。
    """
    key = key.strip()
    if len(key) <= keep_head + keep_tail:
        # 太短，只保留首尾各一半
        mid = len(key) // 2
        return key[:mid] + "***" + key[mid:]
    return key[:keep_head] + "***" + key[-keep_tail:]


# ============================================================
# 历史记录 & 余额变化追踪
# ============================================================

import hashlib
import json

HISTORY_FILE = "balance_history.json"
REPORTS_DIR = "reports"

# Unicode 火花条字符（8 级）
_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _key_id(api_key: str) -> str:
    """用 SHA256 前 12 位标识一个 Key（不存完整 Key）。"""
    return hashlib.sha256(api_key.encode()).hexdigest()[:12]


def _extract_num_balance(result: dict) -> float | None:
    """从结果中提取数值型余额，失败返回 None。"""
    if result["status"] != "ok":
        return None
    bls = result.get("balances", [])
    if not bls:
        return None
    b = bls[0]
    pid = result.get("provider_id", "")
    try:
        if pid == "deepseek":
            return float(b.get("total", 0))
        elif pid == "siliconflow":
            return float(b.get("total", 0))
        elif pid == "moonshot":
            return float(b.get("available_balance", 0))
        elif pid == "zhipu":
            return float(b.get("remain", 0))
        elif pid == "openrouter":
            return float(b.get("total_purchased", 0)) - float(b.get("total_used", 0))
        for v in b.values():
            try:
                return float(v)
            except (ValueError, TypeError):
                continue
        return None
    except (ValueError, TypeError):
        return None


def load_history() -> list[dict]:
    """加载历史记录文件。"""
    path = Path(HISTORY_FILE)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_history(records: list[dict]):
    """写入历史记录文件。"""
    Path(HISTORY_FILE).write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def append_history(records: list[dict], results: list[dict]):
    """将本次查询结果追加到历史记录中。"""
    now = datetime.now().isoformat(timespec="seconds")
    for r in results:
        records.append({
            "ts": now,
            "kid": _key_id(r["key"]),
            "provider": r["provider"],
            "pid": r.get("provider_id", ""),
            "balance": _extract_num_balance(r),
            "status": r["status"],
        })


def _get_key_history(records: list[dict], kid: str) -> list[dict]:
    """获取某个 Key 的全部历史记录（按时间升序）。"""
    recs = [r for r in records if r["kid"] == kid]
    recs.sort(key=lambda r: r["ts"])
    return recs


def _extract_balances_seq(history: list[dict]) -> list[float]:
    """从历史记录中提取连续有效余额序列。"""
    return [r["balance"] for r in history if r["balance"] is not None]


def sparkline(values: list[float], length: int = 0) -> str:
    """用 Unicode 字符绘制火花条。"""
    if not values:
        return ""
    mn, mx = min(values), max(values)
    n = len(_SPARK_CHARS)
    if mx == mn:
        bar = _SPARK_CHARS[n // 2] * len(values)
    else:
        rng = mx - mn
        bar = "".join(
            _SPARK_CHARS[min(int((v - mn) / rng * (n - 1)), n - 1)]
            for v in values
        )
    if length and len(bar) < length:
        bar = bar + " " * (length - len(bar))
    return bar


def _format_currency(provider_id: str, value: float) -> str:
    """根据提供商格式化余额数值。"""
    if provider_id == "openrouter":
        return f"${value:.2f}"
    return f"¥{value:.2f}"


def _delta_text(current: float | None, previous: float | None) -> str:
    """生成两行余额增减文字。"""
    if current is None and previous is None:
        return "—"
    if previous is None:
        return "（首次查询）"
    if current is None:
        return f"（上次 {previous:.2f}，本次失败）"
    diff = current - previous
    if abs(diff) < 0.001:
        return "（持平）"
    if diff > 0:
        return f"↑ +{diff:.2f}"
    return f"↓ {diff:.2f}"


def _format_delta(current: float | None, previous: float | None, provider_id: str) -> str:
    """生成带货币符号的变化描述。"""
    if current is None and previous is None:
        return "—"
    if previous is None:
        return "首次查询"
    if current is None:
        return f"上次 {_format_currency(provider_id, previous)}，本次失败"
    diff = current - previous
    if abs(diff) < 0.001:
        return f"持平（{_format_currency(provider_id, previous)}）"
    arrow = "📈" if diff > 0 else "📉"
    return f"{arrow} {_format_currency(provider_id, previous)} → {_format_currency(provider_id, current)}（{'+' if diff > 0 else ''}{diff:.2f}）"


# ============================================================
# SVG 图表生成（零依赖）
# ============================================================

def _render_svg_chart(
    series: dict[str, list[float | None]],
    *,
    timestamps: list[str] | None = None,
    time_weight: float = 2.0,
    title: str = "",
    width: int = 500,
    height: int = 220,
    margin: dict | None = None,
) -> str:
    """
    用纯 Python 生成 SVG 折线图。
    series: {label: [balance, ...]}  — 每个 label 一条线
    timestamps: 时间标签列表（和 series 最大长度对齐），提供后会在 X 轴显示时间
    time_weight: 时间轴压缩指数，1.0=线性均匀，>1 压缩老数据给近期更多空间，默认 2.0
    """
    has_ts = timestamps is not None and len(timestamps) > 0
    if margin is None:
        margin = {"t": 30, "r": 20, "b": 55 if has_ts else 40, "l": 55}
    mt, mr, mb, ml = margin["t"], margin["r"], margin["b"], margin["l"]
    pw = width - ml - mr  # plot width
    ph = height - mt - mb  # plot height

    # 收集全部数值
    all_vals = [v for vals in series.values() for v in vals if v is not None]
    if not all_vals:
        return f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg"><text x="{width//2}" y="{height//2}" text-anchor="middle" fill="#888">暂无数据</text></svg>'

    y_min = min(all_vals)
    y_max = max(all_vals)
    if y_max == y_min:
        y_max += 1  # 避免除零

    def to_svg(x_frac: float, val: float) -> tuple[float, float]:
        sx = ml + x_frac * pw
        sy = mt + ph - (val - y_min) / (y_max - y_min) * ph
        return sx, sy

    n = len(timestamps) if has_ts else max(len(v) for v in series.values())
    # 时间轴位置：time_weight 控制压缩程度
    raw = [i / (n - 1) if n > 1 else 0.5 for i in range(n)]
    if has_ts and time_weight != 1.0:
        x_positions = [v ** time_weight for v in raw]
    else:
        x_positions = raw

    colors = ["#4CAF50", "#2196F3", "#FF9800", "#E91E63", "#9C27B0", "#00BCD4", "#FF5722", "#607D8B"]

    svg = [f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg" font-family="monospace" font-size="11">']

    # 背景
    svg.append(f'<rect width="{width}" height="{height}" fill="#fafafa" rx="4"/>')

    # Y 轴网格线 & 标签
    y_ticks = 5
    for i in range(y_ticks + 1):
        frac = i / y_ticks
        val = y_min + (y_max - y_min) * frac
        yy = mt + ph - frac * ph
        svg.append(f'<line x1="{ml}" y1="{yy:.1f}" x2="{ml + pw}" y2="{yy:.1f}" stroke="#e0e0e0" stroke-width="1"/>')
        svg.append(f'<text x="{ml - 6}" y="{yy + 4}" text-anchor="end" fill="#666">{val:.2f}</text>')

    # X 轴时间标签
    if has_ts:
        step = max(1, n // 6)
        for i in range(0, n, step):
            ts = timestamps[i]
            formatted = ts[5:16].replace("T", " ")  # "MM-DD HH:MM"
            sx = ml + x_positions[i] * pw
            svg.append(f'<text x="{sx:.1f}" y="{mt + ph + 14}" text-anchor="end" fill="#666" font-size="9" transform="rotate(-30, {sx:.1f}, {mt + ph + 14})">{formatted}</text>')

    # 图例
    legend_x = ml
    for ci, (label, vals) in enumerate(series.items()):
        if ci > 0:
            legend_x += 15
        color = colors[ci % len(colors)]
        lw = len(label) * 7 + 20
        if legend_x + lw > ml + pw:
            break
        svg.append(f'<rect x="{legend_x}" y="{mt - 20}" width="10" height="10" fill="{color}" rx="2"/>')
        svg.append(f'<text x="{legend_x + 14}" y="{mt - 11}" fill="#333" font-size="11">{label}</text>')
        legend_x += lw

    # 折线（遇 None 断线）
    for ci, (label, vals) in enumerate(series.items()):
        color = colors[ci % len(colors)]
        segments: list[list[str]] = [[]]
        for i, v in enumerate(vals):
            if v is None:
                if segments[-1]:
                    segments.append([])
                continue
            sx, sy = to_svg(x_positions[i], v)
            segments[-1].append(f"{sx:.1f},{sy:.1f}")
        for seg in segments:
            if len(seg) >= 2:
                svg.append(f'<polyline points="{" ".join(seg)}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')

    # 数据点圆点 + 末位标签
    for ci, (label, vals) in enumerate(series.items()):
        color = colors[ci % len(colors)]
        last_actual_idx = -1
        for i, v in enumerate(vals):
            if v is not None:
                last_actual_idx = i
        for i, v in enumerate(vals):
            if v is None:
                continue
            sx, sy = to_svg(x_positions[i], v)
            if i == last_actual_idx:
                svg.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="3.5" fill="{color}" stroke="#fff" stroke-width="1.5"/>')
                svg.append(f'<text x="{sx:.1f}" y="{sy - 8}" text-anchor="middle" fill="{color}" font-size="10" font-weight="bold">{v:.2f}</text>')
            else:
                svg.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="2" fill="{color}"/>')

    # 标题
    if title:
        svg.append(f'<text x="{ml + pw // 2}" y="16" text-anchor="middle" fill="#333" font-size="13" font-weight="bold">{title}</text>')

    svg.append("</svg>")
    return "\n".join(svg)


def _build_trend_data(results: list[dict], history: list[dict]) -> dict[str, dict]:
    """
    构建每个 Key 的变化追踪数据。
    返回: {key_id: {"current": float|None, "previous": float|None, "history": [float, ...], "result": dict}}
    """
    trend: dict[str, dict] = {}
    for r in results:
        kid = _key_id(r["key"])
        balances = _extract_balances_seq(_get_key_history(history, kid))
        current = _extract_num_balance(r)
        # 当前值已在本次写入前的一批记录里，还没 append → 从 history 取上一次
        previous = balances[-1] if balances else None
        trend[kid] = {
            "current": current,
            "previous": previous,
            "history": balances,  # 不含本次
            "result": r,
        }
    return trend


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

def format_summary(results: list[dict], history: list[dict] | None = None) -> str:
    """生成汇总文本（控制台 + 文件用），按余额排序 + 服务商分组 + 余额变化 + 速复制区。"""
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
            masked = mask_key(r["key"])
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

    # ── 余额变化追踪 ──
    if history is not None and history:
        trend = _build_trend_data(results, history)
        entries_with_history = [(kid, td) for kid, td in trend.items() if td["history"]]
        if entries_with_history:
            lines.append("")
            lines.append("─" * W)
            lines.append(f"{'余额变化追踪':^{W}}")
            lines.append("─" * W)
            lines.append("")
            for kid, td in entries_with_history:
                r = td["result"]
                masked = mask_key(r["key"])
                pid = r.get("provider_id", "")
                cur = td["current"]
                prev = td["previous"]
                delta = _format_delta(cur, prev, pid)
                spark = sparkline(td["history"] + ([cur] if cur is not None else []), length=0)
                spark_str = f"  {spark}" if spark else ""
                lines.append(f"  {masked:<24s}  {delta:<30s}{spark_str}")
            lines.append("")

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


def format_summary_md(results: list[dict], history: list[dict] | None = None, timestamp: str = "") -> str:
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
            masked = mask_key(r["key"])
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

    # ── 余额变化追踪 ──
    if history is not None and history:
        trend = _build_trend_data(results, history)
        entries_with_history = [(kid, td) for kid, td in trend.items() if td["history"]]
        if entries_with_history:
            md.append("---")
            md.append("")
            md.append("## 📊 余额变化追踪")
            md.append("")
            md.append("| Key | Provider | 变化趋势 | 变化 |")
            md.append("|-----|----------|---------|:----:|")
            for kid, td in entries_with_history:
                r = td["result"]
                masked = mask_key(r["key"])
                pid = r.get("provider_id", "")
                cur = td["current"]
                prev = td["previous"]
                delta_text = _format_delta(cur, prev, pid)
                spark = sparkline(td["history"] + ([cur] if cur is not None else []))
                md.append(f"| `{masked}` | {r['provider']} | `{spark}` | {delta_text} |")
            md.append("")

    # ── SVG 余额趋势图 ──
    if history is not None and history:
        trend = _build_trend_data(results, history)
        # 按 provider 分组绘制
        from collections import defaultdict
        prov_groups: dict[str, list[tuple[str, str, list[float]]]] = defaultdict(list)  # (kid, masked_key, vals)
        for kid, td in trend.items():
            r = td["result"]
            vals = td["history"] + ([td["current"]] if td["current"] is not None else [])
            if len(vals) >= 2:
                prov_groups[r["provider"]].append((kid, mask_key(r["key"]), vals))
        if prov_groups:
            md.append("---")
            md.append("")
            md.append("## 📈 余额趋势图")
            md.append("")
            for prov, items in prov_groups.items():
                # 收集该 provider 下所有 key 的完整时间戳（对齐时间轴）
                all_key_ts: dict[str, list[str]] = {}
                for kid, masked, _ in items:
                    kid_recs = _get_key_history(history, kid)
                    ts_list = [rec["ts"] for rec in kid_recs if rec["balance"] is not None]
                    # 追加本次时间戳
                    trend_entry = trend.get(kid)
                    if trend_entry and trend_entry["current"] is not None:
                        ts_list.append(datetime.now().isoformat(timespec="seconds"))
                    all_key_ts[kid] = ts_list
                # 所有 key 的合并时间轴
                merged_ts = sorted(set(ts for tss in all_key_ts.values() for ts in tss))

                chart_data: dict[str, list[float | None]] = {}
                for kid, masked, vals in items:
                    # 按合并时间轴重排，每时间点有值才能对齐
                    kid_ts = all_key_ts.get(kid, [])
                    kid_map = dict(zip(kid_ts, vals))
                    # 长度必须和 merged_ts 一致，缺失位用 None 占位
                    aligned: list[float | None] = [kid_map.get(ts) for ts in merged_ts]
                    if sum(1 for v in aligned if v is not None) >= 2:
                        chart_data[masked] = aligned

                if chart_data:
                    chart_title = f"{prov} — 余额趋势"
                    svg = _render_svg_chart(chart_data, timestamps=merged_ts, title=chart_title)
                    chart_filename = f"balance_chart_{timestamp}_{prov.replace(' ', '_').replace('(', '').replace(')', '')}.svg" if timestamp else f"balance_chart_{prov.replace(' ', '_')}.svg"
                    chart_path = Path(REPORTS_DIR) / chart_filename
                    chart_path.parent.mkdir(parents=True, exist_ok=True)
                    chart_path.write_text(svg, encoding="utf-8")
                    md.append(f"### {prov}")
                    md.append("")
                    md.append(f'<img src="{REPORTS_DIR}/{chart_filename}" alt="{prov} 余额趋势" />')
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
            print(f"⚠  未知提供商 '{provider}'，跳过 {mask_key(key)}")
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
    timestamp_hr = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 加载历史记录
    history = load_history()

    # 确保报告目录存在
    Path(REPORTS_DIR).mkdir(exist_ok=True)

    # 文本版（控制台 + .txt）
    report_txt = format_summary(results, history)
    print("\n" + report_txt)

    txt_file = f"{REPORTS_DIR}/balance_report_{timestamp}.txt"
    Path(txt_file).write_text(report_txt, encoding="utf-8")
    Path("最新报告.txt").write_text(report_txt, encoding="utf-8")
    print(f"\n📄 文本报告: {txt_file}")

    # Markdown 版（.md）
    report_md = format_summary_md(results, history, timestamp)
    md_file = f"{REPORTS_DIR}/balance_report_{timestamp}.md"
    Path(md_file).write_text(report_md, encoding="utf-8")
    Path("最新报告.md").write_text(report_md, encoding="utf-8")
    print(f"📝 Markdown 报告: {md_file}")

    # 保存历史记录
    append_history(history, results)
    save_history(history)
    print(f"📊 余额历史: {HISTORY_FILE}")


if __name__ == "__main__":
    main()
