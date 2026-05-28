# -*- coding: utf-8 -*-
"""Manual JSON entrypoint for CMB U-BANK transfer-order creation.

This wrapper is intentionally thin: it accepts a pasted JSON-like transfer
template, writes the canonical bank_form.json used by the existing automation,
then optionally launches the original 制单.py workflow.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


ZHIDAN_ROOT = Path(__file__).resolve().parent
FINANCE_ROOT = ZHIDAN_ROOT.parent
FORM_DIR = FINANCE_ROOT / "M3直供合同付款数据获取"
BANK_FORM_PATH = FORM_DIR / "bank_form.json"
BACKUP_DIR = FORM_DIR / "data" / "manual_entry_backups"
SKILL_DIR = ZHIDAN_ROOT / "skills" / "制单单账号单笔转账skill"
ZHIDAN_SCRIPT = SKILL_DIR / "制单.py"

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from zhidan_usb_hub import _USB_HUB_PORT_BY_PAYER, validate_bank_form  # noqa: E402


REQUIRED_FIELDS = [
    "付款单位名称",
    "收方账号",
    "收方户名",
    "开户银行",
    "支行名称",
    "金额",
    "用途",
    "业务参考号",
]

KEY_ALIASES = {
    "付款单位": "付款单位名称",
    "付款方名称": "付款单位名称",
    "付款公司": "付款单位名称",
    "收款账号": "收方账号",
    "收款账户": "收方账号",
    "收款户名": "收方户名",
    "收款单位": "收方户名",
    "收款单位名称": "收方户名",
    "银行账号": "收方账号",
    "银行账户": "收方账号",
    "开户行": "开户银行",
    "开户银行名称": "开户银行",
    "支行": "支行名称",
    "联行名称": "支行名称",
    "申请金额": "金额",
    "转账金额": "金额",
    "备注": "业务参考号",
    "单据编号": "业务参考号",
}

BANK_HEADS = [
    "招商银行",
    "中国工商银行",
    "中国农业银行",
    "中国银行",
    "中国建设银行",
    "交通银行",
    "中信银行",
    "中国光大银行",
    "华夏银行",
    "中国民生银行",
    "广发银行",
    "平安银行",
    "上海浦东发展银行",
    "浦发银行",
    "兴业银行",
    "中国邮政储蓄银行",
    "北京银行",
    "上海银行",
    "江苏银行",
    "宁波银行",
    "南京银行",
    "浙商银行",
    "渤海银行",
    "恒丰银行",
    "桂林国民村镇银行",
]

TEMPLATE = """{
  "付款单位名称": "示例付款方公司",
  "收方账号": "000000000000000",
  "收方户名": "示例收款方公司",
  "开户银行": "示例银行",
  "支行名称": "示例银行示例支行",
  "金额": "0.01",
  "用途": "请勿用于真实提交",
  "业务参考号": "无"
}"""


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def _clean_pasted_text(text: str) -> str:
    cleaned = html.unescape(text or "")
    cleaned = _strip_code_fence(cleaned)
    cleaned = re.sub(r"<\s*br\s*/?\s*>", "\n", cleaned, flags=re.I)
    cleaned = cleaned.replace("\ufeff", "")
    cleaned = cleaned.replace("“", '"').replace("”", '"')
    cleaned = cleaned.replace("‘", "'").replace("’", "'")
    cleaned = cleaned.strip()
    if not cleaned.startswith("{"):
        cleaned = "{\n" + cleaned
    if not cleaned.endswith("}"):
        cleaned = cleaned + "\n}"
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return cleaned


def _parse_json_object(text: str) -> dict:
    cleaned = _clean_pasted_text(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        fallback = _parse_key_value_lines(cleaned)
        if fallback:
            return fallback
        raise ValueError(f"JSON 解析失败：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层必须是对象，例如 {\"收方账号\": \"...\"}")
    return data


def _parse_key_value_lines(text: str) -> dict:
    result = {}
    body = text.strip().strip("{}")
    for raw_line in body.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line or ":" not in line and "：" not in line:
            continue
        key, value = re.split(r"[:：]", line, maxsplit=1)
        key = key.strip().strip('"').strip("'")
        value = value.strip().strip(",").strip().strip('"').strip("'")
        if key:
            result[key] = value
    return result


def _normalize_key(key: str) -> str:
    key = str(key or "").strip()
    return KEY_ALIASES.get(key, key)


def _compact(value: str) -> str:
    return "".join(str(value or "").split())


def _normalize_bank_head(bank: str) -> str:
    compact = _compact(bank)
    if not compact:
        return ""
    if "招商银行" in compact:
        return "招商银行"
    for head in sorted(BANK_HEADS, key=len, reverse=True):
        if compact.startswith(_compact(head)) or _compact(head) in compact:
            return head
    return str(bank or "").strip()


def _normalize_amount(amount: str) -> str:
    raw = str(amount or "").strip()
    raw = raw.replace(",", "").replace("￥", "").replace("¥", "").replace("元", "").strip()
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"金额不是有效数字: [{amount}]") from exc
    if value <= 0:
        raise ValueError(f"金额必须大于 0: [{amount}]")
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized


def normalize_form(data: dict) -> dict:
    normalized = {}
    for key, value in data.items():
        canonical_key = _normalize_key(key)
        if canonical_key in REQUIRED_FIELDS:
            normalized[canonical_key] = str(value or "").strip()

    normalized["开户银行"] = _normalize_bank_head(normalized.get("开户银行", ""))
    if "金额" in normalized:
        normalized["金额"] = _normalize_amount(normalized["金额"])
    if not normalized.get("业务参考号"):
        normalized["业务参考号"] = "无"

    return {field: normalized.get(field, "") for field in REQUIRED_FIELDS}


def validate_or_raise(form: dict) -> None:
    ok, errors = validate_bank_form(form)
    if ok:
        return
    details = "\n".join(f"  - {item}" for item in errors)
    raise ValueError(f"字段校验失败：\n{details}")


def resolve_usb_port(form: dict) -> int | None:
    payer = (form.get("付款单位名称") or form.get("付款单位") or "").strip()
    for keyword, port in _USB_HUB_PORT_BY_PAYER.items():
        if keyword in payer:
            return port
    return None


def save_bank_form(form: dict) -> None:
    FORM_DIR.mkdir(parents=True, exist_ok=True)
    if BANK_FORM_PATH.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(BANK_FORM_PATH, BACKUP_DIR / f"bank_form_{stamp}.json")
    with BANK_FORM_PATH.open("w", encoding="utf-8") as fh:
        json.dump(form, fh, ensure_ascii=False, indent=4)
        fh.write("\n")


def print_form_preview(form: dict) -> None:
    print("\n将写入 bank_form.json 的内容：")
    print(json.dumps(form, ensure_ascii=False, indent=4))


def ask_for_pasted_json() -> str:
    print("=" * 70)
    print("手动录入招行制单 JSON")
    print("请粘贴下面这种 JSON；粘贴完成后，单独输入一行 END 再回车。")
    print("模板：")
    print(TEMPLATE)
    print("=" * 70)
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)
    return "\n".join(lines)


def ask_run_mode(default_run: bool = True) -> str:
    if default_run:
        prompt = "是否现在启动制单？直接回车=测试模式，N=只保存，PROD=生产模式："
    else:
        prompt = "是否现在启动制单？Y=测试模式，N=只保存，PROD=生产模式："
    answer = input(prompt).strip()
    upper = answer.upper()
    if upper in {"N", "NO", "否", "不"}:
        return "save"
    if upper == "PROD":
        return "prod"
    if not answer and default_run:
        return "test"
    if upper in {"Y", "YES", "是"}:
        return "test"
    print("未识别输入，按只保存处理。")
    return "save"


def ask_usb_port_if_needed(form: dict, selected_port: int | None) -> int | None:
    if selected_port:
        return selected_port
    known_port = resolve_usb_port(form)
    if known_port:
        print(f"付款单位已匹配 USBHub {known_port} 口。")
        return None

    payer = form.get("付款单位名称", "")
    print(f"付款单位 [{payer}] 暂未配置自动 USBHub 端口。")
    print("如果你确认当前 U 盾在哪个口，可以输入 1-30 强制本次使用；直接回车则只保存不运行。")
    answer = input("USBHub 端口：").strip()
    if not answer:
        return None
    try:
        port = int(answer)
    except ValueError:
        print("端口不是数字，已只保存不运行。")
        return None
    if not 1 <= port <= 30:
        print("端口必须在 1-30，已只保存不运行。")
        return None
    return port


def run_zhidan(mode: str, usb_port: int | None = None) -> int:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["ZHIDAN_TEST_MODE"] = "0" if mode == "prod" else "1"
    if usb_port:
        env["USB_HUB_FORCE_PORT"] = str(usb_port)
        env["ZHIDAN_ALLOW_FORCE_USB_PORT"] = "1"

    print("\n开始运行原制单流程...")
    print("运行模式：" + ("生产模式（会点击第二次经办）" if mode == "prod" else "测试模式（只点击第一次经办）"))
    if usb_port:
        print(f"本次强制 USBHub {usb_port} 口。")

    proc = subprocess.run(
        [sys.executable, str(ZHIDAN_SCRIPT)],
        cwd=str(SKILL_DIR),
        env=env,
    )
    return proc.returncode


def load_input_text(args: argparse.Namespace) -> str:
    if args.self_test:
        return TEMPLATE
    if args.input:
        return Path(args.input).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        piped = sys.stdin.read()
        if piped.strip():
            return piped
    return ask_for_pasted_json()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="手动粘贴 JSON 后写入 bank_form.json，并可启动招行制单。")
    parser.add_argument("--input", help="从文件读取 JSON/模板文本")
    parser.add_argument("--dry-run", action="store_true", help="只解析和校验，不写入 bank_form.json")
    parser.add_argument("--no-run", action="store_true", help="写入后不启动制单")
    parser.add_argument("--run", action="store_true", help="写入后直接启动测试模式制单")
    parser.add_argument("--prod", action="store_true", help="写入后直接启动生产模式制单（会点击第二次经办）")
    parser.add_argument("--usb-port", type=int, help="本次强制 USBHub 端口，仅在确认 U 盾归属后使用")
    parser.add_argument("--self-test", action="store_true", help="使用内置模板做解析校验测试，不写入、不运行")
    args = parser.parse_args(argv)

    try:
        text = load_input_text(args)
        if not text.strip():
            raise ValueError("没有读取到 JSON 内容")
        form = normalize_form(_parse_json_object(text))
        validate_or_raise(form)
        print_form_preview(form)

        if args.self_test or args.dry_run:
            print("\n解析和校验通过；未写入、未运行。")
            return 0

        save_bank_form(form)
        print(f"\n已写入: {BANK_FORM_PATH}")
        print(f"旧文件备份目录: {BACKUP_DIR}")

        if args.no_run:
            return 0

        mode = "prod" if args.prod else "test" if args.run else ask_run_mode(default_run=True)
        if mode == "save":
            return 0

        usb_port = ask_usb_port_if_needed(form, args.usb_port)
        if resolve_usb_port(form) is None and usb_port is None:
            print("未指定 USBHub 端口，已只保存 bank_form.json，不启动制单。")
            return 0
        return run_zhidan(mode, usb_port)
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except Exception as exc:
        print("=" * 70)
        print(f"手动录入失败: {exc}")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(main())
