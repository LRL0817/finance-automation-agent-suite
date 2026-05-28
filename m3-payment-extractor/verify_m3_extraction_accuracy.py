"""Validate M3/OA extraction records without opening OA or any bank client.

This script checks whether extracted JSON records are internally consistent and
auditable against their saved M3 screenshots. It is intentionally read-only:
it never writes bank queues, starts bank automation, or contacts the gateway.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import time


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
VERIFICATION = RUNTIME / "verification"
FLOW_RE = re.compile(r"YLHT[A-Z0-9-]*-\d{4}-[A-Z0-9-]+-付-\d{4}-\d+")

REQUIRED_DETAIL_FIELDS = (
    "单据编号",
    "申请日期",
    "申请金额",
    "合同编号",
    "收款单位名称",
    "付款单位名称",
    "开户银行",
    "银行账户",
    "M3合同付款列表截图",
    "M3抓取详情截图",
)


def compact(value: object) -> str:
    return "".join(str(value or "").split())


def normalize_amount(value: object) -> str:
    text = str(value or "")
    for token in (",", "，", "¥", "￥", "元"):
        text = text.replace(token, "")
    return "".join(text.split())


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def latest_records_json() -> Path:
    candidates = [p for p in VERIFICATION.glob("*/records.json") if p.is_file()]
    if not candidates:
        raise FileNotFoundError(f"没有找到 records.json: {VERIFICATION}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_records(path: Path) -> list[dict]:
    payload = load_json(path)
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        records = payload.get("records")
        if isinstance(records, list):
            return [r for r in records if isinstance(r, dict)]
        return [payload]
    raise ValueError(f"不支持的 JSON 结构: {path}")


def resolve_payment_bank(record: dict) -> tuple[str, str, str]:
    try:
        from step4_extract import resolve_payment_bank as resolve

        return resolve(record)
    except Exception:
        return "", "", ""


def validate_record(record: dict, index: int) -> list[dict]:
    issues: list[dict] = []
    ref = str(record.get("单据编号") or f"#{index}")

    for key in REQUIRED_DETAIL_FIELDS:
        if not str(record.get(key) or "").strip():
            issues.append({"index": index, "ref": ref, "kind": "missing", "field": key})

    for key in ("M3合同付款列表截图", "M3抓取详情截图"):
        path = str(record.get(key) or "").strip()
        if path and not Path(path).is_file():
            issues.append({"index": index, "ref": ref, "kind": "missing_file", "field": key, "value": path})

    ref_text = str(record.get("单据编号") or "").strip()
    if ref_text and not FLOW_RE.search(ref_text):
        issues.append({"index": index, "ref": ref, "kind": "bad_ref", "field": "单据编号", "value": ref_text})

    contract = str(record.get("合同编号") or "").strip()
    if ref_text and contract and not ref_text.startswith(contract):
        issues.append(
            {
                "index": index,
                "ref": ref,
                "kind": "mismatch",
                "field": "单据编号/合同编号",
                "value": f"{ref_text} / {contract}",
            }
        )

    list_contract = str(record.get("列表合同编号") or "").strip()
    if list_contract and contract and list_contract != contract:
        issues.append(
            {
                "index": index,
                "ref": ref,
                "kind": "mismatch",
                "field": "列表合同编号/合同编号",
                "value": f"{list_contract} / {contract}",
            }
        )

    list_date = str(record.get("列表日期") or "").strip()
    apply_date = str(record.get("申请日期") or "").strip()
    if list_date and apply_date and list_date != apply_date:
        issues.append(
            {
                "index": index,
                "ref": ref,
                "kind": "mismatch",
                "field": "列表日期/申请日期",
                "value": f"{list_date} / {apply_date}",
            }
        )

    account = compact(record.get("银行账户"))
    if account and (not account.isdigit() or not 8 <= len(account) <= 32):
        issues.append({"index": index, "ref": ref, "kind": "bad_account", "field": "银行账户", "value": account})

    amount = normalize_amount(record.get("申请金额"))
    if amount:
        try:
            value = Decimal(amount)
        except InvalidOperation:
            issues.append({"index": index, "ref": ref, "kind": "bad_amount", "field": "申请金额", "value": amount})
        else:
            if value <= 0 or value.as_tuple().exponent < -2:
                issues.append({"index": index, "ref": ref, "kind": "bad_amount", "field": "申请金额", "value": amount})

    return issues


def build_summary(path: Path, records: list[dict], issues: list[dict]) -> dict:
    payer_counter = Counter(str(r.get("付款单位名称") or "").strip() for r in records)
    receive_bank_counter = Counter(str(r.get("开户银行") or "").strip() for r in records)
    route_counter = Counter()
    metadata_route_counter = Counter()
    metadata_route_mismatches = []
    unrecognized_routes = []
    for index, record in enumerate(records, start=1):
        bank, raw, source = resolve_payment_bank(record)
        bank = bank or ""
        route_counter[bank or "未识别"] += 1
        metadata_bank = str(record.get("付款银行识别") or "").strip()
        if metadata_bank:
            metadata_route_counter[metadata_bank] += 1
            if bank and metadata_bank != bank:
                metadata_route_mismatches.append(
                    {
                        "index": index,
                        "ref": record.get("单据编号", ""),
                        "旧识别": metadata_bank,
                        "当前规则": bank,
                        "当前来源": source,
                        "付款单位名称": record.get("付款单位名称", ""),
                    }
                )
        if not bank:
            unrecognized_routes.append(
                {
                    "index": index,
                    "ref": record.get("单据编号", ""),
                    "付款单位名称": record.get("付款单位名称", ""),
                    "收款单位名称": record.get("收款单位名称", ""),
                    "开户银行": record.get("开户银行", ""),
                }
            )

    return {
        "source": str(path),
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "records": len(records),
        "issues": len(issues),
        "issue_kinds": dict(Counter(i["kind"] for i in issues)),
        "route_counts": dict(route_counter),
        "metadata_route_counts": dict(metadata_route_counter),
        "metadata_route_mismatches": metadata_route_mismatches,
        "unrecognized_routes": unrecognized_routes,
        "top_payers": payer_counter.most_common(10),
        "top_receive_banks": receive_bank_counter.most_common(10),
        "sample_detail_screenshots": [
            str(r.get("M3抓取详情截图") or "") for r in records[:5] if str(r.get("M3抓取详情截图") or "").strip()
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate M3 extraction records and screenshots.")
    parser.add_argument("--records", type=Path, default=None, help="records.json path; defaults to newest verification run")
    parser.add_argument("--write-report", action="store_true", help="write a JSON report under runtime/verification")
    parser.add_argument("--max-issues", type=int, default=50, help="maximum issues to print")
    args = parser.parse_args()

    records_path = args.records or latest_records_json()
    records_path = records_path.resolve()
    records = load_records(records_path)

    issues: list[dict] = []
    for index, record in enumerate(records, start=1):
        issues.extend(validate_record(record, index))

    summary = build_summary(records_path, records, issues)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if issues:
        print("\n=== issues ===")
        for issue in issues[: max(0, args.max_issues)]:
            print(json.dumps(issue, ensure_ascii=False))
        if len(issues) > args.max_issues:
            print(f"... {len(issues) - args.max_issues} more")

    if args.write_report:
        out_dir = VERIFICATION / f"m3_field_accuracy_{time.strftime('%Y%m%d_%H%M%S')}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "report.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump({"summary": summary, "issues": issues}, f, ensure_ascii=False, indent=2)
        print(f"\nreport: {out_path}")

    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
