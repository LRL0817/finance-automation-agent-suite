# -*- coding: utf-8 -*-
"""Run the latest M3 payment rows through CIB fill-only verification.

This runner intentionally lives in the CIB project so the M3 extractor can stay
unchanged. It reuses M3's field parser, then calls the existing CIB
run_transfer_flow(fill_only=True) path for each row.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright

from cib_open_bank.main_flow import run_transfer_flow
from cib_open_bank.runtime import all_off_usb_hub_ports, set_screenshot_prefix
from cib_open_bank.windows import close_bank_windows, dismiss_ukey_notice


try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


PROJECT_ROOT = Path(__file__).resolve().parent
DESKTOP = Path.home() / "Desktop"
DEFAULT_M3_ROOT = DESKTOP / "M3直供合同付款数据获取"
DEFAULT_GATEWAY_HOME = DESKTOP / "网关"
RUN_ROOT = PROJECT_ROOT / "screenshots"
AMOUNT_OVERRIDE = "0.01"
DEFAULT_USB_PORT = "12"
MAX_GATEWAY_IMAGES = 5
PAYMENT_ROW_PATTERN = r"YLHT[A-Z0-9-]*-\d{4}-[A-Z0-9-]+(?:-付-\d{4}-\d+)?"
BANK_WORD_RE = re.compile(r"^\s*(.+?银行)")
BANK_ALIASES = {
    "工商银行": "中国工商银行",
    "建设银行": "中国建设银行",
    "农业银行": "中国农业银行",
    "民生银行": "中国民生银行",
    "邮储银行": "中国邮政储蓄银行",
    "中国光大银行": "光大银行",
}
M3_IMAGE_KEYS = ("M3抓取详情截图", "M3合同付款列表截图")
M3_INFO_KEYS = (
    "申请人",
    "申请部门",
    "申请日期",
    "申请金额",
    "付款方式",
    "合同名称",
    "合同编号",
    "收款单位名称",
    "付款单位名称",
    "开户银行",
    "银行账户",
    "合同金额",
    "累计申请金额",
    "申请说明",
    "类别",
    "M3列表行文本",
)


@dataclass
class ExtractedTransfer:
    round_no: int
    row_ref: str
    row_text: str
    raw_data: dict
    bank_form: dict
    transfer: dict


class Tee(io.TextIOBase):
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for stream in self._streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self):
        for stream in self._streams:
            stream.flush()


@contextlib.contextmanager
def tee_output(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as log:
        tee_stdout = Tee(sys.__stdout__, log)
        tee_stderr = Tee(sys.__stderr__, log)
        with contextlib.redirect_stdout(tee_stdout), contextlib.redirect_stderr(tee_stderr):
            yield


def timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_m3_module(m3_root: Path):
    if not (m3_root / "step4_extract.py").exists():
        raise RuntimeError(f"M3 step4_extract.py not found under {m3_root}")
    root_text = str(m3_root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    import step4_extract as m3_step4  # type: ignore

    return m3_step4


def collect_payment_rows(m3_step4, page, limit: int) -> tuple[object, list[dict]]:
    frame, _ = m3_step4.find_frame_with_rows(page)
    rows = frame.evaluate(
        """
        ([patternText, limit]) => {
          const pattern = new RegExp(patternText);
          const seen = new Set();
          const result = [];
          const rowNodes = Array.from(document.querySelectorAll("tr"));
          for (let rowIndex = 0; rowIndex < rowNodes.length; rowIndex += 1) {
            const row = rowNodes[rowIndex];
            const text = (row.innerText || row.textContent || "").replace(/\\s+/g, " ").trim();
            if (!text || !pattern.test(text)) continue;
            const match = text.match(pattern);
            const flow = match ? match[0] : "";
            const key = flow || text;
            if (seen.has(key)) continue;
            seen.add(key);
            result.push({ rowIndex, flow, text: text.slice(0, 500) });
            if (result.length >= limit) break;
          }
          return result;
        }
        """,
        [PAYMENT_ROW_PATTERN, limit],
    )
    return frame, rows


def open_detail_for_row(context, page, m3_step4, row_index: int):
    frame, _ = m3_step4.find_frame_with_rows(page)
    handle = frame.evaluate_handle(
        """
        (rowIndex) => {
          const rows = Array.from(document.querySelectorAll("tr"));
          return rows[rowIndex] || null;
        }
        """,
        row_index,
    )
    element = handle.as_element()
    if element is None:
        raise RuntimeError(f"M3 list row disappeared: rowIndex={row_index}")
    element.scroll_into_view_if_needed()
    try:
        with context.expect_page(timeout=15000) as new_info:
            element.click()
        detail = new_info.value
    except Exception:
        with context.expect_page(timeout=15000) as new_info:
            element.dblclick()
        detail = new_info.value

    detail.wait_for_load_state("domcontentloaded")
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        for target in [detail, *detail.frames]:
            try:
                if target.locator("text=申请人").first.count() > 0:
                    detail.wait_for_timeout(800)
                    return detail
            except Exception:
                continue
        detail.wait_for_timeout(500)
    print("警告：等待详情页『申请人』超时，继续尝试抽取")
    return detail


def build_transfer(bank_form: dict, round_no: int) -> dict:
    ref = str(bank_form.get("业务参考号") or bank_form.get("label") or f"m3_round_{round_no:02d}").strip()
    account = re.sub(r"\D+", "", str(bank_form.get("acct_no") or bank_form.get("收方账号") or ""))
    transfer = {
        "label": ref or f"m3_round_{round_no:02d}",
        "amount": AMOUNT_OVERRIDE,
        "acct_no": account,
        "acct_name": str(bank_form.get("acct_name") or bank_form.get("收方户名") or "").strip(),
        "bank": str(bank_form.get("bank") or bank_form.get("开户银行") or "").strip(),
        "branch_full": str(bank_form.get("branch_full") or bank_form.get("支行名称") or "").strip(),
        "branch_queries": bank_form.get("branch_queries") or [],
        "purpose": str(bank_form.get("purpose") or bank_form.get("用途") or "货款").strip() or "货款",
    }
    if not isinstance(transfer["branch_queries"], list):
        transfer["branch_queries"] = [str(transfer["branch_queries"])]
    transfer["branch_queries"] = [str(item).strip() for item in transfer["branch_queries"] if str(item).strip()]
    return transfer


def enrich_transfer_with_m3_context(transfer: dict, raw_data: dict, bank_form: dict | None = None) -> dict:
    enriched = dict(transfer)
    sources = [raw_data or {}, bank_form or {}]
    for key in M3_INFO_KEYS + M3_IMAGE_KEYS:
        value = next((source.get(key) for source in sources if str(source.get(key) or "").strip()), "")
        if str(value or "").strip():
            enriched[key] = value
    original_amount = next(
        (source.get("原始金额") for source in sources if str(source.get("原始金额") or "").strip()),
        "",
    )
    if str(original_amount or "").strip():
        enriched["M3原始金额"] = original_amount
    return enriched


def read_json_if_exists(path: Path):
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except Exception as exc:
        print(f"读取 JSON 失败，已跳过 {path}: {exc}")
        return {}


def compact(value: object) -> str:
    return "".join(str(value or "").split())


def looks_like_account(value: object) -> bool:
    text = compact(value)
    digits = re.sub(r"\D+", "", text)
    return bool(text) and text == digits and 6 <= len(digits) <= 32


def looks_like_bank(value: object) -> bool:
    text = compact(value)
    return bool(text) and ("银行" in text or "信用社" in text or "农商" in text or "村镇" in text)


def normalize_bank_category(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = BANK_WORD_RE.search(text)
    category = match.group(1) if match else text
    for suffix in ("股份有限公司", "有限责任公司", "有限公司", "股份有限", "有限"):
        category = category.replace(suffix, "")
    category = category.replace("（中国）", "").replace("(中国)", "")
    category = category.strip()
    return BANK_ALIASES.get(category, category)


def repair_m3_data(data: dict) -> dict:
    """Apply generic account/bank-field repairs based on value shape."""
    repaired = dict(data)
    opening_bank = repaired.get("开户银行", "")
    bank_account = repaired.get("银行账户", "")
    swap_detected = looks_like_account(opening_bank) and looks_like_bank(bank_account)
    if swap_detected:
        repaired["开户银行"] = bank_account
        repaired["银行账户"] = opening_bank
        repaired["M3字段修正"] = "开户银行/银行账户疑似调换，已按账号和银行名称特征修正"
    return repaired


def prepare_bank_form(m3_step4, raw_data: dict) -> dict:
    data = repair_m3_data(raw_data)
    bank_form = m3_step4.build_bank_form(data)
    branch_full = str(bank_form.get("branch_full") or bank_form.get("支行名称") or data.get("开户银行") or "").strip()
    bank_category = normalize_bank_category(branch_full) or str(bank_form.get("bank") or bank_form.get("开户银行") or "").strip()
    if bank_category:
        bank_form["开户银行"] = bank_category
        bank_form["bank"] = bank_category
        bank_form["branch_queries"] = m3_step4.build_branch_queries(bank_category, branch_full)
    return bank_form


def validate_transfer(transfer: dict) -> list[str]:
    checks = {
        "金额": transfer.get("amount"),
        "收款账号": transfer.get("acct_no"),
        "收款户名": transfer.get("acct_name"),
        "开户行": transfer.get("bank"),
        "支行": transfer.get("branch_full"),
        "用途": transfer.get("purpose"),
    }
    errors = [f"缺少{field}" for field, value in checks.items() if not str(value or "").strip()]
    account = str(transfer.get("acct_no") or "").strip()
    if account and not account.isdigit():
        errors.append("收款账号不是纯数字")
    return errors


def extract_latest_transfers(m3_root: Path, limit: int, run_dir: Path) -> list[ExtractedTransfer]:
    m3_step4 = load_m3_module(m3_root)
    report_name = str(getattr(m3_step4, "CONTRACT_REPORT_NAME", "合同付款") or "合同付款")
    m3_data_dir = run_dir / "m3_data"
    m3_screen_dir = run_dir / "m3_screenshots"
    m3_data_dir.mkdir(parents=True, exist_ok=True)
    m3_screen_dir.mkdir(parents=True, exist_ok=True)

    transfers: list[ExtractedTransfer] = []
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            m3_step4.USER_DATA_DIR,
            headless=False,
            viewport={"width": 1366, "height": 800},
        )
        context.set_default_navigation_timeout(m3_step4.NAVIGATION_TIMEOUT_MS)
        try:
            page = m3_step4.ensure_logged_in_and_open_list(context)
            print(f"已进入 M3 {report_name}列表。")
            list_screenshot = m3_step4.save_screenshot(page, str(m3_screen_dir), f"00_{safe_name(report_name)}列表")
            _, rows = collect_payment_rows(m3_step4, page, limit)
            print(f"M3 列表可见付款行: {len(rows)}，目标: {limit}")
            if len(rows) < limit:
                raise RuntimeError(f"M3 列表只找到 {len(rows)} 条付款行，不足 {limit} 条")

            for round_no, row in enumerate(rows[:limit], start=1):
                detail = None
                row_ref = str(row.get("flow") or f"row_{round_no:02d}")
                print("\n" + "=" * 70)
                print(f"抽取 M3 第 {round_no}/{limit} 条: {row_ref}")
                print("=" * 70)
                try:
                    detail = open_detail_for_row(context, page, m3_step4, int(row["rowIndex"]))
                    data = m3_step4.extract_fields(detail)
                    detail_screenshot = m3_step4.save_screenshot(
                        detail,
                        str(m3_screen_dir),
                        f"{round_no:02d}_详情_{safe_name(row_ref)}",
                    )
                    if list_screenshot:
                        data["M3合同付款列表截图"] = list_screenshot
                    if detail_screenshot:
                        data["M3抓取详情截图"] = detail_screenshot
                    data["M3列表行文本"] = row.get("text", "")
                    bank_form = prepare_bank_form(m3_step4, data)
                    bank_form["原始金额"] = bank_form.get("金额", "")
                    bank_form["金额"] = AMOUNT_OVERRIDE
                    bank_form["amount"] = AMOUNT_OVERRIDE
                    bank_form["金额覆盖来源"] = "m3_latest30_cib_fill_only_runner"
                    transfer = enrich_transfer_with_m3_context(build_transfer(bank_form, round_no), data, bank_form)
                    write_json(m3_data_dir / f"{round_no:02d}_raw.json", data)
                    write_json(m3_data_dir / f"{round_no:02d}_bank_form.json", bank_form)
                    write_json(m3_data_dir / f"{round_no:02d}_cib_transfer.json", transfer)
                    transfers.append(
                        ExtractedTransfer(
                            round_no=round_no,
                            row_ref=row_ref,
                            row_text=str(row.get("text") or ""),
                            raw_data=data,
                            bank_form=bank_form,
                            transfer=transfer,
                        )
                    )
                    print(
                        f"字段映射: 收款方={transfer['acct_name']} 账号={mask_account(transfer['acct_no'])} "
                        f"开户行={transfer['bank']} 支行={transfer['branch_full']} 金额={transfer['amount']} 用途={transfer['purpose']}"
                    )
                finally:
                    if detail is not None:
                        try:
                            detail.close()
                        except Exception:
                            pass
        finally:
            context.close()
    return transfers


def parse_round_selection(value: str) -> set[int]:
    selected: set[int] = set()
    for part in (item.strip() for item in value.split(",") if item.strip()):
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            selected.update(range(min(start, end), max(start, end) + 1))
        else:
            selected.add(int(part))
    return selected


def normalize_loaded_transfer(transfer: dict) -> dict:
    normalized = dict(transfer)
    normalized["acct_no"] = re.sub(r"\D+", "", str(normalized.get("acct_no") or ""))
    normalized["amount"] = AMOUNT_OVERRIDE
    normalized["purpose"] = str(normalized.get("purpose") or "货款").strip() or "货款"
    return normalized


def load_transfers_json(path: Path, selected_rounds: set[int] | None = None) -> list[ExtractedTransfer]:
    payload = read_json(path)
    if not isinstance(payload, list):
        raise RuntimeError(f"transfers JSON must be a list: {path}")
    transfers: list[ExtractedTransfer] = []
    sidecar_dir = path.resolve().parent / "m3_data"
    for round_no, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            continue
        if selected_rounds and round_no not in selected_rounds:
            continue
        raw_data = read_json_if_exists(sidecar_dir / f"{round_no:02d}_raw.json")
        bank_form = read_json_if_exists(sidecar_dir / f"{round_no:02d}_bank_form.json")
        transfer = enrich_transfer_with_m3_context(normalize_loaded_transfer(item), raw_data, bank_form)
        transfers.append(
            ExtractedTransfer(
                round_no=round_no,
                row_ref=str(transfer.get("label") or f"round_{round_no:02d}"),
                row_text="",
                raw_data=raw_data,
                bank_form=bank_form,
                transfer=transfer,
            )
        )
    return transfers


def mask_account(value: str) -> str:
    digits = re.sub(r"\D+", "", value or "")
    if len(digits) <= 8:
        return digits
    return f"{digits[:4]}...{digits[-4:]}"


def safe_name(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r'[<>:"/\\|?*\s]+', "_", text)
    return text[:80] or "unknown"


def tail_text(path: Path, max_lines: int = 80) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


def concise_failure_reason(log_tail: str, exc_text: str) -> tuple[str, str, bool]:
    combined = "\n".join([log_tail, exc_text])
    bank_picker_markers = (
        "未能精确选择银行大类",
        "未能通过任何关键字精确选择目标银行大类",
        "收款行未选中",
    )
    if any(marker in combined for marker in bank_picker_markers):
        reason_lines = [
            line.strip()
            for line in reversed(combined.splitlines())
            if any(marker in line for marker in bank_picker_markers)
        ]
        return "收款行/开户行", (reason_lines[0] if reason_lines else "收款行候选未精确命中")[:160], True

    field_rules = {
        "登录": ("登录失败", "登录卡片", "登录名", "密码框", "登录页"),
        "网盾": ("网盾", "UKey", "U盾"),
        "金额": ("金额未找到", "未找到金额", "金额输入框"),
        "收款账号": ("收款账号", "账号"),
        "收款户名": ("收款户名", "户名"),
        "收款行/开户行": ("收款行", "开户行", "银行大类", "支行", "填入", "查询"),
        "用途": ("用途",),
        "USB": ("USB Hub", "端口", "COM"),
    }
    problem_field = "流程"
    for field, tokens in field_rules.items():
        if any(token in combined for token in tokens):
            problem_field = field
            break

    reason_lines = [
        line.strip()
        for line in reversed(combined.splitlines())
        if any(token in line for token in ("错误", "失败", "未能", "未找到", "停止", "异常", "不足"))
    ]
    reason = reason_lines[0] if reason_lines else (exc_text or "未知错误")
    needs_human = problem_field in {"收款行/开户行", "收款账号", "收款户名"} or any(
        token in combined for token in ("多个", "未能精确", "候选", "不足")
    )
    return problem_field, reason[:160], needs_human


def notify_failure(round_no: int, transfer: dict, field: str, reason: str) -> bool:
    script = Path(os.getenv("CODEX_GATEWAY_HOME", str(DEFAULT_GATEWAY_HOME))) / "scripts" / "report_to_codex.py"
    if not script.exists():
        print("[飞书通知] 网关脚本不存在，跳过失败通知")
        return False
    payee = transfer.get("acct_name") or "未取到收款方"
    message = f"兴业测试第{round_no}轮失败；收款方：{payee}；问题字段：{field}；原因：{reason}"
    cmd = [
        sys.executable,
        str(script),
        "--project-path",
        str(PROJECT_ROOT),
        "--title",
        "兴业M3测试制单失败",
        "--status",
        "failed",
        "--error",
        message,
        "--context",
        "fill_only测试；未设置CIB_ALLOW_SUBMIT",
        "--source",
        "兴业M3 latest30 fill-only",
    ]
    image_path = transfer.get("M3抓取详情截图") or transfer.get("M3合同付款列表截图") or ""
    if image_path:
        cmd.extend(["--image-path", str(image_path)])
    completed = subprocess.run(cmd, cwd=str(PROJECT_ROOT), text=True, encoding="utf-8", errors="replace", check=False)
    print(f"[飞书通知] 第{round_no}轮失败通知退出码: {completed.returncode}")
    return completed.returncode == 0


def m3_detail_image_path(transfer: dict) -> str:
    for key in M3_IMAGE_KEYS:
        image_path = str(transfer.get(key) or "").strip()
        if image_path and Path(image_path).is_file():
            return image_path
    return ""


def status_label(status: str) -> str:
    labels = {
        "success": "成功",
        "failed": "失败",
        "extract_only": "仅抽取",
    }
    return labels.get(status, status or "未知")


def public_record(record: dict) -> dict:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def build_m3_report_records(results: list[dict], transfers: list[ExtractedTransfer]) -> list[dict]:
    transfers_by_round = {item.round_no: item for item in transfers}
    records: list[dict] = []
    for result in results:
        round_no = int(result.get("round") or 0)
        item = transfers_by_round.get(round_no)
        transfer = item.transfer if item else {}
        raw_data = item.raw_data if item else {}
        m3_info = {key: raw_data.get(key, transfer.get(key, "")) for key in M3_INFO_KEYS}
        m3_info = {key: value for key, value in m3_info.items() if str(value or "").strip()}
        mapped_fields = {
            "收款账号": transfer.get("acct_no", ""),
            "收款户名": transfer.get("acct_name", ""),
            "开户行": transfer.get("bank", ""),
            "支行": transfer.get("branch_full", ""),
            "测试金额": transfer.get("amount", ""),
            "用途": transfer.get("purpose", ""),
        }
        records.append(
            {
                "轮次": round_no,
                "合同编号": transfer.get("label") or result.get("label", ""),
                "状态": status_label(str(result.get("status") or "")),
                "问题字段": result.get("problem_field", ""),
                "问题原因": result.get("reason", ""),
                "需要人工确认": bool(result.get("needs_human")),
                "M3原始信息": m3_info,
                "兴业填单字段": mapped_fields,
                "_image_path": m3_detail_image_path(transfer),
            }
        )
    return records


def direct_m3_images(records: list[dict]) -> list[str]:
    images: list[str] = []
    for record in records:
        image_path = str(record.get("_image_path") or "").strip()
        if image_path and image_path not in images:
            images.append(image_path)
    return images


def create_m3_contact_sheets(records: list[dict], run_dir: Path) -> list[str]:
    image_records = [record for record in records if str(record.get("_image_path") or "").strip()]
    if not image_records:
        return []
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps

        output_dir = run_dir / "feishu_m3_contact_sheets"
        output_dir.mkdir(parents=True, exist_ok=True)
        columns = 2
        per_sheet = 10
        thumb_size = (900, 528)
        padding = 24
        label_height = 48
        font = ImageFont.load_default()
        outputs: list[str] = []
        for sheet_no, start in enumerate(range(0, len(image_records), per_sheet), start=1):
            chunk = image_records[start : start + per_sheet]
            rows = (len(chunk) + columns - 1) // columns
            tile_width = thumb_size[0] + padding * 2
            tile_height = thumb_size[1] + label_height + padding * 2
            sheet = Image.new("RGB", (columns * tile_width, rows * tile_height), "white")
            draw = ImageDraw.Draw(sheet)
            for idx, record in enumerate(chunk):
                source = Image.open(str(record["_image_path"])).convert("RGB")
                thumb = ImageOps.contain(source, thumb_size)
                col = idx % columns
                row = idx // columns
                x0 = col * tile_width + padding
                y0 = row * tile_height + padding
                title = f"Round {record.get('轮次')} {record.get('合同编号')}"
                draw.text((x0, y0), title[:120], fill=(0, 0, 0), font=font)
                sheet.paste(thumb, (x0, y0 + label_height))
            output = output_dir / f"m3_contact_sheet_{sheet_no:02d}.jpg"
            sheet.save(output, "JPEG", quality=88, optimize=True)
            outputs.append(str(output))
            if len(outputs) >= MAX_GATEWAY_IMAGES:
                break
        return outputs
    except Exception as exc:
        print(f"[飞书最终通知] 生成 M3 联页汇总图失败，改用原图前 {MAX_GATEWAY_IMAGES} 张: {exc}")
        return direct_m3_images(records)[:MAX_GATEWAY_IMAGES]


def overall_report_status(summary: dict, extract_only: bool) -> str:
    status = "success"
    if summary.get("extraction_error") or int(summary.get("failed_count") or 0) > 0:
        status = "failed"
    if extract_only and status == "success":
        status = "info"
    return status


def notify_final_report(args, summary: dict, transfers: list[ExtractedTransfer], results: list[dict], run_dir: Path) -> dict:
    script = Path(os.getenv("CODEX_GATEWAY_HOME", str(DEFAULT_GATEWAY_HOME))) / "scripts" / "report_to_codex.py"
    if not script.exists():
        print("[飞书最终通知] 网关脚本不存在，跳过最终通知")
        return {"attempted": False, "ok": False, "reason": "gateway script missing"}

    records = build_m3_report_records(results, transfers)
    image_paths = create_m3_contact_sheets(records, run_dir)
    report_payload = {
        "汇总": {
            "总条数": summary.get("extracted_count", 0),
            "成功": summary.get("success_count", 0),
            "失败": summary.get("failed_count", 0),
            "需要人工确认": summary.get("needs_human_rounds", []),
            "抽取错误": summary.get("extraction_error", ""),
            "安全边界": summary.get("safety", {}),
            "清理结果": summary.get("cleanup", {}),
            "M3图片": f"已附 {len(image_paths)} 张 M3 信息联页汇总图",
        },
        "明细": [public_record(record) for record in records],
    }
    status = overall_report_status(summary, bool(args.extract_only))
    title = "兴业M3制单验证结果" if not args.extract_only else "兴业M3数据抽取验证结果"
    cmd = [
        sys.executable,
        str(script),
        "--project-path",
        str(PROJECT_ROOT),
        "--title",
        title,
        "--status",
        status,
        "--error",
        f"兴业M3流程已跑完：成功{summary.get('success_count', 0)}条，失败{summary.get('failed_count', 0)}条。",
        "--context",
        "用户要求实际/验证跑完后都通过飞书发送 M3 信息图片、成功失败状态和遇到的问题；本次未设置 CIB_ALLOW_SUBMIT。",
        "--log",
        json.dumps(report_payload, ensure_ascii=False, indent=2),
        "--source",
        "兴业M3最终状态通知",
    ]
    for image_path in image_paths:
        cmd.extend(["--image-path", image_path])
    completed = subprocess.run(cmd, cwd=str(PROJECT_ROOT), text=True, encoding="utf-8", errors="replace", check=False)
    print(f"[飞书最终通知] 退出码: {completed.returncode}")
    return {
        "attempted": True,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "image_count": len(image_paths),
    }


def prepare_safe_environment(usb_port: str) -> None:
    os.environ.pop("CIB_ALLOW_SUBMIT", None)
    os.environ["CIB_USB_HUB_PORT"] = usb_port
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


def run_one_transfer(item: ExtractedTransfer, run_dir: Path, usb_port: str) -> dict:
    round_dir = run_dir / f"round_{item.round_no:02d}_{safe_name(item.transfer.get('label', ''))}"
    round_dir.mkdir(parents=True, exist_ok=True)
    log_path = round_dir / "round.log"
    screenshot_subdir = f"{run_dir.name}\\round_{item.round_no:02d}_{safe_name(item.transfer.get('label', ''))}\\"
    prefix = f"{screenshot_subdir}cib_"
    result = {
        "round": item.round_no,
        "label": item.transfer.get("label", ""),
        "payee": item.transfer.get("acct_name", ""),
        "account_masked": mask_account(item.transfer.get("acct_no", "")),
        "bank": item.transfer.get("bank", ""),
        "branch_full": item.transfer.get("branch_full", ""),
        "amount": item.transfer.get("amount", ""),
        "purpose": item.transfer.get("purpose", ""),
        "status": "failed",
        "problem_field": "",
        "reason": "",
        "needs_human": False,
        "log": str(log_path),
        "screenshot_dir": str(round_dir),
        "feishu_notified": False,
    }

    with tee_output(log_path):
        print(f"开始兴业 fill-only 第 {item.round_no} 轮")
        print(json.dumps({k: item.transfer.get(k) for k in ("label", "amount", "acct_no", "acct_name", "bank", "branch_full", "branch_queries", "purpose")}, ensure_ascii=False, indent=2))
        errors = validate_transfer(item.transfer)
        if errors:
            reason = "；".join(errors)
            print(f"预检失败: {reason}")
            result.update({"problem_field": "字段预检", "reason": reason, "needs_human": True})
            result["feishu_notified"] = notify_failure(item.round_no, item.transfer, "字段预检", reason)
            return result

        prepare_safe_environment(usb_port)
        set_screenshot_prefix(prefix)
        exc_text = ""
        try:
            run_transfer_flow(transfer=item.transfer, fill_only=True, screenshot_prefix=prefix)
            result.update({"status": "success", "problem_field": "", "reason": "fill_only 已填完并截图，未点击下一步/提交"})
            print(f"第 {item.round_no} 轮成功：fill_only 完成，未点击下一步/提交。")
        except SystemExit as exc:
            exc_text = f"SystemExit({exc.code})"
            print(f"第 {item.round_no} 轮失败: {exc_text}")
        except Exception as exc:
            exc_text = f"{type(exc).__name__}: {exc}"
            print(f"第 {item.round_no} 轮失败: {exc_text}")
        finally:
            set_screenshot_prefix("")
            print("[轮次收尾] 关闭兴业银行窗口...")
            try:
                close_bank_windows()
            except Exception as exc:
                print(f"关闭窗口异常: {exc}")
            print("[轮次收尾] USB Hub all-off...")
            try:
                all_off_usb_hub_ports()
            except Exception as exc:
                print(f"USB all-off 异常: {exc}")
            try:
                dismiss_ukey_notice(wait_seconds=5)
            except Exception as exc:
                print(f"关闭网盾提示异常: {exc}")

        if result["status"] != "success":
            field, reason, needs_human = concise_failure_reason(tail_text(log_path), exc_text)
            result.update({"problem_field": field, "reason": reason, "needs_human": needs_human})
            result["feishu_notified"] = notify_failure(item.round_no, item.transfer, field, reason)
    return result


def final_cleanup() -> dict:
    cleanup = {"closed_bank_windows": False, "usb_all_off": False, "dismissed_notice": False}
    try:
        cleanup["closed_bank_windows"] = bool(close_bank_windows())
    except Exception as exc:
        cleanup["close_error"] = str(exc)
    try:
        cleanup["usb_all_off"] = bool(all_off_usb_hub_ports())
    except Exception as exc:
        cleanup["usb_error"] = str(exc)
    try:
        cleanup["dismissed_notice"] = bool(dismiss_ukey_notice(wait_seconds=5))
    except Exception as exc:
        cleanup["notice_error"] = str(exc)
    set_screenshot_prefix("")
    return cleanup


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m3-root", default=str(DEFAULT_M3_ROOT), help="M3 payment extractor project root")
    parser.add_argument("--limit", type=int, default=30, help="latest rows to extract and test")
    parser.add_argument("--usb-port", default=DEFAULT_USB_PORT, help="CIB USB hub port for this test")
    parser.add_argument("--extract-only", action="store_true", help="only extract and map data; do not open CIB")
    parser.add_argument("--run-dir", default="", help="optional existing/new run directory")
    parser.add_argument("--transfers-json", default="", help="reuse an existing transfers.json instead of opening M3")
    parser.add_argument("--rounds", default="", help="selected rounds to run, e.g. 6,7,8,12,14-30")
    return parser.parse_args(argv)


def expected_target_count(args, selected_rounds: set[int] | None) -> int:
    if not selected_rounds:
        return args.limit
    return len(selected_rounds)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_dir = Path(args.run_dir) if args.run_dir else RUN_ROOT / f"m3_cib_latest30_{timestamp()}"
    if not run_dir.is_absolute():
        run_dir = PROJECT_ROOT / run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_path = run_dir / "summary.json"

    print(f"运行目录: {run_dir}")
    print(f"安全设置: amount={AMOUNT_OVERRIDE}, CIB_USB_HUB_PORT={args.usb_port}, CIB_ALLOW_SUBMIT=未设置, fill_only=True")
    prepare_safe_environment(args.usb_port)

    results: list[dict] = []
    extraction_error = ""
    selected_rounds = parse_round_selection(args.rounds) if args.rounds else None
    target_count = expected_target_count(args, selected_rounds)
    try:
        if args.transfers_json:
            transfers = load_transfers_json(Path(args.transfers_json), selected_rounds)
            print(f"已从 transfers.json 载入 {len(transfers)} 条待跑轮次")
        else:
            transfers = extract_latest_transfers(Path(args.m3_root), args.limit, run_dir)
            if selected_rounds:
                transfers = [item for item in transfers if item.round_no in selected_rounds]
    except (PlaywrightTimeout, Exception) as exc:
        extraction_error = f"{type(exc).__name__}: {exc}"
        print(f"M3 latest {args.limit} 抽取/载入失败: {extraction_error}")
        transfers = []

    write_json(
        run_dir / "transfers.json",
        [item.transfer for item in transfers],
    )

    if not args.extract_only:
        for item in transfers:
            result = run_one_transfer(item, run_dir, args.usb_port)
            results.append(result)
            write_json(summary_path, build_summary(args, run_dir, results, extraction_error, cleanup={}, target_count=target_count))
    else:
        results = [
            {
                "round": item.round_no,
                "label": item.transfer.get("label", ""),
                "payee": item.transfer.get("acct_name", ""),
                "account_masked": mask_account(item.transfer.get("acct_no", "")),
                "bank": item.transfer.get("bank", ""),
                "branch_full": item.transfer.get("branch_full", ""),
                "amount": item.transfer.get("amount", ""),
                "purpose": item.transfer.get("purpose", ""),
                "status": "extract_only",
                "problem_field": "",
                "reason": "仅抽取映射，未打开兴业",
                "needs_human": False,
            }
            for item in transfers
        ]

    cleanup = final_cleanup()
    summary = build_summary(args, run_dir, results, extraction_error, cleanup, target_count=target_count)
    final_notification = notify_final_report(args, summary, transfers, results, run_dir)
    summary["final_feishu_notification"] = final_notification
    write_json(summary_path, summary)
    print("\n=== 兴业 M3 latest30 fill-only 汇总 ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    notification_failed = not bool(final_notification.get("ok"))
    return 1 if extraction_error or any(item["status"] == "failed" for item in results) or len(transfers) < target_count or notification_failed else 0


def build_summary(args, run_dir: Path, results: list[dict], extraction_error: str, cleanup: dict, target_count: int | None = None) -> dict:
    target_count = args.limit if target_count is None else target_count
    success = [item for item in results if item.get("status") == "success"]
    failed = [item for item in results if item.get("status") == "failed"]
    needs_human = [item for item in results if item.get("needs_human")]
    return {
        "run_dir": str(run_dir),
        "target_count": target_count,
        "extracted_count": len(results),
        "success_count": len(success),
        "failed_count": len(failed),
        "needs_human_rounds": [
            {
                "round": item.get("round"),
                "payee": item.get("payee"),
                "field": item.get("problem_field"),
                "reason": item.get("reason"),
            }
            for item in needs_human
        ],
        "safety": {
            "amount_override": AMOUNT_OVERRIDE,
            "usb_port": args.usb_port,
            "allow_submit": os.environ.get("CIB_ALLOW_SUBMIT", ""),
            "fill_only": True,
        },
        "extraction_error": extraction_error,
        "cleanup": cleanup,
        "rounds": results,
    }


if __name__ == "__main__":
    raise SystemExit(main())
