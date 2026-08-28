#!/usr/bin/env python3
"""Search and read the self-contained deep-sky knowledge snapshot."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from knowledge_common import (
    BundleIntegrityError,
    ValidatedBundle,
    assert_bundle_unchanged,
    is_valid_human_verification,
    load_validated_bundle,
)


SKILL_ROOT = Path(__file__).resolve().parent.parent
REFERENCES_ROOT = SKILL_ROOT / "references"
KNOWLEDGE_ROOT = REFERENCES_ROOT / "knowledge"
ASCII_RE = re.compile(r"[a-z0-9][a-z0-9.+#/_-]*")
CJK_RE = re.compile(r"[\u3400-\u9fff]+")
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
WHITESPACE_RE = re.compile(r"\s+")
CURRENT_DATA_OPTOUT_PATTERNS = (
    re.compile(
        r"(?:不要|不需要|无需)\s*(?:查|查询|检索|核实|验证)?\s*"
        r"(?:今晚|今天|明晚|当前|现在)?\s*(?:的)?\s*"
        r"(?:天气|云量|月相|目标高度|可见时间|观测条件)"
    ),
    re.compile(
        r"\b(?:do not|don't|no need to)\s+(?:check|look up|search|verify)?\s*"
        r"(?:tonight(?:'s)?|today(?:'s)?|tomorrow(?: night's)?|current)?\s*"
        r"(?:weather|cloud cover|moon phase|target altitude|visibility|observing conditions)\b"
    ),
)
STOP_TERMS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "before",
    "can",
    "choose",
    "could",
    "does",
    "for",
    "from",
    "have",
    "help",
    "how",
    "in",
    "into",
    "is",
    "make",
    "my",
    "of",
    "on",
    "please",
    "should",
    "the",
    "this",
    "to",
    "use",
    "what",
    "when",
    "which",
    "with",
    "would",
    "一下",
    "什么",
    "可以",
    "如何",
    "怎么",
    "怎样",
    "我的",
    "是否",
    "请问",
    "需要",
    "问题",
    "进行",
    "这个",
}
GENERIC_COVERAGE_TERMS = {
    "advice",
    "astrophotography",
    "deep-sky",
    "image",
    "imaging",
    "photo",
    "photography",
    "picture",
    "setup",
    "shoot",
    "shooting",
    "一下",
    "使用",
    "天文",
    "建议",
    "怎么",
    "怎样",
    "拍摄",
    "摄影",
    "操作",
    "方法",
    "深空",
    "照片",
    "问题",
}
# The first value is the canonical Chinese intent surfaced in guidance. The boolean controls
# whether that intent can establish bundled coverage; broad domain words only establish scope.
INTENT_ALIASES: tuple[tuple[str, bool, tuple[str, ...]], ...] = (
    (
        "深空摄影领域",
        False,
        (
            "深空摄影",
            "深空拍摄",
            "深空天文摄影",
            "deep sky astrophotography",
            "deep-sky astrophotography",
            "astrophotography",
        ),
    ),
    ("深空摄影概念", True, ("深空摄影是什么", "什么是深空摄影", "what is astrophotography")),
    ("新手入门", True, ("首拍", "第一次拍摄", "first session", "first light", "从零", "新手", "beginner", "入门")),
    ("已有器材起步", True, ("已有设备", "已有器材", "existing equipment", "gear i have")),
    ("预算采购", True, ("预算", "购买", "采购", "buying", "purchase", "budget")),
    ("系统兼容性", True, ("兼容性", "兼容", "compatibility", "compatible", "system design", "系统设计")),
    ("智能望远镜", True, ("智能望远镜", "智能镜", "smart telescope", "seestar", "dwarf 3", "dwarf mini")),
    ("望远镜选型", True, ("望远镜", "主镜", "镜筒", "telescope", "telescopes", "ota")),
    ("赤道仪选型", True, ("赤道仪", "equatorial mount", "mount", "mounts", "gem")),
    ("天文相机选型", True, ("天文相机", "冷冻相机", "astro camera", "astronomy camera", "cmos camera", "osc camera", "mono camera", "zwo asi", "qhy camera", "aps-c", "aps c", "apsc")),
    ("滤镜系统", True, ("滤镜", "filter", "filters", "双窄带", "dual narrowband")),
    ("成像光路", True, ("光路", "后截距", "像圈", "接环", "optical train", "backfocus", "back focus", "image circle")),
    ("关键附件", True, ("附件", "oag", "off-axis guider", "导星镜", "电调焦", "usb hub")),
    ("供电与线缆安全", True, ("供电", "电源", "电池", "线缆", "power", "battery", "cable", "cables", "dew", "防露")),
    ("现场安全", True, ("安全", "停机", "无人值守", "safety", "safe", "shutdown", "unattended")),
    ("城市阳台", True, ("城市阳台", "阳台", "urban balcony", "balcony")),
    ("现场搭建", True, ("现场搭建", "搭建", "field setup", "rig setup", "assemble the rig")),
    ("极轴校准", True, ("极轴", "极轴校准", "polar alignment", "polar align", "polemaster")),
    ("对焦", True, ("对焦", "合焦", "focus", "focusing", "autofocus", "hfr", "bahtinov")),
    ("导星", True, ("导星", "guiding", "guide scope", "phd2", "rms")),
    ("板解与任务恢复", True, ("板解", "子午线翻转", "任务恢复", "plate solving", "plate solve", "meridian flip", "recovery")),
    ("拍摄序列", True, ("采集", "曝光", "序列", "capture", "acquisition", "exposure", "sequence", "sequencing")),
    ("校准帧", True, ("校准帧", "暗场", "平场", "偏置帧", "暗平场", "calibration frames", "bias", "dark", "flat", "dark flat")),
    ("数据管理", True, ("数据管理", "命名", "备份", "归档", "data management", "naming", "backup", "archive")),
    ("总积分与试拍", True, ("总积分", "试拍", "增益", "抖动", "integration time", "test exposure", "gain", "dither", "dithering")),
    ("后期处理", True, ("后期", "后期处理", "修图", "图像处理", "post-processing", "post processing", "processing workflow")),
    ("校准与叠加", True, ("叠加", "校准", "预处理", "stacking", "stack", "integration", "preprocessing", "wbpp", "dss")),
    ("Siril", True, ("siril",)),
    ("PixInsight", True, ("pixinsight", "pi")),
    ("Photoshop", True, ("photoshop",)),
    ("LRGB", True, ("lrgb",)),
    ("窄带与SHO", True, ("窄带", "sho", "ha rgb", "hargb", "ha", "oiii", "sii", "narrowband", "hubble palette")),
    ("M31仙女座", True, ("m31", "仙女座", "andromeda", "andromeda galaxy")),
    ("目标选择", True, ("目标", "星云", "星系", "星团", "target", "targets", "nebula", "galaxy", "galaxies", "star cluster")),
    ("季节窗口", True, ("季节", "窗口", "season", "seasonal", "window")),
    ("构图与焦距", True, ("构图", "焦距", "framing", "composition", "focal length", "field of view", "fov")),
    ("光污染与Bortle", True, ("光污染", "bortle", "light pollution", "dark site")),
    ("观测天气", True, ("天气", "云量", "weather", "cloud cover", "forecast")),
    ("视宁度", True, ("视宁度", "seeing")),
    ("透明度", True, ("透明度", "transparency")),
    ("月相与深空窗口", True, ("月相", "月光", "moon phase", "moonlight")),
    ("目标可见性", True, ("可见时间", "目标高度", "升起", "落下", "过中天", "visibility", "visible time", "target altitude", "rise time", "set time", "transit", "culmination")),
    ("N.I.N.A", True, ("n.i.n.a", "nina")),
    ("采集控制平台", True, ("asiair", "ekos", "indi", "ascom", "alpaca", "采集控制", "capture software")),
    ("规划与解析软件", True, ("astap", "platesolve2", "stellarium", "skysafari", "规划软件", "planning software")),
    ("软件选型", True, ("软件对比", "software comparison", "software choice", "which software")),
    ("故障排查", True, ("故障", "翻车", "失败", "排障", "诊断", "拖线", "结露", "故障排查", "troubleshooting", "diagnose", "star trails", "dew problem")),
    # In-scope adjacent intent intentionally has no matching bundled page today. It proves that a
    # deep-sky request belongs here while allowing the no-coverage gate to fail closed.
    ("光谱观测", True, ("光谱", "光谱仪", "类星体", "spectroscopy", "spectrograph", "quasar spectrum", "quasar")),
)
CATEGORY_HINTS = {
    "00-知识库规范": ("权威", "审核", "来源", "引用", "许可", "知识库"),
    "01-新人入门": ("新手", "入门", "首拍", "第一次", "预算", "已有设备"),
    "02-器材百科": ("器材", "望远镜", "赤道仪", "相机", "滤镜", "光路", "后截距", "智能望远镜"),
    "03-拍摄SOP": ("拍摄", "采集", "现场", "搭建", "极轴", "对焦", "导星", "校准帧", "翻转", "供电"),
    "04-后期处理": ("后期", "叠加", "siril", "lrgb", "sho", "photoshop", "拉伸", "调色"),
    "05-目标图鉴": ("目标", "星云", "星系", "星团", "季节", "焦距"),
    "06-选址与环境": ("选址", "光污染", "bortle", "天气", "云", "视宁度", "透明度", "月相"),
    "07-软件工具": ("软件", "nina", "n.i.n.a", "phd2", "pixinsight", "规划", "板解"),
    "08-FAQ": ("为什么", "怎么办", "问题", "诊断", "检查"),
    "09-踩坑与复盘": ("踩坑", "复盘", "失败", "翻车", "预防"),
}
WEB_VERIFICATION_HINTS = {
    "current_product_or_software_state": (
        "最新",
        "当前版本",
        "固件",
        "app版本",
        "驱动版本",
        "现在支持",
        "菜单位置",
        "产品规格",
        "latest",
        "current version",
        "firmware",
        "driver version",
        "app version",
        "menu location",
        "currently support",
        "specification",
        "specifications",
        "specs",
    ),
    "current_market_state": (
        "价格",
        "多少钱",
        "库存",
        "有货",
        "购买链接",
        "促销",
        "price",
        "cost",
        "stock",
        "in stock",
        "availability",
        "buying link",
        "sale",
    ),
    "explicit_verification_request": ("核实", "验证", "查证", "更新答案", "verify", "fact check", "check current", "update the answer"),
}
CURRENT_TIME_HINTS = (
    "今晚",
    "今天",
    "明晚",
    "现在",
    "当前",
    "本周",
    "tonight",
    "today",
    "tomorrow",
    "tomorrow night",
    "current",
    "right now",
    "currently",
    "as of",
    "this evening",
    "this week",
)
CURRENT_OBSERVING_HINTS = (
    "天气",
    "云量",
    "光污染",
    "月相",
    "视宁度",
    "透明度",
    "可见",
    "升起",
    "落下",
    "能拍",
    "目标高度",
    "过中天",
    "weather",
    "cloud cover",
    "bortle",
    "light pollution",
    "forecast",
    "moon phase",
    "seeing",
    "transparency",
    "visibility",
    "visible",
    "rise",
    "set",
    "target altitude",
    "transit",
    "culmination",
    "observe",
    "shoot",
)
VERSION_SENSITIVE_LEDGER_HINTS = (
    "导出",
    "脚本",
    "支持",
    "兼容",
    "菜单",
    "版本",
    "固件",
    "驱动",
    "siril",
    "pixinsight",
    "n.i.n.a",
    "nina",
    "phd2",
    "seestar",
    "export",
    "script",
    "support",
    "compatible",
    "menu",
    "version",
    "firmware",
    "driver",
)
STRONG_FIELD_NAMES = ("title", "tags", "description", "category", "headings")
ASTRO_MODEL_RE = re.compile(
    r"(?<![a-z0-9])(?:asi\d+[a-z0-9-]*(?:\s*pro)?|seestar\s+s(?:30|50)(?:\s*pro)?|eq\d+[a-z0-9-]*|rst-?\d+[a-z0-9-]*|am[345](?:n)?)(?![a-z0-9])",
    re.IGNORECASE,
)
EXPLICIT_DATE_OR_TIME_RE = re.compile(
    r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)|(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)|(?<![a-z0-9])\d{1,2}(?::\d{2})?\s*(?:am|pm)(?![a-z0-9])",
    re.IGNORECASE,
)
IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9.+#_-]{1,}(?![A-Za-z0-9])")
FORMAT_CONTEXT_RE = re.compile(
    r"(?<![a-z0-9])([a-z][a-z0-9.+#_-]{1,})\s+(?:proprietary\s+)?(?:raw\s+)?(?:file\s+)?format(?![a-z0-9])"
    r"|(?<![a-z0-9])(?:proprietary\s+)?(?:raw\s+)?(?:file\s+)?format\s+([a-z][a-z0-9.+#_-]{1,})(?![a-z0-9])",
    re.IGNORECASE,
)
FALLBACK_IGNORE_TERMS = STOP_TERMS | GENERIC_COVERAGE_TERMS | {
    "check",
    "current",
    "data",
    "file",
    "firmware",
    "format",
    "latest",
    "pro",
    "proprietary",
    "raw",
    "spec",
    "specification",
    "specifications",
    "specs",
    "support",
    "supports",
    "version",
}


class QueryError(RuntimeError):
    """Raised for invalid or incomplete bundle operations."""


def _normalize(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).lower()


def _strip_current_data_opt_outs(value: Any) -> str:
    """Remove only explicit current-condition clauses the user declined.

    This keeps a phrase such as “不要查今晚天气，请给通用 SOP” from becoming
    either a retrieval requirement or a mandatory web gate.  It intentionally
    does not suppress ordinary current-condition requests.
    """

    normalized = _normalize(value)
    for pattern in CURRENT_DATA_OPTOUT_PATTERNS:
        normalized = pattern.sub(" ", normalized)
    return WHITESPACE_RE.sub(" ", normalized).strip()


def _contains_alias(normalized: str, alias: str) -> bool:
    candidate = _normalize(alias)
    if not candidate:
        return False
    if not CJK_RE.search(candidate):
        return re.search(rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])", normalized) is not None
    return candidate in normalized


def _body(text: str) -> str:
    return FRONTMATTER_RE.sub("", text, count=1)


def _terms(text: str) -> set[str]:
    normalized = _normalize(text)
    terms: set[str] = set(ASCII_RE.findall(normalized))
    for chunk in CJK_RE.findall(normalized):
        if 2 <= len(chunk) <= 10:
            terms.add(chunk)
        for size in range(2, min(4, len(chunk)) + 1):
            terms.update(chunk[index : index + size] for index in range(len(chunk) - size + 1))
    return {term for term in terms if term not in STOP_TERMS and len(term) >= 2}


def _intent_terms(text: str) -> set[str]:
    """Return whole aliases/tokens for coverage checks, without Chinese n-gram broadening."""
    normalized = _normalize(text)
    terms = set(ASCII_RE.findall(normalized))
    terms.update(chunk for chunk in CJK_RE.findall(normalized) if len(chunk) >= 2)
    return {term for term in terms if term not in STOP_TERMS and term not in GENERIC_COVERAGE_TERMS}


def _fallback_core_terms(query: str, raw_terms: set[str], recognized_terms: set[str]) -> set[str]:
    normalized = _normalize(query)
    candidates: set[str] = set()
    for token in IDENTIFIER_RE.findall(query):
        normalized_token = _normalize(token)
        if any(character.isdigit() for character in normalized_token) or token.isupper():
            candidates.add(normalized_token)
    for match in FORMAT_CONTEXT_RE.finditer(normalized):
        candidates.add(next(group for group in match.groups() if group))
    # With no recognized concept, retain meaningful terms so exact catalog-language queries can
    # still establish scope. Once an intent is known, only identifier/format fallbacks are core.
    if not recognized_terms:
        candidates.update(term for term in raw_terms if len(term) >= 3)
    return {
        term
        for term in candidates
        if term not in FALLBACK_IGNORE_TERMS
        and not any(term in recognized or recognized in term for recognized in recognized_terms)
    }


def _query_profile(query: str) -> dict[str, Any]:
    effective_query = _strip_current_data_opt_outs(query)
    raw_terms = _terms(effective_query)
    weighted = {term: 1.0 for term in raw_terms}
    normalized = _normalize(effective_query)
    normalized_intents: list[str] = []
    coverage_terms_by_intent: dict[str, set[str]] = {}
    recognized_terms: set[str] = set()

    for canonical, establishes_coverage, aliases in INTENT_ALIASES:
        if not any(_contains_alias(normalized, alias) for alias in aliases):
            continue
        normalized_intents.append(canonical)
        scoring_terms = set().union(*(_terms(alias) for alias in (canonical, *aliases)))
        intent_terms = set().union(*(_intent_terms(alias) for alias in (canonical, *aliases)))
        recognized_terms.update(scoring_terms)
        for term in scoring_terms:
            weighted.setdefault(term, 0.62)
        if establishes_coverage:
            coverage_terms_by_intent[canonical] = intent_terms

    # A specific smart-telescope request should not be broadened into generic OTA selection merely
    # because its Chinese name contains “望远镜”.
    if "智能望远镜" in normalized_intents and "望远镜选型" in normalized_intents:
        normalized_intents.remove("望远镜选型")
        coverage_terms_by_intent.pop("望远镜选型", None)

    for model_match in ASTRO_MODEL_RE.finditer(normalized):
        model = WHITESPACE_RE.sub(" ", model_match.group(0)).strip().upper()
        model_terms = {_normalize(model), _normalize(model).replace(" ", "")}
        coverage_terms_by_intent[model] = model_terms
        normalized_intents.append(model)
        recognized_terms.update(model_terms)
        for term in model_terms:
            weighted.setdefault(term, 0.8)

    fallback_terms = _fallback_core_terms(effective_query, raw_terms, recognized_terms)
    return {
        "weighted_terms": weighted,
        "normalized_intents": sorted(set(normalized_intents)),
        "coverage_terms_by_intent": coverage_terms_by_intent,
        "fallback_terms": fallback_terms,
    }


def _safe_page(relative: str) -> Path:
    cleaned = relative.strip().replace("\\", "/")
    prefix = "references/knowledge/"
    if cleaned.startswith(prefix):
        cleaned = cleaned[len(prefix) :]
    cleaned = cleaned.lstrip("/")
    candidate = (KNOWLEDGE_ROOT / cleaned).resolve()
    try:
        candidate.relative_to(KNOWLEDGE_ROOT.resolve())
    except ValueError as exc:
        raise QueryError("Page path must stay inside references/knowledge") from exc
    if candidate.suffix.lower() != ".md" or not candidate.is_file():
        raise QueryError(f"Bundled Markdown page not found: {relative}")
    return candidate


def _page_text(entry: dict[str, Any]) -> str:
    return _safe_page(str(entry["path"])).read_text(encoding="utf-8")


def _entry_fields(entry: dict[str, Any], text: str) -> dict[str, str]:
    page_body = _body(text)
    headings = " ".join(HEADING_RE.findall(page_body))
    applies_to = json.dumps(entry.get("applies_to") or {}, ensure_ascii=False)
    return {
        "title": _normalize(entry.get("title")),
        "tags": _normalize(" ".join(entry.get("tags") or [])),
        "description": _normalize(entry.get("description")),
        "category": _normalize(entry.get("category")),
        "headings": _normalize(headings),
        "applies_to": _normalize(applies_to),
        "body": _normalize(page_body),
    }


def _score_entries(
    query: str,
    entries: list[dict[str, Any]],
    category: str | None,
) -> tuple[list[tuple[float, dict[str, Any], list[str], str, list[str], list[str]]], dict[str, Any]]:
    profile = _query_profile(query)
    weighted_terms = profile["weighted_terms"]
    if not weighted_terms:
        raise QueryError("Query contains no searchable terms")
    documents: list[tuple[dict[str, Any], str, dict[str, str]]] = []
    for entry in entries:
        if category and _normalize(category) not in _normalize(entry.get("category")):
            continue
        text = _page_text(entry)
        documents.append((entry, text, _entry_fields(entry, text)))
    if not documents:
        raise QueryError(f"No bundled pages match category: {category}")

    document_frequency: Counter[str] = Counter()
    for term in weighted_terms:
        for _, _, fields in documents:
            if any(term in field for field in fields.values()):
                document_frequency[term] += 1

    field_weights = {
        "title": 12.0,
        "tags": 8.0,
        "description": 6.0,
        "category": 5.0,
        "headings": 4.0,
        "applies_to": 3.0,
        "body": 1.0,
    }
    query_normalized = _normalize(query)
    scored: list[tuple[float, dict[str, Any], list[str], str, list[str], list[str]]] = []
    total = len(documents)
    for entry, text, fields in documents:
        score = 0.0
        matched: list[str] = []
        for term, query_weight in weighted_terms.items():
            df = document_frequency.get(term, 0)
            if not df:
                continue
            inverse_frequency = math.log((total + 1) / (df + 1)) + 1.0
            term_score = 0.0
            for field_name, field_weight in field_weights.items():
                frequency = fields[field_name].count(term)
                if frequency:
                    term_score += field_weight * (1.0 + math.log(min(frequency, 8)))
            if term_score:
                length_weight = 1.0 + min(max(len(term) - 2, 0), 4) * 0.18
                score += term_score * inverse_frequency * query_weight * length_weight
                if query_weight >= 1.0:
                    matched.append(term)

        title = fields["title"]
        if title and title in query_normalized:
            score += 45.0
        category_name = str(entry.get("category") or "")
        for hint in CATEGORY_HINTS.get(category_name, ()):
            if _normalize(hint) in query_normalized:
                score += 5.0
        strong_fields = [fields[name] for name in STRONG_FIELD_NAMES]
        strong_intents = sorted(
            canonical
            for canonical, terms in profile["coverage_terms_by_intent"].items()
            if any(term in field for term in terms for field in strong_fields)
        )
        strong_fallback_terms = sorted(
            {
                term
                for term in profile["fallback_terms"]
                if any(term in field for field in strong_fields)
            },
            key=lambda item: (-len(item), item),
        )
        # Body text and category hints may improve ranking, but only metadata/headings can prove
        # that the bundle covers the query. This prevents generic body words from fabricating hits.
        if score > 0 and (strong_intents or strong_fallback_terms):
            scored.append(
                (
                    score,
                    entry,
                    sorted(set(matched), key=lambda item: (-len(item), item)),
                    text,
                    strong_intents,
                    strong_fallback_terms,
                )
            )
    scored.sort(key=lambda item: (-item[0], str(item[1].get("path"))))
    return scored, profile


def _select_scored_results(
    scored: list[tuple[float, dict[str, Any], list[str], str, list[str], list[str]]],
    core_terms: list[str],
    top: int,
) -> list[tuple[float, dict[str, Any], list[str], str, list[str], list[str]]]:
    required_paths: set[str] = set()
    for core in core_terms:
        candidate = next(
            (
                item
                for item in scored
                if core in item[4] or core in item[5]
            ),
            None,
        )
        if candidate is not None:
            required_paths.add(str(candidate[1].get("path")))
    selected = [item for item in scored if str(item[1].get("path")) in required_paths]
    selected_paths = {str(item[1].get("path")) for item in selected}
    selected.extend(item for item in scored if str(item[1].get("path")) not in selected_paths)
    return selected[:top]


def _snippet(text: str, matched_terms: list[str], limit: int = 280) -> str:
    body = WHITESPACE_RE.sub(" ", _body(text)).strip()
    normalized = _normalize(body)
    position = 0
    for term in sorted(matched_terms, key=lambda item: (-len(item), item)):
        candidate = normalized.find(term)
        if candidate >= 0:
            position = candidate
            break
    start = max(position - 70, 0)
    end = min(start + limit, len(body))
    return ("…" if start else "") + body[start:end].strip() + ("…" if end < len(body) else "")


def _is_stale(entry: dict[str, Any]) -> bool | None:
    value = entry.get("stale_after")
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)) < dt.date.today()
    except ValueError:
        return None


def _result(
    rank: int,
    score: float,
    entry: dict[str, Any],
    matched: list[str],
    text: str,
    include_content: bool,
) -> dict[str, Any]:
    stale = _is_stale(entry)
    verified = entry.get("verified")
    unbundled_sources = sorted(
        {
            str(source.get("resource")).lstrip("/")
            for source in entry.get("sources") or []
            if isinstance(source, dict) and str(source.get("resource") or "").startswith("/raw/")
        }
    )
    result = {
        "rank": rank,
        "score": round(score, 3),
        "title": entry.get("title"),
        "path": f"references/knowledge/{entry.get('path')}",
        "category": entry.get("category"),
        "type": entry.get("type"),
        "description": entry.get("description"),
        "status": entry.get("status"),
        "updated": entry.get("updated"),
        "stale_after": entry.get("stale_after"),
        "is_stale": stale,
        "review_state": (entry.get("review") or {}).get("state"),
        "human_verified": is_valid_human_verification(verified),
        "verified_scope": verified.get("scope") if isinstance(verified, dict) else None,
        "applies_to": entry.get("applies_to"),
        "sources": entry.get("sources"),
        "unbundled_source_paths": unbundled_sources,
        "matched_terms": matched[:12],
        "snippet": _snippet(text, matched),
    }
    if include_content:
        result["content"] = text
    return result


def _bundle_summary(validated: ValidatedBundle) -> dict[str, Any]:
    source = validated.manifest["source"]
    facts = validated.facts
    return {
        "source_commit": source.get("git_commit"),
        "packaged_at": validated.manifest.get("packaged_at"),
        "content_page_count": facts["content_page_count"],
        "human_verified_page_count": facts["human_verified_page_count"],
        "needs_human_review_count": facts["needs_human_review_count"],
        "unbundled_internal_source_count": facts["unbundled_internal_source_count"],
    }


def _explicit_exit(query: str) -> tuple[list[str], str | None]:
    normalized = _normalize(query)
    image_format = re.search(r"(?<![a-z0-9])(fits?|xisf|tiff?|png|jpe?g)(?![a-z0-9])", normalized)
    image_noun = bool(image_format) or any(
        term in normalized
        for term in ("这张图", "这幅图", "图片", "图像", "照片", "image", "photo", "picture")
    )
    modification = any(
        term in normalized
        for term in (
            "修改像素",
            "处理成图",
            "替我处理",
            "帮我处理",
            "导出成图",
            "增强这张",
            "把这张",
            "modify pixels",
            "process this image",
            "edit this image",
            "produce an image",
        )
    ) or re.search(r"\b(?:process|edit|modify|enhance)\s+(?:this|my|the attached)\b", normalized)
    inspection = any(
        term in normalized
        for term in (
            "分析这张",
            "检查这张",
            "测量这张",
            "分析文件",
            "检查文件",
            "analyze this",
            "analyse this",
            "inspect this",
            "measure this",
            "file-level diagnosis",
        )
    ) or re.search(
        r"(?:分析|检查|测量)(?:这张|这幅|我的|该)?(?:照片|图片|图像|文件)",
        normalized,
    )
    if image_noun and modification:
        return ["file_backed_pixel_processing"], "$deep-sky-processor"
    if image_noun and inspection:
        return ["file_backed_image_analysis"], "$deep-sky-advisor"

    excluded_patterns = {
        "excluded_planetary_imaging": (
            r"(?<!planetary )\b(jupiter|saturn|mars|venus|mercury)\b",
            r"\bplanet(?:ary)? (?:photo|photography|imaging|capture)\b",
            r"行星(?:摄影|拍摄|成像)",
            r"(?:拍摄|拍|成像)(?:木星|土星|火星|金星|水星)",
        ),
        "excluded_solar_imaging": (
            r"\bsolar (?:photo|photography|imaging|capture)\b",
            r"\b(?:photograph|image|capture) the sun\b",
            r"太阳(?:摄影|拍摄|成像)",
            r"(?:拍摄|拍|成像)太阳",
            r"\bsolar filter\b",
            r"太阳滤镜",
        ),
        "excluded_lunar_imaging": (
            r"\blunar (?:photo|photography|imaging|capture)\b",
            r"\bmoon (?:photo|photography|imaging|capture)\b",
            r"\blucky imaging\b.*\b(?:moon|lunar)\b",
            r"\b(?:moon|lunar)\b.*\blucky imaging\b",
            r"月(?:球|面|亮)(?:摄影|拍摄|成像)",
            r"(?:拍摄|拍|成像)月(?:球|面|亮)",
            r"幸运成像.*月(?:球|面|亮)",
        ),
        "excluded_visual_observing": (
            r"\bvisual observing\b",
            r"\beyepiece observing\b",
            r"\beyepiece\b",
            r"目视观测",
            r"目视天文",
            r"目镜",
        ),
        "excluded_general_photography": (
            r"\b(?:portrait|wedding|street|food|product) photography\b",
            r"(?:人像|婚礼|街拍|美食|商品)摄影",
        ),
    }
    reasons = [
        reason
        for reason, patterns in excluded_patterns.items()
        if any(re.search(pattern, normalized) for pattern in patterns)
    ]
    return sorted(reasons), None


def _scope_decision(
    query: str,
    normalized_intents: list[str],
    scored: list[tuple[float, dict[str, Any], list[str], str, list[str], list[str]]],
) -> tuple[str, list[str], str | None]:
    explicit_reasons, route = _explicit_exit(query)
    if explicit_reasons:
        return "out_of_scope", explicit_reasons, route
    if normalized_intents:
        return "in_scope", ["recognized_deep_sky_intent"], None
    if scored:
        return "in_scope", ["strong_bundled_metadata_match"], None
    return "out_of_scope", ["unrelated_to_deep_sky_astrophotography"], None


def _web_verification_reasons(
    query: str,
    normalized_intents: list[str],
    results: list[dict[str, Any]],
    bundle_coverage: str,
    request_scope: str,
) -> list[str]:
    if request_scope == "out_of_scope":
        return []
    effective_query = _strip_current_data_opt_outs(query)
    effective_intents = _query_profile(effective_query)["normalized_intents"]
    normalized = " ".join((_normalize(effective_query), _normalize(" ".join(effective_intents))))
    reasons = [
        reason
        for reason, hints in WEB_VERIFICATION_HINTS.items()
        if any(_contains_alias(normalized, hint) for hint in hints)
    ]
    has_observing_timing = any(_contains_alias(normalized, hint) for hint in CURRENT_OBSERVING_HINTS)
    has_explicit_time = any(_contains_alias(normalized, hint) for hint in CURRENT_TIME_HINTS) or bool(
        EXPLICIT_DATE_OR_TIME_RE.search(normalized)
    )
    has_transit_request = any(
        _contains_alias(normalized, hint) for hint in ("过中天", "transit", "culmination")
    )
    has_forecast_request = any(
        _contains_alias(normalized, hint) for hint in ("天气预报", "预报", "forecast")
    )
    has_target_or_session_intent = any(
        intent in effective_intents
        for intent in ("M31仙女座", "目标选择", "目标可见性", "季节窗口", "拍摄序列", "观测天气")
    )
    if has_transit_request or has_forecast_request or (
        has_explicit_time and (has_observing_timing or has_target_or_session_intent)
    ):
        reasons.append("current_observing_conditions_or_visibility")
    if bundle_coverage == "insufficient":
        reasons.append("no_bundled_coverage")
    if any(result.get("is_stale") is True for result in results):
        reasons.append("stale_bundled_evidence")
    if any(_contains_alias(normalized, hint) for hint in VERSION_SENSITIVE_LEDGER_HINTS) and any(
        result.get("unbundled_source_paths") for result in results
    ):
        reasons.append("version_sensitive_claim_uses_unbundled_source_ledger")
    return sorted(set(reasons))


def _verify(validated: ValidatedBundle) -> dict[str, Any]:
    facts = dict(validated.facts)
    return {
        "ok": True,
        "bundle": _bundle_summary(validated),
        "facts": facts,
        "markdown_file_count": facts["markdown_file_count"],
        "catalog_entry_count": facts["content_page_count"],
        "knowledge_sha256": facts["knowledge_sha256"],
        "errors": [],
    }


def _print_text(payload: dict[str, Any]) -> None:
    bundle = payload["bundle"]
    print(
        f"Bundle commit {bundle.get('source_commit')} | packaged {bundle.get('packaged_at')} | "
        f"verified {bundle.get('human_verified_page_count')}/{bundle.get('content_page_count')}"
    )
    guidance = payload.get("guidance") or {}
    print(
        f"Scope: {guidance.get('request_scope')} | "
        f"bundle coverage: {guidance.get('bundle_coverage')} | "
        f"action: {guidance.get('recommended_action')}"
    )
    if guidance.get("should_exit_skill"):
        reasons = ", ".join(guidance.get("scope_reasons") or [])
        route = guidance.get("recommended_route") or "another appropriate workflow"
        print(f"Exit skill: {reasons}; route to {route}")
    if guidance.get("normalized_intents"):
        print(f"Normalized intents: {', '.join(guidance['normalized_intents'])}")
    if guidance.get("matched_core_terms"):
        print(f"Matched core terms: {', '.join(guidance['matched_core_terms'])}")
    if guidance.get("unmatched_core_terms"):
        print(f"Unmatched core terms: {', '.join(guidance['unmatched_core_terms'])}")
    if guidance.get("requires_web_verification"):
        reasons = ", ".join(guidance.get("web_verification_reasons") or [])
        print(f"Web verification required: {reasons}")
    for result in payload.get("results", []):
        if result["is_stale"] is True:
            stale = "stale"
        elif result["is_stale"] is False:
            stale = "current-by-date"
        else:
            stale = "staleness-unknown"
        verified = "human-verified" if result["human_verified"] else "not-human-verified"
        print(f"\n{result['rank']}. {result['title']}  score={result['score']}")
        print(f"   {result['path']}")
        print(f"   {result['status']} | {result['review_state']} | {verified} | {stale}")
        print(f"   {result['snippet']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", help="Natural-language search query")
    parser.add_argument("--top", type=int, default=5, help="Number of results, 1-10")
    parser.add_argument("--category", help="Restrict results to a category name")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--include-content", action="store_true", help="Include complete Markdown in results")
    parser.add_argument("--read", metavar="PATH", help="Read one package-relative Markdown page")
    parser.add_argument("--verify-bundle", action="store_true", help="Verify catalog and knowledge hashes")
    args = parser.parse_args()

    if args.verify_bundle and (args.query or args.read):
        print("error: --verify-bundle cannot be combined with a query or --read", file=sys.stderr)
        return 2
    if args.verify_bundle:
        try:
            validated = load_validated_bundle(REFERENCES_ROOT)
            payload = _verify(validated)
            assert_bundle_unchanged(REFERENCES_ROOT, validated)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        except BundleIntegrityError as exc:
            print(
                json.dumps(
                    {"ok": False, "errors": [str(exc)]},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 1

    try:
        validated = load_validated_bundle(REFERENCES_ROOT)
        entries = validated.entries
        if args.read:
            if args.query:
                raise QueryError("Provide either a query or --read, not both")
            page = _safe_page(args.read)
            content = page.read_text(encoding="utf-8")
            if args.format == "json":
                relative = page.relative_to(KNOWLEDGE_ROOT).as_posix()
                entry = next((item for item in entries if item.get("path") == relative), None)
                payload = {
                    "path": f"references/knowledge/{relative}",
                    "metadata": entry,
                    "content": content,
                }
                assert_bundle_unchanged(REFERENCES_ROOT, validated)
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                assert_bundle_unchanged(REFERENCES_ROOT, validated)
                print(content, end="" if content.endswith("\n") else "\n")
            return 0
        if not args.query:
            raise QueryError("Provide a search query, --read PATH, or --verify-bundle")
        if not 1 <= args.top <= 10:
            raise QueryError("--top must be between 1 and 10")
        scored, profile = _score_entries(args.query, entries, args.category)
        request_scope, scope_reasons, recommended_route = _scope_decision(
            args.query,
            profile["normalized_intents"],
            scored,
        )
        matched_intents = sorted(
            {
                intent
                for _, _, _, _, strong_intents, _ in scored
                for intent in strong_intents
            }
        )
        matched_fallback = sorted(
            {
                term
                for _, _, _, _, _, strong_fallback_terms in scored
                for term in strong_fallback_terms
            },
            key=lambda item: (-len(item), item),
        )
        requested_core_intents = sorted(profile["coverage_terms_by_intent"])
        unmatched_core = sorted(
            (set(requested_core_intents) - set(matched_intents))
            | (set(profile["fallback_terms"]) - set(matched_fallback))
        )

        if request_scope == "out_of_scope":
            scored = []
            matched_core: list[str] = []
            unmatched_core = []
            bundle_coverage = "insufficient"
            coverage_reasons = ["request_out_of_scope"]
            recommended_action = "exit_skill"
        elif scored and not unmatched_core:
            matched_core = sorted(set(matched_intents) | set(matched_fallback[:8]))
            bundle_coverage = "sufficient"
            coverage_reasons = ["strong_field_match"]
            recommended_action = "read_bundled_pages"
        else:
            matched_core = sorted(set(matched_intents) | set(matched_fallback[:8]))
            scored = []
            bundle_coverage = "insufficient"
            coverage_reasons = [
                "core_intent_without_strong_field_match"
                if unmatched_core
                else "no_strong_field_match"
            ]
            recommended_action = "web_verify_missing_coverage"

        selected_scored = _select_scored_results(scored, matched_core, args.top)
        results = [
            _result(rank, score, entry, matched, text, args.include_content)
            for rank, (score, entry, matched, text, _, _) in enumerate(selected_scored, start=1)
        ]
        web_reasons = _web_verification_reasons(
            args.query,
            profile["normalized_intents"],
            results,
            bundle_coverage,
            request_scope,
        )
        if web_reasons and recommended_action == "read_bundled_pages":
            recommended_action = "read_bundled_pages_and_web_verify"
        payload = {
            "query": args.query,
            "bundle": _bundle_summary(validated),
            "results": results,
            "guidance": {
                "skill_scope": request_scope,
                "request_scope": request_scope,
                "scope_reasons": scope_reasons,
                "should_exit_skill": request_scope == "out_of_scope",
                "recommended_route": recommended_route,
                "recommended_action": recommended_action,
                "bundle_coverage": bundle_coverage,
                "coverage_reasons": coverage_reasons,
                "normalized_intents": profile["normalized_intents"],
                "matched_core_terms": matched_core,
                "unmatched_core_terms": unmatched_core,
                "read_full_pages_before_answering": True,
                "search_script_uses_network": False,
                "unverified_or_stale_pages_require_non_authoritative_label": True,
                "requires_web_verification": bool(web_reasons),
                "web_verification_reasons": web_reasons,
            },
        }
        assert_bundle_unchanged(REFERENCES_ROOT, validated)
        if args.format == "text":
            _print_text(payload)
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (BundleIntegrityError, QueryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
