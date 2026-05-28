# -*- coding: utf-8 -*-
from __future__ import annotations

"""单笔转账表单填写、开户行/支行下拉处理与批量覆盖填写。"""

import os
import sys
from pathlib import Path


def _add_public_module_dir():
    current = Path(__file__).resolve()
    for parent in current.parents:
        public_dir = parent / "公共"
        if public_dir.is_dir():
            text = str(public_dir)
            if text not in sys.path:
                sys.path.insert(0, text)
            return


_add_public_module_dir()

from bank_branch_matcher import score_branch_candidate  # noqa: E402

from .runtime import *
from .data import normalize_bank_name_for_abc
from .auth import input_kb_password

# ---------------- 表单填写 ----------------
def fill_input_by_placeholder_candidates(
    page: Page, candidates: Iterable[str], value: str, field_name: str
) -> Optional[Locator]:
    """按候选 placeholder 列表依次尝试精确匹配，失败再尝试模糊匹配。"""
    candidates = list(candidates)
    for placeholder in candidates:
        try:
            locator = page.locator(f"input:visible[placeholder='{placeholder}']").first
            locator.wait_for(state="visible", timeout=3000)
            locator.fill(value)
            log.info("[表单] %s 已填写（精确 placeholder=%s）", field_name, placeholder)
            return locator
        except (PlaywrightTimeout, PlaywrightError):
            continue
    for placeholder in candidates:
        try:
            locator = page.locator(f"input:visible[placeholder*='{placeholder}']").first
            locator.wait_for(state="visible", timeout=3000)
            locator.fill(value)
            log.info("[表单] %s 已填写（模糊 placeholder*=%s）", field_name, placeholder)
            return locator
        except (PlaywrightTimeout, PlaywrightError):
            continue
    log.warning("[表单] %s 找不到匹配输入框", field_name)
    return None


def fill_input_by_label_or_placeholder(
    page: Page,
    label_text: str,
    placeholder_candidates: Iterable[str],
    value: str,
) -> Optional[Locator]:
    """优先 aria-label，失败再走 placeholder 候选列表。"""
    try:
        locator = page.get_by_label(label_text).first
        locator.wait_for(state="visible", timeout=2000)
        locator.fill(value)
        log.info("[表单] %s 已通过 label 填写", label_text)
        return locator
    except (PlaywrightTimeout, PlaywrightError):
        pass
    return fill_input_by_placeholder_candidates(
        page, placeholder_candidates, value, label_text
    )


def dismiss_transient_dropdowns(page: Page, reason: str) -> None:
    """
    关闭收款方历史记录/用途候选等临时下拉层。

    默认实现：只发两次 Escape + blur 当前焦点，不直接改 DOM。
    Element 的下拉/popper 在 Escape/blur 之后通常会自行收起，无需我们隐藏。

    显式 DISMISS_DROPDOWNS_HIDE=true 时才走旧的 display='none' 隐藏路径，
    同时把每个元素原来的 inline display 写入 data-codex-prev-display，
    供 restore_transient_dropdowns() 在后续阶段恢复，避免后续交互永久坏掉。
    """
    try:
        for _ in range(2):
            page.keyboard.press("Escape")
            safe_sleep(0.1)
        page.evaluate(
            """() => { const a = document.activeElement; if (a && a.blur) a.blur(); }"""
        )
        if not env_flag("DISMISS_DROPDOWNS_HIDE", False):
            log.info("[表单] 已用 Escape+blur 关闭临时下拉层：%s", reason)
            return
        hidden = page.evaluate(
            """() => {
                const bankLabel = Array.from(document.querySelectorAll('body *'))
                    .filter(el => {
                        const style = window.getComputedStyle(el);
                        const text = (el.textContent || '').replace(/\\s+/g, '');
                        const rect = el.getBoundingClientRect();
                        return text.includes('收款方开户行')
                            && style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && rect.width > 0
                            && rect.height > 0;
                    })
                    .sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        return (ar.width * ar.height) - (br.width * br.height);
                    })[0];
                const bankRect = bankLabel ? bankLabel.getBoundingClientRect() : null;
                const bankProbe = bankRect ? {
                    left: bankRect.right,
                    top: bankRect.top - 40,
                    right: bankRect.right + 760,
                    bottom: bankRect.bottom + 180,
                } : null;

                const selectors = [
                    '.el-autocomplete-suggestion',
                    '.el-autocomplete-suggestion__wrap',
                    '.el-select-dropdown',
                    '.el-popper',
                    '[role="listbox"]',
                    '[class*="autocomplete"]',
                    '[class*="suggest"]',
                    '[class*="dropdown"]',
                    '[class*="popper"]'
                ];
                let hidden = 0;
                const touched = new Set();
                for (const el of document.querySelectorAll(selectors.join(','))) {
                    touched.add(el);
                }
                if (bankProbe) {
                    for (const el of document.querySelectorAll('body *')) {
                        const style = window.getComputedStyle(el);
                        if (!['absolute', 'fixed'].includes(style.position)) continue;
                        const rect = el.getBoundingClientRect();
                        const overlapsBank = rect.left < bankProbe.right
                            && rect.right > bankProbe.left
                            && rect.top < bankProbe.bottom
                            && rect.bottom > bankProbe.top;
                        if (overlapsBank && rect.width > 180 && rect.height > 40) {
                            touched.add(el);
                        }
                    }
                }
                for (const el of touched) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    const visible = style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && rect.width > 0
                        && rect.height > 0;
                    if (!visible) continue;
                    if (el.matches('input, textarea, select, button')) continue;
                    if (!el.hasAttribute('data-codex-hidden-transient')) {
                        el.setAttribute(
                            'data-codex-prev-display',
                            el.style.display || ''
                        );
                        el.setAttribute('data-codex-hidden-transient', 'true');
                    }
                    el.style.display = 'none';
                    hidden += 1;
                }
                return hidden;
            }"""
        )
        safe_sleep(0.15)
        log.info(
            "[表单] 已关闭临时下拉层：%s（隐藏 %s 个候选/遮挡层，原 display 已记录可恢复）",
            reason,
            hidden,
        )
    except PlaywrightError as e:
        log.warning("[表单] 关闭临时下拉层失败（%s）：%s", type(e).__name__, reason)


# 专门挡住「收款方开户行」区域的农行业务浮层（收款方候选历史 / 放大镜入口）。
# 这些层不在 Element UI 的 popper/listbox 体系里，Escape+blur 不会让它们消失，
# 但它们会用 z-index 浮在开户行下拉触发器和下拉面板之上，导致 click 被吞或
# elementFromPoint 命中错误目标。这里**只**针对类名包含这些 token 的元素隐藏，
# 不会触及 .el-select-dropdown / .el-popper / [role='listbox'] 等真正的下拉容器。
_BANK_OVERLAY_CLASS_TOKENS = ("toAccList", "magnifier")
_BANK_PANEL_MARK_ATTR = "data-codex-bank-panel"
_BANK_PANEL_TEXT_TOKENS = (
    "常用银行",
    "中国农业银行",
    "中国工商银行",
    "中国建设银行",
    "招商银行",
    "民生银行",
    "中信银行",
)
_BANK_OPTION_MARK_ATTR = "data-codex-bank-option"
_BANK_QUERY_TRIGGER_MARK_ATTR = "data-codex-bank-query-trigger"
_BRANCH_ROW_MARK_ATTR = "data-codex-branch-row"
_BRANCH_BUTTON_MARK_ATTR = "data-codex-branch-button"
_BRANCH_SELECT_MARK_ATTR = "data-codex-branch-select"


def _dismiss_bank_overlays(page: Page, reason: str) -> int:
    """
    开户行交互前的专项清理。

    流程：
      1) Escape + blur 一次，让标准 popper/listbox 自行收起；
      2) 仅检查类名包含 toAccList / magnifier 的可见绝对/固定定位元素，
         判断它们是否覆盖到「收款方开户行」标签或其右侧的输入区；
      3) 仅对仍然覆盖的目标做 display='none'，并把原 inline display 写入
         data-codex-prev-display，打 data-codex-hidden-transient 标记，
         供 restore_transient_dropdowns 在填表结束时统一恢复，避免后续永久坏掉。

    返回隐藏的元素数量。日志只记录 count + 命中的类名 token，**不**记录候选文本、
    账号、户名等敏感数据。
    """
    try:
        for _ in range(2):
            page.keyboard.press("Escape")
            safe_sleep(0.05)
        page.evaluate(
            """() => { const a = document.activeElement; if (a && a.blur) a.blur(); }"""
        )
        result = page.evaluate(
            """(args) => {
                const tokens = args.tokens;
                const labels = Array.from(document.querySelectorAll('body *'))
                    .filter(el => {
                        const style = window.getComputedStyle(el);
                        const text = (el.textContent || '').replace(/\\s+/g, '');
                        const rect = el.getBoundingClientRect();
                        return text.includes('收款方开户行')
                            && style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && rect.width > 0
                            && rect.height > 0;
                    })
                    .sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        return (ar.width * ar.height) - (br.width * br.height);
                    });
                const label = labels[0] || null;
                if (!label) {
                    return { hidden: 0, names: [] };
                }
                const labelRect = label.getBoundingClientRect();
                // 探测窗口：覆盖标签上方放大镜区域 + 标签右侧 input/下拉触发器区域
                const probe = {
                    left: labelRect.left - 50,
                    top: labelRect.top - 200,
                    right: labelRect.right + 760,
                    bottom: labelRect.bottom + 200,
                };
                const hiddenNames = [];
                let hidden = 0;
                for (const el of document.querySelectorAll('body *')) {
                    const cls = String(el.className || '');
                    const matched = tokens.find(t => cls.indexOf(t) >= 0);
                    if (!matched) continue;
                    const style = window.getComputedStyle(el);
                    if (!['absolute', 'fixed'].includes(style.position)) continue;
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) continue;
                    const overlaps = rect.left < probe.right
                        && rect.right > probe.left
                        && rect.top < probe.bottom
                        && rect.bottom > probe.top;
                    if (!overlaps) continue;
                    if (!el.hasAttribute('data-codex-hidden-transient')) {
                        el.setAttribute('data-codex-prev-display', el.style.display || '');
                        el.setAttribute('data-codex-hidden-transient', 'true');
                    }
                    // 标记为「收款方名册/放大镜」业务浮层：填表结束时
                    // restore_transient_dropdowns 不应把它恢复回来，否则会盖住
                    // 最终人工核对截图（收款户名/收款方开户行 区域）。
                    el.setAttribute('data-codex-bank-overlay', '1');
                    el.style.display = 'none';
                    hiddenNames.push(matched);
                    hidden += 1;
                }
                return { hidden, names: hiddenNames };
            }""",
            {"tokens": list(_BANK_OVERLAY_CLASS_TOKENS)},
        )
        if isinstance(result, dict):
            hidden = int(result.get("hidden", 0) or 0)
            names = result.get("names") or []
        else:
            hidden, names = 0, []
        if hidden:
            log.info(
                "[表单] 已隐藏 %d 个挡住开户行的浮层（命中类名 token：%s）：%s",
                hidden,
                ",".join(sorted({str(n) for n in names})) or "<n/a>",
                reason,
            )
        else:
            log.info(
                "[表单] 开户行区域无 toAccList/magnifier 残留遮挡：%s", reason
            )
        return hidden
    except PlaywrightError as e:
        log.warning(
            "[表单] 隐藏开户行遮挡浮层失败（%s）：%s", type(e).__name__, reason
        )
        return 0


def _bank_target_point_clear(page: Page, x: float, y: float, label: str) -> bool:
    """
    用 elementFromPoint 检查 (x,y) 是否仍命中 toAccList/magnifier 之类的浮层。
    返回 True 表示该点可点击；False 表示仍被浮层覆盖，调用方应放弃这次点击。
    """
    try:
        info = page.evaluate(
            """(coords) => {
                const el = document.elementFromPoint(coords.x, coords.y);
                if (!el) return { tag: null, className: '' };
                return {
                    tag: el.tagName,
                    className: String(el.className || '').slice(0, 120),
                };
            }""",
            {"x": x, "y": y},
        )
    except PlaywrightError as e:
        log.warning("[表单] elementFromPoint 检查失败（%s），不阻塞点击", type(e).__name__)
        return True
    cls = (info or {}).get("className", "") or ""
    matched = next((t for t in _BANK_OVERLAY_CLASS_TOKENS if t in cls), None)
    if matched is not None:
        log.warning(
            "[表单] 目标坐标 (%.0f,%.0f) 仍被开户行遮挡浮层覆盖（%s，命中类名 token=***REDACTED***",
            x, y, label, matched,
        )
        return False
    return True


def _bank_text_key(value: str) -> str:
    """用于开户行显示值比对的轻量归一化；只处理银行大类名称，不处理账号等敏感字段。"""
    text = re.sub(r"\s+", "", str(value or ""))
    if not text:
        return ""
    return normalize_bank_name_for_abc(text)


def _bank_text_matches(actual: str, expected: str) -> bool:
    actual_key = _bank_text_key(actual)
    expected_key = _bank_text_key(expected)
    if not actual_key or not expected_key:
        return False
    if "村镇银行" in actual_key and "村镇银行" in expected_key:
        return expected_key in actual_key or actual_key in expected_key
    return actual_key == expected_key


def _branch_text_key(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    for token in ("股份有限公司", "有限责任公司", "有限公司", "股份公司"):
        text = text.replace(token, "")
    return text


def _branch_core_key(value: str) -> str:
    text = _branch_text_key(value)
    for token in (
        "中国农业银行",
        "中国工商银行",
        "中国建设银行",
        "中国民生银行",
        "民生银行",
        "中信银行",
        "招商银行",
        "桂林国民村镇银行",
        "村镇银行",
    ):
        text = text.replace(token, "")
    return text


def _branch_text_matches(actual: str, expected: str) -> bool:
    score = score_branch_candidate(
        expected,
        actual,
        allow_hierarchy=False,
    )
    return score.accepted_exact_or_safe


def _bank_field_state(page: Page, select_box: Optional[Locator]) -> dict:
    """
    读取开户行字段的实际页面状态。

    这里故意读取页面上最终显示/控件保存的值，而不是“刚刚搜索框里输入过什么”，
    因为农行页面会出现候选看似点击成功、实际仍保留“中国农业银行”的情况。
    """
    values: list[str] = []
    try:
        if select_box is not None:
            value = (select_box.input_value(timeout=1000) or "").strip()
            if value:
                values.append(value)
    except (PlaywrightTimeout, PlaywrightError):
        pass
    try:
        info = page.evaluate(
            """() => {
                const input = document.querySelector('#bankNameBtn')
                    || document.querySelector('input[placeholder*="开户行"]');
                const values = [];
                if (input && input.value) values.push(input.value);
                const root = input
                    ? (input.closest('.el-form-item, .el-row, .el-col, .form-item, tr, td, div') || input.parentElement)
                    : null;
                if (root) {
                    for (const el of root.querySelectorAll('input, textarea, select')) {
                        if (el.value) values.push(el.value);
                    }
                    const text = (root.innerText || root.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (text) values.push(text);
                }
                return Array.from(new Set(values)).slice(0, 8);
            }"""
        )
        if isinstance(info, list):
            for value in info:
                value = str(value or "").strip()
                if value and value not in values:
                    values.append(value)
    except PlaywrightError:
        pass
    return {"values": values[:10]}


def _verify_bank_selection(
    page: Page,
    select_box: Optional[Locator],
    expected_bank: str,
    expected_branch: str = "",
    *,
    exact_candidate: bool,
) -> bool:
    safe_sleep(0.35)
    dismiss_transient_dropdowns(page, F_BANK)
    safe_sleep(0.15)
    state = _bank_field_state(page, select_box)
    values = [str(v) for v in state.get("values") or []]
    bank_matched = any(_bank_text_matches(value, expected_bank) for value in values)
    branch_matched = (
        any(_branch_text_matches(value, expected_branch) for value in values)
        if expected_branch
        else True
    )
    log.info(
        "[校验] %s 页面实际值=%s；目标=%s；候选匹配=%s",
        F_BANK,
        " | ".join(values) if values else "<empty>",
        expected_bank,
        "exact" if exact_candidate else "fuzzy",
    )
    if not exact_candidate:
        log.warning("[校验] %s 候选仅 fuzzy 命中，不计入自动通过，请人工核对", F_BANK)
        return False
    if not bank_matched:
        log.warning(
            "[校验] %s 页面实际值未确认到目标银行，不能算自动通过",
            F_BANK,
        )
        return False
    if expected_branch and not branch_matched:
        log.warning(
            "[校验] %s 页面实际值未显示目标支行；若已通过支行查询弹窗精确确认，可继续按银行大类校验",
            F_BANK,
        )
    log.info("[校验] %s ✓", F_BANK)
    return True


def _mark_bank_option(dropdown: Locator, bank_name: str, *, exact: bool) -> bool:
    """
    在当前银行面板内标记一个可点击候选项。

    不使用全页文本兜底；只在已识别的银行面板内找候选。标记的目标优先是
    li/[role=option]/tr 等可点击行，避免点到只是包含文本的外层面板。
    """
    try:
        found = dropdown.evaluate(
            """(root, args) => {
                const markAttr = args.markAttr;
                const target = String(args.target || '').replace(/\\s+/g, '');
                const exact = Boolean(args.exact);
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && rect.width > 0
                        && rect.height > 0;
                };
                const norm = (text) => String(text || '').replace(/\\s+/g, '');
                const rootDoc = root.ownerDocument || document;
                for (const el of rootDoc.querySelectorAll(`[${markAttr}]`)) {
                    el.removeAttribute(markAttr);
                }
                const candidates = [];
                for (const el of root.querySelectorAll('*')) {
                    if (!visible(el)) continue;
                    if (el.matches('script, style, input, textarea, select')) continue;
                    const text = norm(el.innerText || el.textContent || '');
                    if (!text) continue;
                    const matched = exact ? text === target : text.includes(target);
                    if (!matched) continue;
                    const clickable = el.closest('li,[role="option"],tr,td,button,a,.el-select-dropdown__item')
                        || el;
                    if (!visible(clickable)) continue;
                    const rect = clickable.getBoundingClientRect();
                    candidates.push({
                        el: clickable,
                        area: rect.width * rect.height,
                        y: rect.top,
                        className: String(clickable.className || ''),
                    });
                }
                candidates.sort((a, b) => {
                    const ac = /el-select-dropdown__item|option/.test(a.className) ? 0 : 1;
                    const bc = /el-select-dropdown__item|option/.test(b.className) ? 0 : 1;
                    return (ac - bc) || (a.area - b.area) || (a.y - b.y);
                });
                if (!candidates.length) return false;
                candidates[0].el.setAttribute(markAttr, exact ? 'exact' : 'fuzzy');
                return true;
            }""",
            {
                "markAttr": _BANK_OPTION_MARK_ATTR,
                "target": bank_name,
                "exact": exact,
            },
        )
        return bool(found)
    except PlaywrightError as e:
        log.warning("[表单] %s 候选项定位 JS 失败（%s）", F_BANK, type(e).__name__)
        return False


def _click_marked_bank_option(page: Page) -> bool:
    option = page.locator(f"[{_BANK_OPTION_MARK_ATTR}]").first
    try:
        option.wait_for(state="visible", timeout=800)
        box = option.bounding_box(timeout=1000)
        if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        else:
            option.click(timeout=2000)
        return True
    except (PlaywrightTimeout, PlaywrightError) as e:
        log.warning("[表单] %s 候选项点击失败（%s），请人工核对", F_BANK, type(e).__name__)
        return False


def _current_branch_query_dialog(page: Page, timeout_ms: int = 300) -> Optional[Locator]:
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000
    while True:
        for selector in (
            ".el-dialog__wrapper:visible .el-dialog",
            ".el-dialog:visible",
            "[role='dialog']:visible",
            "[aria-modal='true']:visible",
        ):
            for title in ("查询开户支行", "查询开户行"):
                try:
                    dialog = page.locator(selector).filter(has_text=title).first
                    dialog.wait_for(state="visible", timeout=120)
                    return dialog
                except (PlaywrightTimeout, PlaywrightError):
                    continue
        if time.monotonic() >= deadline:
            return None
        safe_sleep(0.1)


def _close_branch_query_dialog(page: Page) -> None:
    dialog = _current_branch_query_dialog(page, timeout_ms=300)
    if dialog is None:
        return
    for selector in ("button:has-text('取消')", ".el-dialog__headerbtn", "button:has-text('关闭')"):
        try:
            dialog.locator(selector).first.click(timeout=1000)
            safe_sleep(0.2)
            return
        except (PlaywrightTimeout, PlaywrightError):
            continue
    try:
        page.keyboard.press("Escape")
        safe_sleep(0.2)
    except PlaywrightError:
        pass


def _click_branch_dialog_button(page: Page, dialog: Locator, label: str, *, prefer_last: bool = False) -> bool:
    """
    在「查询开户支行」弹窗内点击可见文本按钮。

    这个弹窗的按钮在截图里可见，但 Playwright 的 button:has-text('查询')
    偶发会点到不可操作节点而超时。这里先在弹窗 DOM 内标记真正可见的按钮，
    再点击标记节点，避免全页文本兜底。
    """
    try:
        marked = dialog.evaluate(
            """(root, args) => {
                const markAttr = args.markAttr;
                const target = String(args.label || '').replace(/\\s+/g, '');
                const preferLast = Boolean(args.preferLast);
                const doc = root.ownerDocument || document;
                for (const el of doc.querySelectorAll(`[${markAttr}]`)) {
                    el.removeAttribute(markAttr);
                }
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && rect.width > 0
                        && rect.height > 0;
                };
                const norm = (text) => String(text || '').replace(/\\s+/g, '');
                const candidates = [];
                const nodes = root.querySelectorAll('button,.el-button,[role="button"],a,span,div');
                for (const el of nodes) {
                    if (!visible(el)) continue;
                    if (norm(el.innerText || el.textContent || '') !== target) continue;
                    let clickable = el.closest('button,.el-button,[role="button"],a') || el;
                    if (!visible(clickable)) clickable = el;
                    const rect = clickable.getBoundingClientRect();
                    const className = String(clickable.className || '');
                    const tag = String(clickable.tagName || '').toLowerCase();
                    const buttonLike = tag === 'button'
                        || className.includes('el-button')
                        || clickable.getAttribute('role') === 'button';
                    candidates.push({
                        el: clickable,
                        buttonLike,
                        area: rect.width * rect.height,
                        x: rect.left,
                        y: rect.top,
                    });
                }
                const unique = [];
                const seen = new Set();
                for (const item of candidates) {
                    if (seen.has(item.el)) continue;
                    seen.add(item.el);
                    unique.push(item);
                }
                unique.sort((a, b) => {
                    if (a.buttonLike !== b.buttonLike) return a.buttonLike ? -1 : 1;
                    if (preferLast) return (b.y - a.y) || (b.x - a.x);
                    return (a.y - b.y) || (b.x - a.x) || (a.area - b.area);
                });
                if (!unique.length) return null;
                const chosen = unique[0].el;
                const rect = chosen.getBoundingClientRect();
                chosen.setAttribute(markAttr, target);
                return {
                    x: rect.left,
                    y: rect.top,
                    w: rect.width,
                    h: rect.height,
                };
            }""",
            {
                "markAttr": _BRANCH_BUTTON_MARK_ATTR,
                "label": label,
                "preferLast": prefer_last,
            },
        )
    except PlaywrightError as e:
        log.warning("[表单] 查询开户支行弹窗按钮定位失败：%s（%s）", label, type(e).__name__)
        return False

    if not isinstance(marked, dict):
        log.warning("[表单] 查询开户支行弹窗内未找到按钮：%s", label)
        return False

    button = page.locator(f"[{_BRANCH_BUTTON_MARK_ATTR}]").first
    try:
        button.click(timeout=1000, force=True)
        safe_sleep(0.2)
        return True
    except (PlaywrightTimeout, PlaywrightError):
        pass

    try:
        page.mouse.click(
            float(marked["x"]) + float(marked["w"]) / 2,
            float(marked["y"]) + float(marked["h"]) / 2,
        )
        safe_sleep(0.2)
        return True
    except (KeyError, TypeError, ValueError, PlaywrightError) as e:
        log.warning("[表单] 查询开户支行弹窗按钮点击失败：%s（%s）", label, type(e).__name__)
        return False


def _mark_branch_query_row(dialog: Locator, branch_name: str) -> Optional[str]:
    try:
        rows = dialog.evaluate(
            """(root, args) => {
                const markAttr = args.markAttr;
                const indexAttr = args.indexAttr;
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && rect.width > 0
                        && rect.height > 0;
                };
                const doc = root.ownerDocument || document;
                for (const el of doc.querySelectorAll(`[${markAttr}]`)) {
                    el.removeAttribute(markAttr);
                }
                for (const el of doc.querySelectorAll(`[${indexAttr}]`)) {
                    el.removeAttribute(indexAttr);
                }
                const rows = Array.from(root.querySelectorAll('tr, .el-table__row, [role="row"]'))
                    .filter(visible)
                    .filter(row => {
                        const text = String(row.innerText || row.textContent || '').replace(/\\s+/g, '');
                        return text && text !== '选择支行名称收款方银行大额行号';
                    });
                return rows.map((row, idx) => {
                    row.setAttribute(indexAttr, String(idx));
                    const rect = row.getBoundingClientRect();
                    return {
                        idx,
                        text: String(row.innerText || row.textContent || '').replace(/\\s+/g, ' ').trim(),
                        area: rect.width * rect.height,
                        y: rect.top,
                    };
                });
            }""",
            {
                "markAttr": _BRANCH_ROW_MARK_ATTR,
                "indexAttr": "data-codex-branch-row-index",
            },
        )
        if not isinstance(rows, list):
            return None

        scored = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = str(row.get("text") or "").strip()
            score = score_branch_candidate(branch_name, text, allow_hierarchy=False)
            if score.accepted_exact_or_safe:
                scored.append((score, row))
        if not scored:
            return None

        scored.sort(key=lambda item: (-item[0].score, int(item[1].get("area") or 0), float(item[1].get("y") or 0)))
        best_score = scored[0][0].score
        best = [item for item in scored if item[0].score == best_score]
        unique_texts = []
        seen = set()
        for score, row in best:
            text = str(row.get("text") or "").strip()
            key = re.sub(r"\s+", "", text)
            if key and key not in seen:
                seen.add(key)
                unique_texts.append(text)
        if len(unique_texts) != 1:
            log.warning("[表单] 查询开户支行出现多个同分安全候选，拒绝自动选择：%s", " | ".join(unique_texts))
            return None

        chosen_score, chosen_row = best[0]
        result = dialog.evaluate(
            """(root, args) => {
                const row = root.querySelector(`[${args.indexAttr}="${String(args.idx)}"]`);
                if (!row) return '';
                row.setAttribute(args.markAttr, 'matched');
                return String(row.innerText || row.textContent || '').replace(/\\s+/g, ' ').trim();
            }""",
            {
                "markAttr": _BRANCH_ROW_MARK_ATTR,
                "indexAttr": "data-codex-branch-row-index",
                "idx": chosen_row.get("idx"),
            },
        )
        log.info(
            "[表单] 查询开户支行候选评分命中：score=%s kind=%s reason=%s row=%s",
            chosen_score.score,
            chosen_score.kind,
            chosen_score.reason,
            str(result or "")[:120],
        )
        return str(result) if result else None
    except PlaywrightError as e:
        log.warning("[表单] 开户支行结果行定位失败（%s）", type(e).__name__)
        return None


def _select_marked_branch_row(page: Page) -> bool:
    row = page.locator(f"[{_BRANCH_ROW_MARK_ATTR}]").first
    try:
        row.wait_for(state="visible", timeout=1000)
        selected_js = """(row) => {
            // Element UI 的隐藏 input 可被 Playwright check() 改成 checked，
            // 但页面模型未必同步；这里只认真实 UI 选中态。
            return Boolean(row.querySelector(
                '.el-radio__input.is-checked, label.el-radio.is-checked, .el-radio.is-checked, [role="radio"][aria-checked="true"], [aria-checked="true"]'
            ));
        }"""

        points = row.evaluate(
            """(row, args) => {
                const markAttr = args.markAttr;
                const doc = row.ownerDocument || document;
                for (const el of doc.querySelectorAll(`[${markAttr}]`)) {
                    el.removeAttribute(markAttr);
                }
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && rect.width > 0
                        && rect.height > 0;
                };
                const preferred = [
                    row.querySelector('label.el-radio'),
                    row.querySelector('.el-radio'),
                    row.querySelector('.el-radio__inner'),
                    row.querySelector('td:first-child label'),
                    row.querySelector('td:first-child'),
                    row,
                ].filter(visible);
                const unique = [];
                const seen = new Set();
                for (const el of preferred) {
                    if (seen.has(el)) continue;
                    seen.add(el);
                    const rect = el.getBoundingClientRect();
                    el.setAttribute(markAttr, '1');
                    unique.push({
                        x: rect.left,
                        y: rect.top,
                        w: rect.width,
                        h: rect.height,
                    });
                }
                return unique;
            }""",
            {"markAttr": _BRANCH_SELECT_MARK_ATTR},
        )
        if not isinstance(points, list) or not points:
            return False

        for idx, point in enumerate(points, start=1):
            try:
                page.mouse.click(
                    float(point["x"]) + float(point["w"]) / 2,
                    float(point["y"]) + float(point["h"]) / 2,
                )
                safe_sleep(0.25)
            except (KeyError, TypeError, ValueError, PlaywrightError):
                continue
            if row.evaluate(selected_js):
                log.info("[表单] 开户支行结果行单选已确认（坐标点击%d）", idx)
                return True

        try:
            row.evaluate(
                """(row) => {
                    const input = row.querySelector('input[type="radio"]');
                    const label = input ? input.closest('label') : null;
                    const targets = [
                        label,
                        row.querySelector('label.el-radio'),
                        row.querySelector('.el-radio'),
                        row.querySelector('.el-radio__inner'),
                        row.querySelector('td:first-child'),
                    ].filter(Boolean);
                    for (const target of targets) {
                        for (const type of ['mousedown', 'mouseup', 'click']) {
                            target.dispatchEvent(new MouseEvent(type, {
                                bubbles: true,
                                cancelable: true,
                                view: window,
                            }));
                        }
                    }
                }"""
            )
            safe_sleep(0.25)
            if row.evaluate(selected_js):
                log.info("[表单] 开户支行结果行单选已确认（事件点击）")
                return True
        except PlaywrightError:
            pass

        try:
            selected = row.evaluate(
                """(row) => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && rect.width > 0
                            && rect.height > 0;
                    };
                    const selected = () => {
                        return Boolean(row.querySelector(
                            '.el-radio__input.is-checked, label.el-radio.is-checked, .el-radio.is-checked, [role="radio"][aria-checked="true"], [aria-checked="true"]'
                        ));
                    };
                    const targets = [
                        row.querySelector('label.el-radio'),
                        row.querySelector('.el-radio'),
                        row.querySelector('.el-radio__inner'),
                        row.querySelector('td:first-child'),
                        row,
                    ].filter(visible);
                    for (const target of targets) {
                        target.scrollIntoView({ block: 'center', inline: 'center' });
                        target.click();
                        if (selected()) return true;
                    }
                    return selected();
                }"""
            )
        except PlaywrightError:
            selected = False
        if not selected:
            log.warning("[表单] 开户支行结果行已点击但未检测到单选选中态")
        else:
            log.info("[表单] 开户支行结果行单选已确认（DOM click）")
        return bool(selected)
    except (PlaywrightTimeout, PlaywrightError) as e:
        log.warning("[表单] 开户支行结果行点击失败（%s）", type(e).__name__)
        return False


def _confirm_branch_query_dialog(page: Page, bank_name: str, branch_name: str) -> bool:
    """
    处理农行「查询开户支行」模态：填支行关键字 → 查询 → 精确匹配结果行 → 确定。

    只在结果行能匹配 JSON 中的支行名称时自动确认；否则关闭弹窗并让字段进入待核对。
    """
    dialog = _current_branch_query_dialog(page, timeout_ms=1200)
    if dialog is None:
        return True
    if not branch_name:
        log.warning("[表单] 已出现查询开户支行弹窗，但数据缺少 %s，无法自动确认", F_BRANCH)
        _close_branch_query_dialog(page)
        return False

    log.info("[表单] 检测到查询开户支行弹窗，按支行名称查询并确认")
    try:
        search_input = dialog.locator(
            "input:visible[placeholder*='支行关键字'], "
            "input:visible[placeholder*='大额行号'], "
            "input:visible[placeholder*='支行']"
        ).first
        search_input.wait_for(state="visible", timeout=2000)
        search_input.fill(branch_name)
    except (PlaywrightTimeout, PlaywrightError) as e:
        log.warning("[表单] 查询开户支行弹窗内支行输入失败（%s）", type(e).__name__)
        _close_branch_query_dialog(page)
        return False

    if not _click_branch_dialog_button(page, dialog, "查询"):
        log.warning("[表单] 查询开户支行按钮点击失败")
        _close_branch_query_dialog(page)
        return False

    matched = None
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        matched = _mark_branch_query_row(dialog, branch_name)
        if matched:
            break
        safe_sleep(0.4)
    if not matched:
        log.warning("[表单] 查询开户支行未找到匹配支行结果，请人工核对")
        _close_branch_query_dialog(page)
        return False

    if not _select_marked_branch_row(page):
        _close_branch_query_dialog(page)
        return False

    try:
        if not _click_branch_dialog_button(page, dialog, "确定", prefer_last=True):
            raise PlaywrightTimeout("确定按钮未点击")
        dialog.wait_for(state="hidden", timeout=5000)
        log.info("[表单] 已在查询开户支行弹窗内匹配并确认支行")
        return True
    except (PlaywrightTimeout, PlaywrightError) as e:
        log.warning("[表单] 查询开户支行确定失败（%s）", type(e).__name__)
        _close_branch_query_dialog(page)
        return False


def restore_transient_dropdowns(page: Page) -> None:
    """把 dismiss_transient_dropdowns 隐藏过的元素恢复到原始 inline display。"""
    try:
        result = page.evaluate(
            """() => {
                const els = document.querySelectorAll('[data-codex-hidden-transient]');
                let n = 0;
                let keptHidden = 0;
                for (const el of els) {
                    if (el.hasAttribute('data-codex-bank-overlay')) {
                        // 收款方名册/放大镜搜索浮层：保持隐藏，不恢复到人工
                        // 核对截图里。只清掉 codex 标记，保留 display:none，
                        // 不动任何已填表单字段的值。
                        el.removeAttribute('data-codex-hidden-transient');
                        el.removeAttribute('data-codex-prev-display');
                        el.removeAttribute('data-codex-bank-overlay');
                        keptHidden += 1;
                        continue;
                    }
                    const prev = el.getAttribute('data-codex-prev-display') || '';
                    el.style.display = prev;
                    el.removeAttribute('data-codex-hidden-transient');
                    el.removeAttribute('data-codex-prev-display');
                    n += 1;
                }
                return { restored: n, keptHidden };
            }"""
        )
        if isinstance(result, dict):
            restored = int(result.get("restored", 0) or 0)
            kept_hidden = int(result.get("keptHidden", 0) or 0)
        else:
            restored, kept_hidden = int(result or 0), 0
        if restored:
            log.info("[表单] 已恢复 %s 个临时隐藏元素的 display", restored)
        if kept_hidden:
            log.info(
                "[表单] 保持隐藏 %s 个收款方名册/放大镜业务浮层（避免遮挡最终人工核对截图）",
                kept_hidden,
            )
    except PlaywrightError as e:
        log.warning("[表单] 恢复隐藏元素失败（%s）", type(e).__name__)


def close_residual_payee_popovers(page: Page, reason: str = "最终核对前") -> int:
    """
    最终人工核对/截图前的兜底清理。

    只针对类名包含 toAccList / magnifier 的「收款方名册 / 放大镜搜索」业务
    浮层：如果它们当前仍可见，且覆盖到「收款户名」或「收款方开户行」标签
    区域，就 display='none'。这是为了应对页面在 restore_transient_dropdowns
    之后又自行重新弹出/重渲染该面板的情况（与 codex 标记无关）。

    严格约束：
      - 只按 _BANK_OVERLAY_CLASS_TOKENS（toAccList/magnifier）类名命中；
      - 只处理 absolute/fixed 且与收款户名/开户行区域重叠的元素；
      - 绝不删除 DOM、绝不触碰 input/textarea/select/button 或已填字段值、
        绝不动 .el-select-dropdown / .el-popper / [role=listbox] 等真实下拉。
    返回隐藏的元素数量。
    """
    try:
        hidden = page.evaluate(
            """(tokens) => {
                const norm = (s) => (s || '').replace(/\\s+/g, '');
                const labels = Array.from(document.querySelectorAll('body *'))
                    .filter(el => {
                        const style = window.getComputedStyle(el);
                        const text = norm(el.textContent);
                        const rect = el.getBoundingClientRect();
                        return (text.includes('收款户名') || text.includes('收款方开户行'))
                            && style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && rect.width > 0
                            && rect.height > 0;
                    })
                    .sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        return (ar.width * ar.height) - (br.width * br.height);
                    });
                if (!labels.length) return 0;
                let probe = null;
                for (const lb of labels) {
                    const r = lb.getBoundingClientRect();
                    const box = {
                        left: r.left - 50,
                        top: r.top - 200,
                        right: r.right + 760,
                        bottom: r.bottom + 200,
                    };
                    if (!probe) {
                        probe = box;
                    } else {
                        probe.left = Math.min(probe.left, box.left);
                        probe.top = Math.min(probe.top, box.top);
                        probe.right = Math.max(probe.right, box.right);
                        probe.bottom = Math.max(probe.bottom, box.bottom);
                    }
                }
                let hidden = 0;
                for (const el of document.querySelectorAll('body *')) {
                    const cls = String(el.className || '');
                    if (!tokens.some(t => cls.indexOf(t) >= 0)) continue;
                    if (el.matches('input, textarea, select, button')) continue;
                    const style = window.getComputedStyle(el);
                    if (!['absolute', 'fixed'].includes(style.position)) continue;
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) continue;
                    const overlaps = rect.left < probe.right
                        && rect.right > probe.left
                        && rect.top < probe.bottom
                        && rect.bottom > probe.top;
                    if (!overlaps) continue;
                    el.style.display = 'none';
                    hidden += 1;
                }
                return hidden;
            }""",
            list(_BANK_OVERLAY_CLASS_TOKENS),
        )
        hidden = int(hidden or 0)
        if hidden:
            log.info(
                "[表单] 最终核对前关闭 %s 个残留收款方名册/放大镜浮层：%s",
                hidden,
                reason,
            )
        return hidden
    except PlaywrightError as e:
        log.warning(
            "[表单] 关闭残留收款方浮层失败（%s）：%s", type(e).__name__, reason
        )
        return 0


def log_bank_dom_diagnostics(page: Page, stage: str) -> None:
    """记录开户行区域的 DOM 命中情况；不记录输入框 value，避免日志泄露账号。"""
    try:
        info = page.evaluate(
            """() => {
                const describe = (el) => {
                    if (!el) return null;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return {
                        tag: el.tagName,
                        id: el.id || '',
                        className: String(el.className || '').slice(0, 160),
                        role: el.getAttribute('role') || '',
                        placeholder: el.getAttribute('placeholder') || '',
                        position: style.position,
                        zIndex: style.zIndex,
                        display: style.display,
                        visibility: style.visibility,
                        rect: {
                            x: Math.round(rect.x),
                            y: Math.round(rect.y),
                            w: Math.round(rect.width),
                            h: Math.round(rect.height),
                        },
                    };
                };
                const labels = Array.from(document.querySelectorAll('body *'))
                    .filter(el => {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        const text = (el.textContent || '').replace(/\\s+/g, '');
                        return text.includes('收款方开户行')
                            && style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && rect.width > 0
                            && rect.height > 0;
                    })
                    .sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        return (ar.width * ar.height) - (br.width * br.height);
                    });
                const label = labels[0] || null;
                const labelRect = label ? label.getBoundingClientRect() : null;
                const samplePoints = [];
                if (labelRect) {
                    const y = labelRect.top + labelRect.height / 2;
                    for (const dx of [80, 220, 420]) {
                        samplePoints.push({
                            x: Math.round(labelRect.right + dx),
                            y: Math.round(y),
                            el: describe(document.elementFromPoint(labelRect.right + dx, y)),
                        });
                    }
                }
                const visibleFloating = Array.from(document.querySelectorAll('body *'))
                    .map(el => ({ el, rect: el.getBoundingClientRect(), style: window.getComputedStyle(el) }))
                    .filter(item => item.style.display !== 'none'
                        && item.style.visibility !== 'hidden'
                        && item.rect.width > 180
                        && item.rect.height > 40
                        && ['absolute', 'fixed'].includes(item.style.position))
                    .slice(0, 12)
                    .map(item => describe(item.el));
                return {
                    label: describe(label),
                    inputPlaceholderCount: document.querySelectorAll('input[placeholder*="开户行"]').length,
                    samplePoints,
                    visibleFloating,
                };
            }"""
        )
        log.info("[诊断] 开户行 DOM（%s）: %s", stage, json.dumps(info, ensure_ascii=False))
    except PlaywrightError as e:
        log.warning("[诊断] 开户行 DOM 诊断失败（%s）：%s", type(e).__name__, stage)


def verify_input_value(
    locator: Optional[Locator], expected_value: str, field_name: str
) -> bool:
    if locator is None:
        return False
    try:
        actual = locator.input_value(timeout=2000)
    except PlaywrightError:
        log.warning("[校验] %s 无法读取 value", field_name)
        return False
    # 字段差异：账号要去掉所有空白（农行偶尔会插入空格分组），
    # 金额要去掉千分位逗号（页面回显可能格式化），其他字段维持 strip 后比较。
    if field_name == F_ACCOUNT:
        actual_norm = "".join(actual.split())
        expected_norm = "".join(expected_value.split())
    elif field_name == F_AMOUNT:
        actual_norm = actual.strip().replace(",", "")
        expected_norm = expected_value.strip().replace(",", "")
    else:
        actual_norm = actual.strip()
        expected_norm = expected_value.strip()
    if actual_norm == expected_norm:
        log.info("[校验] %s ✓", field_name)
        return True
    log.warning("[校验] %s 不一致（请人工确认）", field_name)
    return False


def fill_amount_field(page: Page, value: str) -> Optional[Locator]:
    """金额输入框可能带前缀符号，使用模糊匹配优先。"""
    try:
        locator = page.locator("input:visible[placeholder*='金额']").first
        locator.wait_for(state="visible", timeout=10000)
        locator.click()
        locator.fill(value)
        log.info("[表单] %s 已填写", F_AMOUNT)
        return locator
    except (PlaywrightTimeout, PlaywrightError):
        pass
    return fill_input_by_placeholder_candidates(
        page, ("请输入金额", "金额"), value, F_AMOUNT
    )


def locate_bank_select_input(page: Page) -> Optional[Locator]:
    """
    定位「收款方开户行」下拉对应的 input 元素。

    依次尝试：
      1) 直接的 placeholder 模糊匹配（农行页面这是首选）；
      2) 锚定到「收款方开户行」label，找紧随其后的 .el-select 内的 input；
      3) 兜底：找该 label 之后的第一个 input。
    """
    selectors = (
        "input:visible[placeholder*='开户行']",
        "xpath=//*[contains(normalize-space(.), '收款方开户行')]"
        "/following::div[contains(@class, 'el-select')][1]//input",
        "xpath=//*[contains(normalize-space(.), '收款方开户行')]/following::input[1]",
    )
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            locator.wait_for(state="visible", timeout=1200)
            return locator
        except (PlaywrightTimeout, PlaywrightError):
            continue
    return None


def click_bank_select_by_label(page: Page) -> bool:
    """
    坐标兜底：优先用 locate_bank_select_input 返回的 input bounding_box 中心点击；
    没有 locator 时才退回到标签右侧合理偏移；点击前用 elementFromPoint 校验目标点
    没有被 toAccList/magnifier 覆盖，否则放弃这次点击让上游再清理浮层。
    """
    target = locate_bank_select_input(page)
    if target is not None:
        try:
            target.scroll_into_view_if_needed(timeout=2000)
            box = target.bounding_box(timeout=2000)
        except (PlaywrightTimeout, PlaywrightError) as e:
            log.warning("[表单] 读取开户行 input 坐标失败（%s）", type(e).__name__)
            box = None
        if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2
            if not _bank_target_point_clear(page, cx, cy, label="bank input center"):
                return False
            try:
                page.mouse.click(cx, cy)
                log.info("[表单] 已通过 input bounding_box 中心点击 %s", F_BANK)
                safe_sleep(0.3)
                return True
            except (PlaywrightTimeout, PlaywrightError) as e:
                log.warning("[表单] input 中心点击失败（%s）", type(e).__name__)

    # 没有可用 input locator 时，用「收款方开户行」标签右侧的合理偏移坐标兜底。
    try:
        label = page.get_by_text("收款方开户行", exact=False).first
        label.wait_for(state="visible", timeout=2000)
        label_box = label.bounding_box(timeout=2000)
        if not label_box:
            return False
        # 农行表单：input 在 label 右侧紧接着，宽 ~240，落点取 input 中部。
        cx = label_box["x"] + label_box["width"] + 140
        cy = label_box["y"] + label_box["height"] / 2
        if not _bank_target_point_clear(page, cx, cy, label="label-right offset"):
            return False
        page.mouse.click(cx, cy)
        log.info(
            "[表单] 已通过标签右侧偏移坐标点击 %s 下拉框（%.0f,%.0f）",
            F_BANK, cx, cy,
        )
        safe_sleep(0.3)
        return True
    except (PlaywrightTimeout, PlaywrightError) as e:
        log.warning("[表单] 通过标签坐标点击 %s 失败: %s", F_BANK, type(e).__name__)
        return False


def click_bank_query_trigger_near_input(page: Page) -> bool:
    """
    农行偶发把「开户行」输入框旁边的查询入口作为真实触发器。

    最近页面上 input#bankNameBtn 可见，但 input.click 不再弹出银行面板；
    右侧同一行会出现「查询开户行」文本按钮。这里只在 input 右侧同一行
    小范围内标记这个查询入口，不做全页文本兜底，避免误点其它查询按钮。
    """
    try:
        marked = page.evaluate(
            """(args) => {
                const markAttr = args.markAttr;
                for (const old of document.querySelectorAll(`[${markAttr}]`)) {
                    old.removeAttribute(markAttr);
                }
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && rect.width > 0
                        && rect.height > 0;
                };
                const norm = (text) => String(text || '').replace(/\\s+/g, '');
                const input = document.querySelector('#bankNameBtn')
                    || document.querySelector('input[placeholder*="开户行"]');
                if (!visible(input)) return null;
                const inputRect = input.getBoundingClientRect();
                const candidates = [];
                const nodes = document.querySelectorAll('button,.el-button,[role="button"],a,span,div');
                for (const el of nodes) {
                    if (!visible(el)) continue;
                    const text = norm(el.innerText || el.textContent || '');
                    if (!text.includes('查询') || !text.includes('开户')) continue;
                    const rect = el.getBoundingClientRect();
                    const sameRow = rect.top <= inputRect.bottom + 12
                        && rect.bottom >= inputRect.top - 12;
                    const rightSide = rect.left >= inputRect.right - 20
                        && rect.left <= inputRect.right + 180;
                    if (!sameRow || !rightSide) continue;
                    let clickable = el.closest('button,.el-button,[role="button"],a') || el;
                    if (!visible(clickable)) clickable = el;
                    const cRect = clickable.getBoundingClientRect();
                    candidates.push({
                        el: clickable,
                        x: cRect.left,
                        y: cRect.top,
                        w: cRect.width,
                        h: cRect.height,
                    });
                }
                if (!candidates.length) return null;
                candidates.sort((a, b) => (a.x - b.x) || (a.y - b.y));
                const chosen = candidates[0];
                chosen.el.setAttribute(markAttr, '1');
                return {x: chosen.x, y: chosen.y, w: chosen.w, h: chosen.h};
            }""",
            {"markAttr": _BANK_QUERY_TRIGGER_MARK_ATTR},
        )
    except PlaywrightError as e:
        log.warning("[表单] 查询开户行入口定位失败（%s）", type(e).__name__)
        return False

    if not isinstance(marked, dict):
        log.warning("[表单] 开户行 input 右侧未找到查询开户行入口")
        return False

    trigger = page.locator(f"[{_BANK_QUERY_TRIGGER_MARK_ATTR}]").first
    try:
        trigger.click(timeout=1000, force=True)
        log.info("[表单] 已点击开户行右侧查询入口")
        safe_sleep(0.3)
        return True
    except (PlaywrightTimeout, PlaywrightError):
        pass

    try:
        page.mouse.click(
            float(marked["x"]) + float(marked["w"]) / 2,
            float(marked["y"]) + float(marked["h"]) / 2,
        )
        log.info("[表单] 已通过坐标点击开户行右侧查询入口")
        safe_sleep(0.3)
        return True
    except (KeyError, TypeError, ValueError, PlaywrightError) as e:
        log.warning("[表单] 开户行右侧查询入口点击失败（%s）", type(e).__name__)
        return False


def _locate_bank_dropdown_panel(page: Page, timeout_ms: int) -> Optional[Locator]:
    """
    定位当前真正打开的开户行银行面板。

    农行这个控件有两种形态：
      - 标准 Element UI: .el-select-dropdown:visible；
      - 农行自定义银行面板：挂在 bankNameBtn 下方，class 可能为空，position=absolute，
        尺寸约 600x304，包含「常用银行」及多家银行名称。

    为了避免误碰收款方历史候选，第二种形态必须同时满足：靠近 bankNameBtn、
    与输入框横向对齐、包含多个银行面板 token、且不是 toAccList/magnifier。
    命中后只打 data-codex-bank-panel 标记，不记录面板文本。
    """
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000
    while True:
        try:
            result = page.evaluate(
                """(args) => {
                    const markAttr = args.markAttr;
                    const tokens = args.tokens;
                    const overlayTokens = args.overlayTokens;

                    for (const el of document.querySelectorAll(`[${markAttr}]`)) {
                        el.removeAttribute(markAttr);
                    }

                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && rect.width > 0
                            && rect.height > 0;
                    };

                    const describe = (el, kind, tokenHits) => {
                        const rect = el.getBoundingClientRect();
                        el.setAttribute(markAttr, kind);
                        return {
                            found: true,
                            kind,
                            tokenHits,
                            rect: {
                                x: Math.round(rect.x),
                                y: Math.round(rect.y),
                                w: Math.round(rect.width),
                                h: Math.round(rect.height),
                            },
                        };
                    };

                    const input = document.querySelector('#bankNameBtn')
                        || document.querySelector('input[placeholder*="开户行"]');
                    if (!visible(input)) {
                        return { found: false, reason: 'no-input' };
                    }
                    const inputRect = input.getBoundingClientRect();

                    const standardCandidates = [];
                    for (const el of document.querySelectorAll('.el-select-dropdown')) {
                        if (!visible(el)) continue;
                        const cls = String(el.className || '');
                        if (overlayTokens.some(t => cls.includes(t))) continue;
                        const rect = el.getBoundingClientRect();
                        const horizontallyAligned = rect.left <= inputRect.left + 120
                            && rect.right >= inputRect.right - 80;
                        const verticallyNear = rect.top >= inputRect.bottom - 80
                            && rect.top <= inputRect.bottom + 180;
                        const text = (el.textContent || '').replace(/\\s+/g, '');
                        const hits = tokens.filter(t => text.includes(t)).length;
                        if (!horizontallyAligned && hits < 3) continue;
                        if (!verticallyNear && hits < 3) continue;
                        if (hits < 1) continue;
                        standardCandidates.push({ el, hits, area: rect.width * rect.height });
                    }
                    standardCandidates.sort((a, b) => (b.hits - a.hits) || (a.area - b.area));
                    if (standardCandidates.length) {
                        return describe(
                            standardCandidates[0].el,
                            'element',
                            standardCandidates[0].hits
                        );
                    }

                    const candidates = [];
                    for (const el of document.querySelectorAll('body *')) {
                        if (!visible(el)) continue;
                        const cls = String(el.className || '');
                        if (overlayTokens.some(t => cls.includes(t))) continue;
                        const style = window.getComputedStyle(el);
                        if (!['absolute', 'fixed'].includes(style.position)) continue;
                        const rect = el.getBoundingClientRect();
                        if (rect.width < 300 || rect.width > 900) continue;
                        if (rect.height < 120 || rect.height > 600) continue;

                        const horizontallyAligned = rect.left <= inputRect.left + 80
                            && rect.right >= inputRect.right - 30;
                        const verticallyNear = rect.top >= inputRect.bottom - 40
                            && rect.top <= inputRect.bottom + 120;
                        if (!horizontallyAligned || !verticallyNear) continue;

                        const text = (el.textContent || '').replace(/\\s+/g, '');
                        const hits = tokens.filter(t => text.includes(t)).length;
                        const hasBankPanelShape = text.includes('常用银行') || hits >= 3;
                        if (!hasBankPanelShape) continue;
                        candidates.push({ el, hits, area: rect.width * rect.height });
                    }
                    candidates.sort((a, b) => (b.hits - a.hits) || (a.area - b.area));
                    if (!candidates.length) {
                        return { found: false, reason: 'no-custom-panel' };
                    }
                    return describe(candidates[0].el, 'abc-custom', candidates[0].hits);
                }""",
                {
                    "markAttr": _BANK_PANEL_MARK_ATTR,
                    "tokens": list(_BANK_PANEL_TEXT_TOKENS),
                    "overlayTokens": list(_BANK_OVERLAY_CLASS_TOKENS),
                },
            )
        except PlaywrightError as e:
            log.warning("[表单] 开户行面板定位 JS 失败（%s）", type(e).__name__)
            return None

        if isinstance(result, dict) and result.get("found"):
            kind = str(result.get("kind") or "unknown")
            rect = result.get("rect") or {}
            log.info(
                "[表单] %s 下拉面板已定位（kind=%s, rect=%s,%s,%s,%s）",
                F_BANK,
                kind,
                rect.get("x"),
                rect.get("y"),
                rect.get("w"),
                rect.get("h"),
            )
            return page.locator(f"[{_BANK_PANEL_MARK_ATTR}]").first

        if time.monotonic() >= deadline:
            return None
        safe_sleep(0.15)


def _bank_dropdown_panel_visible(page: Page, timeout_ms: int) -> bool:
    """等到当前真正的银行下拉面板出现。"""
    return _locate_bank_dropdown_panel(page, timeout_ms) is not None


def _current_bank_dropdown_panel(page: Page, timeout_ms: int = 1500) -> Optional[Locator]:
    panel = page.locator(f"[{_BANK_PANEL_MARK_ATTR}]").first
    try:
        panel.wait_for(state="visible", timeout=300)
        return panel
    except (PlaywrightTimeout, PlaywrightError):
        return _locate_bank_dropdown_panel(page, timeout_ms)


def open_bank_select_dropdown(page: Page, select_box: Optional[Locator]) -> bool:
    """
    打开开户行下拉框。

    点击之后必须验证银行面板已经出现才算成功——否则极可能点到了 toAccList
    等遮挡浮层，或者目标面板实际没有打开。验证失败时清理浮层并重试一次。
    重试顺序：input click → 坐标兜底 → input click。期间反复调用 _dismiss_bank_overlays
    清掉收款方候选历史浮层，避免点击事件被吃掉。
    """
    def _click_input() -> bool:
        if select_box is None:
            return False
        try:
            select_box.scroll_into_view_if_needed(timeout=2000)
            select_box.click(timeout=3000)
            log.info("[表单] 已通过 input locator 点击 %s 下拉框", F_BANK)
            safe_sleep(0.25)
            return True
        except (PlaywrightTimeout, PlaywrightError) as e:
            log.warning(
                "[表单] %s input locator 点击失败（%s）",
                F_BANK, type(e).__name__,
            )
            log_bank_dom_diagnostics(page, "input 点击失败后")
            return False

    attempts: list = []
    if select_box is not None:
        attempts.append(("input-1", _click_input))
    attempts.append(("coord", lambda: click_bank_select_by_label(page)))
    if select_box is not None:
        attempts.append(("input-2", _click_input))

    for via, fn in attempts:
        _dismiss_bank_overlays(page, f"{via} 点击前")
        if not fn():
            continue
        if _bank_dropdown_panel_visible(page, timeout_ms=2500):
            log.info("[表单] %s 下拉面板已出现（via=%s）", F_BANK, via)
            return True
        log.warning(
            "[表单] %s 下拉面板未出现（via=%s），可能仍被浮层吞掉点击，准备重试",
            F_BANK, via,
        )
        log_bank_dom_diagnostics(page, f"{via} 点击后下拉未出现")

    log.warning("[表单] %s 多轮尝试后仍无法打开下拉面板", F_BANK)
    return False


def fill_bank_dropdown(
    page: Page,
    value: str,
    branch_name: str = "",
) -> tuple[Optional[Locator], bool]:
    """
    开户行是 Element 风格银行大类下拉框：点击 → 在弹出面板搜索 → 选择候选。

    候选项搜索/点击全部限定在当前打开的银行面板内，
    **不**回退到 `[role='listbox']:visible` 或 `page.get_by_text(...)` 全页文本兜底，
    避免误命中收款方账号 / 户名的 history 候选列表。

    返回 (locator, option_selected)：
      - locator 不为 None 表示定位到下拉输入框；
      - option_selected=True 表示候选项被真正点中并选中；False 表示未确认。
    """
    bank_name = normalize_bank_name_for_abc(value)
    try:
        # 进入开户行交互前：先做通用 Escape+blur，再做开户行专项浮层清理。
        dismiss_transient_dropdowns(page, "进入开户行前")
        _dismiss_bank_overlays(page, "进入开户行前")
        log_bank_dom_diagnostics(page, "进入开户行前")
        select_box = locate_bank_select_input(page)
        if not open_bank_select_dropdown(page, select_box):
            if branch_name:
                log.warning(
                    "[表单] %s 下拉未打开，尝试点击右侧查询开户行入口",
                    F_BANK,
                )
                if click_bank_query_trigger_near_input(page):
                    if _confirm_branch_query_dialog(page, bank_name, branch_name):
                        log.info(
                            "[表单] 已通过右侧查询入口处理开户行/支行弹窗，开始核对页面实际值"
                        )
                        selected = _verify_bank_selection(
                            page,
                            select_box,
                            bank_name,
                            branch_name,
                            exact_candidate=True,
                        )
                        return select_box, selected
                    log.warning("[表单] 右侧查询入口未完成开户行/支行弹窗确认")
            log.warning("[表单] %s 找不到/无法打开开户行下拉框", F_BANK)
            return select_box, False

        # 候选项必须严格落在当前已识别的银行面板内。不要扩展到
        # [role='listbox']:visible 或全页 get_by_text，农行表单上 toAccList /
        # 用途候选都可能伪装成 listbox/同名文本。
        dropdown = _current_bank_dropdown_panel(page, timeout_ms=3000)
        if dropdown is None:
            log.warning("[表单] %s 下拉面板未保持可见，放弃候选项选择", F_BANK)
            return select_box, False

        try:
            search_input = dropdown.locator("input:visible").first
            search_input.wait_for(state="visible", timeout=1000)
            search_input.fill(bank_name)
            log.info("[表单] %s 已在下拉搜索框输入银行名（不展示具体值）", F_BANK)
            safe_sleep(0.4)
        except (PlaywrightTimeout, PlaywrightError):
            log.info("[表单] %s 下拉面板无可输入搜索框，直接选择候选", F_BANK)

        exact_candidate = _mark_bank_option(dropdown, bank_name, exact=True)
        if exact_candidate:
            log.info("[表单] %s 在银行面板内 exact 命中候选项", F_BANK)
        elif _mark_bank_option(dropdown, bank_name, exact=False):
            log.warning(
                "[表单] %s 在银行面板内未 exact 命中，退回子串匹配；请人工核对",
                F_BANK,
            )
        else:
            log.warning(
                "[表单] %s 在银行面板内未找到候选项（不做全页文本兜底），请人工核对",
                F_BANK,
            )
            return select_box, False

        if not _click_marked_bank_option(page):
            return select_box, False

        log.info("[表单] %s 已在银行面板内点击候选项，开始核对页面实际值", F_BANK)
        if not _confirm_branch_query_dialog(page, bank_name, branch_name):
            return select_box, False
        selected = _verify_bank_selection(
            page,
            select_box,
            bank_name,
            branch_name,
            exact_candidate=exact_candidate,
        )
        return select_box, selected
    except (PlaywrightTimeout, PlaywrightError) as e:
        log.warning("[表单] %s 填写失败: %s", F_BANK, type(e).__name__)
        return None, False


def _clear_input_field(
    page: Page, candidates: Iterable[str], field_name: str
) -> bool:
    """尝试把 placeholder 候选匹配到的可见 input 清空，返回是否真的为空。"""
    placeholders = list(candidates)
    locator: Optional[Locator] = None
    for selector_template in ("input:visible[placeholder='{0}']", "input:visible[placeholder*='{0}']"):
        for placeholder in placeholders:
            try:
                candidate = page.locator(selector_template.format(placeholder)).first
                candidate.wait_for(state="visible", timeout=1500)
                locator = candidate
                break
            except (PlaywrightTimeout, PlaywrightError):
                continue
        if locator is not None:
            break
    if locator is None:
        return False
    try:
        current = (locator.input_value(timeout=1000) or "").strip()
    except PlaywrightError:
        current = ""
    if not current:
        return True
    try:
        locator.fill("")
        after = (locator.input_value(timeout=1000) or "").strip()
    except (PlaywrightTimeout, PlaywrightError):
        return False
    if after:
        return False
    log.info("[表单] %s 已清空（避免上一条数据残留）", field_name)
    return True


def _clear_bank_select_field(page: Page) -> bool:
    """
    清空 el-select 风格银行下拉。Element 的 clear 图标只在 hover 后才显示，
    这里先尝试 clear 图标，再尝试 fill('')；如果两条路都不灵就放弃，
    由调用方在 statuses 中标 False，避免串单。
    """
    select_box = locate_bank_select_input(page)
    if select_box is None:
        return False
    try:
        current = (select_box.input_value(timeout=1000) or "").strip()
    except PlaywrightError:
        current = ""
    if not current:
        return True
    try:
        select_box.hover(timeout=1500)
        clear_btn = page.locator(
            ".el-select .el-input__icon.el-icon-circle-close:visible, "
            ".el-input__suffix .el-icon-circle-close:visible"
        ).first
        clear_btn.wait_for(state="visible", timeout=600)
        clear_btn.click(timeout=1000, force=True)
        safe_sleep(0.2)
        after = (select_box.input_value(timeout=1000) or "").strip()
        if not after:
            log.info("[表单] %s 已通过 clear 图标清空（避免上一条数据残留）", F_BANK)
            return True
    except (PlaywrightTimeout, PlaywrightError):
        pass
    try:
        select_box.fill("")
        after = (select_box.input_value(timeout=1000) or "").strip()
        if not after:
            log.info("[表单] %s 已通过 fill('') 清空（避免上一条数据残留）", F_BANK)
            return True
    except (PlaywrightTimeout, PlaywrightError):
        pass
    return False


def fill_transfer_form(page: Page, data: dict, clear_residual: bool = False) -> dict:
    """
    根据校验过的 data 填表。永不自动提交。
    返回 {字段名: 是否通过校验} 的状态字典。

    clear_residual=True 时，对未在 data 中提供的可选字段（开户行/用途）会尝试清空，
    用于批量覆盖填写场景，防止上一条数据残留导致串单。
    """
    log.info("[表单] 开始填写（仅填写，不提交）")
    statuses: dict = {}

    # 收款账号
    if F_ACCOUNT in data:
        loc = fill_input_by_placeholder_candidates(
            page,
            (
                "请输入收款方账号或选择已保存的收款方信息",
                "请输入收款方账号",
                "收款方账号",
                "收款账号",
            ),
            data[F_ACCOUNT],
            F_ACCOUNT,
        )
        statuses[F_ACCOUNT] = verify_input_value(loc, data[F_ACCOUNT], F_ACCOUNT)
        dismiss_transient_dropdowns(page, F_ACCOUNT)
        debug_checkpoint(f"表单字段已处理：{F_ACCOUNT}", page)
        safe_sleep(0.3)

    # 收款户名
    if F_NAME in data:
        loc = fill_input_by_placeholder_candidates(
            page,
            (
                "请输入收款户名或选择已保存的收款方信息",
                "请输入收款户名",
                "收款户名",
            ),
            data[F_NAME],
            F_NAME,
        )
        statuses[F_NAME] = verify_input_value(loc, data[F_NAME], F_NAME)
        dismiss_transient_dropdowns(page, F_NAME)
        debug_checkpoint(f"表单字段已处理：{F_NAME}", page)
        safe_sleep(0.3)

    # 开户行（可空）
    if F_BANK in data:
        _loc, selected = fill_bank_dropdown(page, data[F_BANK], data.get(F_BRANCH, ""))
        # 仅当候选项被点中确认才算自动校验通过；
        # 仅写入文本但未选中下拉项 → False，让其出现在「待人工确认」列表里
        statuses[F_BANK] = selected
        debug_checkpoint(f"表单字段已处理：{F_BANK}", page)
        safe_sleep(0.3)
    elif clear_residual:
        cleared = _clear_bank_select_field(page)
        if cleared:
            statuses[F_BANK] = True
            log.info("[表单] 未提供 %s，已清空上一条残留", F_BANK)
        else:
            statuses[F_BANK] = False
            log.warning(
                "[表单] 未提供 %s 且无法可靠清空下拉值，请人工核对该字段，避免上一条残留串单",
                F_BANK,
            )
        debug_checkpoint(f"表单字段已处理：{F_BANK}（清空残留）", page)
        safe_sleep(0.3)
    else:
        log.info("[表单] 未提供 %s，跳过", F_BANK)

    # 金额
    if F_AMOUNT in data:
        loc = fill_amount_field(page, data[F_AMOUNT])
        statuses[F_AMOUNT] = verify_input_value(loc, data[F_AMOUNT], F_AMOUNT)
        debug_checkpoint(f"表单字段已处理：{F_AMOUNT}", page)
        safe_sleep(0.3)

    # 用途（可空）
    if F_PURPOSE in data:
        loc = fill_input_by_placeholder_candidates(
            page,
            ("请选择或输入用途", "请输入用途", "用途"),
            data[F_PURPOSE],
            F_PURPOSE,
        )
        statuses[F_PURPOSE] = verify_input_value(loc, data[F_PURPOSE], F_PURPOSE)
        dismiss_transient_dropdowns(page, F_PURPOSE)
        debug_checkpoint(f"表单字段已处理：{F_PURPOSE}", page)
    elif clear_residual:
        cleared = _clear_input_field(
            page,
            ("请选择或输入用途", "请输入用途", "用途"),
            F_PURPOSE,
        )
        if cleared:
            statuses[F_PURPOSE] = True
        else:
            statuses[F_PURPOSE] = False
            log.warning(
                "[表单] 未提供 %s 且无法可靠清空，请人工核对该字段，避免上一条残留串单",
                F_PURPOSE,
            )
        dismiss_transient_dropdowns(page, F_PURPOSE)
        debug_checkpoint(f"表单字段已处理：{F_PURPOSE}（清空残留）", page)
    else:
        log.info("[表单] 未提供 %s，跳过（不会填默认值）", F_PURPOSE)

    # 填表结束后恢复任何被 DISMISS_DROPDOWNS_HIDE 路径隐藏的元素，
    # 避免人工核对/提交阶段页面组件还处于 display:none 的坏状态。
    # 注意：收款方名册/放大镜业务浮层（toAccList/magnifier）已在
    # restore_transient_dropdowns 内部被有意保持隐藏，不会被恢复，
    # 否则会盖住最终人工核对截图的「收款户名/收款方开户行」区域。
    restore_transient_dropdowns(page)
    # 兜底：若页面在 restore 后又自行重新弹出该业务浮层，再做一次仅限
    # toAccList/magnifier 且覆盖收款户名/开户行区域的定向关闭，确保
    # 最终 checkpoint 截图（runner.py「流程结束前最终状态」）干净。
    # 只动这两类临时浮层，已填字段值不受影响。
    close_residual_payee_popovers(page, "填表结束后最终核对前")

    failed = [k for k, ok in statuses.items() if not ok]
    if failed:
        log.warning("[表单] 以下字段未通过自动校验，请人工确认：%s", ", ".join(failed))
    log.info("[表单] 填写完成。默认不会提交；如启用一次性提交门禁，后续步骤会单独处理。")
    return statuses


def _scroll_submit_area_into_view(page: Page) -> None:
    """ABC uses nested scroll containers; scroll all plausible containers before locating submit."""
    try:
        page.evaluate("""() => {
            const active = document.activeElement;
            if (active && typeof active.blur === 'function') active.blur();
        }""")
    except PlaywrightError:
        pass

    try:
        touched = page.evaluate(
            """() => {
                const scrollables = [];
                const roots = [
                    document.scrollingElement,
                    document.documentElement,
                    document.body,
                ].filter(Boolean);
                for (const el of document.querySelectorAll('*')) {
                    const style = window.getComputedStyle(el);
                    const canScroll = /(auto|scroll|overlay)/.test(style.overflowY || '');
                    if (canScroll && el.scrollHeight > el.clientHeight + 16) {
                        scrollables.push(el);
                    }
                }
                const seen = new Set();
                const touched = [];
                for (const el of roots.concat(scrollables)) {
                    if (!el || seen.has(el)) continue;
                    seen.add(el);
                    const maxTop = Math.max(0, el.scrollHeight - el.clientHeight);
                    if (maxTop <= 0) continue;
                    const before = el.scrollTop;
                    el.scrollTop = maxTop;
                    touched.push({
                        tag: el.tagName,
                        id: el.id || '',
                        className: String(el.className || '').slice(0, 80),
                        before,
                        after: el.scrollTop,
                        maxTop,
                    });
                }
                window.scrollTo(0, Math.max(
                    document.documentElement.scrollHeight,
                    document.body.scrollHeight
                ));
                return touched.slice(0, 12);
            }"""
        )
        log.info("[提交测试] 已尝试滚动 %d 个页面/内部容器到底部", len(touched or []))
    except PlaywrightError as exc:
        log.warning("[提交测试] 滚动内部容器失败：%s", type(exc).__name__)

    try:
        page.keyboard.press("End")
    except PlaywrightError:
        pass

    try:
        viewport = page.viewport_size or {"width": 1280, "height": 900}
        page.mouse.move(viewport["width"] * 0.72, viewport["height"] * 0.72)
        for _ in range(4):
            page.mouse.wheel(0, 1400)
            safe_sleep(0.08)
    except PlaywrightError:
        pass
    safe_sleep(0.4)


def _mark_visible_submit_candidate(page: Page) -> Optional[dict]:
    try:
        return page.evaluate(
            """() => {
                for (const old of document.querySelectorAll('[data-codex-abc-submit-once]')) {
                    old.removeAttribute('data-codex-abc-submit-once');
                }
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 8
                        && rect.height > 8
                        && rect.bottom > 0
                        && rect.right > 0
                        && rect.top < window.innerHeight
                        && rect.left < window.innerWidth
                        && style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.pointerEvents !== 'none'
                        && !el.hasAttribute('disabled')
                        && !String(el.className || '').includes('is-disabled');
                };
                const candidates = [];
                for (const node of document.querySelectorAll('button,[role="button"],a,div,span')) {
                    const text = (node.innerText || node.textContent || '').replace(/\\s+/g, '').trim();
                    if (text !== '提交') continue;
                    const clickable = node.closest(
                        'button,[role="button"],a,.el-button,[class*="button"],[class*="btn"]'
                    ) || node;
                    if (!isVisible(clickable)) continue;
                    const rect = clickable.getBoundingClientRect();
                    const style = window.getComputedStyle(clickable);
                    candidates.push({
                        el: clickable,
                        x: rect.left,
                        y: rect.top,
                        w: rect.width,
                        h: rect.height,
                        bg: style.backgroundColor || '',
                        className: String(clickable.className || '').slice(0, 120),
                    });
                }
                candidates.sort((a, b) => (b.y - a.y) || ((b.w * b.h) - (a.w * a.h)));
                const chosen = candidates[0];
                if (!chosen) return null;
                chosen.el.setAttribute('data-codex-abc-submit-once', 'true');
                return {
                    x: chosen.x,
                    y: chosen.y,
                    w: chosen.w,
                    h: chosen.h,
                    bg: chosen.bg,
                    className: chosen.className,
                };
            }"""
        )
    except PlaywrightError:
        return None


def click_submit_once_after_fill(page: Page, statuses: dict) -> bool:
    """Click the page's submit button once, only when the explicit one-off gate is enabled."""
    if not env_flag("ABC_ALLOW_SUBMIT_ONCE", False):
        log.info("[提交测试] ABC_ALLOW_SUBMIT_ONCE=false，跳过页面提交按钮")
        return False

    failed = [k for k, ok in statuses.items() if not ok]
    if failed:
        raise RuntimeError(
            "存在未通过自动校验字段，拒绝点击提交: " + ", ".join(failed)
        )

    log.warning("[提交测试] ABC_ALLOW_SUBMIT_ONCE=true，本次将点击页面「提交」按钮一次")
    debug_checkpoint("提交前最终表单状态", page, force_screenshot=True)
    _scroll_submit_area_into_view(page)

    candidates = [
        page.get_by_role("button", name=re.compile(r"^\s*提交\s*$")),
        page.locator("button:has-text('提交')"),
        page.locator(".el-button:has-text('提交')"),
        page.locator("[role='button']:has-text('提交')"),
        page.get_by_text("提交", exact=True),
    ]
    last_error = ""
    for attempt in range(1, 4):
        for locator in candidates:
            try:
                count = locator.count()
            except PlaywrightError as exc:
                last_error = str(exc)
                continue
            for index in range(count - 1, -1, -1):
                button = locator.nth(index)
                try:
                    if not button.is_visible(timeout=800):
                        continue
                    if not button.is_enabled(timeout=800):
                        continue
                    button.scroll_into_view_if_needed(timeout=2000)
                    button.click(timeout=5000)
                    log.warning("[提交测试] 已点击页面「提交」按钮一次")
                    debug_checkpoint("已点击页面提交按钮", page, force_screenshot=True)
                    return True
                except (PlaywrightTimeout, PlaywrightError) as exc:
                    last_error = str(exc)
                    continue

        marked = _mark_visible_submit_candidate(page)
        if marked:
            try:
                button = page.locator("[data-codex-abc-submit-once='true']").first
                button.scroll_into_view_if_needed(timeout=2000)
                button.click(timeout=5000)
                log.warning(
                    "[提交测试] 已通过文本候选点击页面「提交」按钮一次: y=%.1f h=%.1f bg=%s",
                    float(marked.get("y") or 0),
                    float(marked.get("h") or 0),
                    marked.get("bg") or "",
                )
                debug_checkpoint("已点击页面提交按钮", page, force_screenshot=True)
                return True
            except (PlaywrightTimeout, PlaywrightError) as exc:
                last_error = str(exc)
        log.info("[提交测试] 第 %d 次未定位到可点提交，继续滚动兜底", attempt)
        _scroll_submit_area_into_view(page)

    raise RuntimeError(f"未找到可点击的页面「提交」按钮，拒绝继续。最后错误: {last_error}")


def _trade_info_confirm_dialog_visible(page: Page) -> bool:
    required_tokens = ("交易信息确认", "付款方信息", "收款方信息", "转账信息")
    wrappers = page.locator(
        ".el-dialog__wrapper:visible, .el-message-box__wrapper:visible, [role='dialog']:visible"
    )
    try:
        count = wrappers.count()
    except PlaywrightError:
        return False
    for index in range(count - 1, -1, -1):
        wrapper = wrappers.nth(index)
        try:
            text = wrapper.inner_text(timeout=1000)
        except (PlaywrightTimeout, PlaywrightError):
            continue
        compact = re.sub(r"\s+", "", text)
        if all(token in compact for token in required_tokens):
            return True
    return False


def click_post_submit_message_confirm_once(page: Page) -> bool:
    """Confirm the narrow ABC post-submit account-length prompt when explicitly gated."""
    if not env_flag("ABC_CONFIRM_SUBMIT_MODAL_ONCE", False):
        log.info("[提交测试] ABC_CONFIRM_SUBMIT_MODAL_ONCE=false，跳过提交后的网页确认弹窗")
        return False

    log.warning("[提交测试] ABC_CONFIRM_SUBMIT_MODAL_ONCE=true，将仅确认提交后的账户位数提示")
    deadline = time.time() + env_float("ABC_CONFIRM_SUBMIT_MODAL_TIMEOUT_S", 8.0)
    last_error = ""
    while time.time() < deadline:
        wrappers = page.locator(
            ".el-message-box__wrapper:visible, .el-dialog__wrapper:visible, [role='dialog']:visible"
        )
        try:
            count = wrappers.count()
        except PlaywrightError as exc:
            last_error = str(exc)
            count = 0
        for index in range(count - 1, -1, -1):
            wrapper = wrappers.nth(index)
            try:
                text = wrapper.inner_text(timeout=1000)
            except (PlaywrightTimeout, PlaywrightError) as exc:
                last_error = str(exc)
                continue
            compact = re.sub(r"\s+", "", text)
            if "消息确认" not in compact or "请确认收款账户位数是否准确" not in compact:
                continue
            debug_checkpoint("提交后网页确认弹窗出现", page, force_screenshot=True)
            button = wrapper.locator(
                ".el-button--primary:has-text('确定'), button:has-text('确定'), .el-button:has-text('确定')"
            ).last
            try:
                button.click(timeout=5000)
                log.warning("[提交测试] 已点击提交后网页确认弹窗「确定」一次")
                debug_checkpoint("已确认提交后网页弹窗", page, force_screenshot=True)
                return True
            except (PlaywrightTimeout, PlaywrightError) as exc:
                last_error = str(exc)
                continue
        if _trade_info_confirm_dialog_visible(page):
            log.info("[提交测试] 未出现收款账户位数确认弹窗，已进入交易信息确认；跳过该窄弹窗确认")
            return False
        safe_sleep(0.25)

    if _trade_info_confirm_dialog_visible(page):
        log.info("[提交测试] 未出现收款账户位数确认弹窗，已进入交易信息确认；跳过该窄弹窗确认")
        return False

    raise RuntimeError(f"未找到可确认的提交后账户位数弹窗，拒绝继续。最后错误: {last_error}")


def click_trade_info_confirm_once(page: Page) -> bool:
    """Confirm ABC's transaction-information dialog when the final one-off gate is enabled."""
    if not env_flag("ABC_CONFIRM_TRADE_INFO_ONCE", False):
        log.info("[提交测试] ABC_CONFIRM_TRADE_INFO_ONCE=false，跳过交易信息确认弹窗")
        return False

    log.warning("[提交测试] ABC_CONFIRM_TRADE_INFO_ONCE=true，将仅确认交易信息弹窗「确定」一次")
    deadline = time.time() + env_float("ABC_CONFIRM_TRADE_INFO_TIMEOUT_S", 10.0)
    last_error = ""
    required_tokens = ("交易信息确认", "付款方信息", "收款方信息", "转账信息")
    while time.time() < deadline:
        wrappers = page.locator(
            ".el-dialog__wrapper:visible, .el-message-box__wrapper:visible, [role='dialog']:visible"
        )
        try:
            count = wrappers.count()
        except PlaywrightError as exc:
            last_error = str(exc)
            count = 0
        for index in range(count - 1, -1, -1):
            wrapper = wrappers.nth(index)
            try:
                text = wrapper.inner_text(timeout=1000)
            except (PlaywrightTimeout, PlaywrightError) as exc:
                last_error = str(exc)
                continue
            compact = re.sub(r"\s+", "", text)
            if not all(token in compact for token in required_tokens):
                continue
            debug_checkpoint("交易信息确认弹窗出现", page, force_screenshot=True)
            button = wrapper.locator(
                ".el-button--primary:has-text('确定'), button:has-text('确定'), .el-button:has-text('确定')"
            ).last
            try:
                button.click(timeout=5000)
                log.warning("[提交测试] 已点击交易信息确认弹窗「确定」一次")
                debug_checkpoint("已确认交易信息弹窗", page, force_screenshot=True)
                return True
            except (PlaywrightTimeout, PlaywrightError) as exc:
                last_error = str(exc)
                marked = _mark_trade_info_confirm_button(page, required_tokens)
                if marked:
                    try:
                        page.mouse.click(
                            float(marked["x"]) + float(marked["w"]) / 2,
                            float(marked["y"]) + float(marked["h"]) / 2,
                        )
                        log.warning(
                            "[提交测试] 已通过坐标兜底点击交易信息确认弹窗「确定」一次: x=%.1f y=%.1f",
                            float(marked["x"]),
                            float(marked["y"]),
                        )
                        debug_checkpoint("已确认交易信息弹窗", page, force_screenshot=True)
                        return True
                    except (PlaywrightTimeout, PlaywrightError) as click_exc:
                        last_error = str(click_exc)
                continue
        safe_sleep(0.25)

    raise RuntimeError(f"未找到可确认的交易信息弹窗，拒绝继续。最后错误: {last_error}")


def transfer_kb_password_dialog_visible(page: Page) -> bool:
    return _find_transfer_kb_password_dialog(page) is not None


def submit_transfer_kb_password_once(page: Page, password: str) -> bool:
    """Submit the web-rendered transfer K宝 password dialog, explicitly gated."""
    if not env_flag("ABC_TRANSFER_KB_PASSWORD_AFTER_TRADE", False):
        log.info("[提交测试] ABC_TRANSFER_KB_PASSWORD_AFTER_TRADE=false，跳过交易 K 宝密码输入")
        return False
    if not password:
        raise RuntimeError("交易 K 宝密码为空，拒绝继续")
    if not password.isascii():
        raise RuntimeError("交易 K 宝密码包含非 ASCII 字符，拒绝自动输入以免误键")

    wait_s = env_float("ABC_TRANSFER_KB_PASSWORD_TIMEOUT_S", 15.0)
    log.warning(
        "[提交测试] ABC_TRANSFER_KB_PASSWORD_AFTER_TRADE=true，将复用登录阶段 K 宝密码窗口策略输入交易 K 宝密码"
    )
    debug_checkpoint("等待交易K宝密码窗口", page, force_screenshot=True)
    if not input_kb_password(password, wait_seconds=wait_s):
        raise RuntimeError("未能通过 K 宝密码窗口提交交易 K 宝密码，拒绝触发物理 OK")
    log.warning("[提交测试] 已通过 K 宝密码窗口提交交易 K 宝密码一次")
    debug_checkpoint("已提交交易K宝密码", page, force_screenshot=True)
    return True


def _find_transfer_kb_password_dialog(page: Page) -> Optional[Locator]:
    wrappers = page.locator(
        ".el-dialog__wrapper:visible, .el-dialog:visible, [role='dialog']:visible, body"
    )
    try:
        count = wrappers.count()
    except PlaywrightError:
        return None
    for index in range(count - 1, -1, -1):
        wrapper = wrappers.nth(index)
        try:
            if not wrapper.is_visible(timeout=300):
                continue
            text = wrapper.inner_text(timeout=800)
        except (PlaywrightTimeout, PlaywrightError):
            continue
        compact = re.sub(r"\s+", "", text)
        if "验证K宝密码" in compact and "密码" in compact and "确定" in compact:
            return wrapper
    return None


def _fill_transfer_kb_password(dialog: Locator, password: str) -> None:
    password_input = dialog.locator("input:visible").first
    try:
        password_input.click(timeout=2000)
        password_input.fill("", timeout=2000)
        password_input.type(password, delay=int(KB_PASSWORD_TYPE_INTERVAL_S * 1000), timeout=5000)
    except (PlaywrightTimeout, PlaywrightError):
        log.warning("[提交测试] 交易 K 宝密码输入框 type 失败，尝试网页软键盘")
        _click_transfer_kb_virtual_keys(dialog, password)

    length = _transfer_kb_password_length(dialog)
    if length != len(password):
        log.warning("[提交测试] 交易 K 宝密码长度校验未通过，改用网页软键盘重试")
        try:
            clear_btn = dialog.locator("button:has-text('清除'), .el-button:has-text('清除'), text=清除").last
            clear_btn.click(timeout=1500)
        except (PlaywrightTimeout, PlaywrightError):
            pass
        _click_transfer_kb_virtual_keys(dialog, password)
        length = _transfer_kb_password_length(dialog)
    if length != len(password):
        raise RuntimeError(f"交易 K 宝密码长度校验失败，期望 {len(password)} 位，实际 {length} 位")


def _transfer_kb_password_length(dialog: Locator) -> int:
    try:
        return int(
            dialog.locator("input:visible").first.evaluate(
                "(el) => String(el.value || '').length"
            )
        )
    except (PlaywrightTimeout, PlaywrightError, ValueError):
        return -1


def _click_transfer_kb_virtual_keys(dialog: Locator, password: str) -> None:
    for char in password:
        if char.isupper():
            _click_transfer_kb_key(dialog, "大写")
            _click_transfer_kb_key(dialog, char.lower())
            _click_transfer_kb_key(dialog, "大写")
        else:
            _click_transfer_kb_key(dialog, char)


def _click_transfer_kb_key(dialog: Locator, key_text: str) -> None:
    selectors = (
        f"button:has-text('{key_text}')",
        f".el-button:has-text('{key_text}')",
        f"text={key_text}",
    )
    last_error = ""
    for selector in selectors:
        locator = dialog.locator(selector).filter(has_text=re.compile(rf"^\s*{re.escape(key_text)}\s*$")).first
        try:
            if not locator.is_visible(timeout=800):
                continue
            locator.click(timeout=1500)
            safe_sleep(0.05)
            return
        except (PlaywrightTimeout, PlaywrightError) as exc:
            last_error = str(exc)
            continue
    raise RuntimeError(f"交易 K 宝软键盘未找到按键 {key_text!r}: {last_error}")


def _click_transfer_kb_confirm(page: Page, dialog: Locator) -> None:
    button = dialog.locator(
        "button:has-text('确定'), .el-button:has-text('确定'), [role='button']:has-text('确定'), text=确定"
    ).last
    try:
        button.click(timeout=5000)
        return
    except (PlaywrightTimeout, PlaywrightError):
        marked = _mark_transfer_kb_confirm_button(page)
        if not marked:
            raise
        page.mouse.click(
            float(marked["x"]) + float(marked["w"]) / 2,
            float(marked["y"]) + float(marked["h"]) / 2,
        )


def _mark_transfer_kb_confirm_button(page: Page) -> Optional[dict]:
    try:
        return page.evaluate(
            """() => {
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 8 && rect.height > 8
                        && rect.bottom > 0 && rect.right > 0
                        && rect.top < window.innerHeight && rect.left < window.innerWidth
                        && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const dialogs = Array.from(document.querySelectorAll(
                    '.el-dialog__wrapper,.el-dialog,[role="dialog"],body'
                )).filter((dialog) => {
                    if (!visible(dialog)) return false;
                    const text = (dialog.innerText || dialog.textContent || '').replace(/\\s+/g, '');
                    return text.includes('验证K宝密码') && text.includes('密码') && text.includes('确定');
                });
                for (const dialog of dialogs) {
                    const buttons = Array.from(dialog.querySelectorAll(
                        'button,.el-button,[role="button"],a,span,div'
                    )).map((node) => {
                        const clickable = node.closest(
                            'button,.el-button,[role="button"],a,[class*="button"],[class*="btn"]'
                        ) || node;
                        const text = (node.innerText || node.textContent || '').replace(/\\s+/g, '').trim();
                        const rect = clickable.getBoundingClientRect();
                        return { clickable, text, rect };
                    }).filter((item) => item.text === '确定' && visible(item.clickable));
                    buttons.sort((a, b) => (b.rect.y - a.rect.y) || (b.rect.x - a.rect.x));
                    const chosen = buttons[0];
                    if (!chosen) continue;
                    return {
                        x: chosen.rect.left,
                        y: chosen.rect.top,
                        w: chosen.rect.width,
                        h: chosen.rect.height,
                    };
                }
                return null;
            }"""
        )
    except PlaywrightError:
        return None


def _mark_trade_info_confirm_button(page: Page, required_tokens: tuple[str, ...]) -> Optional[dict]:
    try:
        return page.evaluate(
            """(requiredTokens) => {
                for (const old of document.querySelectorAll('[data-codex-abc-trade-confirm]')) {
                    old.removeAttribute('data-codex-abc-trade-confirm');
                }
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 8
                        && rect.height > 8
                        && rect.bottom > 0
                        && rect.right > 0
                        && rect.top < window.innerHeight
                        && rect.left < window.innerWidth
                        && style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.pointerEvents !== 'none';
                };
                const dialogs = Array.from(document.querySelectorAll(
                    '.el-dialog__wrapper,.el-dialog,[role="dialog"]'
                )).filter((dialog) => {
                    if (!visible(dialog)) return false;
                    const text = (dialog.innerText || dialog.textContent || '').replace(/\\s+/g, '');
                    return requiredTokens.every((token) => text.includes(token));
                });
                for (const dialog of dialogs) {
                    const buttons = Array.from(dialog.querySelectorAll(
                        'button,.el-button,[role="button"],a,span,div'
                    )).map((node) => {
                        const clickable = node.closest(
                            'button,.el-button,[role="button"],a,[class*="button"],[class*="btn"]'
                        ) || node;
                        const text = (node.innerText || node.textContent || '').replace(/\\s+/g, '').trim();
                        const rect = clickable.getBoundingClientRect();
                        return { node, clickable, text, rect };
                    }).filter((item) => item.text === '确定' && visible(item.clickable));
                    buttons.sort((a, b) => (b.rect.y - a.rect.y) || (b.rect.x - a.rect.x));
                    const chosen = buttons[0];
                    if (!chosen) continue;
                    chosen.clickable.setAttribute('data-codex-abc-trade-confirm', 'true');
                    return {
                        x: chosen.rect.left,
                        y: chosen.rect.top,
                        w: chosen.rect.width,
                        h: chosen.rect.height,
                    };
                }
                return null;
            }""",
            list(required_tokens),
        )
    except PlaywrightError:
        return None


def fill_transfer_batch(page: Page, batch: list[dict]) -> list[dict]:
    """批量覆盖填写同一个单笔转账表单；每条都只填表并截图，不提交。"""
    results: list[dict] = []
    total = len(batch)
    pause_s = env_float("TRANSFER_BATCH_PAUSE_S", 0.8)
    for idx, data in enumerate(batch, start=1):
        label = str(data.get(BATCH_LABEL_KEY) or f"第 {idx} 条")
        source = str(data.get(BATCH_SOURCE_KEY) or "")
        log.info("=" * 60)
        log.info("[批量] 第 %d/%d 条开始填写: %s", idx, total, label)
        if source:
            log.info("[批量] 来源截图: %s", source)
        # 批量模式必须 clear_residual=True：上一条记录的开户行/用途不会自己消失，
        # 当前条若没有这两个字段，残留值会直接被一起提交，造成串单。
        statuses = fill_transfer_form(page, data, clear_residual=True)
        failed = [k for k, ok in statuses.items() if not ok]
        if failed:
            log.warning("[批量] 第 %d/%d 条存在待核对字段: %s", idx, total, ", ".join(failed))
        else:
            log.info("[批量] 第 %d/%d 条字段自动校验通过", idx, total)
        debug_checkpoint(f"批量第_{idx:02d}_条填写结束_{label}", page, force_screenshot=True)
        results.append({"index": idx, "label": label, "failed": failed})
        if idx < total:
            safe_sleep(pause_s)
    log.info("[批量] 已完成 %d 条覆盖填表验证；填表阶段未提交", total)
    return results

