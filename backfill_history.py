#!/usr/bin/env python3
"""
从已有的 balance_report_*.txt 文件中回填历史记录到 balance_history.json。
这些文件是在历史追踪功能上线前生成的，需要解析后导入。

用法:
  python backfill_history.py              # 回填所有报告文件
  python backfill_history.py --dry-run     # 预览要导入哪些记录（不实际写入）
  python backfill_history.py --files *     # 指定特定文件
"""

import glob
import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    from balance_checker import (
        load_history,
        save_history,
        PROVIDERS,
        _key_id,
        HISTORY_FILE,
    )
except ImportError:
    print("[ERROR] 请在 balance_checker.py 同目录下运行")
    sys.exit(1)

# 提供商标签 → provider_id 反向映射
LABEL_TO_PID: dict[str, str] = {}
for pid, cfg in PROVIDERS.items():
    LABEL_TO_PID[cfg["label"]] = pid


# ============================================================
# 解析逻辑
# ============================================================

_RE_TIMESTAMP = re.compile(r"生成:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})")
_RE_PROVIDER_HEADER = re.compile(r"──\s+(.+?)\s*─")
_RE_DETAIL_LINE = re.compile(r"^\s*#\d+\s+(\S+)")
_RE_BALANCE = re.compile(r"[¥$](-?\d+\.?\d*)")
_RE_STATUS_OK = re.compile(r"✅")
_RE_STATUS_ERR = re.compile(r"❌")
_RE_ELAPSED = re.compile(r"([\d.]+)s")


def parse_report(filepath: str) -> list[dict]:
    """解析单个报告文件，返回历史记录列表。"""
    text = Path(filepath).read_text(encoding="utf-8")
    lines = text.splitlines()

    # ── 1) 时间戳 ──
    ts_match = _RE_TIMESTAMP.search(text)
    if not ts_match:
        print(f"  [skip] 找不到时间戳: {filepath}")
        return []
    timestamp_raw = ts_match.group(1)
    timestamp_iso = datetime.strptime(timestamp_raw, "%Y-%m-%d %H:%M:%S").isoformat(timespec="seconds")

    # ── 2) 分离详情区与速复制区 ──
    quick_copy_start = None
    for i, line in enumerate(lines):
        if "速复制区" in line:
            quick_copy_start = i
            break

    if quick_copy_start is None:
        print(f"  [skip] 找不到速复制区: {filepath}")
        return []

    detail_lines = lines[:quick_copy_start]
    quick_lines = lines[quick_copy_start:]

    # ── 3) 解析详情区: (provider, key, balance, status) ──
    detail_entries: list[tuple[str, str, float | None, str]] = []  # (provider, key_or_masked, balance, status)

    current_provider = ""
    for line in detail_lines:
        # 提供商首部
        ph_match = _RE_PROVIDER_HEADER.search(line)
        if ph_match:
            candidate = ph_match.group(1).strip()
            # 排除头部和分隔线中的误匹配
            if candidate and candidate not in ("", "═", "─") and "Key" not in candidate and "速复制" not in candidate and "以上" not in candidate:
                current_provider = candidate
            continue

        # Key 详情行
        dl_match = _RE_DETAIL_LINE.match(line)
        if not dl_match or not current_provider:
            continue

        key_token = dl_match.group(1)  # 可能是完整 key 或脱敏 key

        # 余额
        bal_match = _RE_BALANCE.search(line)
        balance = float(bal_match.group(1)) if bal_match else None

        # 状态
        if _RE_STATUS_OK.search(line):
            status = "ok"
        elif _RE_STATUS_ERR.search(line):
            status = "error"
        else:
            status = "error"

        detail_entries.append((current_provider, key_token, balance, status))

    if not detail_entries:
        print(f"  [skip] 详情区无有效数据: {filepath}")
        return []

    # ── 4) 解析速复制区: 按提供商分组拿到完整 Key ──
    quick_keys: list[tuple[str, list[str]]] = []  # [(provider, [keys])]
    current_q_provider = ""
    current_q_keys: list[str] = []

    for line in quick_lines:
        ph_match = _RE_PROVIDER_HEADER.search(line)
        if ph_match:
            candidate = ph_match.group(1).strip()
            if candidate and candidate not in ("", "═", "─") and "Key" not in candidate and "速复制" not in candidate and "以上" not in candidate and "以下" not in candidate:
                if current_q_keys and current_q_provider:
                    quick_keys.append((current_q_provider, current_q_keys))
                current_q_provider = candidate
                current_q_keys = []
            continue

        stripped = line.strip()
        # 跳过非 key 行
        if not stripped or "─" in stripped or "═" in stripped or "Key" in stripped or "速复制" in stripped or "复制" in stripped or "不可用" in stripped:
            continue
        # 看起来像是一个 key（包含 sk-/moonshot- 等常见前缀，或者看起来不像中文）
        if current_q_provider and not any(c > '\u4e00' for c in stripped[:4]):  # 没有中文字符开头
            current_q_keys.append(stripped)

    if current_q_keys and current_q_provider:
        quick_keys.append((current_q_provider, current_q_keys))

    # ── 5) 匹配完整 Key → 生成历史记录 ──
    records: list[dict] = []

    # 构建详情区的平衡索引（按提供商分组）
    detail_by_provider: dict[str, list[tuple[str, float | None, str]]] = {}
    for prov, key_token, bal, sts in detail_entries:
        detail_by_provider.setdefault(prov, []).append((key_token, bal, sts))

    for q_prov, q_keys_list in quick_keys:
        d_entries = detail_by_provider.get(q_prov, [])
        if len(d_entries) != len(q_keys_list):
            # 长度不匹配：可能是细节区的顺序不同
            # 尝试用脱敏 key 推断：如果 detail key 包含 ***，则用 quick copy 的 key 替换
            fmt_warn = f"  [warn] {Path(filepath).name}: {q_prov} 详情({len(d_entries)}) vs 速复制({len(q_keys_list)}) 数量不一致"
            if len(d_entries) == 0 and len(q_keys_list) > 0:
                print(f"  [warn] {Path(filepath).name}: 有速复制 Key 但无详情数据，跳过该组")
            elif len(q_keys_list) > 0:
                print(fmt_warn)
            # 尽可能匹配：取较短的
            n = min(len(d_entries), len(q_keys_list))
            d_entries = d_entries[:n]
            q_keys_list = q_keys_list[:n]

        for i, (key_token, balance, status) in enumerate(d_entries):
            full_key = q_keys_list[i] if i < len(q_keys_list) else key_token
            pid = LABEL_TO_PID.get(q_prov, "")
            records.append({
                "ts": timestamp_iso,
                "kid": _key_id(full_key),
                "provider": q_prov,
                "pid": pid,
                "balance": balance,
                "status": status,
            })

    return records


def merge_records(existing: list[dict], new_records: list[dict]) -> list[dict]:
    """合并新记录到已有记录中（按 ts + kid 去重）。"""
    seen = set()
    for r in existing:
        seen.add((r["ts"], r["kid"]))

    added = 0
    skipped = 0
    for r in new_records:
        key = (r["ts"], r["kid"])
        if key not in seen:
            existing.append(r)
            seen.add(key)
            added += 1
        else:
            skipped += 1

    # 按时间排序
    existing.sort(key=lambda r: r["ts"])
    return existing, added, skipped


# ============================================================
# Main
# ============================================================

def main():
    args = sys.argv[1:]

    dry_run = "--dry-run" in args
    if dry_run:
        args.remove("--dry-run")

    # 确定要处理的文件
    if args:
        files = []
        for arg in args:
            if arg.startswith("--"):
                continue
            files.extend(glob.glob(arg) if "*" in arg else [arg])
    else:
        files = sorted(glob.glob("balance_report_*.txt"))

    if not files:
        print("[ERROR] 找不到 balance_report_*.txt 文件")
        return

    print(f"找到 {len(files)} 个报告文件:\n")

    all_new_records: list[dict] = []
    for fp in files:
        print(f"  {os.path.basename(fp)} ... ", end="", flush=True)
        recs = parse_report(fp)
        if recs:
            print(f"{len(recs)} 条记录")
            all_new_records.extend(recs)
        else:
            print("无数据")

    if not all_new_records:
        print("\n没有可导入的记录。")
        return

    if dry_run:
        # 统计
        providers = set(r["provider"] for r in all_new_records)
        time_range = (min(r["ts"] for r in all_new_records)[:19],
                      max(r["ts"] for r in all_new_records)[:19])
        print(f"\n{'=' * 50}")
        print(f"[DRY RUN] 将导入 {len(all_new_records)} 条记录:")
        print(f"  提供商:  {', '.join(sorted(providers))}")
        print(f"  时间范围: {time_range[0]} ~ {time_range[1]}")
        print(f"  目标文件: {HISTORY_FILE}")
        print(f"  (加上 --dry-run 去掉后重新运行即可写入)")
        return

    # 合并
    existing = load_history()
    print(f"\n当前历史记录: {len(existing)} 条")

    merged, added, skipped = merge_records(existing, all_new_records)
    save_history(merged)

    print(f"新增记录: {added} 条")
    print(f"跳过重复: {skipped} 条")
    print(f"历史总计: {len(merged)} 条")
    print(f"\n[OK] 已保存到 {HISTORY_FILE}")

    # 展示已导入的 key 列表
    if added > 0:
        print(f"\n{'=' * 50}")
        print(f"  已导入的 Key:")
        imported_kids = set()
        for r in all_new_records:
            imported_kids.add(r["kid"])
        for kid in sorted(imported_kids):
            prov = next((r["provider"] for r in all_new_records if r["kid"] == kid), "")
            count = sum(1 for r in merged if r["kid"] == kid)
            print(f"    {kid:<14} {prov:<24} (共 {count} 条记录)")


if __name__ == "__main__":
    main()
