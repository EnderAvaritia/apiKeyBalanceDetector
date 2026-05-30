#!/usr/bin/env python3
"""
API Key 余额历史查看器
读取 balance_history.json，查询/过滤/统计历史记录，生成趋势图表。

用法:
  python history_viewer.py                 摘要总览（默认）
  python history_viewer.py --records       逐条查看全部记录
  python history_viewer.py --key <部分key_id>   按 Key 过滤
  python history_viewer.py --provider <名>     按提供商过滤
  python history_viewer.py --days <N>      最近 N 天
  python history_viewer.py --stats         详细统计
  python history_viewer.py --chart         生成 SVG 趋势图
  python history_viewer.py --output <文件>  输出到文件
"""

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# Windows GBK 终端兼容：强制 utf-8 输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

# ── 从主模块导入共享函数 ──
try:
    from balance_checker import (
        load_history,
        sparkline,
        _format_currency,
        _render_svg_chart,
        HISTORY_FILE,
    )
except ImportError:
    print("[ERROR] 请在 balance_checker.py 同目录下运行")
    sys.exit(1)


# ============================================================
# 过滤逻辑
# ============================================================

def filter_records(
    records: list[dict],
    *,
    key_filter: str = "",
    provider_filter: str = "",
    days: int = 0,
) -> list[dict]:
    """按条件过滤历史记录。"""
    result = records

    if key_filter:
        result = [r for r in result if key_filter.lower() in r.get("kid", "").lower()]

    if provider_filter:
        result = [
            r
            for r in result
            if provider_filter.lower() in r.get("provider", "").lower()
            or provider_filter.lower() in r.get("pid", "").lower()
        ]

    if days > 0:
        cutoff = datetime.now() - timedelta(days=days)
        cutoff_str = cutoff.isoformat(timespec="seconds")
        result = [r for r in result if r.get("ts", "") >= cutoff_str]

    return result


# ============================================================
# 展示逻辑
# ============================================================

def _color(val: float, threshold: float = 0) -> str:
    if val > threshold:
        return "+"
    elif val < threshold:
        return "-"
    return "="


def _fmt_val(v) -> str:
    if v is None:
        return "  N/A  "
    return f"{v:>8.2f}"


def show_summary(records: list[dict]):
    """显示每个 Key 的摘要总览。"""
    if not records:
        print("(empty) 无历史记录")
        return

    # 按 key_id 分组
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        groups[r["kid"]].append(r)

    # 每组按时间排序
    for kid in groups:
        groups[kid].sort(key=lambda r: r["ts"])

    print(f"{'Key ID':<14} {'Provider':<24} {'次':>3} {'首笔余额':>8}  {'最近余额':>8}  {'最低':>8}  {'最高':>8}  {'趋势'}")
    print("-" * 96)

    for kid in sorted(groups.keys()):
        recs = groups[kid]
        provider = recs[-1].get("provider", "?")
        count = len(recs)

        balances = [r["balance"] for r in recs if r["balance"] is not None]
        if not balances:
            print(f"{kid:<14} {provider:<24} {count:>3}  {'  N/A  ':>8}  {'  N/A  ':>8}  {'  N/A  ':>8}  {'  N/A  ':>8}  -")
            continue

        first_b = balances[0]
        last_b = balances[-1]
        min_b = min(balances)
        max_b = max(balances)
        spark = sparkline(balances)
        direction = _color(last_b - first_b)

        print(
            f"{kid:<14} {provider:<24} {count:>3}"
            f"  {_fmt_val(first_b)}  {_fmt_val(last_b)}"
            f"  {_fmt_val(min_b)}  {_fmt_val(max_b)}"
            f"  {direction} {spark}"
        )

    print("-" * 96)
    total = len(groups)
    all_bal = [r["balance"] for r in records if r["balance"] is not None]
    print(f"  共 {total} 个 Key，{len(records)} 条记录"
          + (f"，余额范围 {min(all_bal):.2f} ~ {max(all_bal):.2f}" if all_bal else ""))


def show_records(records: list[dict]):
    """逐条列出所有记录。"""
    if not records:
        print("(empty) 无历史记录")
        return

    sorted_recs = sorted(records, key=lambda r: r["ts"])
    print(f"{'时间':<22} {'Key ID':<14} {'Provider':<24} {'余额':>8}  {'状态':<6}")
    print("-" * 80)

    for r in sorted_recs:
        ts = r.get("ts", "").replace("T", " ")[:19]
        kid = r.get("kid", "")
        provider = r.get("provider", "")[:24]
        balance = _fmt_val(r.get("balance"))
        status = r.get("status", "")
        status_icon = "[OK]" if status == "ok" else "[ERR]"
        print(f"{ts:<22} {kid:<14} {provider:<24} {balance}  {status_icon:<6}")

    print("-" * 80)
    print(f"  共 {len(sorted_recs)} 条记录")


def show_stats(records: list[dict]):
    """显示每个 Key 的详细统计信息。"""
    if not records:
        print("(empty) 无历史记录")
        return

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        groups[r["kid"]].append(r)

    for kid in sorted(groups.keys()):
        recs = groups[kid]
        recs.sort(key=lambda r: r["ts"])
        balances = [r["balance"] for r in recs if r["balance"] is not None]
        successes = sum(1 for r in recs if r["status"] == "ok")

        print(f"\n{'=' * 54}")
        print(f"  Key ID:    {kid}")
        print(f"  Provider:  {recs[-1].get('provider', '?')}")
        print(f"  查询次数:  {len(recs)} 次（成功 {successes} 次）")
        print(f"  时间范围:  {recs[0]['ts'][:19]} -> {recs[-1]['ts'][:19]}")

        if balances:
            print(f"  余额范围:  {min(balances):.2f} ~ {max(balances):.2f}")
            print(f"  平均余额:  {sum(balances) / len(balances):.2f}")
            if len(balances) >= 2:
                first, last = balances[0], balances[-1]
                diff = last - first
                arrow = "+" if diff > 0 else ("-" if diff < 0 else "=")
                print(f"  总体变化:  {arrow}  {first:.2f} -> {last:.2f} ({'+' if diff >= 0 else ''}{diff:.2f})")

        print(f"  趋势:      {sparkline(balances)}")


def build_chart(records: list[dict], output_path: str = ""):
    """生成 SVG 趋势图。"""
    if not records:
        print("(empty) 无历史记录，无法生成图表")
        return

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r["balance"] is not None:
            groups[r["kid"]].append(r)

    # 每个 key 按时间排序
    series: dict[str, list[float]] = {}
    for kid in sorted(groups.keys()):
        recs = sorted(groups[kid], key=lambda r: r["ts"])
        provider = recs[-1].get("provider", "?")
        label = f"{kid[:8]} ({provider[:12]})"
        series[label] = [r["balance"] for r in recs if r["balance"] is not None]

    if len(series) == 0:
        print("(empty) 无有效余额数据")
        return

    svg = _render_svg_chart(series, title="API Key 余额历史趋势")

    if output_path:
        Path(output_path).write_text(svg, encoding="utf-8")
        print(f"[OK] 图表已保存: {output_path}")
    else:
        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = f"history_chart_{now}.svg"
        Path(out).write_text(svg, encoding="utf-8")
        print(f"[OK] 图表已保存: {out}")

    return svg


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="API Key 余额历史查看器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--records", "-r", action="store_true", help="逐条列出全部记录")
    parser.add_argument("--key", "-k", type=str, default="", help="按 Key ID 过滤（支持部分匹配）")
    parser.add_argument("--provider", "-p", type=str, default="", help="按提供商过滤（支持部分匹配）")
    parser.add_argument("--days", "-d", type=int, default=0, help="最近 N 天")
    parser.add_argument("--stats", "-s", action="store_true", help="显示详细统计")
    parser.add_argument("--chart", "-c", action="store_true", help="生成 SVG 趋势图")
    parser.add_argument("--output", "-o", type=str, default="", help="输出到文件（SVG 或文本）")

    args = parser.parse_args()

    records = load_history()
    if not records:
        print(f"(empty) 未找到历史记录文件 ({HISTORY_FILE})")
        print("   请先运行 python balance_checker.py 生成记录。")
        sys.exit(1)

    # 过滤
    filtered = filter_records(
        records,
        key_filter=args.key,
        provider_filter=args.provider,
        days=args.days,
    )

    # 执行模式
    if args.chart:
        build_chart(filtered, output_path=args.output)
        return

    if args.output:
        from io import StringIO
        import contextlib

        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            if args.records:
                show_records(filtered)
            elif args.stats:
                show_stats(filtered)
            else:
                show_summary(filtered)
        text = buf.getvalue()
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"[OK] 输出已保存: {args.output}")
        return

    # 正常输出到控制台
    if args.records:
        show_records(filtered)
    elif args.stats:
        show_stats(filtered)
    else:
        show_summary(filtered)


if __name__ == "__main__":
    main()
