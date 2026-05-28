import os
import time

from .debug import _debug_checkpoint

# 收款方数据仅来自 BOC_PAYEE_* 环境变量；**不再内置任何真实默认兜底值**。
# 缺少必填环境变量时，这些值为空字符串，_fill_transfer_form 会
# fail-closed 跳过填表（不提交），绝不用历史真实账号/户名/支行兜底。
_ENV_PAYEE_BANK = os.environ.get("BOC_PAYEE_BANK", "").strip()
_ENV_PAYEE_BANK_CODE = os.environ.get("BOC_PAYEE_BANK_CODE", "").strip()
TEST_PAYEE_ACCOUNT = os.environ.get("BOC_PAYEE_ACCOUNT", "").strip()
TEST_PAYEE_NAME = os.environ.get("BOC_PAYEE_NAME", "").strip()
TEST_PAYEE_BANK_NAME = _ENV_PAYEE_BANK
TEST_PAYEE_TYPE = os.environ.get("BOC_PAYEE_TYPE", "").strip() or "单位"
if TEST_PAYEE_TYPE not in {"单位", "个人"}:
    TEST_PAYEE_TYPE = "单位"
# 行号为禁用字段，选择开户行选项后由页面自动回填，无需内置默认值。
TEST_PAYEE_BANK_CODE = _ENV_PAYEE_BANK_CODE
TEST_AMOUNT = os.environ.get("BOC_PAYMENT_AMOUNT", "").strip().replace(",", "")

_BANK_BRANDS = (
    "中国工商银行",
    "中国建设银行",
    "中国农业银行",
    "中国银行",
    "招商银行",
    "中国民生银行",
    "中信银行",
    "中国邮政储蓄银行",
    "兴业银行",
    "交通银行",
    "上海浦东发展银行",
    "浦发银行",
    "中国光大银行",
    "光大银行",
    "华夏银行",
    "平安银行",
    "广发银行",
    "北京银行",
    "桂林国民村镇银行",
)


def _find_input_by_label(frame, label_text: str):
    """在同一行（或紧邻）查找带星号的必填输入框。"""
    handle = None
    try:
        handle = frame.evaluate_handle(
            r"""
            (label) => {
                const norm = (s) => (s || '').replace(/\s+/g, '');
                const labelKey = norm(label).replace(/^[*]+/, '').replace(/[：:]+$/, '');
                const cleanLabel = (s) => norm(s).replace(/^[*]+/, '').replace(/[：:]+$/, '');
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0;
                };
                const usableInput = (el) => {
                    if (!visible(el)) return false;
                    const tag = el.tagName.toLowerCase();
                    const type = (el.getAttribute('type') || '').toLowerCase();
                    return tag === 'textarea'
                        || !['hidden', 'radio', 'checkbox', 'button', 'submit'].includes(type);
                };
                const rectVisible = (box) => box && box.width > 0 && box.height > 0;
                const inputCandidates = () => Array.from(document.querySelectorAll('input:not([type=hidden]),textarea'))
                    .filter(usableInput)
                    .map((inp) => ({ inp, box: inp.getBoundingClientRect() }));
                const nearestInputOnRow = (labelBox) => {
                    const labelY = labelBox.top + labelBox.height / 2;
                    return inputCandidates()
                        .filter(({ box }) => {
                            const inputY = box.top + box.height / 2;
                            return Math.abs(inputY - labelY) <= Math.max(22, labelBox.height * 2)
                                && box.left >= labelBox.right - 16;
                        })
                        .sort((a, b) => {
                            const ax = Math.max(0, a.box.left - labelBox.right);
                            const bx = Math.max(0, b.box.left - labelBox.right);
                            return ax - bx;
                        })[0]?.inp || null;
                };

                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                const textMatches = [];
                while (walker.nextNode()) {
                    const node = walker.currentNode;
                    if (cleanLabel(node.nodeValue || '') !== labelKey) continue;
                    const parent = node.parentElement;
                    if (!parent || !visible(parent)) continue;
                    const range = document.createRange();
                    range.selectNodeContents(node);
                    const box = range.getBoundingClientRect();
                    range.detach();
                    if (!rectVisible(box)) continue;
                    textMatches.push({ parent, box });
                }
                textMatches.sort((a, b) => {
                    if (a.box.top !== b.box.top) return a.box.top - b.box.top;
                    return a.box.left - b.box.left;
                });
                for (const match of textMatches) {
                    const inp = nearestInputOnRow(match.box);
                    if (inp) return inp;
                }

                const labelOwnText = (el) => {
                    const clone = el.cloneNode(true);
                    clone.querySelectorAll('input,textarea,select,button,svg,i').forEach((node) => node.remove());
                    return clone.innerText || clone.textContent || '';
                };
                const nodes = Array.from(document.querySelectorAll('label,span,p,div'));
                const matches = nodes.filter((el) => {
                    if (!visible(el)) return false;
                    const t = norm(labelOwnText(el));
                    return cleanLabel(t) === labelKey;
                }).sort((a, b) => {
                    const ar = a.getBoundingClientRect();
                    const br = b.getBoundingClientRect();
                    const aArea = ar.width * ar.height;
                    const bArea = br.width * br.height;
                    if (aArea !== bArea) return aArea - bArea;
                    const aLen = norm(labelOwnText(a)).length;
                    const bLen = norm(labelOwnText(b)).length;
                    return aLen - bLen;
                });

                for (const target of matches) {
                    const labelBox = target.getBoundingClientRect();
                    const sameRowInput = nearestInputOnRow(labelBox);
                    if (sameRowInput) return sameRowInput;

                    const containers = [
                        target.closest('.el-form-item'),
                        target.closest('.ant-form-item'),
                        target.closest('[class*="form-item"]'),
                        target.closest('[class*="formItem"]'),
                        target.closest('tr'),
                        target.parentElement,
                    ].filter(Boolean);
                    for (const container of containers) {
                        const containerBox = container.getBoundingClientRect();
                        const candidates = Array.from(container.querySelectorAll('input:not([type=hidden]),textarea'))
                            .filter(usableInput)
                            .map((inp) => ({ inp, box: inp.getBoundingClientRect() }))
                            .filter(({ box }) => Math.abs((box.top + box.height / 2) - (labelBox.top + labelBox.height / 2)) <= Math.max(35, containerBox.height / 2))
                            .sort((a, b) => a.box.left - b.box.left);
                        if (candidates.length) return candidates[0].inp;
                    }
                }
                return null;
            }
            """,
            label_text,
        )
        el = handle.as_element()
        if el is None:
            handle.dispose()
        return el
    except Exception:
        if handle is not None:
            try:
                handle.dispose()
            except Exception:
                pass
        return None


def _click_radio_by_text(frame, text: str) -> bool:
    try:
        return bool(frame.evaluate(
            r"""
            (txt) => {
                const norm = (s) => (s || '').replace(/\s+/g, '');
                const labels = Array.from(document.querySelectorAll('label,span,div'));
                for (const el of labels) {
                    if (norm(el.innerText || el.textContent) === txt) {
                        const radio = el.closest('label')?.querySelector('input[type=radio]')
                            || el.parentElement?.querySelector('input[type=radio]')
                            || el.previousElementSibling;
                        const target = radio || el;
                        target.click();
                        return true;
                    }
                }
                return false;
            }
            """,
            text,
        ))
    except Exception:
        return False


def _radio_selected_by_text(frame, text: str) -> bool:
    try:
        return bool(frame.evaluate(
            r"""
            (txt) => {
                const norm = (s) => (s || '').replace(/\s+/g, '');
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0;
                };
                const nodes = Array.from(document.querySelectorAll('label,span,div'))
                    .filter((el) => visible(el) && norm(el.innerText || el.textContent) === txt);
                for (const el of nodes) {
                    const label = el.closest('label') || el.parentElement;
                    const radio = label?.querySelector?.('input[type=radio]')
                        || el.previousElementSibling;
                    if (radio?.checked) return true;
                    const checkedNode = el.closest('[aria-checked="true"],.is-checked,.checked')
                        || label?.querySelector?.('[aria-checked="true"],.is-checked,.checked');
                    if (checkedNode) return true;
                }
                return false;
            }
            """,
            text,
        ))
    except Exception:
        return False


def _set_input_value(handle, value: str) -> None:
    handle.evaluate(
        r"""
        (el, value) => {
            el.focus();
            const proto = el instanceof HTMLTextAreaElement
                ? HTMLTextAreaElement.prototype
                : HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
            if (setter) {
                setter.call(el, value);
            } else {
                el.value = value;
            }
            try {
                el.dispatchEvent(new InputEvent('input', {
                    bubbles: true,
                    cancelable: true,
                    inputType: 'insertText',
                    data: value,
                }));
            } catch (_) {
                el.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
            }
            el.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
            el.blur();
        }
        """,
        value,
    )


def _value_matches(actual: str, expected: str, accept_suffix: bool = False) -> bool:
    actual = (actual or "").strip()
    expected = (expected or "").strip()
    if actual == expected:
        return True
    return bool(accept_suffix and actual and expected.endswith(actual) and len(actual) >= 8)


def _payee_name_matches(actual: str, expected: str) -> bool:
    actual = (actual or "").replace(" ", "").strip()
    expected = (expected or "").replace(" ", "").strip()
    if not actual or not expected:
        return False
    if actual == expected:
        return True
    # 常用收款人联动后会显示为“账号|户名”，这是页面确认过的候选值。
    return expected in actual


def _normalize_bank_name(text: str) -> str:
    return (
        (text or "")
        .replace(" ", "")
        .replace("股份有限公司", "")
        .replace("有限责任公司", "")
        .replace("有限公司", "")
    )


def _bank_branch_hint(text: str) -> tuple[str, str]:
    normalized = _normalize_bank_name(text)
    for brand in _BANK_BRANDS:
        if normalized.startswith(brand):
            return brand, normalized[len(brand):]
    bank_pos = normalized.find("银行")
    if bank_pos >= 0:
        return normalized[: bank_pos + 2], normalized[bank_pos + 2 :]
    return "", normalized


def _bank_name_matches(actual: str, expected: str) -> bool:
    actual_key = _normalize_bank_name(actual)
    expected_key = _normalize_bank_name(expected)
    if not actual_key or not expected_key:
        return False
    if actual_key == expected_key or actual_key in expected_key or expected_key in actual_key:
        return True
    actual_brand, actual_branch = _bank_branch_hint(actual_key)
    expected_brand, expected_branch = _bank_branch_hint(expected_key)
    if actual_brand and expected_brand and actual_brand != expected_brand:
        return False
    return bool(
        expected_branch
        and expected_branch in actual_key
        and (not actual_brand or actual_brand in expected_key or expected_brand in actual_key)
    ) or bool(actual_branch and actual_branch in expected_key)


def _target_payee_bank_type() -> str:
    brand, _ = _bank_branch_hint(TEST_PAYEE_BANK_NAME)
    return "中行" if brand == "中国银行" else "他行"


def _fill_input_handle(handle, value: str, accept_suffix: bool = False) -> bool:
    if not handle:
        return False
    try:
        handle.click()
        try:
            handle.press("Control+A")
            handle.press("Backspace")
        except Exception:
            handle.evaluate("(el) => { el.focus(); el.value = ''; }")
        handle.type(value, delay=30)
        handle.evaluate(
            "(el) => { el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); el.blur(); }"
        )
        actual = _read_input_value(handle)
        if _value_matches(actual, value, accept_suffix=accept_suffix):
            if accept_suffix and actual != value:
                print(f"[提示] 输入框仅显示尾号/截断值：{actual}", flush=True)
            return True
        _set_input_value(handle, value)
        time.sleep(0.1)
        actual = _read_input_value(handle)
        if _value_matches(actual, value, accept_suffix=accept_suffix):
            if accept_suffix and actual != value:
                print(f"[提示] 输入框仅显示尾号/截断值：{actual}", flush=True)
            return True
        return False
    except Exception:
        return False


def _read_input_value(handle) -> str:
    if not handle:
        return ""
    try:
        return str(handle.evaluate("(el) => el.value || el.getAttribute('value') || ''") or "")
    except Exception:
        return ""


def _fill_required(frame, label: str, value: str, accept_suffix: bool = False) -> bool:
    h = _find_input_by_label(frame, label)
    ok = _fill_input_handle(h, value, accept_suffix=accept_suffix)
    if not ok and _value_matches(
        _read_input_value(h),
        value,
        accept_suffix=accept_suffix,
    ):
        ok = True
    if ok:
        print(f"[已填] {label}", flush=True)
    else:
        print(f"[失败] 未能填写 {label}", flush=True)
    return ok


def _fill_required_stable(frame, label: str, value: str, accept_suffix: bool = False) -> bool:
    ok = _fill_required(frame, label, value, accept_suffix=accept_suffix)
    _wait_form_idle(frame)
    if _verify_required_value(frame, label, value, accept_suffix=accept_suffix):
        return ok
    print(f"[重试] {label} 回读不一致，重新填写", flush=True)
    ok = _fill_required(frame, label, value, accept_suffix=accept_suffix)
    _wait_form_idle(frame)
    return ok and _verify_required_value(frame, label, value, accept_suffix=accept_suffix)


def _wait_form_idle(frame, timeout_ms: int = 6000) -> bool:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            busy = frame.evaluate(
                r"""
                () => {
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0;
                    };
                    return Array.from(document.querySelectorAll(
                        '.el-loading-mask,.el-loading-spinner,.ant-spin,[class*="loading"],[class*="Loading"],[aria-busy="true"]'
                    )).some(visible);
                }
                """
            )
            if not busy:
                return True
        except Exception:
            return True
        time.sleep(0.2)
    print("[提示] 表单仍可能处于加载状态，继续执行", flush=True)
    return False


def _verify_required_value(frame, label: str, expected: str = "", accept_suffix: bool = False) -> bool:
    h = _find_input_by_label(frame, label)
    actual = _read_input_value(h).strip()
    if expected:
        if label == "收款人开户行名称":
            ok = _bank_name_matches(actual, expected)
        elif label == "收款人户名":
            ok = _payee_name_matches(actual, expected)
        else:
            ok = _value_matches(actual, expected, accept_suffix=accept_suffix)
    else:
        ok = bool(actual)
    if ok:
        print(f"[已确认] {label} = {actual}", flush=True)
    else:
        print(
            f"[失败] {label} 回读不匹配：actual={actual or '<empty>'}, expected={expected or '<non-empty>'}",
            flush=True,
        )
    return ok


def _click_radio_required(frame, field_name: str, value: str) -> bool:
    ok = False
    for _ in range(3):
        if _click_radio_by_text(frame, value):
            time.sleep(0.2)
            if _radio_selected_by_text(frame, value):
                ok = True
                break
    if ok:
        print(f"[已选] {field_name} = {value}", flush=True)
    else:
        print(f"[失败] 未能选择 {field_name} = {value}", flush=True)
    return ok


def _visible_validation_messages(frame) -> list[str]:
    try:
        messages = frame.evaluate(
            r"""
            () => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0;
                };
                const preferred = Array.from(document.querySelectorAll(
                    '.el-form-item__error,.ant-form-item-explain-error,.form-error,.error,[class*="Error"],[class*="error"],.el-message,.ant-message,.toast,[class*="message"],[class*="Message"]'
                ));
                const nodes = preferred.concat(Array.from(document.querySelectorAll('body *')));
                const seen = new Set();
                const out = [];
                for (const el of nodes) {
                    if (!visible(el)) continue;
                    const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                    if (!text || text.length > 80) continue;
                    if (!/(请选择|请输入|不能为空|必填|错误|不匹配)/.test(text)) continue;
                    if (!seen.has(text)) {
                        seen.add(text);
                        out.push(text);
                    }
                }
                return out;
            }
            """
        )
        return [str(msg) for msg in messages]
    except Exception:
        return []


def _visible_error_or_warning_messages(frame) -> list[str]:
    try:
        messages = frame.evaluate(
            r"""
            () => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0;
                };
                const nodes = Array.from(document.querySelectorAll(
                    '.el-message,.ant-message,.toast,.el-form-item__error,.ant-form-item-explain-error,[class*="error"],[class*="Error"],[class*="warning"],[class*="Warning"]'
                ));
                const seen = new Set();
                const out = [];
                for (const el of nodes) {
                    if (!visible(el)) continue;
                    const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                    if (!text || text.length > 160) continue;
                    if (!/(失败|错误|请输入|请选择|不能为空|必填|不匹配|超过|限额|异常|警告|不可用)/.test(text)) continue;
                    if (!seen.has(text)) {
                        seen.add(text);
                        out.push(text);
                    }
                }
                return out;
            }
            """
        )
        return [str(msg) for msg in messages]
    except Exception:
        return []


def _set_checkbox_by_text(frame, text: str, checked: bool) -> bool:
    try:
        result = frame.evaluate(
            r"""
            async ({ text, checked }) => {
                const norm = (s) => (s || '').replace(/\s+/g, '');
                const key = norm(text);
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0;
                };
                const checkedState = (root) => {
                    const candidate = root?.closest?.('label,[role=checkbox],.el-checkbox,.ant-checkbox-wrapper')
                        || root;
                    if (!candidate) return false;
                    if (candidate.getAttribute?.('aria-checked') === 'true') return true;
                    if (/\bis-checked\b|\bchecked\b/.test(candidate.className || '')) return true;
                    if (candidate.querySelector?.('.is-checked,.checked,[aria-checked="true"]')) return true;
                    const input = root?.querySelector?.('input[type=checkbox]');
                    if (input) return !!input.checked;
                    return false;
                };
                const ownText = (el) => Array.from(el.childNodes || [])
                    .filter((node) => node.nodeType === Node.TEXT_NODE)
                    .map((node) => node.nodeValue || '')
                    .join('');
                const wrapperFor = (el) => {
                    const direct = el.closest?.('label,[role=checkbox],.el-checkbox,.ant-checkbox-wrapper');
                    if (direct?.querySelector?.('input[type=checkbox],.el-checkbox__inner,.ant-checkbox-inner')) {
                        return direct;
                    }
                    const parentWrap = el.parentElement?.closest?.('label,[role=checkbox],.el-checkbox,.ant-checkbox-wrapper');
                    if (parentWrap?.querySelector?.('input[type=checkbox],.el-checkbox__inner,.ant-checkbox-inner')) {
                        return parentWrap;
                    }
                    return null;
                };
                const nodes = Array.from(document.querySelectorAll('label,span,div'))
                    .filter((el) => visible(el))
                    .map((el) => {
                        const own = norm(ownText(el));
                        const all = norm(el.innerText || el.textContent);
                        const wrapper = wrapperFor(el);
                        const rect = el.getBoundingClientRect();
                        return {
                            el,
                            wrapper,
                            exact: own === key || all === key,
                            contains: own.includes(key) || all.includes(key),
                            area: rect.width * rect.height,
                        };
                    })
                    .filter((item) => item.wrapper && (item.exact || item.contains))
                    .sort((a, b) => {
                        if (a.exact !== b.exact) return a.exact ? -1 : 1;
                        return a.area - b.area;
                    });
                for (const item of nodes) {
                    const label = item.wrapper;
                    const checkbox = label.querySelector?.('input[type=checkbox]');
                    const current = checkedState(label);
                    if (current !== checked) {
                        const inner = label.querySelector?.('.el-checkbox__inner,.ant-checkbox-inner');
                        const target = inner || checkbox || label;
                        target.click();
                        await new Promise((resolve) => setTimeout(resolve, 150));
                        return { status: 'changed', after: checkedState(label) };
                    }
                    return { status: 'already', after: current };
                }
                return { status: '', after: false };
            }
            """,
            {"text": text, "checked": checked},
        )
        status = result.get("status") if isinstance(result, dict) else result
        after = result.get("after") if isinstance(result, dict) else checked
        if status == "changed":
            print(f"[已处理] {text} = {'勾选' if checked else '取消勾选'}", flush=True)
            return after == checked
        if status == "already":
            print(f"[已确认] {text} = {'勾选' if checked else '未勾选'}", flush=True)
            return True
    except Exception as exc:
        print(f"[提示] 处理复选框失败 {text}: {type(exc).__name__}", flush=True)
    return False


def _select_dropdown_option(frame, option_text: str, timeout_ms: int = 5000) -> bool:
    """Select a visible autocomplete/dropdown option matching the bank name."""
    key = option_text
    for brand in _BANK_BRANDS:
        key = key.replace(brand, "")
    key = (
        key.replace("股份有限公司", "")
        .replace("有限责任公司", "")
        .replace("有限公司", "")
        .strip()
    )
    if len(key) < 6:
        key = option_text
    deadline = time.time() + timeout_ms / 1000
    last_error = None
    while time.time() < deadline:
        try:
            clicked = frame.evaluate(
                r"""
                ({ optionText, key }) => {
                    const norm = (s) => (s || '').replace(/\s+/g, '');
                    const bankNorm = (s) => norm(s)
                        .replace(/股份有限公司/g, '')
                        .replace(/有限责任公司/g, '')
                        .replace(/有限公司/g, '');
                    const brands = [
                        '中国工商银行',
                        '中国建设银行',
                        '中国农业银行',
                        '中国银行',
                        '招商银行',
                        '中国民生银行',
                        '中信银行',
                        '中国邮政储蓄银行',
                        '兴业银行',
                        '交通银行',
                        '上海浦东发展银行',
                        '浦发银行',
                        '中国光大银行',
                        '光大银行',
                        '华夏银行',
                        '平安银行',
                        '广发银行',
                        '北京银行',
                        '桂林国民村镇银行',
                    ];
                    const splitBank = (s) => {
                        const value = bankNorm(s);
                        for (const brand of brands) {
                            if (value.startsWith(brand)) {
                                return { brand, branch: value.slice(brand.length), value };
                            }
                        }
                        const pos = value.indexOf('银行');
                        if (pos >= 0) {
                            return { brand: value.slice(0, pos + 2), branch: value.slice(pos + 2), value };
                        }
                        return { brand: '', branch: value, value };
                    };
                    const optionNorm = norm(optionText);
                    const optionBankNorm = bankNorm(optionText);
                    const keyNorm = norm(key);
                    const keyBankNorm = bankNorm(key);
                    const optionParts = splitBank(optionText);
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0;
                    };
                    const isDropdownLike = (el) => {
                        if (el.closest('header,footer,nav,[class*="footer"],[class*="Footer"],[class*="copyright"],[class*="Copyright"]')) {
                            return false;
                        }
                        const tag = el.tagName.toLowerCase();
                        if (tag === 'li' || el.getAttribute('role') === 'option') return true;
                        return !!el.closest([
                            '[role="listbox"]',
                            '[role="option"]',
                            '.el-select-dropdown',
                            '.el-autocomplete-suggestion',
                            '.ant-select-dropdown',
                            '.ant-dropdown',
                            '.select-dropdown',
                            '.dropdown-menu',
                            '[class*="select-dropdown"]',
                            '[class*="autocomplete"]',
                            '[class*="suggest"]',
                            '[class*="dropdown"]',
                            '[class*="Dropdown"]',
                            '[class*="popover"]',
                            '[class*="Popper"]',
                            '[class*="option"]',
                            '[class*="Option"]',
                        ].join(','));
                    };
                    const nodes = Array.from(document.querySelectorAll('li,div,span,p,[role="option"]'))
                        .map((el) => {
                            if (!visible(el)) return false;
                            if (!isDropdownLike(el)) return false;
                            const raw = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                            const text = norm(raw);
                            const textBankNorm = bankNorm(raw);
                            if (!text || text.length > 80 || raw.includes('\n')) return false;
                            if (/版权所有|©|Copyright|服务热线|退出|首页|管理员/.test(raw)) return false;
                            const parts = splitBank(raw);
                            let score = 0;
                            if (text === optionNorm || textBankNorm === optionBankNorm) score += 100;
                            if (text.includes(optionNorm) || optionNorm.includes(text)) score += 80;
                            if (textBankNorm.includes(optionBankNorm) || optionBankNorm.includes(textBankNorm)) score += 70;
                            if (optionParts.brand && parts.brand === optionParts.brand) score += 35;
                            if (optionParts.branch && textBankNorm.includes(optionParts.branch)) score += 55;
                            if (parts.branch && optionBankNorm.includes(parts.branch)) score += 35;
                            if (keyNorm.length >= 6 && text.includes(keyNorm)) score += 40;
                            if (keyBankNorm.length >= 6 && textBankNorm.includes(keyBankNorm)) score += 40;
                            if (!score) return false;
                            return { el, raw, score };
                        })
                        .filter(Boolean)
                        .sort((a, b) => {
                            if (a.score !== b.score) return b.score - a.score;
                            const ar = a.el.getBoundingClientRect();
                            const br = b.el.getBoundingClientRect();
                            if (ar.top !== br.top) return ar.top - br.top;
                            return ar.left - br.left;
                        });
                    if (!nodes.length) return false;
                    const target = nodes[0].el;
                    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                    target.click();
                    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                    return nodes[0].raw;
                }
                """,
                {"optionText": option_text, "key": key},
            )
            if clicked:
                print(f"[已选] 下拉候选：{clicked}", flush=True)
                return True
        except Exception as exc:
            last_error = exc
        time.sleep(0.2)
    if last_error:
        print(f"[调试] 下拉候选选择失败：{last_error!r}", flush=True)
    print(f"[失败] 未能选择下拉候选：{option_text}", flush=True)
    return False


def _select_exact_dropdown_option(frame, option_text: str, timeout_ms: int = 5000) -> bool:
    """Select a visible dropdown/autocomplete option by exact or contained text."""
    deadline = time.time() + timeout_ms / 1000
    target_key = "".join((option_text or "").split())
    if not target_key:
        return False
    last_error = None
    while time.time() < deadline:
        handle = None
        try:
            handle = frame.evaluate_handle(
                r"""
                (targetText) => {
                    const norm = (s) => (s || '').replace(/\s+/g, '');
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0;
                    };
                    const key = norm(targetText);
                    const nodes = Array.from(document.querySelectorAll('li,div,span,p'))
                        .map((el) => {
                            if (!visible(el)) return false;
                            const raw = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                            const text = norm(raw);
                            if (!text || text.length > 120 || raw.includes('\n')) return false;
                            let score = 0;
                            if (text === key) score += 100;
                            if (text.includes(key) || key.includes(text)) score += 60;
                            if (!score) return false;
                            const box = el.getBoundingClientRect();
                            return { el, raw, score, area: box.width * box.height };
                        })
                        .filter(Boolean)
                        .sort((a, b) => {
                            if (a.score !== b.score) return b.score - a.score;
                            if (a.area !== b.area) return a.area - b.area;
                            const ar = a.el.getBoundingClientRect();
                            const br = b.el.getBoundingClientRect();
                            if (ar.top !== br.top) return ar.top - br.top;
                            return ar.left - br.left;
                        });
                    return nodes[0]?.el || null;
                }
                """,
                option_text,
            )
            el = handle.as_element()
            if el is not None:
                raw = str(
                    el.evaluate("(node) => (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim()")
                    or ""
                )
                el.click(timeout=1200, force=True)
                time.sleep(0.25)
                print(f"[已点] 下拉候选：{raw}", flush=True)
                return True
        except Exception as exc:
            last_error = exc
        finally:
            if handle is not None:
                try:
                    handle.dispose()
                except Exception:
                    pass
        try:
            clicked = frame.evaluate(
                r"""
                (targetText) => {
                    const norm = (s) => (s || '').replace(/\s+/g, '');
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0;
                    };
                    const key = norm(targetText);
                    const nodes = Array.from(document.querySelectorAll('li,div,span,p'))
                        .map((el) => {
                            if (!visible(el)) return false;
                            const raw = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                            const text = norm(raw);
                            if (!text || text.length > 120 || raw.includes('\n')) return false;
                            let score = 0;
                            if (text === key) score += 100;
                            if (text.includes(key) || key.includes(text)) score += 60;
                            if (!score) return false;
                            return { el, raw, score };
                        })
                        .filter(Boolean)
                        .sort((a, b) => {
                            if (a.score !== b.score) return b.score - a.score;
                            const ar = a.el.getBoundingClientRect();
                            const br = b.el.getBoundingClientRect();
                            if (ar.top !== br.top) return ar.top - br.top;
                            return ar.left - br.left;
                        });
                    if (!nodes.length) return false;
                    const target = nodes[0].el;
                    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                    target.click();
                    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                    return nodes[0].raw;
                }
                """,
                option_text,
            )
            if clicked:
                print(f"[已选] 下拉候选：{clicked}", flush=True)
                return True
        except Exception as exc:
            last_error = exc
        time.sleep(0.2)
    if last_error:
        print(f"[调试] 精确下拉候选选择失败：{last_error!r}", flush=True)
    return False


def _fill_autocomplete_required(frame, label: str, value: str) -> bool:
    h = _find_input_by_label(frame, label)
    if not h:
        print(f"[失败] 未找到 {label} 输入框", flush=True)
        return False
    queries = []
    if len(value) >= 4:
        queries.append(value[:4])
    queries.append(value)
    for query in dict.fromkeys(queries):
        try:
            h.click()
            try:
                h.press("Control+A")
                h.press("Backspace")
            except Exception:
                h.evaluate("(el) => { el.focus(); el.value = ''; }")
            h.type(query, delay=35)
            time.sleep(0.8)
            if _select_exact_dropdown_option(frame, value, timeout_ms=2500):
                _wait_form_idle(frame)
                if _verify_required_value(frame, label, value):
                    print(f"[已填] {label}", flush=True)
                    return True
        except Exception as exc:
            print(f"[提示] {label} 自动完成输入失败：{type(exc).__name__}", flush=True)

    if _fill_required(frame, label, value):
        _wait_form_idle(frame)
        return _verify_required_value(frame, label, value)
    return False


def _fill_transfer_form(page, timeout_ms: int = 60000) -> bool:
    """填写转账汇款页面的所有红星必填项。收款方数据必须由 BOC_PAYEE_* 环境变量提供。"""
    _debug_checkpoint("开始填写转账汇款表单", page)

    # fail-closed：收款方关键字段必须由环境变量显式提供；缺失时不使用任何
    # 内置/历史真实默认值，直接跳过填表（调用方据返回 False 不会提交）。
    _missing_payee_env = [
        name
        for name, value in (
            ("BOC_PAYEE_ACCOUNT", TEST_PAYEE_ACCOUNT),
            ("BOC_PAYEE_NAME", TEST_PAYEE_NAME),
            ("BOC_PAYEE_BANK", TEST_PAYEE_BANK_NAME),
            ("BOC_PAYMENT_AMOUNT", TEST_AMOUNT),
        )
        if not value
    ]
    if _missing_payee_env:
        print(
            "[安全·fail-closed] 缺少收款方环境变量 "
            + ", ".join(_missing_payee_env)
            + "；不使用任何内置默认值填表，已跳过转账表单填写（未提交）。"
            "真实填表请显式设置 BOC_PAYEE_ACCOUNT/BOC_PAYEE_NAME/BOC_PAYEE_BANK/BOC_PAYMENT_AMOUNT。",
            flush=True,
        )
        _debug_checkpoint("缺少收款方环境变量_fail_closed_跳过填表", page)
        return False

    deadline = time.time() + timeout_ms / 1000
    target_frame = None
    while time.time() < deadline:
        for frame in page.frames:
            try:
                if frame.get_by_text("收款人账号", exact=False).first.is_visible(timeout=300):
                    target_frame = frame
                    break
            except Exception:
                continue
        if target_frame is not None:
            break
        time.sleep(0.5)

    if target_frame is None:
        print("[警告] 未找到 转账汇款 表单", flush=True)
        _debug_checkpoint("转账汇款表单未找到", page)
        return False

    _debug_checkpoint("转账汇款表单已找到", page)
    time.sleep(1.0)

    results = []
    payee_bank_type = _target_payee_bank_type()
    _set_checkbox_by_text(target_frame, "保存为常用收款人", False)
    _debug_checkpoint("已处理常用收款人保存勾选", page)
    time.sleep(0.2)

    account_ok = _fill_required_stable(
        target_frame,
        "收款人账号",
        TEST_PAYEE_ACCOUNT,
    )
    results.append(account_ok)
    _debug_checkpoint("已尝试填写收款人账号", page)
    time.sleep(0.4)

    name_ok = False
    if _select_exact_dropdown_option(target_frame, TEST_PAYEE_NAME, timeout_ms=2500):
        _wait_form_idle(target_frame)
        name_ok = _verify_required_value(target_frame, "收款人户名", TEST_PAYEE_NAME)
    if not name_ok:
        name_ok = _verify_required_value(target_frame, "收款人户名", TEST_PAYEE_NAME)
    if name_ok:
        print("[已确认] 收款人户名由账号联动自动带出", flush=True)
    else:
        name_ok = _fill_required_stable(target_frame, "收款人户名", TEST_PAYEE_NAME)
    if not name_ok:
        print("[重试] 收款人户名按自动完成候选选择", flush=True)
        name_ok = _fill_autocomplete_required(target_frame, "收款人户名", TEST_PAYEE_NAME)
    results.append(name_ok)
    _debug_checkpoint("已尝试填写收款人户名", page)
    time.sleep(0.4)

    # 选择常用收款人候选后，页面可能自动重新勾选保存项；在联动完成后再取消一次。
    _set_checkbox_by_text(target_frame, "保存为常用收款人", False)
    _debug_checkpoint("已复核常用收款人保存勾选", page)
    time.sleep(0.2)

    results.append(_click_radio_required(target_frame, "收款行类型", payee_bank_type))
    _debug_checkpoint(f"已尝试选择收款行类型_{payee_bank_type}", page)
    time.sleep(0.4)

    bank_name_ok = False
    if payee_bank_type == "中行":
        print("[跳过] 行内收款由页面自动带出开户行名称，不填写具体支行", flush=True)
        _debug_checkpoint("行内收款开户行自动带出前", page)
        bank_name_ok = _verify_required_value(target_frame, "收款人开户行名称", "中国银行")
        _debug_checkpoint("行内收款开户行已确认", page)
    else:
        _debug_checkpoint("准备填写收款人开户行名称", page)
        bank_text_ok = _fill_required(target_frame, "收款人开户行名称", TEST_PAYEE_BANK_NAME)
        if bank_text_ok:
            _debug_checkpoint("收款人开户行名称已输入_等待候选", page)
            bank_name_ok = _select_dropdown_option(target_frame, TEST_PAYEE_BANK_NAME, timeout_ms=5000)
            time.sleep(0.8)
            _debug_checkpoint("收款人开户行候选已处理", page)
            bank_name_ok = _verify_required_value(target_frame, "收款人开户行名称", TEST_PAYEE_BANK_NAME)
    results.append(bank_name_ok)
    _debug_checkpoint("已尝试填写收款人开户行名称", page)
    time.sleep(0.6)

    if payee_bank_type == "中行":
        print("[跳过] 行内收款不填写/校验开户行行号", flush=True)
    else:
        results.append(_verify_required_value(target_frame, "收款人开户行行号", TEST_PAYEE_BANK_CODE))
    _debug_checkpoint("已尝试填写收款人开户行行号", page)
    time.sleep(0.4)

    if not _verify_required_value(target_frame, "收款人账号", TEST_PAYEE_ACCOUNT):
        print("[重试] 收款人账号在开户行联动后被清空，重新填写", flush=True)
        account_ok = _fill_required_stable(
            target_frame,
            "收款人账号",
            TEST_PAYEE_ACCOUNT,
        )
    results.append(account_ok)
    _debug_checkpoint("已复核收款人账号", page)
    time.sleep(0.3)

    bank_type_ok = _radio_selected_by_text(target_frame, payee_bank_type)
    if bank_type_ok:
        print(f"[已确认] 收款行类型 = {payee_bank_type}", flush=True)
    else:
        print(f"[失败] 收款行类型未保持为 {payee_bank_type}", flush=True)
    results.append(bank_type_ok)
    _debug_checkpoint("已复核收款行类型", page)
    time.sleep(0.4)

    results.append(_click_radio_required(target_frame, "收款人类型", TEST_PAYEE_TYPE))
    _debug_checkpoint("已尝试选择收款人类型", page)
    time.sleep(0.4)

    results.append(_fill_required(target_frame, "金额", TEST_AMOUNT))
    _debug_checkpoint("已尝试填写金额", page)
    time.sleep(0.3)

    final_account_ok = _verify_required_value(
        target_frame,
        "收款人账号",
        TEST_PAYEE_ACCOUNT,
    )
    if not final_account_ok:
        print("[重试] 收款人账号最终回读不一致，重新填写", flush=True)
        final_account_ok = _fill_required_stable(
            target_frame,
            "收款人账号",
            TEST_PAYEE_ACCOUNT,
        )
    results.append(final_account_ok)
    results.append(_verify_required_value(target_frame, "收款人户名", TEST_PAYEE_NAME))
    results.append(_radio_selected_by_text(target_frame, payee_bank_type))
    results.append(_verify_required_value(target_frame, "收款人开户行名称", TEST_PAYEE_BANK_NAME))
    if payee_bank_type != "中行":
        results.append(_verify_required_value(target_frame, "收款人开户行行号", TEST_PAYEE_BANK_CODE))

    validation_messages = _visible_validation_messages(target_frame)
    if validation_messages:
        print(
            "[失败] 表单仍有可见校验提示："
            + "；".join(validation_messages[:6]),
            flush=True,
        )
        results.append(False)
    else:
        print("[已确认] 未发现可见必填校验提示", flush=True)

    if not all(results):
        print("[失败] 转账表单存在未填写/未选择项（未提交）", flush=True)
        _debug_checkpoint("转账汇款表单填写不完整", page)
        return False

    print("[完成] 转账表单必填项已填写（未提交）", flush=True)
    _debug_checkpoint("转账汇款表单填写完成_未提交", page)
    return True


def _submit_transfer_order(page, timeout_ms: int = 30000) -> bool:
    """Scroll to the bottom and click the first-step 制单提交 button only."""
    _debug_checkpoint("准备点击制单提交按钮", page)
    deadline = time.time() + timeout_ms / 1000
    last_state = ""
    while time.time() < deadline:
        for frame in page.frames:
            try:
                result = frame.evaluate(
                    r"""
                    () => {
                        const norm = (s) => (s || '').replace(/\s+/g, '');
                        const visible = (el) => {
                            const style = window.getComputedStyle(el);
                            const box = el.getBoundingClientRect();
                            return style.visibility !== 'hidden'
                                && style.display !== 'none'
                                && box.width > 0
                                && box.height > 0;
                        };
                        const disabled = (el) => {
                            return el.disabled
                                || el.getAttribute('disabled') !== null
                                || el.getAttribute('aria-disabled') === 'true'
                                || /\bis-disabled\b|\bdisabled\b/.test(el.className || '');
                        };
                        const scrollables = [
                            document.scrollingElement,
                            document.documentElement,
                            document.body,
                            ...Array.from(document.querySelectorAll('main,.main,.content,.container,[class*="content"],[class*="scroll"],[class*="body"]')),
                        ].filter(Boolean);
                        for (const el of scrollables) {
                            try { el.scrollTop = el.scrollHeight; } catch (_) {}
                        }
                        window.scrollTo(0, document.body.scrollHeight || document.documentElement.scrollHeight);
                        const candidates = Array.from(document.querySelectorAll(
                            'button,[role="button"],input[type="button"],input[type="submit"],.el-button,.ant-btn'
                        ))
                            .map((el) => {
                                const text = norm(el.value || el.innerText || el.textContent || el.getAttribute('aria-label') || '');
                                return { el, text, box: el.getBoundingClientRect() };
                            })
                            .filter(({ el, text }) => visible(el) && text === '提交')
                            .sort((a, b) => {
                                if (a.box.top !== b.box.top) return b.box.top - a.box.top;
                                return b.box.left - a.box.left;
                            });
                        if (!candidates.length) return { status: 'not-found' };
                        const target = candidates[0].el;
                        target.scrollIntoView({ block: 'center', inline: 'center' });
                        if (disabled(target)) return { status: 'disabled' };
                        return new Promise((resolve) => {
                            setTimeout(() => {
                                try {
                                    target.click();
                                    resolve({ status: 'clicked' });
                                } catch (err) {
                                    resolve({ status: 'error', message: String(err && err.message || err) });
                                }
                            }, 100);
                        });
                    }
                    """
                )
                status = result.get("status") if isinstance(result, dict) else ""
                if status == "clicked":
                    print("[已点击] 制单提交按钮", flush=True)
                    time.sleep(4.0)
                    _debug_checkpoint("制单提交后页面状态", page)
                    for frame_after in page.frames:
                        messages = _visible_error_or_warning_messages(frame_after)
                        if messages:
                            print("[提示] 制单提交后页面提示：" + "；".join(messages[:5]), flush=True)
                            break
                    return True
                if status and status != last_state:
                    print(f"[调试] 制单提交按钮状态：{status}", flush=True)
                    last_state = status
            except Exception as exc:
                last_state = type(exc).__name__
        time.sleep(0.5)
    print("[失败] 超时未能点击制单提交按钮", flush=True)
    return False
