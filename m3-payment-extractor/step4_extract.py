"""获取数据 - 步骤4: 按顺序打开 M3/OA 付款报表 → 扫描最新付款行 → 逐条打开详情抽取字段

抽出的字段（用于后续按付款银行分类输出 JSON）：
  申请人 / 申请部门 / 申请日期 / 申请金额
  付款方式
  付款银行 / 付款开户银行 / 制单银行 等付款银行候选字段
  合同名称 / 合同编号 / 合同名称链接（蓝色采购合同链接）
  收款单位名称 / 付款单位名称
  开户银行 / 银行账户
  合同金额 / 累计申请金额
  申请说明 / 类别 / 单据编号
"""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import re
import shutil
import sys
import time
from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout, sync_playwright

from step1_open import USER_DATA_DIR, OA_URL, chromium_launch_args, mark_browser_profile_clean, try_login
from step3_finance_contract import REPORT_URL

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

FLOW_PATTERN = r"YL[A-Z0-9-]*-\d{4}-[A-Z0-9-]+-[\u4e00-\u9fa5A-Z0-9]+-\d{4}-\d+"
FALLBACK_FLOW_PATTERN = r"YL[A-Z0-9-]*-\d{4}-[A-Z0-9-]+"
BILL_PATTERN = rf"(?:{FLOW_PATTERN}|{FALLBACK_FLOW_PATTERN})"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.join(SCRIPT_DIR, "runtime")
CURRENT_DIR = os.path.join(RUNTIME_DIR, "current")
BANK_FORMS_DIR = os.path.join(RUNTIME_DIR, "bank_forms")
BANK_BATCH_DIR = os.path.join(RUNTIME_DIR, "bank_batches")
SKIPPED_DIR = os.path.join(RUNTIME_DIR, "skipped")
SCREENSHOT_ROOT = os.path.join(RUNTIME_DIR, "screenshots")
NAVIGATION_TIMEOUT_MS = int(os.getenv("OA_NAVIGATION_TIMEOUT_MS", "90000"))
NAVIGATION_RETRIES = max(1, int(os.getenv("OA_NAVIGATION_RETRIES", "4")))
BROWSER_RESTARTS = max(0, int(os.getenv("M3_BROWSER_RESTARTS", "2")))
RETRY_DELAY_SECONDS = max(1.0, float(os.getenv("OA_RETRY_DELAY_SECONDS", "8")))
# 导航连续失败、且顶层页仍停在空白(about:blank)时，从第几次尝试起不再叠加完整退避，
# 而是直接抛 M3WhiteScreenError，交给 run() 关闭 context 并按 M3_BROWSER_RESTARTS 快速重启。
# 旧逻辑会在 about:blank 上叠加 8/20/45 秒退避，叠加外层超时正好把抽取拖成 exit=124 白屏。
try:
    NAV_BLANK_RESTART_AFTER_ATTEMPTS = int(os.getenv("M3_NAV_BLANK_RESTART_AFTER_ATTEMPTS", "2"))
except ValueError:
    NAV_BLANK_RESTART_AFTER_ATTEMPTS = 2
NAV_BLANK_RESTART_AFTER_ATTEMPTS = max(1, NAV_BLANK_RESTART_AFTER_ATTEMPTS)
# 有些 OA/VPN 断连场景下，Playwright 的 page.url 不一定还能稳定反映可见窗口
# 已停在 about:blank；只要出现连接类导航失败，就直接重启浏览器上下文，比在白屏页
# 继续退避等待更稳定。需要排查时可用 M3_NAV_TRANSIENT_RESTART_AFTER_ATTEMPTS 调大。
try:
    NAV_TRANSIENT_RESTART_AFTER_ATTEMPTS = int(
        os.getenv("M3_NAV_TRANSIENT_RESTART_AFTER_ATTEMPTS", "1")
    )
except ValueError:
    NAV_TRANSIENT_RESTART_AFTER_ATTEMPTS = 1
NAV_TRANSIENT_RESTART_AFTER_ATTEMPTS = max(1, NAV_TRANSIENT_RESTART_AFTER_ATTEMPTS)
# 顶层 about:blank 白屏判定延迟：列表/详情等待阶段先给页面一点加载时间，
# 超过该毫秒数后仍停在空白顶层页才开始累计连续命中，避免误伤页面切换的瞬间。
try:
    TOP_BLANK_GRACE_MS = int(os.getenv("M3_TOP_BLANK_GRACE_MS", "2500"))
except ValueError:
    TOP_BLANK_GRACE_MS = 2500
TOP_BLANK_GRACE_MS = max(0, TOP_BLANK_GRACE_MS)
DIRECT_REPORT_FIRST = os.getenv("M3_DIRECT_REPORT_FIRST", "1").strip().lower() not in {"0", "false", "no", "off"}
ENABLE_SCREENSHOTS = os.getenv("M3_ENABLE_SCREENSHOTS", "1").strip().lower() not in {"0", "false", "no", "off"}
REQUIRE_SCREENSHOTS = os.getenv("M3_REQUIRE_SCREENSHOTS", "1").strip().lower() not in {"0", "false", "no", "off"}
SCREENSHOT_KEEP_RUNS = max(1, int(os.getenv("M3_SCREENSHOT_KEEP_RUNS", "500")))
SCREENSHOT_KEEP_DAYS = max(1, int(os.getenv("M3_SCREENSHOT_KEEP_DAYS", "7")))
try:
    SCAN_ROW_LIMIT = int(os.getenv("M3_SCAN_ROW_LIMIT", "30"))
except ValueError:
    SCAN_ROW_LIMIT = 30
SCAN_ROW_LIMIT = max(1, min(SCAN_ROW_LIMIT, 200))
SCAN_FORCE_DETAILS = os.getenv("M3_SCAN_FORCE_DETAILS", "0").strip().lower() in {"1", "true", "yes", "on"}
try:
    DETAIL_REFRESH_SECONDS = int(os.getenv("M3_DETAIL_REFRESH_SECONDS", "900"))
except ValueError:
    DETAIL_REFRESH_SECONDS = 900
DETAIL_REFRESH_SECONDS = max(0, DETAIL_REFRESH_SECONDS)
PERIODIC_RECHECK_REASON = f"详情定期复查（{DETAIL_REFRESH_SECONDS}秒）"
try:
    DETAIL_RECHECK_MAX_PER_ROUND = int(os.getenv("M3_DETAIL_RECHECK_MAX_PER_ROUND", "5"))
except ValueError:
    DETAIL_RECHECK_MAX_PER_ROUND = 5
CONTINUE_ON_ROW_ERROR = os.getenv("M3_CONTINUE_ON_ROW_ERROR", "0").strip().lower() in {"1", "true", "yes", "on"}
SKIP_UNROUTABLE_RECORDS = os.getenv("M3_SKIP_UNROUTABLE_RECORDS", "0").strip().lower() in {"1", "true", "yes", "on"}
CLICK_CONTRACT_LINK = os.getenv("M3_CLICK_CONTRACT_LINK", "1").strip().lower() not in {"0", "false", "no", "off"}
try:
    CONTRACT_LINK_OPEN_TIMEOUT_MS = int(os.getenv("M3_CONTRACT_LINK_OPEN_TIMEOUT_MS", "6000"))
except ValueError:
    CONTRACT_LINK_OPEN_TIMEOUT_MS = 6000
CONTRACT_LINK_OPEN_TIMEOUT_MS = max(1000, min(CONTRACT_LINK_OPEN_TIMEOUT_MS, 30000))
try:
    CONTRACT_LINK_READY_WAIT_MS = int(os.getenv("M3_CONTRACT_LINK_READY_WAIT_MS", "5000"))
except ValueError:
    CONTRACT_LINK_READY_WAIT_MS = 5000
CONTRACT_LINK_READY_WAIT_MS = max(0, min(CONTRACT_LINK_READY_WAIT_MS, 30000))
DEFAULT_CONTRACT_REPORT_NAME = "合同付款（凭证）"
DEFAULT_MONITOR_REPORT_NAMES = [
    "合同付款（云链）",
    "合同付款（凭证）",
    "日常报销",
    "对公请款（云链）",
    "对公请款（社保公积金）",
]


def parse_report_names(raw: str) -> list[str]:
    names = []
    for part in re.split(r"[,;，；\n]+", str(raw or "")):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    return names


CONTRACT_REPORT_NAME = os.getenv("M3_CONTRACT_REPORT_NAME", DEFAULT_CONTRACT_REPORT_NAME).strip() or DEFAULT_CONTRACT_REPORT_NAME
REPORT_NAMES = parse_report_names(os.getenv("M3_REPORT_NAMES", "")) or [CONTRACT_REPORT_NAME]
EXTRACT_STATE_PATH = os.path.join(RUNTIME_DIR, "m3_extract_state.json")
TRANSIENT_NAVIGATION_ERROR_MARKERS = (
    "ERR_CONNECTION_RESET",
    "ERR_TIMED_OUT",
    "ERR_NETWORK_CHANGED",
    "ERR_CONNECTION_CLOSED",
    "ERR_EMPTY_RESPONSE",
    "ERR_NAME_NOT_RESOLVED",
    "Timeout",
)


class M3PrecheckError(RuntimeError):
    """Raised for deterministic OA-to-JSON validation failures."""


class M3WhiteScreenError(RuntimeError):
    """Raised when OA opens a report iframe but the report body stays blank."""


def parse_retry_backoff_seconds() -> list[float]:
    raw = os.getenv("OA_NAVIGATION_BACKOFF_SECONDS", "").strip()
    if not raw:
        return [RETRY_DELAY_SECONDS, 20.0, 45.0]
    values: list[float] = []
    for part in re.split(r"[,;\s]+", raw):
        if not part:
            continue
        try:
            seconds = float(part)
        except ValueError:
            continue
        if seconds > 0:
            values.append(seconds)
    return values or [RETRY_DELAY_SECONDS]


NAVIGATION_BACKOFF_SECONDS = parse_retry_backoff_seconds()


def report_name_variants(report_name: str) -> list[str]:
    """Return exact report titles/aliases to try, preserving the requested title first."""
    target = (report_name or DEFAULT_CONTRACT_REPORT_NAME).strip() or DEFAULT_CONTRACT_REPORT_NAME
    aliases = {
        "合同付款凭证": "合同付款（凭证）",
        "合同付款云链": "合同付款（云链）",
        "日常报销云链": "日常报销（云链）",
        "对公请款云链": "对公请款（云链）",
        "对公请款社保公积金": "对公请款（社保公积金）",
    }
    canonical = aliases.get(target, target)
    variants = [canonical]
    if canonical == "合同付款（凭证）":
        variants.append("合同付款凭证")
    elif canonical == "合同付款（云链）":
        variants.append("合同付款云链")
    elif canonical == "日常报销":
        variants.append("日常报销（云链）")
    elif canonical == "日常报销（云链）":
        variants.append("日常报销")
        variants.append("日常报销云链")
    elif canonical == "对公请款（云链）":
        variants.append("对公请款云链")
    elif canonical == "对公请款（社保公积金）":
        variants.append("对公请款社保公积金")
        variants.append("对公请款（社保公积金或工资）")
        variants.append("对公请款（云链）（社保公积金或工资）")
    elif canonical == "对公请款（云链）（社保公积金或工资）":
        variants.append("对公请款（社保公积金）")
        variants.append("对公请款社保公积金")
    return list(dict.fromkeys(variants))


def contract_report_variants(report_name: str) -> list[str]:
    """Backward-compatible alias for older helper callers."""
    return report_name_variants(report_name)

PAYMENT_BANK_LABELS = [
    "付款银行", "付款银行名称", "付款行", "付款开户银行", "付款开户行",
    "付方银行", "付方开户银行", "付方开户行",
    "付款方银行", "付款方开户银行", "付款方开户行",
    "付方账户开户银行", "付方账户开户行", "付方账号开户银行", "付方账号开户行",
    "付款账户开户银行", "付款账户开户行", "付款账号开户银行", "付款账号开户行",
    "付款单位开户银行", "付款单位开户行",
    "出款银行", "出款行", "转出银行", "转出开户银行", "转出开户行",
    "制单银行", "经办银行", "承办银行", "承付银行", "支付银行", "网银银行", "付款网银",
]

WANT_LABELS = [
    "申请人", "申请部门", "申请日期", "申请金额",
    "报销金额", "请款金额", "付款金额", "本次付款金额", "金额",
    "付款方式",
    *PAYMENT_BANK_LABELS,
    "单据编号", "单号", "流水号", "报销单号", "请款单号", "付款编号",
    "合同名称", "合同编号",
    "收款单位名称", "收款单位", "收款户名", "收款人名称", "收款人", "收方户名",
    "付款单位名称", "付款单位",
    "开户银行", "开户行", "收款开户行", "收款方开户行", "收款方开户银行",
    "银行账户", "银行账号", "收款账号", "收款账户", "收款方账号", "收方账号",
    # OA 极少展示，但若有则用于推导招行付款方账号尾号（同一 U-BANK 多账号场景）。
    # 仅参与尾号推导，不写回完整账号；下游 bank_route.derive_cmb_payer_account_suffix
    # 会按 `,` / `，` 切分并只取账号段末 3 位。
    "账户性质", "付款账户性质", "付方账户性质",
    "付款账号", "付款方账号", "付方账号",
    "付款账户", "付款账户账号", "付方账户", "付方账户账号",
    "付款人账号", "付款人账户",
    "合同金额", "累计申请金额",
    "申请说明", "类别",
]

CANONICAL_FIELD_ALIASES = {
    "申请金额": ["报销金额", "请款金额", "付款金额", "本次付款金额", "金额"],
    "收款单位名称": ["收款单位", "收款户名", "收款人名称", "收款人", "收方户名"],
    "付款单位名称": ["付款单位"],
    "开户银行": ["开户行", "收款开户行", "收款方开户行", "收款方开户银行"],
    "银行账户": ["银行账号", "收款账号", "收款账户", "收款方账号", "收方账号"],
}

M3_BANK_FORM_METADATA_KEYS = [
    "M3报表名称", "M3报表入口", "M3报表序号",
    "M3列表行键", "疑似附件编号",
    "申请人", "申请部门", "申请日期", "申请金额",
    "付款方式",
    "合同名称", "合同名称链接文本", "合同名称链接", "合同名称链接原始href", "合同名称链接onclick",
    "采购合同名称", "采购合同链接",
    "采购合同打开状态", "采购合同打开方式", "采购合同打开URL", "采购合同打开标题", "采购合同打开截图",
    "采购合同申请说明", "采购合同支付单号",
    "M3跳过制单", "M3跳过原因",
    "收款单位名称", "银行账户",
    "账户性质", "付款账户性质", "付方账户性质",
    "付款账号", "付款方账号", "付方账号",
    "付款账户", "付款账户账号", "付方账户", "付方账户账号",
    "付款人账号", "付款人账户",
    # 招行双账号特殊场景：登录窗口账号选择、制单页付款方账号选择是两条独立链路。
    # 这些字段只由 M3-origin 本地账户规则或显式 M3 字段带出；下游 bank_route
    # 仍会做强制开关/fail-closed 校验。
    "招行登录名", "CMB_LOGIN_ACCOUNT_NAME", "CMB登录名",
    "CMB_REQUIRE_LOGIN_ACCOUNT_SELECTION", "招行强制选择登录名",
    "招行付款方账号尾号", "CMB_PAYER_ACCOUNT_SUFFIX", "CMB付款方账号尾号",
    "CMB_DISABLE_PAYER_ACCOUNT_SELECTION", "招行禁用付款方账号选择",
    "合同金额", "累计申请金额",
    "申请说明", "类别",
]


# 下游银行脚本通常需要把「开户银行」拆成「总行名 + 完整支行名」。
# 注意顺序：长前缀放前面，短前缀（如「邮储银行」）放后面，
# 否则「中国邮政储蓄银行xxx」会被「邮储银行」先命中。
BANK_HEADS = [
    "中国工商银行", "中国建设银行", "中国农业银行",
    "中国民生银行", "中国邮政储蓄银行", "中国银行",
    "招商银行", "交通银行", "中信银行", "浦发银行",
    "兴业银行", "光大银行", "华夏银行", "平安银行",
    "广发银行", "邮储银行", "宁波银行", "北京银行",
    "上海银行", "浙商银行", "渤海银行",
]

BANK_ALIASES = {
    "招商银行": ("招商银行", "招行", "CMB"),
    "兴业银行": ("兴业银行", "兴业", "CIB"),
    "中国农业银行": ("中国农业银行", "农业银行", "农行", "ABC"),
    "中国银行": ("中国银行", "中行", "BOC"),
}

# OA/M3 详情页通常不展示付款银行字段。这里只用「已人工核实、可解释」的
# 付款单位关键词做保守兜底——映射的是「付款单位 → 实际付款银行」这一业务事实，
# 与收款开户行无关；绝不用收款开户行/收款银行/支行名推断付款银行，未命中仍 fail-closed。
#
# 仅对 M3 监控/抽取链路（带 M3 抽取来源字段的记录）生效；
# 通过 `_is_m3_origin_data` 在 `resolve_payment_bank` 里 gate 住，
# 手工 bank_form.json / 本地测试 JSON / 银行子项目调用时不会触发。
PAYER_BANK_FALLBACKS = {
    # ───────── M3 监控-公司名硬编码（用户人工确认） ─────────
    # 完整公司名（含“有限公司/食品/电子商务/科技”等后缀）；
    # 与下方关键词条目并存：完整名优先命中，保留关键词以兼容 OA 字段里的
    # 简写。绝不写入真实账号/支行/金额。
    "来参缘食品（北京）有限公司": "兴业银行",
    "浙江云炫农科技有限公司": "招商银行",
    "北京得鲜电子商务有限公司": "中国农业银行",
    "蒙特（北京）食品有限公司": "中国农业银行",
    "蒙选（北京）食品有限公司": "中国农业银行",
    "北京巡鲜电子商务有限公司": "中国农业银行",
    "河南迅动智能仓储服务有限公司": "中国银行",
    "河南讯动智能仓储服务有限公司": "中国银行",
    # ───────── 历史已维护的付款单位关键词（保留） ─────────
    # 云链 1-9 号付款单位关键词：1 跳动、2 跃动、3 悠动、4 智动、5 逸动、
    # 6 灵动、7 讯动、8 慧动、9 炫动，均经用户确认走招商银行。
    "云链跳动": "招商银行",
    "云链跃动": "招商银行",
    "云链悠动": "招商银行",
    "云链智动": "招商银行",
    "云链逸动": "招商银行",
    "云链灵动": "招商银行",
    "云链讯动": "招商银行",
    "云链慧动": "招商银行",
    "云链炫动": "招商银行",
    "蒙鲜": "招商银行",
    "云炫农": "招商银行",
    # 来参缘：经人工确认同时存在兴业/招行账户，单凭付款单位无法区分（OA 详情页无付方账户）。
    # 完整「兴业 vs 招行」区分规则待补；在规则明确前一律按兴业银行路由。
    "来参缘": "兴业银行",
    # 以下经人工核实走中国农业银行（与上方招行口径无关，不用收款开户行推断）。
    "巡鲜": "中国农业银行",
    "得鲜": "中国农业银行",
    # 蒙特（北京）食品有限公司：付款单位与「蒙鲜」是不同公司，经人工核实走中国农业银行。
    "蒙特": "中国农业银行",
    # 河南迅动/讯动：用户确认使用中国银行 UKey（当前 BOC USBHub 10 口）。
    # 只匹配带「河南」前缀的公司，避免误伤「云链讯动」等招行主体。
    "河南迅动": "中国银行",
    "河南讯动": "中国银行",
}

# M3 监控链路的付款单位主路由。它比本地「公司+付款账号」账户表优先级更高：
# 用户确认 M3 自动制单时这些付款单位固定使用主业务账户，不因同一公司在本机
# 账户表里还有招行一般户就改走招行。
M3_PAYER_COMPANY_FIXED_BANKS = (
    ("来参缘食品（北京）有限公司", "兴业银行"),
    ("来参缘", "兴业银行"),
    ("北京得鲜电子商务有限公司", "中国农业银行"),
    ("得鲜", "中国农业银行"),
    ("蒙特（北京）食品有限公司", "中国农业银行"),
    ("蒙特", "中国农业银行"),
    ("河南迅动智能仓储服务有限公司", "中国银行"),
    ("河南讯动智能仓储服务有限公司", "中国银行"),
    ("河南迅动", "中国银行"),
    ("河南讯动", "中国银行"),
)


# 触发付款单位→付款银行兜底前用于判定「数据来源是否为 M3/OA 抽取详情页」的字段。
# 只要 data 里任一字段非空就视为 M3-origin；用于把硬编码兜底关在 M3 监控链路里，
# 避免手工 bank_form.json / 本地测试 JSON / 银行子项目调用时被公司名命中绕过校验。
_M3_ORIGIN_MARKER_FIELDS = (
    "M3报表名称",
    "M3报表入口",
    "M3报表序号",
    "M3列表行键",
    "M3抓取详情截图",
    "M3合同付款列表截图",
)

M3_COMPANY_BANK_ACCOUNT_RULES_PATH = os.getenv(
    "M3_COMPANY_BANK_ACCOUNT_RULES_PATH",
    os.path.join(RUNTIME_DIR, "config", "m3_company_bank_accounts.local.json"),
)
_M3_PAYMENT_ACCOUNT_KEYS = (
    "付款账号", "付款方账号", "付方账号",
    "付款账户", "付款账户账号",
    "付方账户", "付方账户账号",
    "付款人账号", "付款人账户",
)
_M3_PAYMENT_ACCOUNT_TYPE_KEYS = ("账户性质", "付款账户性质", "付方账户性质")
_COMPANY_ACCOUNT_RULE_METADATA_KEYS = (
    "招行登录名", "CMB_LOGIN_ACCOUNT_NAME", "CMB登录名",
    "CMB_REQUIRE_LOGIN_ACCOUNT_SELECTION", "招行强制选择登录名",
    "招行付款方账号尾号", "CMB_PAYER_ACCOUNT_SUFFIX", "CMB付款方账号尾号",
    "CMB_DISABLE_PAYER_ACCOUNT_SELECTION", "招行禁用付款方账号选择",
)
_COMPANY_BANK_ACCOUNT_RULES_CACHE: list[dict] | None = None


def _is_m3_origin_data(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    for key in _M3_ORIGIN_MARKER_FIELDS:
        if str(data.get(key) or "").strip():
            return True
    return False


def resolve_m3_fixed_company_bank(data: dict, payer_name: str) -> tuple[str, str, str]:
    """Return fixed M3 monitor bank route for confirmed payer companies."""
    if not _is_m3_origin_data(data):
        return "", "", ""
    payer_norm = normalize_text(payer_name)
    if not payer_norm:
        return "", "", ""
    for keyword, bank_name in M3_PAYER_COMPANY_FIXED_BANKS:
        if normalize_text(keyword) in payer_norm:
            return bank_name, payer_name, "M3付款单位固定银行"
    return "", "", ""


def account_digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _truthy_value(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "是", "真"}


def load_company_bank_account_rules() -> list[dict]:
    """Load local, gitignored payer-company account routing rules."""
    global _COMPANY_BANK_ACCOUNT_RULES_CACHE
    if _COMPANY_BANK_ACCOUNT_RULES_CACHE is not None:
        return _COMPANY_BANK_ACCOUNT_RULES_CACHE

    path = M3_COMPANY_BANK_ACCOUNT_RULES_PATH
    if not os.path.exists(path):
        _COMPANY_BANK_ACCOUNT_RULES_CACHE = []
        return _COMPANY_BANK_ACCOUNT_RULES_CACHE

    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:  # noqa: BLE001 - fail-closed with a readable message
        raise M3PrecheckError(f"M3付款单位账户本地规则读取失败: {path}: {type(exc).__name__}: {exc}") from exc

    if not isinstance(payload, list):
        raise M3PrecheckError(f"M3付款单位账户本地规则必须是数组: {path}")

    rules: list[dict] = []
    for index, item in enumerate(payload, 1):
        if not isinstance(item, dict):
            raise M3PrecheckError(f"M3付款单位账户本地规则第 {index} 条不是对象")
        company = str(item.get("company") or item.get("公司") or "").strip()
        bank = normalize_payment_bank(str(item.get("payment_bank") or item.get("付款银行") or item.get("bank") or ""))
        if not company:
            raise M3PrecheckError(f"M3付款单位账户本地规则第 {index} 条缺少 company")
        if not bank or bank not in SUPPORTED_PAYMENT_BANKS:
            raise M3PrecheckError(f"M3付款单位账户本地规则第 {index} 条付款银行不支持: {bank or '(empty)'}")
        account = account_digits(item.get("account") or item.get("银行账号") or item.get("付款账号"))
        if account and not (8 <= len(account) <= 32):
            raise M3PrecheckError(f"M3付款单位账户本地规则第 {index} 条账号位数异常")
        account_type = str(item.get("account_type") or item.get("账户性质") or "").strip()
        metadata = {
            key: item.get(key)
            for key in _COMPANY_ACCOUNT_RULE_METADATA_KEYS
            if key in item and str(item.get(key) or "").strip()
        }
        rules.append(
            {
                "id": item.get("id") or item.get("ID") or "",
                "company": company,
                "company_norm": normalize_text(company),
                "payment_bank": bank,
                "account": account,
                "account_type": normalize_text(account_type),
                "is_default": _truthy_value(item.get("default")) or account_type == "基本户",
                "index": index,
                "metadata": metadata,
            }
        )

    _COMPANY_BANK_ACCOUNT_RULES_CACHE = rules
    return rules


def _m3_payment_account_digits(data: dict) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for key in _M3_PAYMENT_ACCOUNT_KEYS:
        digits = account_digits(data.get(key))
        if digits:
            result.append((key, digits))
    return result


def _m3_payment_account_type(data: dict) -> str:
    for key in _M3_PAYMENT_ACCOUNT_TYPE_KEYS:
        text = normalize_text(data.get(key))
        if text:
            return text
    return ""


def _apply_company_account_rule_metadata(data: dict, rule: dict, source: str) -> None:
    """Attach safe routing metadata from a uniquely matched local account rule."""
    metadata = rule.get("metadata")
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            if str(value or "").strip():
                data[key] = value
    data["M3本地付款账户规则ID"] = str(rule.get("id") or rule.get("index") or "")
    data["M3本地付款账户规则来源"] = source


def resolve_payment_bank_from_company_account_rules(data: dict, payer_name: str) -> tuple[str, str, str, bool]:
    """Resolve payer bank from the local account table.

    Returns (bank, raw, source, matched_company). If matched_company is True
    while bank is empty, the local table recognized this payer but could not
    uniquely select an account, so the caller must not fall back to broad
    keyword matching.
    """
    if not _is_m3_origin_data(data):
        return "", "", "", False
    payer_norm = normalize_text(payer_name)
    if not payer_norm:
        return "", "", "", False

    rules = [rule for rule in load_company_bank_account_rules() if rule["company_norm"] == payer_norm]
    if not rules:
        return "", "", "", False

    for source_key, digits in _m3_payment_account_digits(data):
        matched = [rule for rule in rules if rule.get("account") == digits]
        if len(matched) == 1:
            source = f"本地付款账户表:{source_key}"
            _apply_company_account_rule_metadata(data, matched[0], source)
            return matched[0]["payment_bank"], payer_name, source, True
        if len(matched) > 1:
            data["M3付款银行识别失败原因"] = "付款账号同时命中多个本地账户规则，已拒绝默认猜测"
            return "", payer_name, "本地付款账户表冲突", True

    account_type = _m3_payment_account_type(data)
    if account_type:
        matched = [rule for rule in rules if rule.get("account_type") == account_type]
        if len(matched) == 1:
            source = "本地付款账户表:账户性质"
            _apply_company_account_rule_metadata(data, matched[0], source)
            return matched[0]["payment_bank"], payer_name, source, True
        if len(matched) > 1:
            data["M3付款银行识别失败原因"] = f"账户性质 {account_type} 命中多条本地账户规则，已拒绝默认猜测"
            return "", payer_name, "本地付款账户表冲突", True

    defaults = [rule for rule in rules if rule.get("is_default")]
    if len(defaults) == 1:
        source = "本地付款账户表:默认基本户"
        _apply_company_account_rule_metadata(data, defaults[0], source)
        return defaults[0]["payment_bank"], payer_name, source, True
    if len(defaults) > 1:
        data["M3付款银行识别失败原因"] = "付款单位命中多条默认账户规则，已拒绝默认猜测"
        return "", payer_name, "本地付款账户表冲突", True

    if len(rules) == 1:
        source = "本地付款账户表:唯一账户"
        _apply_company_account_rule_metadata(data, rules[0], source)
        return rules[0]["payment_bank"], payer_name, source, True

    data["M3付款银行识别失败原因"] = "付款单位命中本地账户表但存在多条账户，M3未提供付款账号/账户性质，已拒绝默认猜测"
    return "", payer_name, "本地付款账户表未唯一", True

BANK_OUTPUT_DIRS = {
    "招商银行": "招行",
    "兴业银行": "兴业银行",
    "中国农业银行": "农业银行",
    "中国银行": "中国银行",
}

UNKNOWN_BANK_DIR = "未识别银行"
BANK_BATCH_FILES = {
    "招商银行": "招行.json",
    "兴业银行": "兴业银行.json",
    "中国农业银行": "农业银行.json",
    "中国银行": "中国银行.json",
}
SUPPORTED_PAYMENT_BANKS = set(BANK_BATCH_FILES)


def normalize_purpose() -> str:
    # 用户规则：用途始终填「货款」，不读 OA 申请说明。
    return "货款"


def normalize_text(value) -> str:
    return "".join(str(value or "").split())


def normalize_amount(value) -> str:
    text = str(value or "").replace(",", "").replace("，", "")
    text = text.replace("￥", "").replace("¥", "").replace("元", "")
    return "".join(text.split())


def normalize_account(value) -> str:
    """Remove whitespace that often appears when OA copies bank accounts."""
    return normalize_text(value)


def looks_like_bank_account(value) -> bool:
    account = normalize_account(value)
    return account.isdigit() and 8 <= len(account) <= 32


def looks_like_bank_branch(value) -> bool:
    text = str(value or "").strip()
    compact_text = normalize_text(text)
    if not compact_text:
        return False
    bank_tokens = ("银行", "信用社", "信用合作", "村镇银行", "农商行", "农信")
    return any(token in compact_text for token in bank_tokens)


def first_non_empty(*values) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def split_bank_head(full_bank: str) -> str:
    """把完整支行名拆成银行大类；未命中时保留原文。"""
    full_bank = full_bank or ""
    for h in BANK_HEADS:
        if full_bank.startswith(h):
            return h
    return full_bank


def normalize_payment_bank(value: str) -> str:
    """把 OA/M3 里的付款银行描述归一成制单路由用的银行名。"""
    text = normalize_text(value)
    if not text:
        return ""
    upper_text = text.upper()
    for bank_name, aliases in BANK_ALIASES.items():
        for alias in aliases:
            alias_text = normalize_text(alias)
            if not alias_text:
                continue
            if alias_text.upper() in upper_text:
                return bank_name
    for bank_name in BANK_HEADS:
        if normalize_text(bank_name) in text:
            return bank_name
    return text


def resolve_payment_bank(data: dict) -> tuple[str, str, str]:
    """返回 (归一银行名, 原始文本, 来源字段)。只看付款银行字段，不用收款开户行兜底。"""
    for label in PAYMENT_BANK_LABELS:
        value = data.get(label)
        if value:
            return normalize_payment_bank(value), str(value).strip(), label

    # 兼容未来 OA 标签略有差异的情况：字段名同时带“银行/行”和付款语义时尝试识别。
    payment_tokens = ("付款", "付方", "出款", "转出", "支付", "制单", "经办", "承办", "承付", "网银")
    bank_tokens = ("银行", "开户行", "付款行")
    derived_tokens = ("识别", "原文", "来源", "判定", "相关")
    for key, value in data.items():
        key_norm = normalize_text(key)
        if not value:
            continue
        if any(token in key_norm for token in derived_tokens):
            continue
        if any(token in key_norm for token in payment_tokens) and any(token in key_norm for token in bank_tokens):
            return normalize_payment_bank(value), str(value).strip(), key

    payer_name = str(data.get("付款单位名称") or "").strip()
    payer_compact = normalize_text(payer_name)
    # M3-origin guard：只有带 M3 抽取来源字段的记录才允许走「付款单位 → 付款银行」
    # 硬编码兜底；手工 bank_form.json / 本地测试 JSON / 银行子项目直接传入 dict
    # 不会触发兜底，沿用既有的 fail-closed/单条隔离逻辑。
    if payer_compact and _is_m3_origin_data(data):
        fixed_bank, fixed_raw, fixed_source = resolve_m3_fixed_company_bank(data, payer_name)
        if fixed_bank:
            return fixed_bank, fixed_raw, fixed_source
        rule_bank, rule_raw, rule_source, matched_company = resolve_payment_bank_from_company_account_rules(data, payer_name)
        if rule_bank or matched_company:
            return rule_bank, rule_raw, rule_source
        for keyword, bank_name in PAYER_BANK_FALLBACKS.items():
            if normalize_text(keyword) in payer_compact:
                return bank_name, payer_name, "付款单位名称兜底"

    return "", "", ""


def canonicalize_extracted_fields(data: dict) -> dict:
    """Fill canonical OA field names from common reimbursement/request aliases."""
    for canonical, aliases in CANONICAL_FIELD_ALIASES.items():
        if str(data.get(canonical) or "").strip():
            continue
        for alias in aliases:
            value = data.get(alias)
            if str(value or "").strip():
                data[canonical] = value
                break
    bank_text = data.get("开户银行")
    account_text = data.get("银行账户")
    if looks_like_bank_account(bank_text) and looks_like_bank_branch(account_text):
        data["开户银行"] = str(account_text or "").strip()
        data["银行账户"] = normalize_account(bank_text)
        data["M3字段修正"] = "开户银行/银行账户按内容识别已交换"
    return data


def build_branch_queries(bank_head: str, branch_full: str) -> list[str]:
    """生成收款行搜索关键字，供下游银行脚本按需使用。"""
    bank_head = (bank_head or "").strip()
    branch_full = (branch_full or "").strip()
    if not branch_full:
        return [bank_head] if bank_head else []

    simplified = branch_full
    for token in ("股份有限公司", "有限责任公司", "有限公司", "股份有限", "有限"):
        simplified = simplified.replace(token, "")
    if bank_head:
        simplified = simplified.replace(bank_head, "")
    simplified = simplified.replace("支行", "").replace("分行", "").strip()

    candidates = [simplified, f"{bank_head}{simplified}" if bank_head and simplified else "", bank_head, branch_full]
    result = []
    for item in candidates:
        item = (item or "").strip()
        if item and item not in result:
            result.append(item)
    return result


def m3_screenshot_errors(payload: dict) -> list[str]:
    """Return screenshot contract errors without raising."""
    errors = []
    if not REQUIRE_SCREENSHOTS:
        return errors
    if not ENABLE_SCREENSHOTS:
        errors.append("M3_ENABLE_SCREENSHOTS=0，但当前规则要求付款信息必须配截图")

    for key, label in (
        ("M3合同付款列表截图", "合同付款列表截图"),
        ("M3抓取详情截图", "付款详情截图"),
    ):
        path = str((payload or {}).get(key) or "").strip()
        if not path:
            errors.append(f"缺少{label}")
        elif not os.path.isfile(path):
            errors.append(f"{label}不存在: {path}")

    return errors


def validate_m3_screenshot_info(payload: dict, *, context: str = "M3付款信息") -> None:
    """Fail closed unless payment information carries auditable M3 screenshots."""
    errors = m3_screenshot_errors(payload)
    if errors:
        raise RuntimeError(f"{context}缺少可核验截图，停止写出/分发付款信息：" + "；".join(errors))


def validate_oa_json_precheck(data: dict, bank_form: dict) -> None:
    """Validate OA text after JSON mapping, before writing any bank queue files."""
    errors: list[str] = []

    for key in ("单据编号", "收款单位名称", "银行账户", "开户银行", "申请金额"):
        if not str((data or {}).get(key) or "").strip():
            errors.append(f"缺少OA字段: {key}")

    errors.extend(m3_screenshot_errors(data))
    errors.extend(m3_screenshot_errors(bank_form))

    payment_bank = str(bank_form.get("付款银行") or "").strip()
    if not payment_bank:
        errors.append("未识别付款银行")
    elif payment_bank not in SUPPORTED_PAYMENT_BANKS:
        errors.append(f"付款银行不在当前自动制单白名单: {payment_bank}")

    account = normalize_account(first_non_empty(bank_form.get("收方账号"), bank_form.get("acct_no"), data.get("银行账户")))
    if not account:
        errors.append("缺少收款账号")
    elif not account.isdigit():
        errors.append(f"收款账号必须为纯数字: {account}")
    elif not (8 <= len(account) <= 32):
        errors.append(f"收款账号位数异常({len(account)}): {account}")

    payee_name = first_non_empty(bank_form.get("收方户名"), bank_form.get("acct_name"), data.get("收款单位名称"))
    if not payee_name:
        errors.append("缺少收款户名")

    receive_bank = first_non_empty(bank_form.get("开户银行"), bank_form.get("bank"))
    branch_full = first_non_empty(bank_form.get("支行名称"), bank_form.get("branch_full"), data.get("开户银行"))
    if not receive_bank:
        errors.append("缺少收款开户银行")
    if payment_bank == "招商银行" and not str(bank_form.get("付款单位名称") or data.get("付款单位名称") or "").strip():
        errors.append("招行制单需要付款单位名称来匹配U盾端口")
    if payment_bank in {"兴业银行", "中国银行"} and not branch_full:
        errors.append(f"{payment_bank}制单需要完整开户行")

    amount = normalize_amount(first_non_empty(bank_form.get("金额"), bank_form.get("amount"), data.get("申请金额")))
    if not amount:
        errors.append("缺少金额")
    else:
        try:
            amount_value = Decimal(amount)
        except InvalidOperation:
            errors.append(f"金额不是有效数字: {amount}")
        else:
            if not amount_value.is_finite():
                errors.append(f"金额不是有效数字: {amount}")
            else:
                if amount_value <= 0:
                    errors.append(f"金额必须大于0: {amount}")
                if amount_value.as_tuple().exponent < -2:
                    errors.append(f"金额最多保留两位小数: {amount}")

    if errors:
        raise M3PrecheckError("OA/M3 JSON预检失败，停止写出/分发付款信息：" + "；".join(errors))


# 「对公请款（云链/社保公积金）」中一类记录在 M3 详情页本身的「开户银行 / 银行账户」就
# 是占位值 0（社保/公积金/工资/个税不走普通银行转账制单），不是被本脚本抽坏。
# 这类记录不能 silently 跳过整轮抽取（会掩盖真正的字段错误），但也不能整轮 fail-closed
# （会让同轮其它已正确识别的记录全部停在白屏前）。下面三个 helper 用来把这类记录单条
# 隔离到 runtime/skipped/：不写任何银行队列、不启动银行，仍 fail-closed，但只针对该条。
_PLACEHOLDER_ACCOUNT_LITERALS = {"-", "—", "无", "暂无", "无效", "/", "N/A", "NA"}
_SOCIAL_FUND_KEYWORDS = (
    # 简称
    "社保",
    "社保费",
    "公积金",
    "住房公积金",
    "工资",
    "个税",
    # 社保正式名称——必须够长够具体，避免「商业保险/财产保险/普通保险费」误判。
    # 严禁单独使用「保险」或「缴费」做关键词。
    "社会保险",
    "社会保险费",
    "保险费缴费申报表",
    "医疗保险",
    "养老保险",
    "失业保险",
    "工伤保险",
    "生育保险",
)
_CORPORATE_REQUEST_REPORT_KEYWORDS = ("对公请款",)
# 工资代发等场景，M3 的「银行账户/开户银行」字段不是真账号，而是一句指向模板/附件的
# 中文说明（例如「详情请见招行代发工资导入模板」「详见附件」）。匹配的是「指向其它来源」
# 这件事，而不是任何关键词的简单出现；普通账号/正常银行名不会命中。
_UNVOUCHERABLE_DESCRIPTOR_PHRASES = (
    "详情请见招行代发工资导入模板",
    "详见招行代发工资导入模板",
    "招行代发工资导入模板",
    "代发工资导入模板",
    "工资导入模板",
    "代发工资模板",
    "详情请见附件",
    "详见附件",
    "见附件",
    "详情请见模板",
    "详见模板",
    "见模板",
)
_UNVOUCHERABLE_PRECHECK_MARKERS = (
    "收款账号位数异常",
    "收款账号必须为纯数字",
    "缺少收款账号",
    "缺少收款开户银行",
    "缺少OA字段: 银行账户",
    "缺少OA字段: 开户银行",
)


def is_placeholder_payment_account(value) -> bool:
    """Return True for M3 fields that are placeholder/non-routable values (e.g. '0', '无')."""
    text = normalize_text(value)
    if not text:
        return True
    if all(ch == "0" for ch in text):
        return True
    if text in _PLACEHOLDER_ACCOUNT_LITERALS:
        return True
    if text.upper() in {"N/A", "NA"}:
        return True
    return False


def is_unvoucherable_template_descriptor(value) -> bool:
    """Return True when 银行账户/开户银行 字段是「详见招行代发工资导入模板/详见附件/详见模板」等
    指向其它来源的说明文本——这本身就意味着该字段不是可制单的账号/开户行。
    严禁单独命中「附件/模板」这类宽泛词；必须命中带『详见/详情请见/见』前缀的整段说明
    或具体的「代发工资导入模板/工资导入模板」等。
    """
    text = str(value or "").strip()
    if not text:
        return False
    return any(phrase in text for phrase in _UNVOUCHERABLE_DESCRIPTOR_PHRASES)


def is_social_fund_corporate_request(data: dict) -> bool:
    """Return True when the record is a 对公请款 社保/公积金/工资/个税 entry, not a普通银行转账."""
    if not isinstance(data, dict):
        return False
    report_fields = (
        data.get("M3报表名称"),
        data.get("M3报表入口"),
        data.get("类别"),
        data.get("标题"),
    )
    if not any(any(kw in str(v or "") for kw in _CORPORATE_REQUEST_REPORT_KEYWORDS) for v in report_fields):
        return False
    haystack = " ".join(
        str(data.get(key) or "")
        for key in (
            "M3报表名称",
            "M3报表入口",
            "类别",
            "标题",
            "申请说明",
            "收款单位名称",
            "M3列表行文本",
        )
    )
    return any(token in haystack for token in _SOCIAL_FUND_KEYWORDS)


def has_unvoucherable_corporate_request_fields(data: dict) -> bool:
    """Return True for 社保/工资/公积金 records whose account/bank fields are non-voucherable.

    This data-only check is used before the "unknown payment bank" isolation branch so
    a clearly non-voucherable payroll/social-fund record is reported as normal business
    isolation instead of asking finance to add a payer-bank rule.
    """
    if not is_social_fund_corporate_request(data):
        return False
    account_value = data.get("银行账户")
    bank_value = data.get("开户银行")
    account_unvoucherable = (
        is_placeholder_payment_account(account_value)
        or is_unvoucherable_template_descriptor(account_value)
    )
    bank_unvoucherable = (
        is_placeholder_payment_account(bank_value)
        or is_unvoucherable_template_descriptor(bank_value)
    )
    return account_unvoucherable or bank_unvoucherable


def should_isolate_unvoucherable_corporate_request(data: dict, bank_form: dict, exc: Exception) -> bool:
    """Decide whether a M3PrecheckError should isolate the single record instead of整轮 fail-closed.

    严格条件（同时满足）：
      1) 记录归属「对公请款」类报表且明确属于社保/公积金/工资/个税；
      2) M3 明细的「银行账户」或「开户银行」字段是占位/不可制单值（如 0/无），或者是
         「详情请见招行代发工资导入模板/详见附件/详见模板」等指向其它来源的说明文本——
         这两种情况都意味着该字段不是可银行制单的账号/开户行；
      3) M3PrecheckError 的失败项确实落在收款账号/开户行/账号位数等无法制单的字段——金额/
         截图/白名单/普通银行账号被抽错等情况一律不在此分支放过。
    """
    if not has_unvoucherable_corporate_request_fields(data):
        return False
    text = str(exc or "")
    return any(marker in text for marker in _UNVOUCHERABLE_PRECHECK_MARKERS)


def attach_m3_screenshot_info_or_abort(data: dict, list_screenshot: str, detail_screenshot: str) -> None:
    """Attach screenshot paths to extracted data and enforce the screenshot contract."""
    if list_screenshot:
        data["M3合同付款列表截图"] = list_screenshot
    if detail_screenshot:
        data["M3抓取详情截图"] = detail_screenshot
    validate_m3_screenshot_info(data, context="M3抽取结果")


def build_bank_form(data: dict) -> dict:
    """生成制单程序通用银行 JSON；启动逻辑由 run_bank_*.py 负责。"""
    canonicalize_extracted_fields(data)
    validate_m3_screenshot_info(data, context="M3抽取结果")
    full_bank = first_non_empty(
        data.get("开户银行"),
        data.get("开户行"),
        data.get("收款开户行"),
        data.get("收款方开户行"),
        data.get("收款方开户银行"),
    )
    bank_head = split_bank_head(full_bank)
    account = normalize_account(first_non_empty(
        data.get("银行账户"),
        data.get("银行账号"),
        data.get("收款账号"),
        data.get("收款账户"),
        data.get("收款方账号"),
        data.get("收方账号"),
    ))
    amount = normalize_amount(first_non_empty(
        data.get("申请金额"),
        data.get("报销金额"),
        data.get("请款金额"),
        data.get("付款金额"),
        data.get("本次付款金额"),
    ))
    purpose = normalize_purpose()
    payment_bank, payment_bank_raw, payment_bank_source = resolve_payment_bank(data)
    ref = data.get("单据编号", "") or data.get("合同编号", "")
    payee_name = first_non_empty(
        data.get("收款单位名称"),
        data.get("收款单位"),
        data.get("收款户名"),
        data.get("收款人名称"),
        data.get("收款人"),
        data.get("收方户名"),
    )
    payer_name = first_non_empty(data.get("付款单位名称"), data.get("付款单位"))

    form = {
        # 路由信息
        "付款银行": payment_bank,
        "付款银行原文": payment_bank_raw,
        "付款银行来源字段": payment_bank_source,
        "M3合同付款列表截图": data.get("M3合同付款列表截图", ""),
        "M3抓取详情截图": data.get("M3抓取详情截图", ""),

        # 招行兼容字段
        "付款单位名称": payer_name,
        "收方账号": account,
        "收方户名": payee_name,
        "开户银行": bank_head,                          # 总行名（用于下拉搜索）
        "支行名称": full_bank,                          # 完整支行名（含『...支行』）
        "金额": amount,
        "用途": purpose,
        "业务参考号": ref,
        "合同编号": data.get("合同编号", ""),

        # 兴业兼容字段
        "label": ref or "default",
        "amount": amount,
        "acct_no": account,
        "acct_name": payee_name,
        "bank": bank_head,
        "branch_full": full_bank,
        "branch_queries": build_branch_queries(bank_head, full_bank),
        "purpose": purpose,
    }
    for key in M3_BANK_FORM_METADATA_KEYS:
        value = data.get(key)
        if str(value or "").strip() and key not in form:
            form[key] = value
    return form


def safe_filename(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()
    text = re.sub(r'[<>:"/\\|?*]+', "_", text)
    text = re.sub(r"[\x00-\x1f]+", "_", text).strip(" .")
    return (text[:180] if text else "unknown")


def clean_business_ref(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()
    matches = re.findall(BILL_PATTERN, text)
    if not matches:
        return text
    full_matches = [m for m in matches if re.fullmatch(FLOW_PATTERN, m)]
    return (full_matches[0] if full_matches else matches[0]).strip().rstrip("-")


def expected_bill_prefixes_for_report(report_name: str) -> tuple[str, ...]:
    name = normalize_text(report_name)
    if "日常报销" in name:
        return ("YLFYBX-",)
    if "对公请款" in name:
        return ("YLDGQK-",)
    if "合同付款" in name and "云链" in name:
        return ("YLHTFK-",)
    if "合同付款" in name:
        return ("YLHT",)
    return ()


def is_expected_bill_for_report(ref: str, report_name: str) -> bool:
    ref = str(ref or "").strip()
    prefixes = expected_bill_prefixes_for_report(report_name)
    if not ref or not prefixes:
        return bool(ref)
    return any(ref.startswith(prefix) for prefix in prefixes)


def apply_row_ref_policy(data: dict, row: dict) -> None:
    """Keep business refs from drifting to attachment names on detail pages."""
    ref = str(row.get("ref") or "").strip()
    report_name = str(row.get("report_name") or row.get("requested_report_name") or "")
    current_ref = clean_business_ref(data.get("单据编号", ""))
    if current_ref:
        data["单据编号"] = current_ref
    if ref and not row.get("ref_is_generated"):
        if not current_ref or not is_expected_bill_for_report(current_ref, report_name):
            data["单据编号"] = ref
        return
    if row.get("ref_is_generated"):
        data["M3列表行键"] = ref
        if current_ref and not is_expected_bill_for_report(current_ref, report_name):
            data["疑似附件编号"] = current_ref
            current_ref = ""
        if not current_ref and ref:
            data["单据编号"] = ref


def xpath_literal(value: str) -> str:
    text = str(value or "")
    if '"' not in text:
        return f'"{text}"'
    if "'" not in text:
        return f"'{text}'"
    parts = text.split('"')
    return "concat(" + ', \'"\', '.join(f'"{part}"' for part in parts) + ")"


def write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def read_json_dict(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_json_list(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def write_json_list(path: str, payload: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def item_ref(item: dict) -> str:
    return str(
        item.get("业务参考号")
        or item.get("label")
        or item.get("单据编号")
        or item.get("合同编号")
        or ""
    ).strip()


def known_queue_refs() -> set[str]:
    refs: set[str] = set()
    for filename in BANK_BATCH_FILES.values():
        path = os.path.join(BANK_BATCH_DIR, filename)
        for item in read_json_list(path):
            ref = item_ref(item)
            if ref:
                refs.add(ref)
    return refs


FINGERPRINT_VOLATILE_KEYS = {
    "M3合同付款列表截图",
    "M3抓取详情截图",
    "M3抽取失败截图",
    "采购合同打开截图",
    "M3调度批次",
    "M3调度序号",
    "M3调度排序",
}


def stable_fingerprint_payload(value):
    """Drop run-local paths/schedule fields before comparing business content."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in FINGERPRINT_VOLATILE_KEYS:
                continue
            if "截图" in key_text:
                continue
            cleaned[key_text] = stable_fingerprint_payload(item)
        return cleaned
    if isinstance(value, list):
        return [stable_fingerprint_payload(item) for item in value]
    return value


def payload_fingerprint(value) -> str:
    payload = stable_fingerprint_payload(value or {})
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def known_queue_bank_fingerprints() -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for filename in BANK_BATCH_FILES.values():
        path = os.path.join(BANK_BATCH_DIR, filename)
        for item in read_json_list(path):
            ref = item_ref(item)
            if ref:
                fingerprints[ref] = payload_fingerprint(item)
    return fingerprints


def result_detail_fingerprint(result: dict) -> str:
    return payload_fingerprint(
        {
            "data": result.get("data") or {},
            "bank_form": result.get("bank_form") or {},
            "skip_bank_queue": bool(result.get("skip_bank_queue")),
        }
    )


def result_bank_form_fingerprint(result: dict) -> str:
    bank_form = result.get("bank_form")
    if not isinstance(bank_form, dict) or not bank_form:
        return ""
    return payload_fingerprint(bank_form)


def detail_refresh_due(state_ref: dict, now_epoch: float | None = None) -> bool:
    if DETAIL_REFRESH_SECONDS <= 0:
        return False
    if not isinstance(state_ref, dict):
        return False
    try:
        last_checked = float(state_ref.get("detail_checked_at_epoch") or 0)
    except (TypeError, ValueError):
        last_checked = 0.0
    now_epoch = time.time() if now_epoch is None else now_epoch
    return (now_epoch - last_checked) >= DETAIL_REFRESH_SECONDS


def row_fingerprint(row_text: str) -> str:
    compact_text = re.sub(r"\s+", " ", str(row_text or "")).strip()
    return hashlib.sha256(compact_text.encode("utf-8", errors="ignore")).hexdigest()


def load_extract_state() -> dict:
    state = read_json_dict(EXTRACT_STATE_PATH)
    refs = state.get("refs")
    if not isinstance(refs, dict):
        refs = {}
    return {"refs": refs}


def save_extract_state(state: dict) -> None:
    refs = state.get("refs")
    if not isinstance(refs, dict):
        refs = {}
    write_json(EXTRACT_STATE_PATH, {"refs": refs, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})


def update_observed_row_state(state: dict, rows: list[dict]) -> None:
    refs = state.setdefault("refs", {})
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for row in rows:
        ref = str(row.get("ref") or "").strip()
        if not ref:
            continue
        row_text = str(row.get("row_text") or "").strip()
        existing = refs.get(ref) if isinstance(refs.get(ref), dict) else {}
        refs[ref] = {
            **existing,
            "row_text_fingerprint": row_fingerprint(row_text),
            "row_text": row_text[:800],
            "last_observed_at": now,
        }


def mark_skipped_row_state(state: dict, row: dict, data: dict) -> None:
    refs = state.setdefault("refs", {})
    ref = str(row.get("ref") or data.get("单据编号") or "").strip()
    if not ref:
        return
    row_text = str(row.get("row_text") or data.get("M3列表行文本") or "").strip()
    existing = refs.get(ref) if isinstance(refs.get(ref), dict) else {}
    refs[ref] = {
        **existing,
        "row_text_fingerprint": row_fingerprint(row_text),
        "row_text": row_text[:800],
        "skip_bank_queue": True,
        "skip_reason": data.get("M3跳过原因", ""),
        "payment_order_no": data.get("采购合同支付单号", ""),
        "skipped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def detail_state_ref(row: dict, result: dict) -> str:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    # Some corporate-request list rows do not expose the real voucher number.
    # The list scanner then uses a ROW-* temporary key; once the detail page
    # yields 单据编号, use that stable business reference for de-duplication.
    return str(data.get("单据编号") or row.get("ref") or "").strip()


def mark_detail_checked_state(state: dict, row: dict, result: dict, *, detail_fingerprint: str = "", bank_form_fingerprint: str = "") -> None:
    refs = state.setdefault("refs", {})
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    ref = detail_state_ref(row, result)
    if not ref:
        return
    row_text = str(row.get("row_text") or data.get("M3列表行文本") or "").strip()
    existing = refs.get(ref) if isinstance(refs.get(ref), dict) else {}
    now_epoch = time.time()
    refs[ref] = {
        **existing,
        "row_text_fingerprint": row_fingerprint(row_text),
        "row_text": row_text[:800],
        "detail_fingerprint": detail_fingerprint or result_detail_fingerprint(result),
        "bank_form_fingerprint": bank_form_fingerprint or result_bank_form_fingerprint(result),
        "detail_checked_at_epoch": now_epoch,
        "detail_checked_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_epoch)),
    }


def detail_result_is_unchanged(state: dict, row: dict, result: dict, queue_bank_fingerprints: dict[str, str]) -> bool:
    ref = detail_state_ref(row, result)
    if not ref:
        return False
    state_ref = state.get("refs", {}).get(ref)
    if not isinstance(state_ref, dict):
        state_ref = {}
    new_detail_fp = result_detail_fingerprint(result)
    new_bank_fp = result_bank_form_fingerprint(result)
    if new_bank_fp and ref not in queue_bank_fingerprints:
        # If the per-bank queue was pruned or lost, a matching historical
        # detail fingerprint is not enough; rewrite the queue item.
        return False
    old_detail_fp = str(state_ref.get("detail_fingerprint") or "")
    if old_detail_fp and old_detail_fp == new_detail_fp:
        mark_detail_checked_state(state, row, result, detail_fingerprint=new_detail_fp)
        return True

    old_bank_fp = str(state_ref.get("bank_form_fingerprint") or queue_bank_fingerprints.get(ref) or "")
    if new_bank_fp and old_bank_fp and old_bank_fp == new_bank_fp:
        mark_detail_checked_state(
            state,
            row,
            result,
            detail_fingerprint=new_detail_fp,
            bank_form_fingerprint=new_bank_fp,
        )
        return True
    return False


def build_row_error_result(row: dict, exc: Exception, screenshot_path: str = "") -> dict:
    reason = f"M3详情抽取失败: {type(exc).__name__}: {exc}"
    data = {
        "单据编号": str(row.get("ref") or "").strip(),
        "M3列表行文本": str(row.get("row_text") or "").strip(),
        "M3跳过制单": True,
        "M3跳过原因": reason,
        "M3抽取失败截图": screenshot_path,
        "M3报表名称": row.get("report_name") or "",
        "M3报表入口": row.get("requested_report_name") or row.get("report_name") or "",
        "M3列表序号": int(row.get("position") or 0),
    }
    return {
        "row": row,
        "data": data,
        "bank_form": {},
        "skip_bank_queue": True,
    }


def partition_periodic_recheck(
    selected_rows: list[dict],
    state: dict,
    *,
    max_per_round: int | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split selected rows into priority + periodic-recheck buckets and cap the latter.

    Priority rows (new/changed/forced) are always processed this round.
    Pure "periodic recheck" rows are sorted oldest-checked first and limited by
    M3_DETAIL_RECHECK_MAX_PER_ROUND (<=0 disables the cap).
    Returns (priority_rows, recheck_run, recheck_deferred).
    Deferred rows are intentionally skipped this round — caller must NOT mark
    them failed, skipped, or update detail_checked_at.
    """
    refs = state.get("refs", {}) if isinstance(state, dict) else {}
    priority_rows: list[dict] = []
    recheck_candidates: list[dict] = []
    for row in selected_rows:
        if str(row.get("scan_reason") or "") == PERIODIC_RECHECK_REASON:
            recheck_candidates.append(row)
        else:
            priority_rows.append(row)

    def _last_checked(row: dict) -> float:
        ref = str(row.get("ref") or "").strip()
        state_ref = refs.get(ref) if ref else None
        if not isinstance(state_ref, dict):
            return 0.0
        try:
            return float(state_ref.get("detail_checked_at_epoch") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    recheck_candidates.sort(key=_last_checked)

    budget = DETAIL_RECHECK_MAX_PER_ROUND if max_per_round is None else max_per_round
    if budget is None or budget <= 0:
        return priority_rows, recheck_candidates, []
    return priority_rows, recheck_candidates[:budget], recheck_candidates[budget:]


def should_extract_row(row: dict, state: dict, existing_refs: set[str]) -> tuple[bool, str]:
    ref = str(row.get("ref") or "").strip()
    if not ref:
        return False, "缺少单据号"
    if SCAN_FORCE_DETAILS:
        return True, "强制详情扫描"
    state_ref = state.get("refs", {}).get(ref)
    current_fingerprint = row_fingerprint(str(row.get("row_text") or ""))
    if isinstance(state_ref, dict):
        old_fingerprint = str(state_ref.get("row_text_fingerprint") or "")
        if old_fingerprint and old_fingerprint != current_fingerprint:
            return True, "列表行内容变化"
        if detail_refresh_due(state_ref):
            return True, PERIODIC_RECHECK_REASON
        if state_ref.get("skip_bank_queue") and old_fingerprint == current_fingerprint:
            reason = str(state_ref.get("skip_reason") or "已标记跳过制单")
            return False, reason
    if ref not in existing_refs:
        return True, "新增单据"
    return False, "已在队列且列表行未变"


def cleanup_old_screenshot_runs() -> None:
    if not os.path.isdir(SCREENSHOT_ROOT):
        return
    run_dirs = [
        os.path.join(SCREENSHOT_ROOT, name)
        for name in os.listdir(SCREENSHOT_ROOT)
        if os.path.isdir(os.path.join(SCREENSHOT_ROOT, name))
    ]
    run_dirs.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    cutoff = time.time() - SCREENSHOT_KEEP_DAYS * 86400
    for index, old_dir in enumerate(run_dirs):
        try:
            mtime = os.path.getmtime(old_dir)
        except OSError:
            mtime = 0
        if index < SCREENSHOT_KEEP_RUNS or mtime >= cutoff:
            continue
        shutil.rmtree(old_dir, ignore_errors=True)


def create_screenshot_dir() -> str:
    run_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
    path = os.path.join(SCREENSHOT_ROOT, run_id)
    os.makedirs(path, exist_ok=True)
    return path


def save_screenshot(page, screenshot_dir: str, name: str) -> str:
    if not ENABLE_SCREENSHOTS:
        return ""
    if not screenshot_dir:
        return ""
    path = os.path.join(screenshot_dir, f"{name}.png")
    try:
        page.screenshot(path=path, full_page=True)
        print(f"截图已保存: {path}")
        return path
    except Exception as exc:
        print(f"警告：截图失败 [{name}]: {exc}")
        return ""


def save_context_error_screenshot(context, screenshot_dir: str, name: str) -> str:
    if not context:
        return ""
    pages = []
    try:
        pages = list(context.pages)
    except Exception:
        pages = []
    for page in reversed(pages):
        try:
            if page.is_closed():
                continue
        except Exception:
            continue
        return save_screenshot(page, screenshot_dir, name)
    return ""


def ensure_bank_batch_files() -> list[str]:
    paths = []
    os.makedirs(BANK_BATCH_DIR, exist_ok=True)
    for filename in BANK_BATCH_FILES.values():
        path = os.path.join(BANK_BATCH_DIR, filename)
        if not os.path.exists(path):
            write_json_list(path, [])
        paths.append(path)
    return paths


def upsert_bank_batch(bank_form: dict) -> str:
    """把查询结果写入对应银行队列 JSON，按业务参考号去重更新。"""
    validate_m3_screenshot_info(bank_form, context="银行队列付款信息")
    ensure_bank_batch_files()
    payment_bank = bank_form.get("付款银行") or ""
    if payment_bank not in BANK_BATCH_FILES:
        return ""
    filename = BANK_BATCH_FILES[payment_bank]
    path = os.path.join(BANK_BATCH_DIR, filename)
    items = read_json_list(path)
    ref = bank_form.get("业务参考号") or bank_form.get("label") or ""

    replaced = False
    for index, item in enumerate(items):
        item_ref = item.get("业务参考号") or item.get("label") or ""
        if ref and item_ref == ref:
            items[index] = bank_form
            replaced = True
            break
    if not replaced:
        items.append(bank_form)

    write_json_list(path, items)
    return path


def write_classified_outputs(data: dict, bank_form: dict) -> tuple[str, list[str]]:
    """把 JSON 写入按付款银行划分的目录，并返回银行名与写出的路径。"""
    validate_m3_screenshot_info(data, context="M3原始付款信息")
    validate_m3_screenshot_info(bank_form, context="银行制单付款信息")
    payment_bank = bank_form.get("付款银行") or ""
    bank_dir_name = BANK_OUTPUT_DIRS.get(payment_bank, safe_filename(payment_bank) if payment_bank else UNKNOWN_BANK_DIR)
    bill_no = safe_filename(data.get("单据编号") or bank_form.get("业务参考号") or "latest")

    base_dir = os.path.join(BANK_FORMS_DIR, bank_dir_name)
    paths = [
        os.path.join(base_dir, "latest.json"),
        os.path.join(base_dir, f"{bill_no}.json"),
        os.path.join(base_dir, "bank_form.json"),
    ]
    for path in paths:
        write_json(path, bank_form)

    raw_paths = [
        os.path.join(base_dir, "latest_raw.json"),
        os.path.join(base_dir, f"{bill_no}_raw.json"),
    ]
    for path in raw_paths:
        write_json(path, data)

    batch_paths = ensure_bank_batch_files()
    queue_path = upsert_bank_batch(bank_form)

    route = {
        "付款银行": payment_bank,
        "付款银行原文": bank_form.get("付款银行原文", ""),
        "付款银行来源字段": bank_form.get("付款银行来源字段", ""),
        "分类目录": base_dir,
        "制单JSON": os.path.join(base_dir, "bank_form.json"),
        "银行JSON": os.path.join(base_dir, "bank_form.json"),
        "银行队列JSON": queue_path,
        "原始JSON": os.path.join(base_dir, "latest_raw.json"),
        "M3合同付款列表截图": bank_form.get("M3合同付款列表截图", ""),
        "M3抓取详情截图": bank_form.get("M3抓取详情截图", ""),
        "单据编号": data.get("单据编号", ""),
        "合同名称": data.get("合同名称", ""),
        "合同名称链接": data.get("合同名称链接", ""),
        "采购合同名称": data.get("采购合同名称", ""),
        "采购合同链接": data.get("采购合同链接", ""),
    }
    route_path = os.path.join(BANK_FORMS_DIR, "latest_route.json")
    write_json(route_path, route)

    return payment_bank, [path for path in [*paths, *raw_paths, queue_path, *batch_paths, route_path] if path]


def remove_ref_from_bank_batches(ref: str) -> list[str]:
    """Remove a skipped record from any existing bank queue files."""
    ref = str(ref or "").strip()
    if not ref:
        return []
    changed_paths: list[str] = []
    ensure_bank_batch_files()
    for filename in BANK_BATCH_FILES.values():
        path = os.path.join(BANK_BATCH_DIR, filename)
        items = read_json_list(path)
        kept = [item for item in items if item_ref(item) != ref]
        if len(kept) != len(items):
            write_json_list(path, kept)
            changed_paths.append(path)
    return changed_paths


def write_skipped_outputs(data: dict) -> list[str]:
    """Persist an audit trail for records skipped before bank dispatch."""
    os.makedirs(SKIPPED_DIR, exist_ok=True)
    ref = str(data.get("单据编号") or "").strip()
    bill_no = safe_filename(ref or "latest")
    paths = [
        os.path.join(SKIPPED_DIR, "latest.json"),
        os.path.join(SKIPPED_DIR, f"{bill_no}.json"),
    ]
    for path in paths:
        write_json(path, data)

    skipped_records_path = os.path.join(SKIPPED_DIR, "skipped_records.json")
    records = read_json_list(skipped_records_path)
    replaced = False
    for index, item in enumerate(records):
        if item_ref(item) == ref:
            records[index] = data
            replaced = True
            break
    if not replaced:
        records.append(data)
    write_json_list(skipped_records_path, records)
    paths.append(skipped_records_path)

    paths.extend(remove_ref_from_bank_batches(ref))

    current_latest = os.path.join(CURRENT_DIR, "latest.json")
    write_json(current_latest, data)
    paths.append(current_latest)

    stale_marker = {
        "M3跳过制单": True,
        "M3跳过原因": data.get("M3跳过原因", ""),
        "业务参考号": ref,
        "单据编号": ref,
        "合同名称": data.get("合同名称", ""),
        "采购合同支付单号": data.get("采购合同支付单号", ""),
    }
    current_bank_form = os.path.join(CURRENT_DIR, "bank_form.json")
    existing_current = read_json_dict(current_bank_form)
    if item_ref(existing_current) == ref:
        write_json(current_bank_form, stale_marker)
        paths.append(current_bank_form)

    latest_route = os.path.join(BANK_FORMS_DIR, "latest_route.json")
    existing_route = read_json_dict(latest_route)
    if item_ref(existing_route) == ref:
        write_json(latest_route, stale_marker)
        paths.append(latest_route)

    return paths


def reset_page_before_retry(page, label: str) -> None:
    """重试前只停掉当前页正在进行的加载，不再主动 goto about:blank。

    旧实现会在退避等待前把可见浏览器 goto 到 about:blank，使用户在 8/20/45 秒退避
    期间长时间盯着空白页。现在仅 window.stop()：已成功加载过的页面保留原样，全新
    启动本就停在 about:blank，由 goto_page 的空白快速重启分支兜底，避免长时间空白。
    """
    try:
        page.evaluate("window.stop()")
    except Exception:
        pass
    print(f"{label} 已停止当前加载，准备重试打开页面。")


def is_transient_navigation_error(exc: Exception) -> bool:
    text = str(exc)
    return any(marker in text for marker in TRANSIENT_NAVIGATION_ERROR_MARKERS)


def navigation_retry_delay_seconds(attempt: int, exc: Exception) -> float:
    if not is_transient_navigation_error(exc):
        return RETRY_DELAY_SECONDS
    index = max(0, min(attempt - 1, len(NAVIGATION_BACKOFF_SECONDS) - 1))
    return NAVIGATION_BACKOFF_SECONDS[index]


def goto_page(page, url: str, label: str, *, wait_until: str = "domcontentloaded", tolerate_aborted: bool = False):
    last_exc = None
    for attempt in range(1, NAVIGATION_RETRIES + 1):
        try:
            print(f"{label} 导航尝试 {attempt}/{NAVIGATION_RETRIES}: {url}")
            return page.goto(url, wait_until=wait_until, timeout=NAVIGATION_TIMEOUT_MS)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            last_exc = exc
            if tolerate_aborted and "net::ERR_ABORTED" in str(exc):
                page.wait_for_timeout(2000)
                print(f"{label} 导航被页面跳转中断，继续检查当前页面: {page.url}")
                return None
            print(f"{label} 导航失败 {attempt}/{NAVIGATION_RETRIES}: {exc}")
            if attempt < NAVIGATION_RETRIES:
                reset_page_before_retry(page, label)
                # 顶层页仍停在 about:blank 的 transient 失败：不要在空白页上继续叠加长退避，
                # 直接抛 M3WhiteScreenError 交给 run() 关闭 context、按 M3_BROWSER_RESTARTS 重启，
                # 既不让用户长时间盯着 about:blank，也不让退避叠加外层超时拖成 exit=124。
                if is_transient_navigation_error(exc):
                    page_is_blank = looks_like_top_level_blank(page)
                    if attempt >= NAV_BLANK_RESTART_AFTER_ATTEMPTS and page_is_blank:
                        raise M3WhiteScreenError(
                            f"{label} 导航连续 {attempt} 次失败且页面停在空白顶层"
                            f"(url={page_url_safe(page)})，关闭浏览器并按 M3_BROWSER_RESTARTS 重启；"
                            f"当前错误: {exc}"
                        ) from exc
                    if attempt >= NAV_TRANSIENT_RESTART_AFTER_ATTEMPTS:
                        raise M3WhiteScreenError(
                            f"{label} 导航连续 {attempt} 次发生网络/连接类失败"
                            f"(url={page_url_safe(page)})，不再等待长退避，关闭浏览器并按 "
                            f"M3_BROWSER_RESTARTS 重启；当前错误: {exc}"
                        ) from exc
                delay_seconds = navigation_retry_delay_seconds(attempt, exc)
                print(f"{label} 将在 {delay_seconds:g} 秒后重试。")
                page.wait_for_timeout(int(delay_seconds * 1000))
    raise RuntimeError(
        f"{label} 打开失败：{url}。已自动重试 {NAVIGATION_RETRIES} 次；"
        f"请确认 OA 网络/VPN 可用后重试；当前错误: {last_exc}"
    ) from last_exc


def wait_domcontentloaded_soft(page, timeout_ms: int = 8000) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass


def has_login_form(page) -> bool:
    try:
        return page.locator('input[type="password"]').count() > 0
    except Exception:
        return False


def looks_like_login_page(page) -> bool:
    if has_login_form(page):
        return True
    try:
        url = page.url or ""
    except Exception:
        return False
    return "main.do" in url and "vReport.do" not in url


def looks_like_forced_logout_page(page) -> bool:
    try:
        body_text = page.locator("body").inner_text(timeout=2000)
    except Exception:
        body_text = ""
    if "被迫下线" in body_text or "与服务器失去连接" in body_text:
        return True
    try:
        url = page.url or ""
    except Exception:
        return False
    return "method=logout" in url or "reason=被迫下线" in url


def ensure_oa_login(page) -> bool:
    """Open the OA login/home page only when a login refresh is needed."""
    goto_page(page, OA_URL, "OA 登录页", wait_until="commit", tolerate_aborted=True)
    wait_domcontentloaded_soft(page)
    if has_login_form(page):
        print("检测到 OA 登录表单，开始自动登录。")
        try_login(page)
        page.wait_for_timeout(1500)
        return True
    print("OA 登录页未检测到密码框，沿用当前登录态。")
    return False


def open_report_page_with_login(page) -> None:
    """Prefer the report URL; fall back to OA login only if the session requires it."""
    direct_error: Exception | None = None
    if DIRECT_REPORT_FIRST:
        try:
            goto_page(page, REPORT_URL, "报表分析直达")
            wait_domcontentloaded_soft(page)
            if looks_like_forced_logout_page(page):
                print("报表分析直达后检测到 OA 强制下线页面，转入 OA 登录兜底。")
            elif not looks_like_login_page(page):
                return
            else:
                print("报表分析直达后检测到登录页，转入 OA 登录兜底。")
        except M3WhiteScreenError as exc:
            direct_error = exc
            print(f"报表分析直达出现白屏/连接关闭，将尝试 OA 登录兜底: {exc}")
        except RuntimeError as exc:
            direct_error = exc
            print(f"报表分析直达失败，将尝试 OA 登录兜底: {exc}")

    try:
        ensure_oa_login(page)
        goto_page(page, REPORT_URL, "报表分析登录后")
        wait_domcontentloaded_soft(page)
    except Exception as exc:
        if direct_error is not None:
            print(f"OA 登录兜底仍失败；报表直达原始错误: {direct_error}")
        raise


def click_visible_text(page, text: str, label: str, *, timeout_ms: int = 30000, fallback_texts: tuple[str, ...] = ()) -> None:
    """点击可见入口，优先精确匹配；带 fallback_texts 的入口仅作最后兜底。"""
    deadline = time.monotonic() + timeout_ms / 1000
    locators = [
        page.locator(f'xpath=//*[normalize-space(text())="{text}"]'),
        page.get_by_text(text, exact=True),
        page.locator(f"text={text}"),
    ]
    last_error = ""
    while time.monotonic() < deadline:
        fallback_item = None
        fallback_text = ""
        for locator in locators:
            try:
                count = locator.count()
            except Exception as exc:
                last_error = str(exc)
                continue
            for index in range(count):
                item = locator.nth(index)
                try:
                    if not item.is_visible():
                        continue
                    item_text = item.inner_text(timeout=1000).strip()
                    if text not in item_text:
                        continue
                    if any(fallback in item_text for fallback in fallback_texts):
                        fallback_item = item
                        fallback_text = item_text
                        continue
                    item.scroll_into_view_if_needed(timeout=2000)
                    item.click(timeout=5000)
                    print(f"已点击{label}入口: {item_text or text}")
                    return
                except Exception as exc:
                    last_error = str(exc)
        if fallback_item is not None:
            try:
                fallback_item.scroll_into_view_if_needed(timeout=2000)
                fallback_item.click(timeout=5000)
                print(f"已点击{label}兜底入口: {fallback_text or text}")
                return
            except Exception as exc:
                last_error = str(exc)
        page.wait_for_timeout(300)
    suffix = f"最后错误: {last_error}" if last_error else "未找到可见元素"
    raise RuntimeError(f"未找到可见的{label}入口：需要文本包含『{text}』。{suffix}")


def click_contract_report(page, report_name: str = CONTRACT_REPORT_NAME, *, timeout_ms: int = 30000) -> str:
    """点击指定报表卡片/侧栏入口。"""
    deadline = time.monotonic() + timeout_ms / 1000
    last_error = ""
    while time.monotonic() < deadline:
        for name in report_name_variants(report_name):
            locators = [
                page.locator(f'xpath=//*[normalize-space(text())="{name}"]'),
                page.get_by_text(name, exact=True),
                page.locator(f"text={name}"),
            ]
            for locator in locators:
                try:
                    count = locator.count()
                except Exception as exc:
                    last_error = str(exc)
                    continue
                for index in range(count):
                    item = locator.nth(index)
                    try:
                        if not item.is_visible():
                            continue
                        item_text = item.inner_text(timeout=1000).strip()
                        item.scroll_into_view_if_needed(timeout=2000)
                        item.click(timeout=5000)
                        selected = item_text or name
                        print(f"已点击报表入口: {selected}")
                        return selected
                    except Exception as exc:
                        last_error = str(exc)
        page.wait_for_timeout(300)
    suffix = f"最后错误: {last_error}" if last_error else "未找到可见元素"
    raise RuntimeError(f"未找到可见的报表入口：需要『{report_name}』。{suffix}")


def open_report_list(page, report_name: str) -> str:
    """Open one report list from the report center page."""
    try:
        selected = click_contract_report(page, report_name, timeout_ms=5000)
    except RuntimeError:
        click_visible_text(page, "财务报表", "财务报表")
        page.wait_for_timeout(500)
        selected = click_contract_report(page, report_name)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1500)
    return selected


def ensure_logged_in_and_open_list(context, report_name: str = CONTRACT_REPORT_NAME):
    """打开指定报表列表页，必要时登录。返回 page。"""
    page = context.pages[0] if context.pages else context.new_page()
    open_report_page_with_login(page)
    open_report_list(page, report_name)
    return page


def collect_payment_rows_from_frame(frame, limit: int) -> list[dict]:
    js = """
    ({ pattern, fallbackPattern, limit }) => {
      const re = new RegExp(pattern, "g");
      const fallbackRe = new RegExp(fallbackPattern, "g");
      const clean = (text) => (text || "").replace(/\\s+/g, " ").trim();
      const attrText = (node) => {
        if (!node || !node.getAttribute) return "";
        const values = [
          node.getAttribute("title"),
          node.getAttribute("aria-label"),
          node.getAttribute("data-qtip"),
          node.getAttribute("data-title"),
          node.getAttribute("value"),
        ];
        return clean(values.filter(Boolean).join(" "));
      };
      const deepText = (node) => {
        if (!node) return "";
        const pieces = [node.innerText || node.textContent || "", attrText(node)];
        if (node.querySelectorAll) {
          for (const child of Array.from(node.querySelectorAll("td, th, div, span, a")).slice(0, 80)) {
            pieces.push(child.innerText || child.textContent || "", attrText(child));
          }
        }
        return clean(pieces.filter(Boolean).join(" "));
      };
      const seen = new Set();
      const rows = [];
      const push = (text, sourceIndex, sourceKind, sourceSelector = "") => {
        const rowText = clean(text);
        if (!rowText) return;
        const fullMatches = rowText.match(re) || [];
        const fallbackMatches = rowText.match(fallbackRe) || [];
        const matches = fullMatches.length ? fullMatches : fallbackMatches;
        for (const ref of matches) {
          if (seen.has(ref)) continue;
          seen.add(ref);
          rows.push({ ref, row_text: rowText, source_index: sourceIndex, source_kind: sourceKind, source_selector: sourceSelector });
          break;
        }
      };
      const pushGenerated = (text, sourceIndex, sourceKind, sourceSelector = "") => {
        const rowText = clean(text);
        if (!rowText || seen.has(rowText)) return;
        if (!/\\d{4}-\\d{2}-\\d{2}/.test(rowText)) return;
        if (!/(银行转账|申请金额|付款方式|收款单位|开户银行|银行账户|请款|报销|付款)/.test(rowText)) return;
        seen.add(rowText);
        rows.push({
          ref: "",
          row_text: rowText,
          source_index: sourceIndex,
          source_kind: sourceKind,
          source_selector: sourceSelector,
          ref_is_generated: true,
        });
      };

      Array.from(document.querySelectorAll("tr")).forEach((row, index) => {
        push(deepText(row), index, "tr");
      });

      if (rows.length < limit) {
        Array.from(document.querySelectorAll("tr.v-easy-table-row")).forEach((row, index) => {
          pushGenerated(deepText(row), index, "data-row", "tr.v-easy-table-row");
        });
      }

      if (rows.length < limit) {
        Array.from(document.querySelectorAll("td, div, span, a")).forEach((node, index) => {
          push(deepText(node), index, "text");
        });
      }
      return rows.slice(0, limit);
    }
    """
    try:
        rows = frame.evaluate(js, {"pattern": FLOW_PATTERN, "fallbackPattern": FALLBACK_FLOW_PATTERN, "limit": limit})
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    result = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        ref = str(row.get("ref") or "").strip()
        row_text = str(row.get("row_text") or "").strip()
        ref_is_generated = bool(row.get("ref_is_generated"))
        if not ref and row_text:
            ref = f"ROW-{row_fingerprint(row_text)[:16]}"
            ref_is_generated = True
        if not ref:
            continue
        result.append(
            {
                "ref": ref,
                "row_text": row_text,
                "source_index": row.get("source_index"),
                "source_kind": row.get("source_kind") or "",
                "source_selector": row.get("source_selector") or "",
                "ref_is_generated": ref_is_generated,
                "position": index,
            }
        )
    return result


def frame_body_text(frame) -> str:
    try:
        return str(
            frame.evaluate(
                "() => (document.body && (document.body.innerText || document.body.textContent) || '').replace(/\\s+/g, ' ').trim()"
            )
            or ""
        ).strip()
    except Exception:
        return ""


def is_about_blank_url(url) -> bool:
    """True for an empty / about:blank 顶层 URL（含 about:blank#... / about:blank?... 形式）。"""
    text = str(url or "").strip().lower()
    if not text:
        return True
    return text == "about:blank" or text.startswith("about:blank#") or text.startswith("about:blank?")


def page_url_safe(page) -> str:
    try:
        return page.url or ""
    except Exception:
        return ""


def looks_like_top_level_blank(page) -> bool:
    """检测顶层窗口停在 about:blank 且正文基本为空的白屏。

    用于列表行/详情页等待阶段：如果 OA 始终没从 about:blank 跳走（连 report frame
    都没有），就当成白屏，让 run() 关闭 context 并按 M3_BROWSER_RESTARTS 重开，而不是
    干等外层 M3_EXTRACT_TIMEOUT_SECONDS。调用方必须配合 TOP_BLANK_GRACE_MS 小阈值 +
    连续命中，避免误伤 reset_page_before_retry 主动 goto about:blank 的瞬间。
    """
    if not is_about_blank_url(page_url_safe(page)):
        return False
    return len(frame_body_text(page)) < 20


def looks_like_report_white_screen(page) -> bool:
    """Detect the common OA report blank iframe case quickly."""
    report_frames = []
    try:
        frames = list(page.frames)
    except Exception:
        return False
    for frame in frames:
        url = ""
        try:
            url = frame.url or ""
        except Exception:
            url = ""
        if "report4Result.do" in url or "/rest/cap4/report/" in url:
            report_frames.append(frame)
    if not report_frames:
        return False

    meaningful_frames = 0
    for frame in report_frames:
        text = frame_body_text(frame)
        if any(token in text for token in ("流水号", "申请日期", "申请金额", "付款方式", "银行账户", "合同名称", "标题")):
            meaningful_frames += 1
        elif len(text) >= 120:
            meaningful_frames += 1
    return meaningful_frames == 0


DETAIL_READY_MARKERS = (
    "申请人",
    "申请金额",
    "付款方式",
    "收款单位",
    "付款单位",
    "开户银行",
    "银行账户",
    "合同编号",
    "单据编号",
)


def looks_like_detail_white_screen(detail) -> bool:
    """Detect a detail popup that opened but did not render the payment form."""
    texts: list[str] = []
    try:
        frames = [detail] + list(detail.frames)
    except Exception:
        frames = [detail]

    for frame in frames:
        text = frame_body_text(frame)
        if text:
            texts.append(text)

    combined = " ".join(texts)
    if not combined:
        return True
    if any(marker in combined for marker in DETAIL_READY_MARKERS):
        return False
    return len(combined) < 180


def find_frame_with_payment_rows(page, limit: int) -> tuple[object, list[dict]]:
    """遍历所有 frame，找到包含付款列表行的 frame，并返回最新 N 条。"""
    deadline_ms = 12000
    waited = 0
    step = 500
    blank_hits = 0
    top_blank_hits = 0
    while waited < deadline_ms:
        for fr in page.frames:
            rows = collect_payment_rows_from_frame(fr, limit)
            if rows:
                return fr, rows
        if waited >= TOP_BLANK_GRACE_MS and looks_like_top_level_blank(page):
            top_blank_hits += 1
            if top_blank_hits >= 2:
                raise M3WhiteScreenError(
                    f"列表等待阶段顶层页面仍停在空白页(url={page_url_safe(page)})，"
                    "关闭浏览器并立即重开"
                )
        else:
            top_blank_hits = 0
        if waited >= 1500 and looks_like_report_white_screen(page):
            blank_hits += 1
            if blank_hits >= 2:
                raise M3WhiteScreenError("检测到 OA 报表白屏，关闭浏览器并立即重开")
        else:
            blank_hits = 0
        page.wait_for_timeout(step)
        waited += step
    raise RuntimeError("未在任何 frame 中找到付款列表行")


def find_frame_with_rows(page):
    """兼容旧调用：返回第一条付款流水所在 frame 和 locator。"""
    fr, rows = find_frame_with_payment_rows(page, 1)
    return fr, visible_row_ref_locator(fr, rows[0]["ref"])


def visible_row_ref_locator(frame, ref: str):
    ref_xpath = xpath_literal(ref)
    locators = [
        frame.locator(f"text={ref}"),
        frame.locator(f"text=/{re.escape(ref)}/"),
        frame.locator(
            "xpath=//*["
            f"contains(@title, {ref_xpath}) or "
            f"contains(@aria-label, {ref_xpath}) or "
            f"contains(@data-qtip, {ref_xpath}) or "
            f"contains(@data-title, {ref_xpath})"
            "]"
        ),
    ]
    for locator in locators:
        try:
            count = min(locator.count(), 30)
        except Exception:
            count = 0
        for index in range(count):
            item = locator.nth(index)
            try:
                if item.is_visible(timeout=500):
                    return item
            except Exception:
                continue
    raise RuntimeError(f"未找到可点击的合同付款列表行: {ref}")


def wait_detail_ready(detail) -> None:
    detail.wait_for_load_state("domcontentloaded")
    # 等表格渲染完：扫描所有 frame 直到出现『申请人』文字
    deadline = 25000
    waited = 0
    step = 500
    top_blank_hits = 0
    while waited < deadline:
        for fr in [detail] + list(detail.frames):
            try:
                if fr.locator('text=申请人').first.count() > 0:
                    detail.wait_for_timeout(800)
                    return
            except Exception:
                continue
        # 新开的详情页本身停在 about:blank/空白表单时快速失败，让 run() 重开浏览器，
        # 不等满 25 秒；TOP_BLANK_GRACE_MS 小阈值 + 连续命中避免误伤刚弹出的瞬间。
        if waited >= TOP_BLANK_GRACE_MS and looks_like_top_level_blank(detail):
            top_blank_hits += 1
            if top_blank_hits >= 2:
                raise M3WhiteScreenError(
                    f"付款详情页停在空白页(url={page_url_safe(detail)})，关闭浏览器并重开"
                )
        else:
            top_blank_hits = 0
        detail.wait_for_timeout(step)
        waited += step
    if looks_like_detail_white_screen(detail):
        raise M3WhiteScreenError(
            f"付款详情页白屏或表单未渲染(url={page_url_safe(detail)})，关闭浏览器并重开"
        )
    print("警告：等待详情页『申请人』超时，但页面已有其它表单字段，继续尝试抽取")


def click_row_open_detail(context, page, frame, row: dict, screenshot_dir: str) -> tuple[object, str]:
    """点击指定付款行，等待新详情页打开，并返回详情页和该行列表截图。"""
    ref = str(row.get("ref") or "").strip()
    try:
        page.bring_to_front()
    except Exception:
        pass
    row_element = None
    if row.get("ref_is_generated"):
        selector = str(row.get("source_selector") or "tr.v-easy-table-row")
        try:
            row_element = frame.locator(selector).nth(int(row.get("source_index") or 0))
            row_element.scroll_into_view_if_needed()
        except Exception:
            row_element = None
    ref_cell = row_element or visible_row_ref_locator(frame, ref)
    ref_cell.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    list_screenshot = save_screenshot(
        page,
        screenshot_dir,
        f"01_第{int(row.get('position') or 0):02d}条_{safe_filename(ref)}_列表定位",
    )
    # 首选：点击该单元格所在的整行 <tr>，若不存在再回落到单元格本身。
    row_handle = ref_cell.evaluate_handle("el => el.closest('tr') || el")
    try:
        with context.expect_page(timeout=15000) as new_info:
            try:
                row_handle.as_element().click()
            except Exception:
                ref_cell.click()
        detail = new_info.value
    except Exception:
        # 兜底：双击行
        with context.expect_page(timeout=15000) as new_info:
            row_element = row_handle.as_element()
            if row_element is not None:
                row_element.dblclick()
            else:
                ref_cell.dblclick()
        detail = new_info.value
    wait_detail_ready(detail)
    return detail, list_screenshot


def click_first_row_open_detail(context, page):
    """兼容旧调用：点击列表第一条数据行，等待新页面打开并返回。"""
    fr, rows = find_frame_with_payment_rows(page, 1)
    detail, _ = click_row_open_detail(context, page, fr, rows[0], "")
    return detail


def extract_payment_row(context, page, frame, row: dict, screenshot_dir: str) -> dict:
    """打开一条付款详情，抽取并完成预校验；不在这里写银行队列。"""
    detail = None
    ref = str(row.get("ref") or "").strip()
    position = int(row.get("position") or 0)
    try:
        detail, list_screenshot = click_row_open_detail(context, page, frame, row, screenshot_dir)
        print(f"已打开第 {position} 条详情页 [{ref}]: {detail.url}")

        data = extract_fields(detail)
        apply_row_ref_policy(data, row)
        data["M3列表行文本"] = str(row.get("row_text") or "")
        data["M3列表序号"] = position
        data["M3报表名称"] = row.get("report_name") or ""
        data["M3报表入口"] = row.get("requested_report_name") or row.get("report_name") or ""
        data["M3报表序号"] = row.get("report_index") or ""
        detail_screenshot = save_screenshot(
            detail,
            screenshot_dir,
            f"02_第{position:02d}条_{safe_filename(data.get('单据编号') or ref)}_详情抽取后",
        )
        attach_m3_screenshot_info_or_abort(data, list_screenshot, detail_screenshot)
        if should_click_contract_link(data):
            data.update(click_contract_link_if_present(context, detail, data, screenshot_dir, position, ref))
        else:
            print(f"跳过蓝色合同链接：{data.get('M3报表名称') or data.get('M3报表入口') or '未知报表'}")

        if data.get("M3跳过制单"):
            print(f"已跳过制单记录 [{data.get('单据编号') or ref}]: {data.get('M3跳过原因')}")
            return {
                "row": row,
                "data": data,
                "bank_form": None,
                "skip_bank_queue": True,
            }

        bank_form = build_bank_form(data)
        # 单条 fail-closed 隔离：对公请款工资/社保/公积金记录若账号/开户行本身是
        # 0/附件/模板说明，优先按“业务正常不可制单”隔离；不要先落到“未识别付款银行”
        # 去要求财务补路由规则。
        if (
            not str(bank_form.get("付款银行") or "").strip()
            and has_unvoucherable_corporate_request_fields(data)
        ):
            data["M3跳过制单"] = True
            data["M3跳过原因"] = (
                "对公请款社保/公积金/工资类记录，M3 明细中的开户银行/银行账户为占位值 0 "
                "或指向「招行代发工资导入模板/附件/模板」等说明文本（非可制单账号），"
                "按 fail-closed 单条隔离；不写银行队列，需人工确认处理方式"
            )
            print(f"已隔离对公请款社保/公积金/工资类记录 [{data.get('单据编号') or ref}]: {data.get('M3跳过原因')}")
            return {
                "row": row,
                "data": data,
                "bank_form": None,
                "skip_bank_queue": True,
            }

        # 单条 fail-closed 隔离：付款单位无法归一到任何已维护的付款银行时，
        # 既不写银行队列、也绝不默认猜银行；该条隔离到 runtime/skipped 并标记 state，
        # 但不再让整轮抛错中断——确保同轮其它已正确识别的记录仍能写出/分发。
        # 这里仍是 fail-closed：被隔离的记录永远不会进入任何银行制单队列。
        # （注意：此判断只针对“未识别付款银行”；付款银行已识别的记录仍照常进入
        #   validate_oa_json_precheck，金额/账号/截图等数据质量问题仍按既有策略整轮 fail-closed。）
        if not str(bank_form.get("付款银行") or "").strip():
            data["M3跳过制单"] = True
            failure_reason = str(data.get("M3付款银行识别失败原因") or "").strip()
            data["M3跳过原因"] = (
                (failure_reason + "；" if failure_reason else "未识别付款银行；")
                + "按 fail-closed 单条隔离（不写银行队列、不默认猜银行）；"
                "需人工核实付款单位→付款银行/付款账户规则后再重跑该条"
            )
            print(f"已隔离不可路由记录 [{data.get('单据编号') or ref}]: {data.get('M3跳过原因')}")
            return {
                "row": row,
                "data": data,
                "bank_form": None,
                "skip_bank_queue": True,
            }
        try:
            validate_oa_json_precheck(data, bank_form)
        except M3PrecheckError as exc:
            # 严格的单条隔离：仅对「对公请款 社保/公积金/工资/个税」且 M3 明细中
            # 开户银行/银行账户本身就是占位值 0 的记录生效。普通合同付款/日常报销/
            # 普通对公请款的账号/金额/截图/白名单等问题仍走整轮 fail-closed。
            if should_isolate_unvoucherable_corporate_request(data, bank_form, exc):
                data["M3跳过制单"] = True
                data["M3跳过原因"] = (
                    "对公请款社保/公积金/工资类记录，M3 明细中的开户银行/银行账户为占位值 0 "
                    "或指向「招行代发工资导入模板/附件/模板」等说明文本（非可制单账号），"
                    "按 fail-closed 单条隔离；不写银行队列，需人工确认处理方式"
                )
                print(f"已隔离对公请款社保/公积金/工资类记录 [{data.get('单据编号') or ref}]: {data.get('M3跳过原因')}")
                return {
                    "row": row,
                    "data": data,
                    "bank_form": None,
                    "skip_bank_queue": True,
                }
            if not SKIP_UNROUTABLE_RECORDS:
                raise
            data["M3跳过制单"] = True
            data["M3跳过原因"] = f"OA/M3预检失败，测试配置 M3_SKIP_UNROUTABLE_RECORDS=1 已跳过：{exc}"
            print(f"已跳过制单记录 [{data.get('单据编号') or ref}]: {data.get('M3跳过原因')}")
            return {
                "row": row,
                "data": data,
                "bank_form": None,
                "skip_bank_queue": True,
            }
        return {
            "row": row,
            "data": data,
            "bank_form": bank_form,
        }
    finally:
        if detail is not None:
            try:
                detail.close()
            except Exception:
                pass


def find_contract_link_locator(detail_page, data: dict):
    """Find the clickable contract link locator on the detail page or one of its frames."""
    link_text = first_non_empty(
        data.get("合同名称链接文本"),
        data.get("采购合同名称"),
        data.get("合同名称"),
    )
    targets = [detail_page] + [fr for fr in detail_page.frames if fr != detail_page.main_frame]
    for tgt in targets:
        locators = []
        if link_text:
            locators.extend(
                [
                    tgt.locator('a, [role="link"], [onclick]').filter(has_text=link_text),
                    tgt.get_by_text(link_text, exact=True),
                ]
            )
        locators.append(tgt.locator('a:has-text("采购合同"), [role="link"]:has-text("采购合同"), [onclick]:has-text("采购合同")'))

        for locator in locators:
            try:
                count = min(locator.count(), 10)
            except Exception:
                count = 0
            for index in range(count):
                item = locator.nth(index)
                try:
                    if item.is_visible(timeout=500):
                        return item
                except Exception:
                    continue
    return None


def payment_order_no_from_text(text: str) -> str:
    match = re.search(r"支付单号\s*[：:]\s*([A-Za-z0-9_-]+)", str(text or ""))
    if match:
        return match.group(1).strip()
    return ""


def extract_application_description_from_page(page) -> str:
    """Read the linked contract page's 申请说明 field."""
    js = """
    () => {
      const norm = (s) => (s || "").replace(/\\s+/g, "").trim();
      const clean = (s) => (s || "").replace(/\\s+/g, " ").trim();
      const valueOf = (el) => clean(el ? (el.innerText || el.textContent || "") : "");
      const nodes = Array.from(document.querySelectorAll("td, th, div, span, label"));
      for (const node of nodes) {
        const nodeText = valueOf(node);
        const nodeNorm = norm(nodeText);
        for (const sep of ["：", ":"]) {
          const prefix = "申请说明" + sep;
          if (nodeNorm.startsWith(prefix)) {
            return clean(nodeText.slice(nodeText.indexOf(sep) + 1));
          }
        }
        if (nodeNorm === "申请说明") {
          const cell = node.closest("td,th");
          const tr = node.closest("tr");
          if (tr) {
            const cells = Array.from(tr.children).filter((el) => /^(TD|TH)$/i.test(el.tagName));
            const idx = cells.indexOf(cell);
            if (idx >= 0 && idx + 1 < cells.length) {
              const value = valueOf(cells[idx + 1]);
              if (value && norm(value) !== "申请说明") return value;
            }
          }
          let sib = node.nextElementSibling;
          while (sib) {
            const value = valueOf(sib);
            if (value && norm(value) !== "申请说明") return value;
            sib = sib.nextElementSibling;
          }
        }
      }
      return "";
    }
    """
    try:
        targets = [page] + [fr for fr in page.frames if fr != page.main_frame]
    except Exception:
        return ""
    for tgt in targets:
        try:
            value = str(tgt.evaluate(js) or "").strip()
        except Exception:
            continue
        if value:
            return value
    return ""


def wait_for_application_description(page, timeout_ms: int) -> str:
    deadline = time.time() + max(0, timeout_ms) / 1000
    description = extract_application_description_from_page(page)
    while not description and time.time() < deadline:
        try:
            page.wait_for_timeout(500)
        except Exception:
            return ""
        description = extract_application_description_from_page(page)
    return description


def should_click_contract_link(data: dict) -> bool:
    report_text = normalize_text(first_non_empty(data.get("M3报表名称"), data.get("M3报表入口")))
    return "合同付款" in report_text and "凭证" in report_text


def click_contract_link_if_present(context, detail_page, data: dict, screenshot_dir: str, position: int, ref: str) -> dict:
    """Click the blue procurement contract link and record whether OA opens it."""
    if not CLICK_CONTRACT_LINK:
        return {}

    link_text = first_non_empty(data.get("合同名称链接文本"), data.get("采购合同名称"), data.get("合同名称"))
    if not link_text:
        return {"采购合同打开状态": "未找到合同名称"}

    locator = find_contract_link_locator(detail_page, data)
    if locator is None:
        return {"采购合同打开状态": "未找到可点击采购合同链接"}

    result = {
        "合同名称链接文本": link_text,
        "采购合同名称": link_text,
        "采购合同打开状态": "点击失败",
        "采购合同打开方式": "",
        "采购合同打开URL": "",
        "采购合同打开标题": "",
        "采购合同打开截图": "",
    }
    opened_page = None
    page_to_capture = detail_page
    try:
        with context.expect_page(timeout=CONTRACT_LINK_OPEN_TIMEOUT_MS) as new_info:
            locator.click()
        opened_page = new_info.value
        page_to_capture = opened_page
        result["采购合同打开状态"] = "已打开"
        result["采购合同打开方式"] = "新页签"
        try:
            opened_page.wait_for_load_state("domcontentloaded", timeout=CONTRACT_LINK_OPEN_TIMEOUT_MS)
        except Exception:
            pass
        if CONTRACT_LINK_READY_WAIT_MS:
            try:
                opened_page.wait_for_timeout(CONTRACT_LINK_READY_WAIT_MS)
            except Exception:
                pass
    except PlaywrightTimeout:
        try:
            detail_page.wait_for_timeout(CONTRACT_LINK_READY_WAIT_MS or 1000)
        except Exception:
            pass
        page_to_capture = detail_page
        result["采购合同打开状态"] = "已点击"
        result["采购合同打开方式"] = "当前页或弹层"
    except Exception as exc:
        result["采购合同打开状态"] = f"点击失败: {exc}"
        return result

    try:
        result["采购合同打开URL"] = page_to_capture.url
        if result["采购合同打开URL"]:
            result.setdefault("合同名称链接", result["采购合同打开URL"])
            result.setdefault("采购合同链接", result["采购合同打开URL"])
    except Exception:
        pass
    try:
        result["采购合同打开标题"] = page_to_capture.title(timeout=2000)
    except Exception:
        pass

    description = wait_for_application_description(page_to_capture, CONTRACT_LINK_READY_WAIT_MS)
    if description:
        result["采购合同申请说明"] = description
        payment_order_no = payment_order_no_from_text(description)
        if payment_order_no or "支付单号" in description:
            result["采购合同支付单号"] = payment_order_no or "已检测到支付单号"
            result["M3跳过制单"] = True
            result["M3跳过原因"] = f"采购合同申请说明包含支付单号：{result['采购合同支付单号']}"

    screenshot = save_screenshot(
        page_to_capture,
        screenshot_dir,
        f"03_第{position:02d}条_{safe_filename(ref or data.get('单据编号') or link_text)}_采购合同打开后",
    )
    if screenshot:
        result["采购合同打开截图"] = screenshot

    print(
        "采购合同链接点击结果:",
        result.get("采购合同打开状态"),
        result.get("采购合同打开方式"),
        result.get("采购合同打开标题") or result.get("采购合同打开URL"),
    )

    if opened_page is not None:
        try:
            opened_page.close()
        except Exception:
            pass
    return {key: value for key, value in result.items() if str(value or "").strip()}


def extract_contract_link_fields(detail_page, contract_name: str = "") -> dict:
    """Extract the clickable contract-name link shown as blue text in OA detail pages."""
    js = """
    (contractName) => {
      const norm = (s) => (s || "").replace(/\\s+/g, "").trim();
      const clean = (s) => (s || "").replace(/\\s+/g, " ").trim();
      const contractNorm = norm(contractName);
      const hasClick = (el) => {
        if (!el) return false;
        const rawHref = el.getAttribute && el.getAttribute("href");
        return Boolean(
          (rawHref && rawHref.trim() && rawHref.trim() !== "#") ||
          (el.getAttribute && el.getAttribute("onclick")) ||
          (el.tagName || "").toUpperCase() === "A" ||
          (el.getAttribute && el.getAttribute("role") === "link")
        );
      };
      const absoluteHref = (el, rawHref) => {
        if (!rawHref) return "";
        const raw = rawHref.trim();
        if (!raw || raw === "#" || /^javascript:/i.test(raw)) return "";
        if (raw.startsWith("/") && window.location.origin && window.location.origin !== "null") {
          return window.location.origin + raw;
        }
        try {
          const base = document.baseURI && !/^about:/i.test(document.baseURI)
            ? document.baseURI
            : window.location.href;
          return new URL(raw, base).href;
        } catch (_) {
          return "";
        }
      };
      const linkInfo = (el) => {
        if (!el) return null;
        const text = clean(el.innerText || el.textContent || el.getAttribute("title") || "");
        if (!text) return null;
        const rawHref = clean(el.getAttribute ? (el.getAttribute("href") || "") : "");
        const href = absoluteHref(el, rawHref) || (
          el.href && !/^javascript:/i.test(String(el.href)) ? String(el.href) : ""
        );
        return {
          text,
          href,
          raw_href: rawHref,
          onclick: clean(el.getAttribute ? (el.getAttribute("onclick") || "") : ""),
        };
      };
      const isRelevant = (info, requireStrongMatch = false) => {
        if (!info || !info.text) return false;
        const textNorm = norm(info.text);
        if (!textNorm || textNorm === "合同名称") return false;
        if (contractNorm && (textNorm.includes(contractNorm) || contractNorm.includes(textNorm))) return true;
        if (textNorm.includes("采购合同")) return true;
        return !requireStrongMatch;
      };
      const candidateLinks = (container) => {
        if (!container) return [];
        const links = [];
        if (hasClick(container)) links.push(container);
        if (container.querySelectorAll) {
          links.push(...Array.from(container.querySelectorAll('a, [role="link"], [onclick]')));
        }
        return links;
      };
      const pushAdjacentContainers = (node, containers) => {
        const cell = node.closest ? node.closest("td,th") : null;
        const tr = node.closest ? node.closest("tr") : null;
        if (tr) {
          const cells = Array.from(tr.children).filter((el) => /^(TD|TH)$/i.test(el.tagName));
          const idx = cells.indexOf(cell);
          if (idx >= 0 && idx + 1 < cells.length) containers.push(cells[idx + 1]);
        }
        let sib = node.nextElementSibling;
        while (sib) {
          containers.push(sib);
          sib = sib.nextElementSibling;
        }
      };

      const nodes = Array.from(document.querySelectorAll("td, th, div, span, label"));
      const containers = [];
      for (const node of nodes) {
        const text = clean(node.innerText || node.textContent || "");
        const nodeNorm = norm(text);
        if (nodeNorm === "合同名称" || nodeNorm.startsWith("合同名称:") || nodeNorm.startsWith("合同名称：")) {
          containers.push(node);
          pushAdjacentContainers(node, containers);
        }
      }

      for (const container of containers) {
        for (const link of candidateLinks(container)) {
          const info = linkInfo(link);
          if (isRelevant(info)) return info;
        }
      }

      for (const link of Array.from(document.querySelectorAll('a, [role="link"], [onclick]'))) {
        const info = linkInfo(link);
        if (isRelevant(info, true)) return info;
      }
      return null;
    }
    """
    targets = [detail_page] + [fr for fr in detail_page.frames if fr != detail_page.main_frame]
    for tgt in targets:
        try:
            info = tgt.evaluate(js, contract_name or "")
        except Exception:
            continue
        if not isinstance(info, dict):
            continue

        link_text = str(info.get("text") or "").strip()
        href = str(info.get("href") or "").strip()
        raw_href = str(info.get("raw_href") or "").strip()
        onclick = str(info.get("onclick") or "").strip()
        if not link_text:
            continue

        fields = {"合同名称链接文本": link_text}
        if href:
            fields["合同名称链接"] = href
        if raw_href:
            fields["合同名称链接原始href"] = raw_href
        if onclick:
            fields["合同名称链接onclick"] = onclick
        if "采购合同" in link_text or "采购合同" in str(contract_name or ""):
            fields["采购合同名称"] = link_text
            if href:
                fields["采购合同链接"] = href
        return fields
    return {}


def extract_fields(detail_page) -> dict:
    """在详情页里按标签抓取字段值。"""
    result: dict = {}

    # 单据编号: 标题旁，遍历所有 frame 找带 -付- 的编号
    targets_for_bill = [detail_page] + list(detail_page.frames)
    for tgt in targets_for_bill:
        try:
            bill_loc = tgt.locator(f"text=/{BILL_PATTERN}/").first
            if bill_loc.count() > 0:
                result["单据编号"] = bill_loc.inner_text(timeout=2000).strip()
                break
        except Exception:
            continue

    # 用 JS 在 DOM 中扫描表格/普通节点：支持「标签右侧单元格」「标签同格冒号值」
    # 以及值放在 input/textarea/select 的 OA 表单结构。
    js = """
    (labels) => {
      const norm = (s) => (s || "").replace(/\\s+/g, "").trim();
      const clean = (s) => (s || "").replace(/\\s+/g, " ").trim();
      const valueOf = (el) => {
        if (!el) return "";
        const direct = clean(el.innerText || el.textContent || "");
        const controls = Array.from(el.querySelectorAll ? el.querySelectorAll('input, textarea, select') : []);
        const controlValues = controls.map((node) => {
          if (node.tagName === "SELECT") {
            const opt = node.options && node.selectedIndex >= 0 ? node.options[node.selectedIndex] : null;
            return clean((opt && opt.textContent) || node.value || "");
          }
          return clean(node.value || node.getAttribute("value") || "");
        }).filter(Boolean);
        return clean([direct, ...controlValues].filter(Boolean).join(" "));
      };
      const pushValue = (out, lab, value) => {
        const v = clean(value);
        if (v && norm(v) !== lab && !(lab in out)) out[lab] = v;
      };
      const out = {};
      const nodes = Array.from(document.querySelectorAll('td, th, div, span, label'));
      for (const lab of labels) {
        for (let i = 0; i < nodes.length; i++) {
          const node = nodes[i];
          const nodeText = valueOf(node);
          const nodeNorm = norm(nodeText);

          for (const sep of ["：", ":"]) {
            const prefix = lab + sep;
            if (nodeNorm.startsWith(prefix)) {
              pushValue(out, lab, nodeText.slice(nodeText.indexOf(sep) + 1));
              if (lab in out) break;
            }
          }
          if (lab in out) break;

          if (nodeNorm === lab) {
            // 优先：同 tr 下下一个 td/th
            const cell = node.closest('td,th');
            const tr = node.closest('tr');
            if (tr) {
              const cells = Array.from(tr.children).filter((el) => /^(TD|TH)$/i.test(el.tagName));
              const idx = cells.indexOf(cell);
              if (idx >= 0 && idx + 1 < cells.length) {
                pushValue(out, lab, valueOf(cells[idx + 1]));
                if (lab in out) break;
              }
            }

            // 兜底：普通 div/span/label 布局里取下一个兄弟节点
            let sib = node.nextElementSibling;
            while (sib) {
              const v = valueOf(sib);
              if (v && norm(v) !== lab) {
                pushValue(out, lab, v);
                break;
              }
              sib = sib.nextElementSibling;
            }
            if (lab in out) break;
          }
        }
      }
      return out;
    }
    """
    # 在主页面 + 所有子 frame 中扫描
    targets = [detail_page] + [fr for fr in detail_page.frames if fr != detail_page.main_frame]
    for tgt in targets:
        try:
            scanned = tgt.evaluate(js, WANT_LABELS)
            for k, v in scanned.items():
                if v and k not in result:
                    result[k] = v
        except Exception:
            continue

    canonicalize_extracted_fields(result)

    for k, v in extract_contract_link_fields(detail_page, result.get("合同名称", "")).items():
        if v and k not in result:
            result[k] = v

    canonicalize_extracted_fields(result)
    return result


def run_extract_once(p, screenshot_dir: str) -> None:
    context = None
    try:
        mark_browser_profile_clean()
        context = p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=False,
            viewport={"width": 1366, "height": 800},
            args=chromium_launch_args(),
        )
        context.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
        page = context.pages[0] if context.pages else context.new_page()
        open_report_page_with_login(page)
        state = load_extract_state()
        existing_refs = known_queue_refs()
        queue_bank_fingerprints = known_queue_bank_fingerprints()
        all_observed_rows: list[dict] = []
        scan_batch_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
        extracted_results: list[dict] = []
        total_skipped_existing = 0

        print("本轮监控报表顺序: " + " -> ".join(REPORT_NAMES))
        for report_index, requested_report_name in enumerate(REPORT_NAMES, start=1):
            print(f"\n=== 扫描报表 {report_index}/{len(REPORT_NAMES)}: {requested_report_name} ===")
            selected_report_name = open_report_list(page, requested_report_name)
            print(f"已进入{selected_report_name}列表。")
            save_screenshot(
                page,
                screenshot_dir,
                f"00_{report_index:02d}_{safe_filename(selected_report_name)}列表概览",
            )

            frame, rows = find_frame_with_payment_rows(page, SCAN_ROW_LIMIT)
            for row in rows:
                row["report_name"] = selected_report_name
                row["requested_report_name"] = requested_report_name
                row["report_index"] = report_index
            all_observed_rows.extend(rows)
            print(f"已扫描{selected_report_name}列表前 {len(rows)} 条付款记录（上限 {SCAN_ROW_LIMIT}）。")

            candidate_rows: list[dict] = []
            skipped_rows: list[dict] = []
            for row in rows:
                should_extract, reason = should_extract_row(row, state, existing_refs)
                row["scan_reason"] = reason
                if should_extract:
                    candidate_rows.append(row)
                else:
                    skipped_rows.append(row)

            priority_rows, recheck_run, recheck_deferred = partition_periodic_recheck(
                candidate_rows, state
            )
            selected_rows = priority_rows + recheck_run

            if skipped_rows:
                total_skipped_existing += len(skipped_rows)
                print(f"{selected_report_name} 跳过 {len(skipped_rows)} 条已处理且列表未变的记录。")
            recheck_total = len(recheck_run) + len(recheck_deferred)
            if recheck_total:
                budget_label = (
                    "不限"
                    if DETAIL_RECHECK_MAX_PER_ROUND <= 0
                    else str(DETAIL_RECHECK_MAX_PER_ROUND)
                )
                print(
                    f"{selected_report_name} 本轮定期复查候选 {recheck_total} 条，"
                    f"按 M3_DETAIL_RECHECK_MAX_PER_ROUND={budget_label} 处理 "
                    f"{len(recheck_run)} 条，暂缓 {len(recheck_deferred)} 条。"
                )
            if not selected_rows:
                print(f"{selected_report_name} 未发现新增/变更记录。")
                continue

            print(
                f"{selected_report_name} 需要逐条点进详情页抽取 {len(selected_rows)} 条"
                f"（其中新增/变更/强制 {len(priority_rows)} 条，定期复查 {len(recheck_run)} 条）；"
                "外层列表只用于定位和排序。"
            )
            for row in selected_rows:
                print(
                    f"\n=== 打开报表[{row.get('report_name')}]第 {row.get('position')} 条付款详情 "
                    f"[{row.get('ref')}]：{row.get('scan_reason')} ==="
                )
                try:
                    result = extract_payment_row(context, page, frame, row, screenshot_dir)
                    if detail_result_is_unchanged(state, row, result, queue_bank_fingerprints):
                        print(
                            f"详情复查未发现制单字段变化 [{row.get('ref')}]，"
                            "仅更新检查时间，不重写银行队列。"
                        )
                        continue
                    mark_detail_checked_state(state, row, result)
                    extracted_results.append(result)
                except Exception as exc:
                    if not CONTINUE_ON_ROW_ERROR:
                        raise
                    error_name = (
                        f"99_第{int(row.get('position') or 0):02d}条_"
                        f"{safe_filename(str(row.get('ref') or 'UNKNOWN'))}_抽取异常"
                    )
                    error_screenshot = save_screenshot(page, screenshot_dir, error_name)
                    print(f"[单条失败] {row.get('ref')} 已记录并继续: {type(exc).__name__}: {exc}")
                    if error_screenshot:
                        print(f"[单条失败] 现场截图: {error_screenshot}")
                    result = build_row_error_result(row, exc, error_screenshot)
                    mark_detail_checked_state(state, row, result)
                    extracted_results.append(result)
                    try:
                        selected_report_name = open_report_list(page, requested_report_name)
                        frame, _ = find_frame_with_payment_rows(page, SCAN_ROW_LIMIT)
                    except Exception as recover_exc:
                        print(f"[单条失败] 回到列表失败，整轮停止: {type(recover_exc).__name__}: {recover_exc}")
                        raise

        if not extracted_results:
            update_observed_row_state(state, all_observed_rows)
            save_extract_state(state)
            print(f"本轮未发现新增/变更付款记录，已跳过既有记录 {total_skipped_existing} 条，不写银行队列。")
            print("\n抽取完成，关闭浏览器。")
            return

        for sequence, result in enumerate(extracted_results, start=1):
            row = result["row"]
            schedule_fields = {
                "M3调度批次": scan_batch_id,
                "M3调度序号": sequence,
                "M3报表名称": row.get("report_name") or "",
                "M3报表入口": row.get("requested_report_name") or row.get("report_name") or "",
                "M3报表序号": int(row.get("report_index") or 0),
                "M3列表序号": int(row.get("position") or sequence),
                "M3调度排序": f"{scan_batch_id}:{sequence:04d}",
            }
            result["data"].update(schedule_fields)
            if isinstance(result.get("bank_form"), dict):
                result["bank_form"].update(schedule_fields)
            if result.get("skip_bank_queue") or result["data"].get("M3跳过制单"):
                mark_skipped_row_state(state, row, result["data"])

        print("\n所有新增/变更付款详情已完成预检/跳过判断，开始写入 JSON、银行队列或跳过记录。")
        all_written_paths: list[str] = []
        skipped_count = 0
        queued_count = 0
        # 列表通常是最新在前；倒序写入可让 current/latest_route 最终仍指向最新一条。
        for result in reversed(extracted_results):
            data = result["data"]
            print("\n=== 抽取结果 ===")
            print(json.dumps(data, ensure_ascii=False, indent=2))

            if result.get("skip_bank_queue") or data.get("M3跳过制单"):
                written_paths = write_skipped_outputs(data)
                skipped_count += 1
                print(f"\n已跳过银行制单队列: {data.get('M3跳过原因')}")
                all_written_paths.extend(written_paths)
                continue

            bank_form = result["bank_form"]

            out_path = os.path.join(CURRENT_DIR, "latest.json")
            write_json(out_path, data)
            print(f"\n已保存到: {out_path}")

            # 当前最新表单入口。按银行分类的稳定入口在 runtime/bank_forms/<银行>/bank_form.json。
            bank_path = os.path.join(CURRENT_DIR, "bank_form.json")
            write_json(bank_path, bank_form)
            print(f"表单字段已保存到: {bank_path}")

            payment_bank, written_paths = write_classified_outputs(data, bank_form)
            if payment_bank:
                print(f"已按付款银行 [{payment_bank}] 分类写出 JSON。")
            else:
                print(f"警告：未识别到付款银行，已写入 {BANK_FORMS_DIR}\\未识别银行。请确认 OA/M3 字段名是否已加入 PAYMENT_BANK_LABELS。")
            print("\n=== 制单表单映射 ===")
            print(json.dumps(bank_form, ensure_ascii=False, indent=2))
            all_written_paths.extend(written_paths)
            queued_count += 1

        update_observed_row_state(state, all_observed_rows)
        save_extract_state(state)
        print("\n=== 分类输出文件 ===")
        for path in list(dict.fromkeys(all_written_paths)):
            print(path)

        print(f"\n抽取完成，本轮写入 {queued_count} 条制单队列，跳过 {skipped_count} 条记录，关闭浏览器。")
    except Exception:
        error_screenshot = save_context_error_screenshot(context, screenshot_dir, "00_OA_M3抽取失败现场")
        if error_screenshot:
            print(f"OA/M3 抽取失败现场截图: {error_screenshot}")
        raise
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass


def run():
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    with sync_playwright() as p:
        screenshot_dir = create_screenshot_dir() if ENABLE_SCREENSHOTS else ""
        cleanup_old_screenshot_runs()
        last_exc = None
        for attempt in range(1, BROWSER_RESTARTS + 2):
            try:
                print(f"启动浏览器抽取尝试 {attempt}/{BROWSER_RESTARTS + 1}")
                run_extract_once(p, screenshot_dir)
                return
            except M3PrecheckError as exc:
                last_exc = exc
                print(f"OA/M3 JSON预检失败，不重启浏览器重试: {exc}")
                raise
            except M3WhiteScreenError as exc:
                last_exc = exc
                print(f"检测到 OA/M3 报表白屏 {attempt}/{BROWSER_RESTARTS + 1}: {exc}")
                if attempt <= BROWSER_RESTARTS:
                    print("立即关闭并重启浏览器重试。")
                    continue
            except Exception as exc:
                last_exc = exc
                print(f"浏览器抽取尝试 {attempt}/{BROWSER_RESTARTS + 1} 失败: {exc}")
                if attempt <= BROWSER_RESTARTS:
                    delay_seconds = NAVIGATION_BACKOFF_SECONDS[min(attempt - 1, len(NAVIGATION_BACKOFF_SECONDS) - 1)]
                    print(f"准备关闭并重启浏览器后重试，等待 {delay_seconds:g} 秒...")
                    time.sleep(delay_seconds)
        raise last_exc


if __name__ == "__main__":
    run()
