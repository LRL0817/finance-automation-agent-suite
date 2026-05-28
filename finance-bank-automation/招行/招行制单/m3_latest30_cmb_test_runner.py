# -*- coding: utf-8 -*-
"""Fetch latest M3 payments and run them through CMB ZHIDAN in test mode.

This is a guarded batch runner for the user-requested M3 -> 招行制单 test flow:

- Extract up to N latest contract-payment details from the external M3 project.
- Override every transfer amount to 0.01.
- Run the existing 招行制单 entrypoint in ZHIDAN_TEST_MODE=1.
- Force a test USB Hub port only with the paired allow env var.
- Collect logs, per-item result JSON, and loose U-BANK screenshots into one batch folder.

It does not alter the production submit gate. The second 经办 click remains disabled.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SCRIPT_PATH = Path(__file__).resolve()
CMB_ROOT = SCRIPT_PATH.parent
FINANCE_ROOT = CMB_ROOT.parent.parent
DESKTOP = Path.home() / "Desktop"
DEFAULT_M3_ROOT = DESKTOP / "M3直供合同付款数据获取"
M3_ROOT = Path(os.getenv("M3_ROOT", str(DEFAULT_M3_ROOT)))

CMB_ENTRY = CMB_ROOT / "招行" / "skills" / "制单单账号单笔转账skill" / "制单.py"
CMB_FORM_PATH = CMB_ROOT / "M3直供合同付款数据获取" / "bank_form.json"
SCREENSHOT_ROOT = CMB_ROOT / "招行" / "screenshots"
RUNS_ROOT = SCREENSHOT_ROOT

FLOW_PATTERN = r"YLHT[A-Z0-9-]*-\d{4}-[A-Z0-9-]+-付-\d{4}-\d+"
DEFAULT_AMOUNT = "0.01"
DEFAULT_USB_PORT = 7
DEFAULT_LIMIT = 30
DEFAULT_TIMEOUT_SECONDS = 420
DEFAULT_REPORT_NAME = os.getenv("M3_CONTRACT_REPORT_NAME", "合同付款（凭证）")
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
CITY_COMMERCIAL_BANK_KEYWORDS = (
    "江苏银行",
    "浙江泰隆商业银行",
    "广东南粤银行",
    "汉口银行",
    "临商银行",
    "杭州银行",
    "北京银行",
    "上海银行",
    "南京银行",
    "宁波银行",
    "徽商银行",
    "盛京银行",
    "齐鲁银行",
    "青岛银行",
    "重庆银行",
    "成都银行",
    "广州银行",
    "长沙银行",
    "厦门银行",
    "苏州银行",
)
RURAL_COMMERCIAL_BANK_KEYWORDS = ("农村商业银行", "农商银行")
RURAL_CREDIT_KEYWORDS = ("农村信用社",)
MAJOR_BANK_HEAD_PATTERNS = (
    ("中国工商银行", "中国工商银行"),
    ("工商银行", "中国工商银行"),
    ("中国农业银行", "中国农业银行"),
    ("农业银行", "中国农业银行"),
    ("中国建设银行", "中国建设银行"),
    ("建设银行", "中国建设银行"),
    ("中国银行", "中国银行"),
    ("交通银行", "交通银行"),
    ("招商银行", "招商银行"),
    ("中国邮政储蓄银行", "中国邮政储蓄银行"),
    ("邮政储蓄银行", "中国邮政储蓄银行"),
    ("邮储银行", "中国邮政储蓄银行"),
    ("兴业银行", "兴业银行"),
    ("中信银行", "中信银行"),
    ("中国光大银行", "中国光大银行"),
    ("光大银行", "中国光大银行"),
    ("华夏银行", "华夏银行"),
    ("广发银行", "广发银行"),
    ("平安银行", "平安银行"),
    ("上海浦东发展银行", "上海浦东发展银行"),
    ("浦发银行", "上海浦东发展银行"),
    ("中国民生银行", "中国民生银行"),
    ("民生银行", "中国民生银行"),
    ("浙商银行", "浙商银行"),
    ("恒丰银行", "恒丰银行"),
    ("渤海银行", "渤海银行"),
)
BRANCH_MARKERS = ("支行", "分行", "营业部", "分理处", "办事处")
CMB_PAYMENT_BANK_NAMES = {"招行", "招商银行"}


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass


@dataclass
class AttemptResult:
    exit_code: int
    timed_out: bool
    log_path: Path
    err_path: Path
    screenshots: list[Path]
    final_screenshot: Path | None
    log_text: str


def load_m3_module():
    if not M3_ROOT.exists():
        raise RuntimeError(f"M3 目录不存在: {M3_ROOT}")
    sys.path.insert(0, str(M3_ROOT))
    import step4_extract as m3  # type: ignore

    return m3


def disable_proxy_for_oa() -> None:
    """OA is reachable directly on this machine; the local proxy closes TLS."""
    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    no_proxy_hosts = [
        "newoa.guorui.net",
        "*.guorui.net",
        "111.203.220.108",
        "localhost",
        "127.0.0.1",
        "::1",
    ]
    os.environ["NO_PROXY"] = ",".join(no_proxy_hosts)
    os.environ["no_proxy"] = os.environ["NO_PROXY"]


def now_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def safe_filename(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r'[<>:"/\\|?*]+', "_", text)
    return text or "unknown"


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def apply_cmb_form_corrections(form: dict) -> dict:
    """Normalize M3 bank names to the broad heads used by CMB U-BANK."""
    item = dict(form)
    bank = str(item.get("开户银行") or item.get("bank") or "").strip()
    branch = str(item.get("支行名称") or item.get("branch_full") or "").strip()
    if not branch and any(marker in bank for marker in BRANCH_MARKERS):
        branch = bank
        item["支行名称"] = branch
        item["branch_full"] = branch
        item["自动支行来源"] = "开户银行字段含支行名，已复制到支行名称"
    combined = f"{bank} {branch}"

    bank_head = ""
    for keyword, normalized in MAJOR_BANK_HEAD_PATTERNS:
        if keyword in combined:
            bank_head = normalized
            break
    if not bank_head and any(keyword in combined for keyword in CITY_COMMERCIAL_BANK_KEYWORDS):
        bank_head = "城市商业银行"
    elif not bank_head and any(keyword in combined for keyword in RURAL_COMMERCIAL_BANK_KEYWORDS):
        bank_head = "农村商业银行"
    elif not bank_head and any(keyword in combined for keyword in RURAL_CREDIT_KEYWORDS):
        bank_head = "农村信用社"

    if bank_head and bank != bank_head:
        item["原开户银行"] = bank
        item["开户银行"] = bank_head
        item["bank"] = bank_head
        item["自动银行大类修正"] = f"{bank} -> {bank_head}"

    if branch:
        original_queries = item.get("branch_queries") or []
        if original_queries != [branch]:
            item["原branch_queries"] = original_queries
        item["branch_queries"] = [branch]

    return item


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return path.read_text(errors="replace")


def parse_indexes(value: str | None) -> set[int] | None:
    if not value:
        return None
    indexes: set[int] = set()
    for chunk in value.split(","):
        part = chunk.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start <= 0 or end < start:
                raise ValueError(f"非法索引范围: {part}")
            indexes.update(range(start, end + 1))
        else:
            index = int(part)
            if index <= 0:
                raise ValueError(f"非法索引: {part}")
            indexes.add(index)
    return indexes


def parse_overrides(value: str | None) -> dict[int, str]:
    if not value:
        return {}
    overrides: dict[int, str] = {}
    for chunk in re.split(r"[;,]", value):
        part = chunk.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"覆盖参数必须形如 7=值: {part}")
        index_text, replacement = part.split("=", 1)
        index = int(index_text.strip())
        if index <= 0:
            raise ValueError(f"非法覆盖索引: {index_text}")
        replacement = replacement.strip()
        if not replacement:
            raise ValueError(f"覆盖值不能为空: {part}")
        overrides[index] = replacement
    return overrides


def load_existing_forms(
    forms_json: Path,
    only_indexes: set[int] | None,
    city_bank_indexes: set[int] | None,
    branch_overrides: dict[int, str] | None = None,
) -> list[dict]:
    with forms_json.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, list):
        raise RuntimeError(f"forms-json 必须是列表: {forms_json}")
    forms: list[dict] = []
    for source_index, form in enumerate(payload, start=1):
        if only_indexes and source_index not in only_indexes:
            continue
        if not isinstance(form, dict):
            raise RuntimeError(f"forms-json 第 {source_index} 条不是对象")
        item = apply_cmb_form_corrections(form)
        if city_bank_indexes and source_index in city_bank_indexes:
            original_bank = str(item.get("开户银行") or item.get("bank") or "")
            item["原开户银行"] = original_bank
            item["开户银行"] = "城市商业银行"
            item["bank"] = "城市商业银行"
            item["银行大类修正"] = f"{original_bank} -> 城市商业银行"
            branch = str(item.get("支行名称") or item.get("branch_full") or original_bank).strip()
            if branch and not item.get("branch_queries"):
                item["branch_queries"] = [branch]
        if branch_overrides and source_index in branch_overrides:
            original_branch = str(item.get("支行名称") or item.get("branch_full") or "")
            replacement = branch_overrides[source_index]
            item["原支行名称"] = original_branch
            item["支行名称"] = replacement
            item["branch_full"] = replacement
            item["branch_queries"] = [replacement]
            item["支行修正"] = f"{original_branch} -> {replacement}"
        item["__runner_index"] = source_index
        forms.append(item)
    return forms


def find_python() -> str:
    configured = os.getenv("BANK_ROUTE_PYTHON", "").strip()
    if configured:
        return configured
    local_appdata = os.getenv("LOCALAPPDATA", "")
    py312 = Path(local_appdata) / "Programs" / "Python" / "Python312" / "python.exe"
    if py312.exists():
        return str(py312)
    return sys.executable


def first_existing_path(candidates: Iterable[Path | str | None]) -> Path | None:
    fallback: Path | None = None
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(str(candidate))
        if fallback is None:
            fallback = path
        if path.exists():
            return path
    return fallback


def resolve_hub_ctrl() -> Path | None:
    return first_existing_path(
        [
            os.getenv("ZHIDAN_USB_HUB_CTRL"),
            os.getenv("USB_HUB_CTRL"),
            FINANCE_ROOT / "公共" / "usbhub" / "usbhub" / "多口USB控制器软件以及驱动" / "hub_ctrl.py",
            DESKTOP / "财务" / "公共" / "usbhub" / "usbhub" / "多口USB控制器软件以及驱动" / "hub_ctrl.py",
            DESKTOP / "usbhub" / "usbhub" / "多口USB控制器软件以及驱动" / "hub_ctrl.py",
        ]
    )


def append_usb_cleanup_log(run_dir: Path, text: str) -> None:
    with (run_dir / "usb_cleanup.log").open("a", encoding="utf-8", errors="replace") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def stop_residual_workflow(reason: str, run_dir: Path, python_exe: str) -> None:
    print(f"[{reason}] 清理 U-BANK 与 USB Hub...")
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/IM", "Firmbank.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception as exc:
        append_usb_cleanup_log(run_dir, f"taskkill failed: {exc}")

    hub_ctrl = resolve_hub_ctrl()
    if not hub_ctrl or not hub_ctrl.exists():
        append_usb_cleanup_log(run_dir, f"hub_ctrl not found: {hub_ctrl}")
        return
    try:
        completed = subprocess.run(
            [python_exe, str(hub_ctrl), "all-off", "--com", os.getenv("USB_HUB_COM", "COM3")],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        append_usb_cleanup_log(
            run_dir,
            f"=== {reason} {time.strftime('%Y-%m-%d %H:%M:%S')} exit={completed.returncode} ===\n"
            + (completed.stdout or ""),
        )
    except Exception as exc:
        append_usb_cleanup_log(run_dir, f"USB cleanup failed: {exc}")


def run_generated_artifact_cleanup(run_dir: Path) -> None:
    if os.getenv("ZHIDAN_AUTO_CLEANUP_ARTIFACTS", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    cleanup = CMB_ROOT / "cleanup_generated_artifacts.ps1"
    if not cleanup.exists():
        return

    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(cleanup),
        "-Apply",
        "-KeepBatchDays",
        os.getenv("ZHIDAN_CLEANUP_KEEP_BATCH_DAYS", "30"),
        "-KeepRecentBatches",
        os.getenv("ZHIDAN_CLEANUP_KEEP_RECENT_BATCHES", "100"),
        "-PurgeArchiveDays",
        os.getenv("ZHIDAN_CLEANUP_PURGE_ARCHIVE_DAYS", "90"),
    ]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(CMB_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
        (run_dir / "artifact_cleanup.log").write_text(
            completed.stdout or "",
            encoding="utf-8",
            errors="replace",
        )
        print(f"[清理] 招行运行产物清理退出码: {completed.returncode}")
    except Exception as exc:
        (run_dir / "artifact_cleanup.log").write_text(
            f"artifact cleanup failed: {exc}",
            encoding="utf-8",
            errors="replace",
        )
        print(f"[清理] 招行运行产物清理失败: {exc}")


def capture_page_screenshot(page, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(path), full_page=True)
        print(f"截图已保存: {path}")
        return str(path)
    except Exception as exc:
        print(f"警告：截图失败 [{path.name}]: {exc}")
        return ""


def click_first_visible(page, locators: list, label: str, timeout_ms: int = 15000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    last_error = ""
    while time.monotonic() < deadline:
        for loc in locators:
            try:
                count = min(loc.count(), 80)
            except Exception as exc:
                last_error = str(exc)
                continue
            for i in range(count):
                el = loc.nth(i)
                try:
                    if not el.is_visible(timeout=500):
                        continue
                    el.scroll_into_view_if_needed(timeout=3000)
                    try:
                        handle = el.evaluate_handle(
                            "el => el.closest('a,button,li,[role=\"button\"],.card,.item,.report-item') || el"
                        )
                        handle.as_element().click(timeout=5000)
                    except Exception:
                        el.click(timeout=5000)
                    return
                except Exception as exc:
                    last_error = str(exc)
                    continue
        page.wait_for_timeout(500)
    raise RuntimeError(f"未找到可点击的可见节点: {label}; last_error={last_error}")


def wait_frame_with_rows(page, timeout_ms: int = 60000):
    deadline = time.monotonic() + timeout_ms / 1000
    last_error = ""
    while time.monotonic() < deadline:
        for frame in [page] + list(page.frames):
            try:
                rows = collect_payment_rows(frame)
                if rows:
                    return frame
            except Exception as exc:
                last_error = str(exc)
                continue
        page.wait_for_timeout(800)
    raise RuntimeError(f"未在任何 frame 中找到合同付款列表行; last_error={last_error}")


def report_click_locators(page, report_name: str) -> list:
    target = (report_name or DEFAULT_REPORT_NAME).strip() or DEFAULT_REPORT_NAME
    target_names = [target]
    if target == "合同付款凭证":
        target_names.append("合同付款（凭证）")
    elif target == "合同付款云链":
        target_names.append("合同付款（云链）")
    if target == "合同付款":
        return [
            page.locator('xpath=//*[normalize-space(text())="合同付款"]'),
            page.locator('xpath=//*[contains(normalize-space(text()),"合同付款") and not(contains(normalize-space(text()),"凭证"))]'),
            page.locator("text=合同付款"),
        ]
    locators = []
    for name in target_names:
        locators.extend(
            [
                page.locator(f'xpath=//*[normalize-space(text())="{name}"]'),
                page.locator(f'xpath=//*[contains(normalize-space(text()),"{name}")]'),
                page.locator(f"text={name}"),
            ]
        )
    return locators


def ensure_logged_in_and_open_contract_list(m3, context, run_dir: Path, report_name: str):
    page = context.pages[0] if context.pages else context.new_page()
    m3.goto_page(page, m3.OA_URL, "OA 首页", wait_until="commit", tolerate_aborted=True)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass
    if page.locator('input[type="password"]').count() > 0:
        m3.try_login(page)
        page.wait_for_timeout(1500)

    m3.goto_page(page, m3.REPORT_URL, "报表分析")
    click_first_visible(
        page,
        [
            page.locator('xpath=//*[normalize-space(text())="财务报表"]'),
            page.locator("text=财务报表"),
        ],
        "财务报表",
    )
    page.wait_for_timeout(600)
    click_first_visible(
        page,
        report_click_locators(page, report_name),
        report_name,
    )
    try:
        page.wait_for_load_state("domcontentloaded", timeout=12000)
    except Exception:
        pass
    page.wait_for_timeout(1500)
    capture_page_screenshot(page, run_dir / "m3" / f"02_{safe_filename(report_name)}点击后.png")
    wait_frame_with_rows(page)
    return page


def collect_visible_refs(frame) -> list[str]:
    js = r"""
    (pattern) => {
      const rx = new RegExp(pattern, "g");
      const out = [];
      const seen = new Set();
      const visible = (el) => {
        if (!el || !el.ownerDocument || !el.ownerDocument.defaultView) return false;
        const win = el.ownerDocument.defaultView;
        const style = win.getComputedStyle(el);
        if (!style || style.display === "none" || style.visibility === "hidden") return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      };
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        const parent = node.parentElement;
        if (!visible(parent)) continue;
        const text = node.nodeValue || "";
        let match;
        rx.lastIndex = 0;
        while ((match = rx.exec(text))) {
          if (!seen.has(match[0])) {
            seen.add(match[0]);
            out.push(match[0]);
          }
        }
      }
      return out;
    }
    """
    try:
        refs = frame.evaluate(js, FLOW_PATTERN)
    except Exception:
        refs = []
    result: list[str] = []
    for ref in refs or []:
        text = str(ref).strip()
        if text and text not in result:
            result.append(text)
    return result


def collect_payment_rows(target) -> list[dict]:
    js = r"""
    () => {
      const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
      const visibleEnough = (el) => {
        if (!el || !el.ownerDocument || !el.ownerDocument.defaultView) return false;
        const win = el.ownerDocument.defaultView;
        const style = win.getComputedStyle(el);
        if (!style || style.display === "none" || style.visibility === "hidden") return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      };
      const rows = Array.from(document.querySelectorAll("tr"));
      const out = [];
      rows.forEach((row, index) => {
        const text = norm(row.innerText || row.textContent || "");
        if (!text || text.includes("流水号 申请人") || text.includes("合同编号 合同名称")) return;
        if (!/YLH[A-Z0-9-]*/.test(text)) return;
        if (!visibleEnough(row)) return;
        out.push({ index, text });
      });
      return out;
    }
    """
    try:
        rows = target.evaluate(js)
    except Exception:
        return []
    result: list[dict] = []
    seen: set[str] = set()
    for row in rows or []:
        text = str(row.get("text", "")).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append({"index": int(row.get("index", 0)), "text": text})
    return result


def frame_with_rows(m3, page):
    return wait_frame_with_rows(page, timeout_ms=20000)


def payment_rows_target(page):
    best_target = None
    best_rows: list[dict] = []
    for target in [page] + list(page.frames):
        rows = collect_payment_rows(target)
        if len(rows) > len(best_rows):
            best_target = target
            best_rows = rows
    if best_target is None or not best_rows:
        raise RuntimeError("未找到可点击的合同付款表格行")
    return best_target, best_rows


def wait_detail_ready(detail_page) -> None:
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        for target in [detail_page] + list(detail_page.frames):
            try:
                if target.locator("text=申请人").first.count() > 0:
                    detail_page.wait_for_timeout(800)
                    return
            except Exception:
                continue
        detail_page.wait_for_timeout(500)
    print("警告：等待详情页『申请人』超时，仍尝试抽取")


def click_ref_open_detail(m3, context, page, ref: str):
    frame = frame_with_rows(m3, page)
    escaped = re.escape(ref)
    loc = frame.locator(f"text=/{escaped}/").first
    loc.scroll_into_view_if_needed(timeout=8000)
    row_handle = loc.evaluate_handle("el => el.closest('tr') || el")
    try:
        with context.expect_page(timeout=15000) as new_info:
            try:
                row_handle.as_element().click()
            except Exception:
                loc.click(timeout=8000)
        detail = new_info.value
    except Exception:
        with context.expect_page(timeout=15000) as new_info:
            row_handle.as_element().dblclick()
        detail = new_info.value
    detail.wait_for_load_state("domcontentloaded")
    wait_detail_ready(detail)
    return detail


def click_payment_row_open_detail(context, page, row_index: int):
    target, _rows = payment_rows_target(page)
    js = r"""
    (rowIndex) => {
      const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
      const rows = Array.from(document.querySelectorAll("tr"));
      const row = rows[rowIndex];
      if (!row) return null;
      row.scrollIntoView({ block: "center", inline: "nearest" });
      return row;
    }
    """
    row_handle = target.evaluate_handle(js, row_index)
    element = row_handle.as_element()
    if element is None:
        raise RuntimeError(f"表格行句柄为空: row_index={row_index}")
    try:
        with context.expect_page(timeout=15000) as new_info:
            element.click(timeout=8000)
        detail = new_info.value
    except Exception:
        with context.expect_page(timeout=15000) as new_info:
            element.dblclick(timeout=8000)
        detail = new_info.value
    detail.wait_for_load_state("domcontentloaded")
    wait_detail_ready(detail)
    return detail


def scroll_list_down(m3, page) -> bool:
    frame = frame_with_rows(m3, page)
    js = r"""
    () => {
      const candidates = [document.scrollingElement, ...Array.from(document.querySelectorAll("*"))]
        .filter((el) => el && el.scrollHeight > el.clientHeight + 40);
      candidates.sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
      let moved = false;
      for (const el of candidates.slice(0, 6)) {
        const before = el.scrollTop;
        const delta = Math.max(240, Math.floor((el.clientHeight || 600) * 0.82));
        el.scrollTop = Math.min(el.scrollHeight, before + delta);
        if (el.scrollTop !== before) moved = true;
      }
      window.scrollBy(0, Math.floor(window.innerHeight * 0.8));
      return moved;
    }
    """
    try:
        moved = bool(frame.evaluate(js))
    except Exception:
        moved = False
    page.wait_for_timeout(900)
    return moved


def precheck_form(form: dict) -> list[str]:
    errors: list[str] = []
    payment_bank = str(form.get("付款银行") or "").strip()
    payer = str(form.get("付款单位名称") or form.get("付款单位") or "").strip()
    account = str(form.get("收方账号") or "").strip()
    payee = str(form.get("收方户名") or "").strip()
    bank = str(form.get("开户银行") or "").strip()
    branch = str(form.get("支行名称") or "").strip()
    amount = str(form.get("金额") or "").strip()
    purpose = str(form.get("用途") or "").strip()

    if payment_bank not in CMB_PAYMENT_BANK_NAMES:
        errors.append(f"付款银行不是招商银行: {payment_bank or '未识别'}")
    if not payer:
        errors.append("缺少付款单位名称")
    if not account:
        errors.append("缺少收方账号")
    elif not account.isdigit():
        errors.append("收方账号不是纯数字")
    elif not (8 <= len(account) <= 24):
        errors.append(f"收方账号位数异常: {len(account)}")
    if not payee:
        errors.append("缺少收方户名")
    if not bank:
        errors.append("缺少开户银行")
    if bank and bank != "招商银行" and not branch:
        errors.append("非招商银行收款行缺少支行名称")
    if not amount:
        errors.append("缺少金额")
    else:
        try:
            if float(amount.replace(",", "")) <= 0:
                errors.append("金额必须大于 0")
        except Exception:
            errors.append("金额不是有效数字")
    if not purpose:
        errors.append("缺少用途")
    return errors


def extract_latest_forms(m3, limit: int, run_dir: Path, amount_override: str, report_name: str) -> tuple[list[dict], list[dict]]:
    forms: list[dict] = []
    failures: list[dict] = []
    seen_refs: set[str] = set()
    stagnant_rounds = 0

    disable_proxy_for_oa()
    os.makedirs(m3.USER_DATA_DIR, exist_ok=True)
    with m3.sync_playwright() as p:
        context = None
        try:
            context = p.chromium.launch_persistent_context(
                m3.USER_DATA_DIR,
                headless=False,
                viewport={"width": 1366, "height": 800},
                args=["--no-proxy-server"],
            )
            context.set_default_navigation_timeout(m3.NAVIGATION_TIMEOUT_MS)
            page = ensure_logged_in_and_open_contract_list(m3, context, run_dir, report_name)
            print(f"已进入{report_name}列表。")
            list_screenshot = capture_page_screenshot(page, run_dir / "m3" / f"01_{safe_filename(report_name)}列表.png")

            loops = 0
            while len(forms) < limit and stagnant_rounds < 12 and loops < 180:
                loops += 1
                _target, rows = payment_rows_target(page)
                new_rows = [row for row in rows if row["text"] not in seen_refs]
                if not new_rows:
                    moved = scroll_list_down(m3, page)
                    stagnant_rounds += 1
                    print(f"[M3] 暂无新可见行，向下滚动 moved={moved} stagnant={stagnant_rounds}")
                    continue

                stagnant_rounds = 0
                for row in new_rows:
                    if len(forms) >= limit:
                        break
                    seen_refs.add(row["text"])
                    detail = None
                    index = len(forms) + 1
                    row_label = row["text"][:90]
                    print(f"[M3] 抽取第 {index}/{limit} 条: {row_label}")
                    try:
                        detail = click_payment_row_open_detail(context, page, row["index"])
                        data = m3.extract_fields(detail)
                        ref = data.get("单据编号") or row_label
                        if not data.get("单据编号"):
                            data["单据编号"] = ref
                        detail_screenshot = capture_page_screenshot(
                            detail,
                            run_dir / "m3" / f"{index:02d}_{safe_filename(ref)}_详情.png",
                        )
                        m3.attach_m3_screenshot_info_or_abort(data, list_screenshot, detail_screenshot)
                        bank_form = m3.build_bank_form(data)
                        original_amount = bank_form.get("金额", "")
                        bank_form["原始金额"] = original_amount
                        bank_form["金额"] = amount_override
                        bank_form["amount"] = amount_override
                        bank_form["测试金额覆盖"] = amount_override
                        bank_form["M3抽取来源"] = str(M3_ROOT)
                        bank_form = apply_cmb_form_corrections(bank_form)

                        errors = precheck_form(bank_form)
                        form_path = run_dir / "m3_forms" / f"{index:02d}_{safe_filename(ref)}_bank_form_test.json"
                        raw_path = run_dir / "m3_forms" / f"{index:02d}_{safe_filename(ref)}_raw.json"
                        write_json(form_path, bank_form)
                        write_json(raw_path, data)
                        if errors:
                            failures.append({"ref": ref, "stage": "precheck", "errors": errors, "form": str(form_path)})
                            print(f"[M3] 预检失败，跳过: {ref} -> {'; '.join(errors)}")
                            continue
                        forms.append(bank_form)
                        write_json(run_dir / "m3_forms" / "latest30_bank_forms_test.json", forms)
                    except Exception as exc:
                        failures.append({"ref": ref, "stage": "extract", "error": f"{type(exc).__name__}: {exc}"})
                        print(f"[M3] 抽取失败，跳过: {ref} -> {type(exc).__name__}: {exc}")
                    finally:
                        if detail is not None:
                            try:
                                detail.close()
                            except Exception:
                                pass
                        try:
                            page.bring_to_front()
                        except Exception:
                            pass
                        page.wait_for_timeout(500)

                if len(forms) < limit:
                    scroll_list_down(m3, page)
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass

    write_json(run_dir / "m3_forms" / "extract_failures.json", failures)
    write_json(run_dir / "m3_forms" / "latest30_bank_forms_test.json", forms)
    return forms, failures


def get_new_run_screenshots(start_ts: float) -> list[Path]:
    if not SCREENSHOT_ROOT.exists():
        return []
    shots: list[Path] = []
    for path in SCREENSHOT_ROOT.glob("*.png"):
        try:
            if path.is_file() and path.stat().st_mtime >= start_ts - 1:
                if re.match(r"^\d{6}_.*\.png$", path.name):
                    shots.append(path)
        except OSError:
            continue
    return sorted(shots, key=lambda p: p.stat().st_mtime)


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for i in range(1, 1000):
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法生成唯一文件名: {path}")


def move_screenshots_to_item_dir(index: int, screenshots: list[Path], run_dir: Path) -> list[Path]:
    if not screenshots:
        return []
    shot_dir = run_dir / f"{index:02d}_screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for shot in screenshots:
        if not shot.exists():
            continue
        destination = unique_destination(shot_dir / shot.name)
        try:
            shutil.move(str(shot), str(destination))
            moved.append(destination)
        except Exception as exc:
            print(f"警告：移动截图失败 {shot} -> {destination}: {exc}")
    return sorted(moved, key=lambda p: p.stat().st_mtime if p.exists() else 0)


def completion_logged(log_text: str) -> bool:
    markers = (
        "制单测试流程完成",
        "第一次经办等待后",
        "ZHIDAN_TEST_MODE=1",
        "跳过第二次",
    )
    mojibake_markers = (
        "鍒跺崟娴嬭瘯娴佺▼瀹屾垚",
        "绗竴娆＄粡鍔炵瓑寰呭悗",
    )
    return any(marker in log_text for marker in (*markers, *mojibake_markers))


def run_attempt(
    index: int,
    ref: str,
    log_path: Path,
    err_path: Path,
    env: dict[str, str],
    timeout_seconds: int,
    python_exe: str,
) -> AttemptResult:
    start_ts = time.time()
    with log_path.open("a", encoding="utf-8", errors="replace") as log_f, err_path.open(
        "a", encoding="utf-8", errors="replace"
    ) as err_f:
        proc = subprocess.Popen(
            [python_exe, str(CMB_ENTRY)],
            cwd=str(CMB_ROOT),
            env=env,
            stdout=log_f,
            stderr=err_f,
        )
        timed_out = False
        while proc.poll() is None:
            time.sleep(2)
            if time.time() - start_ts > timeout_seconds:
                timed_out = True
                try:
                    proc.kill()
                except Exception:
                    pass
                break
        if not timed_out:
            proc.wait()
        exit_code = 124 if timed_out else int(proc.returncode or 0)

    log_text = read_text(log_path)
    shots = move_screenshots_to_item_dir(index, get_new_run_screenshots(start_ts), log_path.parent)
    final = next((p for p in reversed(shots) if "10_第一次经办等待后" in p.name), None)
    print(f"[招行] {index:02d} attempt done: ref={ref}, exit={exit_code}, final_shot={bool(final)}")
    return AttemptResult(exit_code, timed_out, log_path, err_path, shots, final, log_text)


def should_safe_retry(result: AttemptResult) -> tuple[bool, str]:
    if result.timed_out:
        return False, ""
    ok = result.final_screenshot is not None and completion_logged(result.log_text)
    if ok:
        return False, ""
    no_screenshots = not result.screenshots
    main_window_failure = no_screenshots and (
        "未找到主界面窗口" in result.log_text or "未找到包含 V12/U-BANK/招商银行/企业银行" in result.log_text
    )
    crash_failure = (
        "招行客户端崩溃" in result.log_text
        or "守护线程已捕获并强杀招行崩溃" in result.log_text
        or "Firmbank.exe - 应用程序错误" in result.log_text
    )
    if main_window_failure:
        return True, "登录后未进入主界面"
    if crash_failure:
        return True, "U-BANK客户端崩溃"
    return False, ""


def run_cmb_batch(forms: list[dict], run_dir: Path, usb_port: int, timeout_seconds: int) -> list[dict]:
    python_exe = find_python()
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "ZHIDAN_TEST_MODE": "1",
            "USB_HUB_FORCE_PORT": str(usb_port),
            "ZHIDAN_ALLOW_FORCE_USB_PORT": "1",
            "ZHIDAN_ALLOW_REGION_BRANCH_MATCH": "1",
            "ZHIDAN_NOTIFY_REGION_BRANCH_MATCH": "1",
            "ZHIDAN_REQUIRE_REGION_BRANCH_NOTIFY": "1",
            "ZHIDAN_AUTO_CLEANUP_ARTIFACTS": "0",
        }
    )

    results: list[dict] = []
    stop_residual_workflow("batch-start", run_dir, python_exe)
    for position, raw_form in enumerate(forms, start=1):
        form = dict(raw_form)
        index = int(form.pop("__runner_index", position) or position)
        ref = str(form.get("业务参考号") or form.get("label") or f"item_{index:02d}")
        label = safe_filename(ref)
        print(
            f"[招行] [{position}/{len(forms)}|源{index}] 开始: ref={ref}, 金额={form.get('金额')}, "
            f"强制测试U盾={usb_port}口"
        )
        CMB_FORM_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_json(CMB_FORM_PATH, form)

        log_path = run_dir / f"{index:02d}_{label}.log"
        err_path = run_dir / f"{index:02d}_{label}.err.log"
        with log_path.open("w", encoding="utf-8", errors="replace") as f:
            f.write(f"=== RUN {index} START {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            f.write(json.dumps(form, ensure_ascii=False, indent=2))
            f.write("\n")
            f.write(
                f"TEST_GATES: ZHIDAN_TEST_MODE=1, USB_HUB_FORCE_PORT={usb_port}, "
                "ZHIDAN_ALLOW_FORCE_USB_PORT=1\n"
            )
        err_path.write_text("", encoding="utf-8")

        attempt = run_attempt(index, ref, log_path, err_path, env, timeout_seconds, python_exe)
        retry, retry_cause = should_safe_retry(attempt)
        first_log = ""
        first_err = ""
        if retry:
            first_log = str(log_path)
            first_err = str(err_path)
            print(f"[招行] [{position}/{len(forms)}|源{index}] {retry_cause}，清理后自动重试一次...")
            stop_residual_workflow(f"run-{index}-safe-retry", run_dir, python_exe)
            time.sleep(3)
            retry_log = run_dir / f"{index:02d}_{label}.retry1.log"
            retry_err = run_dir / f"{index:02d}_{label}.retry1.err.log"
            with retry_log.open("w", encoding="utf-8", errors="replace") as f:
                f.write(f"=== RUN {index} RETRY 1 START {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                f.write(json.dumps(form, ensure_ascii=False, indent=2))
                f.write("\n")
            retry_err.write_text("", encoding="utf-8")
            write_json(CMB_FORM_PATH, form)
            attempt = run_attempt(index, ref, retry_log, retry_err, env, timeout_seconds, python_exe)

        ok = attempt.final_screenshot is not None and completion_logged(attempt.log_text) and not attempt.timed_out
        status = "OK" if ok else ("TIMEOUT" if attempt.timed_out else "FAIL")
        if ok:
            reason = "completion marker and final screenshot found"
            if retry_cause:
                reason += f" after safe retry: {retry_cause}"
        elif attempt.timed_out:
            reason = f"timeout after {timeout_seconds} seconds"
        elif retry_cause:
            reason = f"safe retry failed after {retry_cause}; exit={attempt.exit_code}; final screenshot found={attempt.final_screenshot is not None}"
        else:
            reason = f"exit={attempt.exit_code}; final screenshot found={attempt.final_screenshot is not None}"

        if status != "OK":
            stop_residual_workflow(f"run-{index}-failed", run_dir, python_exe)

        result = {
            "Index": index,
            "Status": status,
            "Reason": reason,
            "Attempt": 2 if retry_cause else 1,
            "RetryCause": retry_cause,
            "ExitCode": attempt.exit_code,
            "Ref": ref,
            "Payer": form.get("付款单位名称", ""),
            "Payee": form.get("收方户名", ""),
            "Amount": form.get("金额", ""),
            "OriginalAmount": form.get("原始金额", ""),
            "Log": str(attempt.log_path),
            "ErrorLog": str(attempt.err_path),
            "FirstAttemptLog": first_log,
            "FirstAttemptErrorLog": first_err,
            "FinalScreenshot": str(attempt.final_screenshot) if attempt.final_screenshot else "",
            "Screenshots": [str(p) for p in attempt.screenshots],
        }
        results.append(result)
        write_json(run_dir / f"{index:02d}_result.json", result)
        write_json(run_dir / "summary.json", results)
        print(f"[招行] [{position}/{len(forms)}|源{index}] {status}: {reason}")

    stop_residual_workflow("batch-end", run_dir, python_exe)
    write_json(run_dir / "summary.json", results)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M3 最新付款信息 -> 招行测试制单自动化入口")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="抽取并运行的有效记录数")
    parser.add_argument("--amount", default=DEFAULT_AMOUNT, help="测试金额覆盖值")
    parser.add_argument("--usb-port", type=int, default=DEFAULT_USB_PORT, help="测试强制 USB Hub 口")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="每笔招行流程超时")
    parser.add_argument("--skip-bank", action="store_true", help="只抽取并生成测试 JSON，不启动招行")
    parser.add_argument("--report-name", default=DEFAULT_REPORT_NAME, help="M3 财务报表卡片名称，默认合同付款（凭证）；可设为 合同付款云链")
    parser.add_argument("--forms-json", type=Path, help="使用已有表单 JSON 重跑，不重新抓取 M3")
    parser.add_argument("--only-indexes", help="只重跑指定原始索引，逗号分隔，支持范围如 8,15,17-19")
    parser.add_argument("--city-bank-indexes", help="将指定原始索引的开户银行/银行大类改为城市商业银行")
    parser.add_argument("--branch-override", help="覆盖指定原始索引支行，格式如 7=中国工商银行广州黄埔支行")
    parser.add_argument(
        "--fail-on-extract-failures",
        action="store_true",
        help="M3 抽取/预检有跳过记录时也返回非 0，便于网关上报自动化异常",
    )
    args = parser.parse_args(argv)

    if not (1 <= args.usb_port <= 30):
        print(f"[终止] USB 口必须在 1-30: {args.usb_port}")
        return 2
    if args.amount != DEFAULT_AMOUNT:
        print(f"[终止] 当前受控测试只允许金额 {DEFAULT_AMOUNT}，收到: {args.amount}")
        return 2
    if args.limit <= 0:
        print(f"[终止] limit 必须大于 0: {args.limit}")
        return 2
    if not CMB_ENTRY.exists():
        print(f"[终止] 招行入口不存在: {CMB_ENTRY}")
        return 2

    only_indexes = parse_indexes(args.only_indexes)
    city_bank_indexes = parse_indexes(args.city_bank_indexes)
    branch_overrides = parse_overrides(args.branch_override)
    run_prefix = "batch_m3_retry" if args.forms_json else "batch_m3_latest30"
    run_dir = RUNS_ROOT / f"{run_prefix}_{now_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"RUN_DIR={run_dir}")
    print(f"M3_ROOT={M3_ROOT}")
    print(f"CMB_ENTRY={CMB_ENTRY}")
    print(f"M3_REPORT_NAME={args.report_name}")
    print(
        f"SAFETY=ZHIDAN_TEST_MODE=1, amount={args.amount}, USB_HUB_FORCE_PORT={args.usb_port}, "
        "branch_dropdown_region_route=1, require_gateway_notify_before_jingban=1"
    )

    extract_failures: list[dict] = []
    try:
        if args.forms_json:
            forms = load_existing_forms(args.forms_json, only_indexes, city_bank_indexes, branch_overrides)
            print(f"[重跑] 从已有表单读取 {len(forms)} 条: {args.forms_json}")
            if not forms:
                print("[终止] 没有匹配的重跑表单")
                return 3
        else:
            m3 = load_m3_module()
            forms, extract_failures = extract_latest_forms(m3, args.limit, run_dir, args.amount, args.report_name)
            print(f"[M3] 有效表单 {len(forms)} 条，抽取/预检失败 {len(extract_failures)} 条")
            if len(forms) < args.limit:
                print(f"[终止] 只拿到 {len(forms)} 条有效表单，少于要求 {args.limit} 条，不启动招行。")
                return 3
        if args.skip_bank:
            print("[完成] 已跳过招行启动。")
            summary_path = args.forms_json or (run_dir / "m3_forms" / "latest30_bank_forms_test.json")
            print(f"SUMMARY_PATH={summary_path}")
            if args.fail_on_extract_failures and extract_failures:
                print(f"[异常] M3 抽取/预检失败 {len(extract_failures)} 条，按自动化上报策略返回失败。")
                return 1
            return 0
        selected_forms = forms if args.forms_json else forms[: args.limit]
        results = run_cmb_batch(selected_forms, run_dir, args.usb_port, args.timeout_seconds)
        ok_count = sum(1 for item in results if item.get("Status") == "OK")
        print(f"SUMMARY_PATH={run_dir / 'summary.json'}")
        print(f"[完成] 招行测试批量结果: OK {ok_count}/{len(results)}")
        if ok_count != len(results):
            return 1
        if args.fail_on_extract_failures and extract_failures:
            print(f"[异常] M3 抽取/预检失败 {len(extract_failures)} 条，按自动化上报策略返回失败。")
            return 1
        return 0
    except KeyboardInterrupt:
        print("[中断] 用户中断")
        return 130
    except Exception as exc:
        print(f"[终止] {type(exc).__name__}: {exc}")
        return 2
    finally:
        run_generated_artifact_cleanup(run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
