# -*- coding: utf-8 -*-
"""Shared bank/branch candidate scoring for local bank GUI automations.

This module is intentionally side-effect free: it never clicks UI controls and
never decides whether a transfer may proceed. Bank-specific flows use the score
only to order visible candidates, then apply their own fail-closed rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


BANK_BRANDS = (
    "中国工商银行",
    "中国建设银行",
    "中国农业银行",
    "中国银行",
    "招商银行",
    "中国民生银行",
    "民生银行",
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
    "村镇银行",
)

COMPANY_SUFFIXES = ("股份有限公司", "有限责任公司", "有限公司", "股份公司", "股份有限", "有限")
BRANCH_MARKERS = ("支行", "分行", "营业部", "分理处", "办事处")
HIERARCHY_KINDS = {
    "district_branch",
    "city_branch",
    "province_branch",
    "same_district_branch",
    "same_city_branch",
    "same_province_branch",
}


@dataclass(frozen=True)
class BranchCandidateScore:
    candidate: str
    kind: str
    score: int
    reason: str
    brand: str = ""
    target_branch: str = ""
    candidate_branch: str = ""
    region_level: str = ""

    @property
    def accepted_exact_or_safe(self) -> bool:
        return self.kind in {"exact", "safe_equivalent"}

    @property
    def accepted_hierarchy(self) -> bool:
        return self.kind in HIERARCHY_KINDS

    @property
    def accepted(self) -> bool:
        return self.accepted_exact_or_safe or self.accepted_hierarchy


def compact_text(value: str) -> str:
    return "".join(str(value or "").split())


def normalize_bank_text(value: str) -> str:
    text = compact_text(value)
    for token in COMPANY_SUFFIXES:
        text = text.replace(token, "")
    return text.replace("上海市", "上海").replace("北京市", "北京")


def has_branch_marker(value: str) -> bool:
    return any(marker in str(value or "") for marker in BRANCH_MARKERS)


def split_bank_and_branch(value: str) -> tuple[str, str]:
    text = normalize_bank_text(value)
    for brand in sorted(BANK_BRANDS, key=len, reverse=True):
        if text.startswith(brand):
            return brand, text[len(brand) :]
    pos = text.find("银行")
    if pos >= 0:
        return text[: pos + 2], text[pos + 2 :]
    return "", text


def same_bank_brand(left: str, right: str) -> bool:
    left = compact_text(left)
    right = compact_text(right)
    if not left or not right:
        return False
    if left == right:
        return True
    return left.removeprefix("中国") == right.removeprefix("中国")


def _region_tokens(value: str | None) -> list[str]:
    text = compact_text(value or "")
    if not text:
        return []
    tokens = [text]
    for suffix in (
        "特别行政区",
        "壮族自治区",
        "回族自治区",
        "维吾尔自治区",
        "自治区",
        "自治州",
        "地区",
        "林区",
        "省",
        "市",
        "区",
        "县",
        "旗",
        "盟",
    ):
        if text.endswith(suffix) and len(text) > len(suffix):
            tokens.append(text[: -len(suffix)])
    result = []
    for token in tokens:
        if token and token not in result:
            result.append(token)
    return result


def strongest_region_level(candidate: str, region: Mapping[str, str] | None) -> str:
    if not region:
        return ""
    text = compact_text(candidate)
    for level, key in (("district_branch", "district"), ("city_branch", "city"), ("province_branch", "province")):
        for token in _region_tokens(region.get(key)):
            if token and token in text:
                return level
    return ""


def hierarchy_rank(kind: str) -> int:
    return {
        "district_branch": 0,
        "city_branch": 10,
        "province_branch": 20,
        "same_district_branch": 30,
        "same_city_branch": 40,
        "same_province_branch": 50,
    }.get(kind, 99)


def _safe_alias_match(target: str, candidate: str, safe_aliases: Iterable[tuple[str, str]] | None) -> bool:
    target_key = compact_text(target)
    candidate_key = compact_text(candidate)
    for left, right in safe_aliases or ():
        if {target_key, candidate_key} == {compact_text(left), compact_text(right)}:
            return True
    return False


def _branch_core_contains(target_branch: str, candidate_branch: str) -> bool:
    if not target_branch or not candidate_branch:
        return False
    if target_branch == candidate_branch:
        return True
    if len(target_branch) >= 4 and target_branch in candidate_branch and has_branch_marker(target_branch):
        return True
    if len(candidate_branch) >= 4 and candidate_branch in target_branch and has_branch_marker(candidate_branch):
        return True
    return False


def score_branch_candidate(
    target: str,
    candidate: str,
    *,
    bank_head: str = "",
    region: Mapping[str, str] | None = None,
    allow_hierarchy: bool = True,
    allow_same_region_branch: bool = False,
    safe_aliases: Iterable[tuple[str, str]] | None = None,
) -> BranchCandidateScore:
    target_raw = str(target or "").strip()
    candidate_raw = str(candidate or "").strip()
    if not target_raw or not candidate_raw:
        return BranchCandidateScore(candidate_raw, "reject", 0, "empty")

    target_compact = compact_text(target_raw)
    candidate_compact = compact_text(candidate_raw)
    if target_compact == candidate_compact:
        return BranchCandidateScore(candidate_raw, "exact", 1000, "raw exact")

    target_norm = normalize_bank_text(target_raw)
    candidate_norm = normalize_bank_text(candidate_raw)
    target_brand, target_branch = split_bank_and_branch(target_raw)
    candidate_brand, candidate_branch = split_bank_and_branch(candidate_raw)
    expected_brand, _ = split_bank_and_branch(bank_head or target_raw)

    brand_ok = False
    if target_brand and candidate_brand:
        brand_ok = same_bank_brand(target_brand, candidate_brand)
    elif expected_brand and candidate_brand:
        brand_ok = same_bank_brand(expected_brand, candidate_brand)
    elif expected_brand:
        brand_ok = expected_brand in candidate_norm
    else:
        brand_ok = True

    if target_norm == candidate_norm:
        return BranchCandidateScore(
            candidate_raw,
            "safe_equivalent",
            940,
            "normalized exact",
            candidate_brand,
            target_branch,
            candidate_branch,
        )

    if _safe_alias_match(target_raw, candidate_raw, safe_aliases):
        return BranchCandidateScore(
            candidate_raw,
            "safe_equivalent",
            930,
            "configured safe alias",
            candidate_brand,
            target_branch,
            candidate_branch,
        )

    if brand_ok and _branch_core_contains(target_branch, candidate_branch):
        return BranchCandidateScore(
            candidate_raw,
            "safe_equivalent",
            900,
            "same bank branch core contains",
            candidate_brand,
            target_branch,
            candidate_branch,
        )

    if allow_hierarchy and brand_ok:
        level = strongest_region_level(candidate_raw, region)
        if level and ("分行" in candidate_norm or ("营业部" in candidate_norm and "支行" not in candidate_norm)):
            return BranchCandidateScore(
                candidate_raw,
                level,
                760 - hierarchy_rank(level),
                "same bank same region higher-level branch",
                candidate_brand,
                target_branch,
                candidate_branch,
                level,
            )
        if allow_same_region_branch and level and "支行" in candidate_norm:
            kind = f"same_{level}"
            return BranchCandidateScore(
                candidate_raw,
                kind,
                700 - hierarchy_rank(kind),
                "same bank same region branch",
                candidate_brand,
                target_branch,
                candidate_branch,
                level,
            )

    if brand_ok:
        overlap = 0
        for size in range(min(len(target_branch), len(candidate_branch)), 3, -1):
            if target_branch[:size] and target_branch[:size] in candidate_branch:
                overlap = size
                break
        return BranchCandidateScore(
            candidate_raw,
            "weak",
            200 + overlap,
            "same bank weak branch overlap",
            candidate_brand,
            target_branch,
            candidate_branch,
        )

    return BranchCandidateScore(
        candidate_raw,
        "reject",
        0,
        "bank brand mismatch",
        candidate_brand,
        target_branch,
        candidate_branch,
    )


def score_bank_category_candidate(target_bank: str, candidate: str) -> BranchCandidateScore:
    target_raw = str(target_bank or "").strip()
    candidate_raw = str(candidate or "").strip()
    if not target_raw or not candidate_raw:
        return BranchCandidateScore(candidate_raw, "reject", 0, "empty")
    if compact_text(target_raw) == compact_text(candidate_raw):
        return BranchCandidateScore(candidate_raw, "exact", 1000, "raw exact")
    target_norm = normalize_bank_text(target_raw)
    candidate_norm = normalize_bank_text(candidate_raw)
    target_brand, _ = split_bank_and_branch(target_raw)
    candidate_brand, _ = split_bank_and_branch(candidate_raw)
    if target_norm == candidate_norm:
        return BranchCandidateScore(candidate_raw, "safe_equivalent", 940, "normalized exact", candidate_brand)
    if target_brand and candidate_brand and same_bank_brand(target_brand, candidate_brand):
        return BranchCandidateScore(candidate_raw, "safe_equivalent", 850, "same bank category brand", candidate_brand)
    if target_norm and (target_norm in candidate_norm or candidate_norm in target_norm):
        return BranchCandidateScore(candidate_raw, "contains", 500, "normalized contains", candidate_brand)
    return BranchCandidateScore(candidate_raw, "reject", 0, "bank category mismatch", candidate_brand)
