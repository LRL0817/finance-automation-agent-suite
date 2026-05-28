# -*- coding: utf-8 -*-
from __future__ import annotations

"""转账数据加载、规范化与 K 宝密码获取。"""

from .runtime import *

# ---------------- 转账数据 ----------------
def _first_transfer_value(raw: dict, field_name: str) -> tuple[object, str]:
    for key in TRANSFER_FIELD_ALIASES[field_name]:
        if key in raw:
            value = raw.get(key)
            if str(value or "").strip():
                return value, key
    return "", ""


def normalize_bank_name_for_abc(value: str) -> str:
    """
    农行单笔转账页的「收款方开户行」是银行大类下拉框，不是支行全称输入框。
    M3 的 `支行名称` 可能是完整支行名，这里收敛成页面可选的银行大类。
    """
    text = re.sub(r"\s+", "", value.strip())
    # 村镇银行在农行下拉里是具体机构名，不是一个安全的大类。
    # 例如「桂林国民村镇银行」如果压成「村镇银行」，会 fuzzy 命中别的村镇银行。
    if "村镇银行" in text and text != "村镇银行":
        return text
    for bank_name in COMMON_BANK_NAMES:
        if bank_name in text:
            return bank_name
    return text


def normalize_transfer_data(raw: dict) -> dict:
    """
    规范化并校验转账数据。支持标准字段名，也支持云链融/付款单常见别名。

    校验规则：
    - 收款账号：必填，去掉所有空白字符
    - 收款户名：必填，去首尾空格
    - 金额：必填，正数，保留两位小数
    - 收款方开户行：可空，可空时跳过该字段
    - 用途：可空，可空时跳过该字段（不会乱填）
    校验失败抛 ValueError。
    """
    if not isinstance(raw, dict):
        raise ValueError("转账数据必须是对象")

    data: dict = {}

    account_raw, account_key = _first_transfer_value(raw, F_ACCOUNT)
    account = "".join(str(account_raw or "").split())
    if not account:
        raise ValueError(f"{F_ACCOUNT} 不能为空")
    if not ACCOUNT_RE.match(account):
        raise ValueError(f"{F_ACCOUNT} 必须为 8-32 位纯数字（不接受任何分隔符或字母）")
    data[F_ACCOUNT] = account
    if account_key and account_key != F_ACCOUNT:
        log.info("[数据] %s 使用别名字段 %s", F_ACCOUNT, account_key)

    name_raw, name_key = _first_transfer_value(raw, F_NAME)
    name = str(name_raw or "").strip()
    if not name:
        raise ValueError(f"{F_NAME} 不能为空")
    data[F_NAME] = name
    if name_key and name_key != F_NAME:
        log.info("[数据] %s 使用别名字段 %s", F_NAME, name_key)

    amount_raw, amount_key = _first_transfer_value(raw, F_AMOUNT)
    amount_override = os.getenv("ABC_TRANSFER_AMOUNT_OVERRIDE", "").strip()
    if amount_override:
        log.warning("[数据] 使用 ABC_TRANSFER_AMOUNT_OVERRIDE 覆盖金额，仅用于受控测试")
        amount_raw = amount_override
        amount_key = "ABC_TRANSFER_AMOUNT_OVERRIDE"
    amount_str = str(amount_raw).strip()
    if "," in amount_str:
        if not AMOUNT_WITH_COMMAS_RE.match(amount_str):
            raise ValueError(
                f"{F_AMOUNT} 千分位格式不合法: {amount_raw!r}"
            )
        log.info("[数据] %s 已去除千分位逗号", F_AMOUNT)
        amount_str = amount_str.replace(",", "")
    if not AMOUNT_RE.match(amount_str):
        raise ValueError(
            f"{F_AMOUNT} 格式不合法（仅接受普通数字或标准千分位格式，最多两位小数，不接受科学计数/正负号）"
        )
    try:
        amount = Decimal(amount_str)
        amount_2dp = amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"{F_AMOUNT} 解析失败: {amount_raw!r}") from e
    if not amount.is_finite():
        raise ValueError(f"{F_AMOUNT} 必须是有限数字: {amount_raw!r}")
    if amount <= 0:
        raise ValueError(f"{F_AMOUNT} 必须为正数")
    if amount != amount_2dp:
        raise ValueError(f"{F_AMOUNT} 最多保留两位小数，不会自动四舍五入: {amount_raw!r}")
    data[F_AMOUNT] = format(amount_2dp, "f")
    if amount_key and amount_key != F_AMOUNT:
        log.info("[数据] %s 使用别名字段 %s", F_AMOUNT, amount_key)

    bank_raw, bank_key = _first_transfer_value(raw, F_BANK)
    bank = str(bank_raw or "").strip()
    if bank:
        normalized_bank = normalize_bank_name_for_abc(bank)
        data[F_BANK] = normalized_bank
        if bank_key and bank_key != F_BANK:
            log.info("[数据] %s 使用别名字段 %s", F_BANK, bank_key)
        if normalized_bank != bank:
            log.info("[数据] %s 从 %s 规范化为 %s", F_BANK, bank, normalized_bank)

    branch_raw, branch_key = _first_transfer_value(raw, F_BRANCH)
    branch = str(branch_raw or "").strip()
    if branch:
        data[F_BRANCH] = branch
        if branch_key and branch_key != F_BRANCH:
            log.info("[数据] %s 使用别名字段 %s", F_BRANCH, branch_key)

    purpose_raw, purpose_key = _first_transfer_value(raw, F_PURPOSE)
    purpose = str(purpose_raw or "").strip()
    if purpose:
        data[F_PURPOSE] = purpose
        if purpose_key and purpose_key != F_PURPOSE:
            log.info("[数据] %s 使用别名字段 %s", F_PURPOSE, purpose_key)

    return data


def load_transfer_data(path: str) -> dict:
    """
    读取并校验 JSON 文件中的转账数据。
    校验失败抛 ValueError；文件不存在抛 FileNotFoundError。
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"转账数据文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return normalize_transfer_data(raw)


def load_transfer_batch(path: str) -> list[dict]:
    """
    读取批量转账 JSON。支持：
      - 顶层 list
      - {"records": [...]} / {"items": [...]} / {"data": [...]}
    每条记录仍复用 normalize_transfer_data 校验；不会提交。
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"批量转账数据文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if isinstance(raw, list):
        records = raw
    elif isinstance(raw, dict):
        records = None
        for key in ("records", "items", "data"):
            value = raw.get(key)
            if isinstance(value, list):
                records = value
                break
        if records is None:
            raise ValueError("批量转账数据对象必须包含 records/items/data 数组")
    else:
        raise ValueError("批量转账数据必须是数组或包含 records/items/data 的对象")

    if not records:
        raise ValueError("批量转账数据不能为空")

    only_index_raw = (
        os.getenv("ABC_TRANSFER_BATCH_INDEX", "").strip()
        or os.getenv("TRANSFER_BATCH_INDEX", "").strip()
    )
    if only_index_raw:
        try:
            only_index = int(only_index_raw)
        except ValueError as e:
            raise ValueError(f"TRANSFER_BATCH_INDEX 必须是数字: {only_index_raw!r}") from e
        if only_index < 1 or only_index > len(records):
            raise ValueError(f"TRANSFER_BATCH_INDEX 超出范围: {only_index}，总条数 {len(records)}")
        log.warning("[批量] 仅运行第 %d/%d 条数据", only_index, len(records))
        records = [records[only_index - 1]]

    normalized: list[dict] = []
    for idx, item in enumerate(records, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"批量转账第 {idx} 条必须是对象")
        try:
            data = normalize_transfer_data(item)
        except ValueError as e:
            raise ValueError(f"批量转账第 {idx} 条校验失败: {e}") from e
        label = (
            str(item.get("业务参考号") or item.get("单据编号") or item.get("合同编号") or "")
            .strip()
        )
        source = str(item.get("来源截图") or "").strip()
        data[BATCH_LABEL_KEY] = label or f"第 {idx} 条"
        data[BATCH_SOURCE_KEY] = source
        normalized.append(data)
    return normalized


def load_configured_transfer_batch() -> Optional[list[dict]]:
    batch_path = os.getenv("TRANSFER_BATCH_PATH", "").strip()
    if not batch_path:
        return None
    log.info("[批量] 使用 TRANSFER_BATCH_PATH: %s", batch_path)
    return load_transfer_batch(batch_path)


def load_transfer_data_from_env_fields() -> Optional[dict]:
    raw: dict = {}
    used: list[str] = []
    for field_name, env_names in TRANSFER_ENV_FIELD_NAMES.items():
        for env_name in env_names:
            value = os.getenv(env_name, "").strip()
            if value:
                raw[field_name] = value
                used.append(env_name)
                break
    if not raw:
        return None
    log.info("[数据] 使用 .env 内联转账字段：%s", ", ".join(used))
    return normalize_transfer_data(raw)


def m3_transfer_candidate_paths() -> list[str]:
    data_dir = os.getenv("M3_TRANSFER_DATA_DIR", "").strip() or DEFAULT_M3_TRANSFER_DATA_DIR
    explicit_bank_form = os.getenv("M3_BANK_FORM_PATH", "").strip()
    candidates: list[str] = []
    if explicit_bank_form:
        candidates.append(explicit_bank_form)
    candidates.extend(
        [
            # M3 按付款银行分类的精确产物（直接运行农行脚本时应优先读这个），
            # 再回退到旧的目录级 bank_form.json / latest.json 以保持兼容。
            os.path.join(data_dir, "runtime", "bank_forms", "农业银行", "bank_form.json"),
            os.path.join(data_dir, "bank_form.json"),
            os.path.join(data_dir, "latest.json"),
        ]
    )
    return candidates


def load_m3_transfer_data_if_available() -> Optional[dict]:
    for path in m3_transfer_candidate_paths():
        if os.path.exists(path):
            log.info("[数据] 使用 M3 直供合同付款数据: %s", path)
            return load_transfer_data(path)
    log.info("[数据] 未找到 M3 直供合同付款数据文件: %s", DEFAULT_M3_TRANSFER_DATA_DIR)
    return None


def load_configured_transfer_data() -> Optional[dict]:
    """
    转账数据入口优先级：
      1) TRANSFER_DATA_PATH 明确指定的 JSON 文件
      2) 项目根目录 transfer_data.json
      3) M3 直供合同付款数据目录中的 bank_form.json / latest.json
      4) .env 内联字段 TRANSFER_ACCOUNT / TRANSFER_NAME / TRANSFER_AMOUNT 等
    明确配置或默认文件存在但校验失败时抛错，避免带着坏数据登录。
    """
    transfer_data_path = os.getenv("TRANSFER_DATA_PATH", "").strip()
    if transfer_data_path:
        log.info("使用 TRANSFER_DATA_PATH: %s", transfer_data_path)
        return load_transfer_data(transfer_data_path)

    if os.path.exists(DEFAULT_TRANSFER_DATA_PATH):
        log.info(
            "未配置 TRANSFER_DATA_PATH，自动使用默认转账数据文件: %s",
            DEFAULT_TRANSFER_DATA_PATH,
        )
        return load_transfer_data(DEFAULT_TRANSFER_DATA_PATH)

    transfer_data = load_m3_transfer_data_if_available()
    if transfer_data:
        return transfer_data

    transfer_data = load_transfer_data_from_env_fields()
    if transfer_data:
        return transfer_data

    log.info(
        "未找到转账数据：可配置 TRANSFER_DATA_PATH，或在项目根目录创建 transfer_data.json，"
        "或使用 M3 直供合同付款数据目录，或在 .env 中填写 TRANSFER_ACCOUNT/TRANSFER_NAME/TRANSFER_AMOUNT"
    )
    return None


def log_transfer_summary(data: dict) -> None:
    """日志只暴露最少必要信息：账号户名脱敏，开户行/金额/用途仅显示是否提供。"""
    def _flag(key: str) -> str:
        return "<已提供>" if key in data else "<未提供，跳过>"

    log.info("====== 转账数据（脱敏，请人工核对）======")
    log.info("  %s: %s", F_ACCOUNT, mask_account(data[F_ACCOUNT]))
    log.info("  %s: %s", F_NAME, mask_name(data[F_NAME]))
    log.info("  %s: %s", F_BANK, _flag(F_BANK))
    log.info("  %s: %s", F_AMOUNT, _flag(F_AMOUNT))
    log.info("  %s: %s", F_PURPOSE, _flag(F_PURPOSE))
    log.info("==========================================")


def log_transfer_batch_summary(batch: list[dict]) -> None:
    log.info("====== 批量转账数据（脱敏，请人工核对）======")
    log.info("  条数: %d", len(batch))
    for idx, data in enumerate(batch, start=1):
        log.info(
            "  第 %02d 条 %s: %s / %s / %s / 金额<已提供>",
            idx,
            data.get(BATCH_LABEL_KEY, ""),
            mask_account(data[F_ACCOUNT]),
            mask_name(data[F_NAME]),
            data.get(F_BANK, "<未提供开户行>"),
        )
    log.info("  付方账户: 登录前固定切换到 USB Hub 29 口 U 盾")
    log.info("============================================")


# ---------------- 密码获取 ----------------
def get_kb_password() -> Optional[str]:
    """
    优先级：
      1) 默认使用 getpass.getpass() 运行时输入；用户直接回车 → 返回 None，由人工输入。
      2) 仅当 ALLOW_INSECURE_ENV_PASSWORD=true 且 .env 中 KB_PASSWORD 非空 → 使用 .env 密码（高风险，仅供受信任本机）。
    任何分支都不会把密码内容写入日志。
    """
    env_pwd = os.getenv("KB_PASSWORD", "")
    allow_env = env_flag("ALLOW_INSECURE_ENV_PASSWORD")
    if allow_env and env_pwd:
        log.warning(
            "[安全] 正在使用 .env 中的 KB_PASSWORD（ALLOW_INSECURE_ENV_PASSWORD=true）。"
            "明文密码存放在磁盘存在泄露风险，请仅在受信任的本机使用，并妥善保管/限制 .env 权限。"
        )
        return env_pwd
    if env_pwd and not allow_env:
        log.warning(
            ".env 含 KB_PASSWORD 但未启用 ALLOW_INSECURE_ENV_PASSWORD=true，已忽略，改为交互输入。"
        )
    try:
        pwd = input("请输入 K 宝密码（直接回车则跳过自动输入，由人工在浏览器中输入）: ")
    except (EOFError, KeyboardInterrupt):
        log.warning("未读取到密码输入，跳过自动密码")
        return None
    if not pwd:
        log.warning("用户未输入密码，跳过自动输入；请在浏览器弹出的 K 宝窗口中手动输入")
        return None
    return pwd

