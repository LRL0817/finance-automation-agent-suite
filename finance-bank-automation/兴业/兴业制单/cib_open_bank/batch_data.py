"""Batch transfer data consumed only by batch_fill_only.py.

batch_fill_only.py fills each form, screenshots, then closes the bank
window — it never submits. The entries below are **placeholder/sample
data only** (含占位 token，不是真实收款方)。真实批量填单前，请由操作人
用真实数据替换 TRANSFERS（不要把真实账号/户名/金额提交进版本库）。
"""

COMMON_PURPOSE = "请勿用于真实提交"

TRANSFERS = [
    {
        "label": "SAMPLE_01_请勿用于真实提交",
        "amount": "0.01",
        "acct_no": "000000000000000",
        "acct_name": "示例收款方公司一",
        "bank": "示例银行",
        "branch_full": "示例银行示例支行",
        "branch_queries": ["示例支行", "示例银行"],
        "purpose": COMMON_PURPOSE,
    },
    {
        "label": "SAMPLE_02_请勿用于真实提交",
        "amount": "0.01",
        "acct_no": "000000000000000",
        "acct_name": "示例收款方公司二",
        "bank": "示例银行",
        "branch_full": "示例银行示例支行",
        "branch_queries": ["示例支行", "示例银行"],
        "purpose": COMMON_PURPOSE,
    },
]
