#!/usr/bin/env python3
"""Query the pinned Siril 1.4.4 manual without network access.

Queries write only when the caller explicitly supplies ``--output``; that path
is atomically created and never replaced.
"""

from __future__ import annotations

import sys

# This must be set before importing the sibling module.  The query is valid in a
# read-only extracted Skill even when the caller forgets Python's -B option.
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import math
import os
import posixpath
import re
import secrets
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence, TextIO

from siril_manual_bundle import (
    BundleError,
    BundleSnapshot,
    EXPECTED_INDEX_PATHS,
    MANUAL_COMMIT,
    MANUAL_VERSION,
    QUERY_SCHEMA,
    bundle_metadata,
    bundle_verification_document,
    captures_match,
    read_skill_file,
    safe_relative_path,
    strict_json_bytes,
    strict_json_lines,
    verify_bundle,
)


CATALOG_SCHEMA = "deep-sky-siril.siril-manual-catalog/v1"
COMMANDS_SCHEMA = "deep-sky-siril.siril-manual-commands/v1"
ALIASES_SCHEMA = "deep-sky-siril.siril-manual-aliases/v1"
IMAGE_SELECTION_SCHEMA = "deep-sky-siril.siril-manual-image-selection/v1"
SECTION_SCHEMA = "deep-sky-siril.siril-manual-section/v1"
POLICY_SCHEMA = "starun-siril.command-policy.v1"
CONTRACT_VERSION = "1"
EXPECTED_PROTOCOLS = frozenset(
    {
        "input.inspect",
        "geometry.crop-near-black",
        "background.subtract",
        "color.calibrate",
        "color.map",
        "restoration.denoise",
        "restoration.deconvolve",
        "stars.separate",
        "stretch",
        "stars.recompose",
        "color.finish",
        "delivery.render",
    }
)
FROZEN_COMMAND_COUNT = 199
MAX_INCLUDE_DEPTH = 32
POLICY_PATH = "references/command-policy.json"
_ASCII_TOKEN = re.compile(r"[a-z0-9][a-z0-9._+-]*")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_INCLUDE = re.compile(
    r"^(?P<indent>[ \t]*)\.\.\s+(?P<kind>include|literalinclude)::\s+"
    r"(?P<target>\S.*?)\s*$"
)
_CSV_TABLE = re.compile(r"^(?P<indent>[ \t]*)\.\.\s+csv-table::(?:\s+.*)?$")
_DIRECTIVE_OPTION = re.compile(
    r"^(?P<indent>[ \t]+):(?P<name>[A-Za-z0-9_-]+):(?:\s*(?P<value>.*?))?\s*$"
)


class QueryUsageError(RuntimeError):
    """The CLI request is ambiguous or unsafe."""


class ExactNotFound(RuntimeError):
    """An exact command, page, or section does not exist."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise QueryUsageError(message)


@dataclass(frozen=True)
class ManualIndexes:
    catalog: tuple[Mapping[str, Any], ...]
    commands: tuple[Mapping[str, Any], ...]
    sections: tuple[Mapping[str, Any], ...]
    aliases: tuple[Mapping[str, Any], ...]
    image_selection: Mapping[str, Any]


@dataclass(frozen=True)
class PolicySnapshot:
    document: Mapping[str, Any]
    capture: Any
    allowed_protocols: Mapping[str, tuple[str, ...]]


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise BundleError(f"{label} must be a JSON object")
    return value


def _array(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise BundleError(f"{label} must be a JSON array")
    return value


def _string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise BundleError(f"{label} must be a string")
    return value


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise BundleError(f"{label} must be an integer >= {minimum}")
    return value


def _sha256(value: Any, label: str) -> str:
    digest = _string(value, label)
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise BundleError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _index_document(snapshot: BundleSnapshot, name: str) -> Mapping[str, Any]:
    path = EXPECTED_INDEX_PATHS[name]
    return _mapping(strict_json_bytes(snapshot.data(path), document=path), path)


def _validate_source_reference(
    snapshot: BundleSnapshot,
    value: Mapping[str, Any],
    *,
    label: str,
) -> str:
    path = safe_relative_path(value.get("path"), label=f"{label}.path")
    if not path.startswith("doc/"):
        raise BundleError(f"{label}.path must be below doc/")
    component_path = "source/" + path
    source_sha = _sha256(value.get("source_sha256"), f"{label}.source_sha256")
    if snapshot.sha256(component_path) != source_sha:
        raise BundleError(f"{label}.source_sha256 does not match {component_path}")
    return path


def _dependency_component_paths(
    snapshot: BundleSnapshot,
    value: Mapping[str, Any],
    *,
    label: str,
) -> tuple[str, ...]:
    raw_dependencies = value.get("dependencies", [])
    dependencies = _array(raw_dependencies, f"{label}.dependencies")
    paths: list[str] = []
    seen: set[str] = set()
    for index, raw_dependency in enumerate(dependencies):
        dependency = _mapping(raw_dependency, f"{label}.dependencies[{index}]")
        logical_path = safe_relative_path(
            dependency.get("path"),
            label=f"{label}.dependencies[{index}].path",
        )
        if not logical_path.startswith("doc/"):
            raise BundleError(
                f"{label}.dependencies[{index}].path must be below doc/"
            )
        component_path = "source/" + logical_path
        expected_sha256 = _sha256(
            dependency.get("sha256"),
            f"{label}.dependencies[{index}].sha256",
        )
        if component_path in seen:
            raise BundleError(f"{label} repeats dependency {logical_path!r}")
        seen.add(component_path)
        if snapshot.sha256(component_path) != expected_sha256:
            raise BundleError(
                f"{label} dependency hash does not match {component_path}"
            )
        paths.append(component_path)
    return tuple(paths)


def _validated_component_paths(value: Mapping[str, Any]) -> tuple[str, ...]:
    source = "source/" + value["path"]
    dependencies = tuple(
        "source/" + dependency["path"]
        for dependency in value.get("dependencies", [])
    )
    return (source, *dependencies)


def _load_indexes(snapshot: BundleSnapshot) -> ManualIndexes:
    catalog_document = _index_document(snapshot, "catalog")
    if catalog_document.get("schema") != CATALOG_SCHEMA:
        raise BundleError(f"catalog.json schema must be {CATALOG_SCHEMA!r}")
    raw_catalog = _array(catalog_document.get("records"), "catalog.records")
    catalog: list[Mapping[str, Any]] = []
    catalog_ids: set[str] = set()
    for index, raw in enumerate(raw_catalog):
        record = _mapping(raw, f"catalog.records[{index}]")
        identifier = _string(record.get("id"), f"catalog.records[{index}].id")
        if identifier in catalog_ids:
            raise BundleError(f"duplicate catalog id: {identifier}")
        catalog_ids.add(identifier)
        kind = record.get("kind")
        if kind not in {"page", "command"}:
            raise BundleError(f"catalog record has invalid kind: {identifier}")
        _validate_source_reference(snapshot, record, label=f"catalog record {identifier}")
        _dependency_component_paths(
            snapshot, record, label=f"catalog record {identifier}"
        )
        for field in ("title", "section", "search_text"):
            _string(
                record.get(field),
                f"catalog record {identifier}.{field}",
                allow_empty=field in {"section", "search_text"},
            )
        headings = _array(record.get("headings"), f"catalog record {identifier}.headings")
        if any(not isinstance(item, str) for item in headings):
            raise BundleError(f"catalog record {identifier}.headings must contain strings")
        aliases = _array(record.get("aliases"), f"catalog record {identifier}.aliases")
        if any(not isinstance(item, str) or not item for item in aliases):
            raise BundleError(f"catalog record {identifier}.aliases must contain strings")
        catalog.append(record)

    commands_document = _index_document(snapshot, "commands")
    if commands_document.get("schema") != COMMANDS_SCHEMA:
        raise BundleError(f"commands.json schema must be {COMMANDS_SCHEMA!r}")
    raw_commands = _array(commands_document.get("commands"), "commands.commands")
    commands: list[Mapping[str, Any]] = []
    command_names: set[str] = set()
    for index, raw in enumerate(raw_commands):
        command = _mapping(raw, f"commands[{index}]")
        name = _string(command.get("name"), f"commands[{index}].name")
        if any(char.isspace() for char in name):
            raise BundleError(f"command name contains whitespace: {name!r}")
        lookup_name = _normalize(name)
        if lookup_name in command_names:
            raise BundleError(f"case-fold-colliding command name: {name}")
        command_names.add(lookup_name)
        _validate_source_reference(snapshot, command, label=f"command {name}")
        _dependency_component_paths(snapshot, command, label=f"command {name}")
        _string(command.get("title"), f"command {name}.title")
        if not isinstance(command.get("scriptable"), bool):
            raise BundleError(f"command {name}.scriptable must be boolean")
        usage = command.get("usage")
        if not isinstance(usage, str) and not (
            isinstance(usage, list) and all(isinstance(item, str) for item in usage)
        ):
            raise BundleError(f"command {name}.usage must be text or an array of text")
        _string(
            command.get("description"),
            f"command {name}.description",
            allow_empty=True,
        )
        _string(command.get("section_id"), f"command {name}.section_id")
        if "id" in command:
            expected_id = f"command:{lookup_name}"
            if command.get("id") != expected_id:
                raise BundleError(f"command {name}.id must be {expected_id!r}")
        if "sha256" in command:
            _sha256(command.get("sha256"), f"command {name}.sha256")
        if "include_paths" in command:
            include_paths = _array(
                command.get("include_paths"), f"command {name}.include_paths"
            )
            for include_index, include_path in enumerate(include_paths):
                logical_path = safe_relative_path(
                    include_path,
                    label=f"command {name}.include_paths[{include_index}]",
                )
                if not logical_path.startswith("doc/"):
                    raise BundleError(
                        f"command {name} include path must be below doc/"
                    )
                snapshot.sha256("source/" + logical_path)
        commands.append(command)

    sections_path = EXPECTED_INDEX_PATHS["sections"]
    sections = list(
        strict_json_lines(snapshot.data(sections_path), document=sections_path)
    )
    section_ids: set[str] = set()
    for index, section in enumerate(sections):
        if section.get("schema") != SECTION_SCHEMA:
            raise BundleError(f"sections line {index + 1} has the wrong schema")
        identifier = _string(section.get("id"), f"sections[{index}].id")
        if identifier in section_ids:
            raise BundleError(f"duplicate section id: {identifier}")
        section_ids.add(identifier)
        _validate_source_reference(snapshot, section, label=f"section {identifier}")
        _dependency_component_paths(snapshot, section, label=f"section {identifier}")
        for field in ("title", "heading", "body"):
            _string(
                section.get(field),
                f"section {identifier}.{field}",
                allow_empty=field in {"heading", "body"},
            )
        start = _integer(section.get("start_line"), f"section {identifier}.start_line", minimum=1)
        end = _integer(section.get("end_line"), f"section {identifier}.end_line", minimum=1)
        if end < start:
            raise BundleError(f"section {identifier} has an inverted line range")
        body_hash = hashlib.sha256(section["body"].encode("utf-8")).hexdigest()
        if _sha256(section.get("sha256"), f"section {identifier}.sha256") != body_hash:
            raise BundleError(f"section {identifier} body hash does not match")

    valid_command_sections = section_ids | catalog_ids
    for command in commands:
        if command["section_id"] not in valid_command_sections:
            raise BundleError(
                f"command {command['name']} refers to a missing section: "
                f"{command['section_id']}"
            )

    aliases_document = _index_document(snapshot, "aliases")
    if aliases_document.get("schema") != ALIASES_SCHEMA:
        raise BundleError(f"aliases.zh-en.json schema must be {ALIASES_SCHEMA!r}")
    raw_aliases = _array(aliases_document.get("aliases"), "aliases.aliases")
    aliases: list[Mapping[str, Any]] = []
    alias_values: set[str] = set()
    valid_targets = catalog_ids | section_ids | {
        f"command:{command_name}" for command_name in command_names
    }
    for index, raw in enumerate(raw_aliases):
        alias = _mapping(raw, f"aliases[{index}]")
        value = _string(alias.get("alias"), f"aliases[{index}].alias")
        normalized = _normalize(value)
        if normalized in alias_values:
            raise BundleError(f"duplicate normalized manual alias: {value!r}")
        alias_values.add(normalized)
        if alias.get("language") != "zh-CN":
            raise BundleError(f"alias {value!r} language must be 'zh-CN'")
        if alias.get("reviewed") is not True:
            raise BundleError(f"alias {value!r} must be reviewed")
        targets = _array(alias.get("target_ids"), f"alias {value}.target_ids")
        if not targets or any(not isinstance(item, str) for item in targets):
            raise BundleError(f"alias {value!r} must have string target_ids")
        unknown = sorted(set(targets).difference(valid_targets))
        if unknown:
            raise BundleError(f"alias {value!r} has unknown targets: {unknown}")
        aliases.append(alias)

    image_selection = _index_document(snapshot, "image_selection")
    if image_selection.get("schema") != IMAGE_SELECTION_SCHEMA:
        raise BundleError(
            f"image-selection.json schema must be {IMAGE_SELECTION_SCHEMA!r}"
        )
    _mapping(image_selection.get("policy"), "image-selection.policy")
    selected = _array(image_selection.get("selected"), "image-selection.selected")
    selected_paths: set[str] = set()
    for index, raw in enumerate(selected):
        item = _mapping(raw, f"image-selection.selected[{index}]")
        path = safe_relative_path(item.get("path"), label=f"selected image {index}.path")
        if path in selected_paths:
            raise BundleError(f"duplicate selected image: {path}")
        selected_paths.add(path)
        if snapshot.sha256(path) != _sha256(item.get("sha256"), f"selected image {path}.sha256"):
            raise BundleError(f"selected image hash does not match: {path}")
        if snapshot.captures[path].size_bytes != _integer(
            item.get("size_bytes"), f"selected image {path}.size_bytes"
        ):
            raise BundleError(f"selected image size does not match: {path}")
        safe_relative_path(item.get("upstream_path"), label=f"selected image {path}.upstream_path")
        references = _array(item.get("references"), f"selected image {path}.references")
        if not references:
            raise BundleError(f"selected image {path} must name its references")
        _validate_image_references(references, f"selected image {path}.references")
        _string(item.get("reason"), f"selected image {path}.reason")
    omitted = _array(
        image_selection.get("omitted_local_references"),
        "image-selection.omitted_local_references",
    )
    for index, raw in enumerate(omitted):
        item = _mapping(raw, f"omitted_local_references[{index}]")
        safe_relative_path(item.get("upstream_path"), label=f"omitted image {index}.upstream_path")
        references = _array(item.get("references"), f"omitted image {index}.references")
        if not references:
            raise BundleError(f"omitted image {index} must name its references")
        _validate_image_references(references, f"omitted image {index}.references")
        _string(item.get("reason"), f"omitted image {index}.reason")

    counts = _mapping(snapshot.manifest.get("counts"), "manifest.counts")
    if counts.get("commands") != len(commands):
        raise BundleError("manifest command count does not match commands.json")
    if len(commands) != FROZEN_COMMAND_COUNT:
        raise BundleError(
            f"pinned Siril manual must contain exactly {FROZEN_COMMAND_COUNT} commands"
        )
    if counts.get("sections") != len(sections):
        raise BundleError("manifest section count does not match sections.jsonl")
    if counts.get("selected_images") != len(selected):
        raise BundleError("manifest selected image count does not match image-selection.json")
    return ManualIndexes(
        catalog=tuple(catalog),
        commands=tuple(commands),
        sections=tuple(sections),
        aliases=tuple(aliases),
        image_selection=image_selection,
    )


def _validate_image_references(references: Sequence[Any], label: str) -> None:
    for index, raw in enumerate(references):
        reference = _mapping(raw, f"{label}[{index}]")
        logical_path = safe_relative_path(
            reference.get("path"), label=f"{label}[{index}].path"
        )
        if not logical_path.startswith("doc/"):
            raise BundleError(f"{label}[{index}].path must be below doc/")
        _string(reference.get("directive"), f"{label}[{index}].directive")
        _integer(reference.get("line"), f"{label}[{index}].line", minimum=1)


def _load_policy(snapshot: BundleSnapshot, indexes: ManualIndexes) -> PolicySnapshot:
    capture = read_skill_file(snapshot.skill_root, POLICY_PATH)
    document = _mapping(strict_json_bytes(capture.data, document=POLICY_PATH), POLICY_PATH)
    if document.get("schema") != POLICY_SCHEMA:
        raise BundleError(f"command policy schema must be {POLICY_SCHEMA!r}")
    if document.get("contract_version") != CONTRACT_VERSION:
        raise BundleError(
            f"command policy contract_version must be {CONTRACT_VERSION!r}"
        )
    protocols = _mapping(
        document.get("protocol_commands"), "command-policy.protocol_commands"
    )
    if set(protocols) != EXPECTED_PROTOCOLS:
        missing = sorted(EXPECTED_PROTOCOLS.difference(protocols))
        extra = sorted(set(protocols).difference(EXPECTED_PROTOCOLS))
        raise BundleError(
            "command policy must contain exactly the standalone v1 protocol set; "
            f"missing={missing}, extra={extra}"
        )
    known = {_normalize(command["name"]): command for command in indexes.commands}
    reverse: dict[str, list[str]] = {}
    for protocol, raw_commands in protocols.items():
        _string(protocol, "command-policy protocol name")
        commands = _array(raw_commands, f"command-policy protocol {protocol}")
        seen: set[str] = set()
        for raw_name in commands:
            name = _string(raw_name, f"command-policy protocol {protocol} command")
            lookup_name = _normalize(name)
            if lookup_name in seen:
                raise BundleError(f"command policy repeats {name!r} in {protocol}")
            seen.add(lookup_name)
            command = known.get(lookup_name)
            if command is None:
                raise BundleError(f"command policy references undocumented command: {name}")
            if command["scriptable"] is not True:
                raise BundleError(f"command policy permits non-scriptable command: {name}")
            reverse.setdefault(lookup_name, []).append(protocol)
    return PolicySnapshot(
        document=document,
        capture=capture,
        allowed_protocols={
            name: tuple(sorted(protocol_names))
            for name, protocol_names in reverse.items()
        },
    )


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _tokens(value: str) -> tuple[str, ...]:
    normalized = _normalize(value)
    tokens: list[str] = _ASCII_TOKEN.findall(normalized)
    for run in _CJK_RUN.findall(normalized):
        tokens.extend(run[index : index + size] for size in (1, 2, 3) for index in range(len(run) - size + 1))
    return tuple(dict.fromkeys(tokens))


def _alias_targets(indexes: ManualIndexes) -> tuple[dict[str, tuple[str, ...]], dict[str, list[str]]]:
    alias_to_targets: dict[str, tuple[str, ...]] = {}
    target_to_aliases: dict[str, list[str]] = {}
    for record in indexes.aliases:
        normalized = _normalize(record["alias"])
        targets = tuple(record["target_ids"])
        alias_to_targets[normalized] = targets
        for target in targets:
            target_to_aliases.setdefault(target, []).append(record["alias"])
    return alias_to_targets, target_to_aliases


def _snippet(text: str, query_tokens: Sequence[str], *, limit: int = 240) -> str:
    plain = " ".join(text.split())
    normalized = _normalize(plain)
    starts = [normalized.find(token) for token in query_tokens if normalized.find(token) >= 0]
    start = max(0, min(starts) - 60) if starts else 0
    snippet = plain[start : start + limit]
    if start:
        snippet = "…" + snippet
    if start + limit < len(plain):
        snippet += "…"
    return snippet


def _search(
    indexes: ManualIndexes,
    query: str,
    top: int,
    used_paths: set[str],
) -> Mapping[str, Any]:
    normalized_query = _normalize(query)
    if not normalized_query:
        raise QueryUsageError("search query must not be empty")
    query_tokens = _tokens(query)
    if not query_tokens:
        raise QueryUsageError("search query contains no searchable text")
    alias_to_targets, target_to_aliases = _alias_targets(indexes)

    candidates: list[dict[str, Any]] = []
    for record in indexes.catalog:
        identifier = record["id"]
        aliases = [*record["aliases"], *target_to_aliases.get(identifier, [])]
        candidates.append(
            {
                "id": identifier,
                "kind": record["kind"],
                "title": record["title"],
                "section": record["section"],
                "path": record["path"],
                "source_sha256": record["source_sha256"],
                "headings": list(record["headings"]),
                "aliases": aliases,
                "text": record["search_text"],
                "component_paths": _validated_component_paths(record),
            }
        )
    catalog_ids = {candidate["id"] for candidate in candidates}
    for section in indexes.sections:
        if section["id"] in catalog_ids:
            continue
        candidates.append(
            {
                "id": section["id"],
                "kind": "section",
                "title": section["title"],
                "section": section["heading"],
                "path": section["path"],
                "source_sha256": section["source_sha256"],
                "headings": [section["heading"]],
                "aliases": target_to_aliases.get(section["id"], []),
                "text": section["body"],
                "component_paths": _validated_component_paths(section),
            }
        )

    document_tokens = [set(_tokens(" ".join((item["title"], item["section"], item["text"])))) for item in candidates]
    document_frequency = {
        token: sum(token in tokens for tokens in document_tokens) for token in query_tokens
    }
    result_rows: list[tuple[dict[str, Any], tuple[str, ...]]] = []
    exact_alias_targets = set(alias_to_targets.get(normalized_query, ()))
    for candidate, present_tokens in zip(candidates, document_tokens):
        normalized_id = _normalize(candidate["id"])
        normalized_title = _normalize(candidate["title"])
        normalized_headings = _normalize(" ".join(candidate["headings"]))
        normalized_text = _normalize(candidate["text"])
        normalized_aliases = [_normalize(alias) for alias in candidate["aliases"]]
        score = 0.0
        matched: set[str] = set()
        if normalized_query == normalized_id:
            score += 1200.0
            matched.add(normalized_query)
        if candidate["id"] in exact_alias_targets or normalized_query in normalized_aliases:
            score += 900.0
            matched.add(normalized_query)
        if normalized_query == normalized_title:
            score += 700.0
            matched.add(normalized_query)
        elif normalized_query in normalized_title:
            score += 260.0
            matched.add(normalized_query)
        if normalized_query and normalized_query in normalized_headings:
            score += 180.0
            matched.add(normalized_query)
        for token in query_tokens:
            alias_hits = sum(alias.count(token) for alias in normalized_aliases)
            title_hits = normalized_title.count(token)
            heading_hits = normalized_headings.count(token)
            body_hits = min(normalized_text.count(token), 12)
            if alias_hits or title_hits or heading_hits or token in present_tokens:
                matched.add(token)
            frequency = max(document_frequency[token], 1)
            idf = math.log((len(candidates) + 1) / (frequency + 1)) + 1.0
            score += idf * (
                min(alias_hits, 4) * 80.0
                + min(title_hits, 4) * 28.0
                + min(heading_hits, 4) * 16.0
                + body_hits * 2.0
            )
        if score <= 0:
            continue
        result_rows.append(
            (
                {
                    "id": candidate["id"],
                    "kind": candidate["kind"],
                    "title": candidate["title"],
                    "section": candidate["section"],
                    "path": _logical_doc_path(candidate["path"]),
                    "source_sha256": candidate["source_sha256"],
                    "score": round(score, 6),
                    "matched_terms": sorted(matched),
                    "snippet": _snippet(candidate["text"], query_tokens),
                },
                candidate["component_paths"],
            )
        )
    result_rows.sort(key=lambda item: (-item[0]["score"], item[0]["id"]))
    cjk_runs = _CJK_RUN.findall(normalized_query)
    known_aliases = tuple(alias_to_targets)
    unmatched = [
        run
        for run in cjk_runs
        if not any(run in alias or alias in run for alias in known_aliases)
    ]
    selected_with_paths = result_rows[:top]
    selected = [row for row, _ in selected_with_paths]
    for _, component_paths in selected_with_paths:
        used_paths.update(component_paths)
    return {
        "status": "matches" if selected else "no_match",
        "query": query,
        "normalized_query": normalized_query,
        "top": top,
        "unmatched_terms": unmatched,
        "results": selected,
    }


def _logical_doc_path(component_path: str) -> str:
    prefix = "source/"
    return component_path[len(prefix) :] if component_path.startswith(prefix) else component_path


def _command_result(
    indexes: ManualIndexes,
    policy: PolicySnapshot,
    requested: str,
    used_paths: set[str],
) -> Mapping[str, Any]:
    name = _normalize(requested)
    if not name or any(char.isspace() for char in name):
        raise QueryUsageError("--command requires one exact Siril command name")
    command = next(
        (item for item in indexes.commands if _normalize(item["name"]) == name), None
    )
    if command is None:
        raise ExactNotFound(f"Siril command is not in the pinned manual: {requested}")
    used_paths.update(_validated_component_paths(command))
    used_paths.add("source/doc/Commands.rst")
    for include_path in command.get("include_paths", []):
        used_paths.add("source/" + include_path)
    protocols = list(policy.allowed_protocols.get(name, ()))
    if command["scriptable"] is not True:
        state = "non_scriptable"
    elif protocols:
        state = "allowed"
    else:
        state = "manual_only"
    return {
        "status": "found",
        "command": command["name"],
        "documentation": {
            "title": command["title"],
            "scriptable": command["scriptable"],
            "usage": command["usage"],
            "description": command["description"],
            "section_id": command["section_id"],
            "path": _logical_doc_path(command["path"]),
            "source_sha256": command["source_sha256"],
        },
        "execution_policy": {
            "state": state,
            "allowed_protocols": protocols,
            "policy_path": POLICY_PATH,
            "policy_sha256": policy.capture.sha256,
            "note": "Documentation and scriptability do not grant execution authority.",
        },
    }


def _component_doc_path(requested: str) -> str:
    try:
        logical = safe_relative_path(requested, label="--read path")
    except BundleError as exc:
        raise QueryUsageError(str(exc)) from exc
    if not logical.startswith("doc/"):
        raise QueryUsageError("--read path must be below doc/")
    return "source/" + logical


def _resolve_doc_target(path: str, target: str, *, directive: str) -> str:
    target = target.strip().strip('"\'')
    if (
        not target
        or target.startswith("/")
        or "://" in target
        or "<" in target
        or ">" in target
    ):
        raise BundleError(f"unsafe RST {directive} target in {path}: {target!r}")
    normalized = posixpath.normpath(
        posixpath.join(PurePosixPath(path).parent.as_posix(), target)
    )
    try:
        safe_relative_path(normalized, label=f"RST {directive} in {path}")
    except BundleError as exc:
        raise BundleError(
            f"unsafe RST {directive} target in {path}: {target!r}"
        ) from exc
    if not normalized.startswith("source/doc/"):
        raise BundleError(f"RST {directive} escapes source/doc in {path}: {target!r}")
    return normalized


def _indent_width(value: str) -> int:
    return len(value.expandtabs(8))


def _resolve_includes(
    snapshot: BundleSnapshot,
    path: str,
    *,
    used_paths: set[str],
    resolved_includes: set[str],
    resolved_csv_tables: set[str],
    stack: tuple[str, ...] = (),
) -> str:
    if len(stack) >= MAX_INCLUDE_DEPTH:
        raise BundleError(f"RST include depth exceeds {MAX_INCLUDE_DEPTH}: {path}")
    if path in stack:
        raise BundleError("RST include cycle: " + " -> ".join((*stack, path)))
    try:
        raw = snapshot.data(path)
    except BundleError as exc:
        raise BundleError(f"RST include target is outside the verified closure: {path}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError(f"RST include target is not UTF-8 text: {path}") from exc
    used_paths.add(path)
    output: list[str] = []
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        line = lines[index]
        csv_table = _CSV_TABLE.match(line.rstrip("\r\n"))
        if csv_table is not None:
            output.append(line)
            option_index = index + 1
            csv_target: str | None = None
            while option_index < len(lines):
                option_line = lines[option_index]
                option = _DIRECTIVE_OPTION.match(option_line.rstrip("\r\n"))
                if option is None or _indent_width(option.group("indent")) <= _indent_width(
                    csv_table.group("indent")
                ):
                    break
                output.append(option_line)
                if option.group("name").casefold() == "file":
                    if csv_target is not None:
                        raise BundleError(f"csv-table repeats :file: in {path}")
                    csv_target = option.group("value") or ""
                option_index += 1
            if csv_target is not None:
                normalized = _resolve_doc_target(
                    path, csv_target, directive="csv-table :file:"
                )
                try:
                    dependency = snapshot.data(normalized).decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise BundleError(
                        f"csv-table dependency is not UTF-8: {normalized}"
                    ) from exc
                used_paths.add(normalized)
                resolved_csv_tables.add(normalized)
                if dependency and not dependency.endswith(("\n", "\r")):
                    dependency += "\n"
                marker_indent = csv_table.group("indent")
                output.append(
                    f"{marker_indent}.. bundled-csv-table-begin:: "
                    f"{_logical_doc_path(normalized)}\n"
                    f"{dependency}"
                    f"{marker_indent}.. bundled-csv-table-end:: "
                    f"{_logical_doc_path(normalized)}\n"
                )
            index = option_index
            continue
        match = _INCLUDE.match(line.rstrip("\r\n"))
        if match is None:
            output.append(line)
            index += 1
            continue
        normalized = _resolve_doc_target(
            path, match.group("target"), directive=match.group("kind")
        )
        included = snapshot.data(normalized)
        used_paths.add(normalized)
        resolved_includes.add(normalized)
        if match.group("kind") == "literalinclude":
            try:
                replacement = included.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BundleError(f"literalinclude is not UTF-8: {normalized}") from exc
        else:
            replacement = _resolve_includes(
                snapshot,
                normalized,
                used_paths=used_paths,
                resolved_includes=resolved_includes,
                resolved_csv_tables=resolved_csv_tables,
                stack=(*stack, path),
            )
        if replacement and not replacement.endswith(("\n", "\r")):
            replacement += "\n"
        newline = "\n" if line.endswith("\n") else ""
        output.append(
            f"{match.group('indent')}.. bundled-include-begin:: {_logical_doc_path(normalized)}\n"
            f"{replacement}"
            f"{match.group('indent')}.. bundled-include-end:: {_logical_doc_path(normalized)}{newline}"
        )
        index += 1
    return "".join(output)


def _read_result(
    snapshot: BundleSnapshot,
    indexes: ManualIndexes,
    requested: str,
    used_paths: set[str],
) -> Mapping[str, Any]:
    if requested.startswith("section:"):
        suffix = requested[len("section:") :]
        if not suffix:
            raise QueryUsageError("--read section: requires a section id")
        candidates = (requested, suffix)
        section = next(
            (item for item in indexes.sections if item["id"] in candidates), None
        )
        if section is None:
            raise ExactNotFound(f"manual section does not exist: {suffix}")
        identifier = section["id"]
        used_paths.add(EXPECTED_INDEX_PATHS["sections"])
        used_paths.update(_validated_component_paths(section))
        return {
            "status": "found",
            "kind": "section",
            "id": identifier,
            "title": section["title"],
            "heading": section["heading"],
            "path": _logical_doc_path(section["path"]),
            "line_range": [section["start_line"], section["end_line"]],
            "source_sha256": section["source_sha256"],
            "slice_sha256": section["sha256"],
            "content": section["body"],
        }
    component_path = _component_doc_path(requested)
    if component_path not in snapshot.captures:
        raise ExactNotFound(f"manual page does not exist: {requested}")
    resolved_includes: set[str] = set()
    resolved_csv_tables: set[str] = set()
    content = _resolve_includes(
        snapshot,
        component_path,
        used_paths=used_paths,
        resolved_includes=resolved_includes,
        resolved_csv_tables=resolved_csv_tables,
    )
    return {
        "status": "found",
        "kind": "page",
        "path": requested,
        "source_sha256": snapshot.sha256(component_path),
        "resolved_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content": content,
        "resolved_includes": sorted(
            _logical_doc_path(path) for path in resolved_includes
        ),
        "resolved_csv_tables": sorted(
            _logical_doc_path(path) for path in resolved_csv_tables
        ),
    }


def _manual_metadata(snapshot: BundleSnapshot) -> Mapping[str, Any]:
    return bundle_metadata(snapshot)


def _envelope(snapshot: BundleSnapshot, mode: str, result: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "schema": QUERY_SCHEMA,
        "status": "ok",
        "manual": _manual_metadata(snapshot),
        "mode": mode,
        "result": result,
    }


def verify_query_evidence_documents(
    documents: Sequence[Mapping[str, Any]],
    *,
    skill_root: Path | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Rebuild saved command/read envelopes from the pinned Bundle.

    This authenticates only evidence the Agent elected to save.  It never
    chooses a command, protocol, parameter, or additional manual lookup.
    """

    if not documents:
        return ()
    snapshot = verify_bundle(skill_root)
    indexes = _load_indexes(snapshot)
    policy = _load_policy(snapshot, indexes)
    used_paths = set(EXPECTED_INDEX_PATHS.values())
    verified: list[Mapping[str, Any]] = []
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            raise BundleError(f"manual evidence {index} must be a JSON object")
        mode = document.get("mode")
        raw_result = _mapping(document.get("result"), f"manual evidence {index}.result")
        if mode == "command":
            requested = _string(
                raw_result.get("command"),
                f"manual evidence {index}.result.command",
            )
            result = _command_result(indexes, policy, requested, used_paths)
        elif mode == "read":
            kind = raw_result.get("kind")
            if kind == "section":
                requested = "section:" + _string(
                    raw_result.get("id"),
                    f"manual evidence {index}.result.id",
                )
            elif kind == "page":
                requested = _string(
                    raw_result.get("path"),
                    f"manual evidence {index}.result.path",
                )
            else:
                raise BundleError(
                    f"manual evidence {index} read result must be a page or section"
                )
            result = _read_result(snapshot, indexes, requested, used_paths)
        else:
            raise BundleError(
                f"manual evidence {index} mode must be 'command' or 'read'"
            )
        expected = _envelope(snapshot, str(mode), result)
        if document != expected:
            raise BundleError(
                f"manual evidence {index} is not the deterministic pinned query result"
            )
        verified.append(expected)
    snapshot.reverify(used_paths)
    current_policy = read_skill_file(snapshot.skill_root, POLICY_PATH)
    if not captures_match(policy.capture, current_policy):
        raise BundleError("command policy changed while verifying manual evidence")
    return tuple(verified)


def map_script_commands(
    commands: Sequence[str],
    *,
    protocol: str,
    skill_root: Path | None = None,
) -> Mapping[str, Any]:
    """Bind actual SSF commands to the frozen index and current policy.

    This is a deterministic integrity mapping, not a manual query, workflow
    recommendation, or source of execution authority.
    """

    snapshot = verify_bundle(skill_root)
    indexes = _load_indexes(snapshot)
    policy = _load_policy(snapshot, indexes)
    used_paths = set(EXPECTED_INDEX_PATHS.values())
    indexed = {_normalize(item["name"]): item for item in indexes.commands}
    mapped: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for raw_name in commands:
        name = _normalize(str(raw_name))
        if not name or name in seen:
            continue
        seen.add(name)
        command = indexed.get(name)
        if command is None:
            raise BundleError(f"SSF command is absent from the pinned manual: {raw_name}")
        used_paths.update(_validated_component_paths(command))
        used_paths.add("source/doc/Commands.rst")
        for include_path in command.get("include_paths", []):
            used_paths.add("source/" + include_path)
        allowed_protocols = list(policy.allowed_protocols.get(name, ()))
        if command.get("scriptable") is not True or protocol not in allowed_protocols:
            raise BundleError(
                f"SSF command is not scriptable and policy-authorized for {protocol}: {name}"
            )
        mapped.append(
            {
                "command": command["name"],
                "path": _logical_doc_path(command["path"]),
                "source_sha256": command["source_sha256"],
                "section_id": command["section_id"],
                "entry_sha256": command["sha256"],
                "scriptable": True,
                "policy_authorized": True,
            }
        )
    snapshot.reverify(used_paths)
    current_policy = read_skill_file(snapshot.skill_root, POLICY_PATH)
    if not captures_match(policy.capture, current_policy):
        raise BundleError("command policy changed while mapping SSF commands")
    return {
        "manual": bundle_metadata(snapshot),
        "command_policy": {
            "path": POLICY_PATH,
            "sha256": policy.capture.sha256,
            "size": policy.capture.size_bytes,
        },
        "protocol": protocol,
        "commands": mapped,
    }


def _error_envelope(code: str, message: str) -> Mapping[str, Any]:
    return {
        "schema": QUERY_SCHEMA,
        "status": "error",
        "manual": {
            "version": MANUAL_VERSION,
            "commit": MANUAL_COMMIT,
            "upstream_reverified_now": False,
        },
        "error": {"code": code, "message": message},
    }


def _emit_json(document: Mapping[str, Any], stream: TextIO) -> None:
    stream.write(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))
    stream.write("\n")


def _atomic_write_json_exclusive(path_value: str, document: Mapping[str, Any]) -> None:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        raise QueryUsageError("--output requires an absolute path")
    path = path.absolute()
    parent = path.parent
    if (
        path.exists()
        or path.is_symlink()
        or parent.is_symlink()
        or not parent.is_dir()
        or parent.resolve() != parent
    ):
        raise QueryUsageError("--output must name a new file in a real existing directory")
    data = (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )
    temporary = parent / f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        try:
            directory_fd = os.open(parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    except (FileExistsError, OSError) as exc:
        raise QueryUsageError(f"cannot create --output without replacing a file: {path}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _emit_text(document: Mapping[str, Any], stream: TextIO) -> None:
    if "error" in document:
        stream.write(f"error[{document['error']['code']}]: {document['error']['message']}\n")
        return
    manual = document["manual"]
    stream.write(
        f"Siril manual {manual['version']} @ {manual['commit']} "
        f"(bundle {manual['bundle_fingerprint']})\n"
    )
    mode = document["mode"]
    result = document["result"]
    if mode == "verify_bundle":
        stream.write(
            f"verified: {result['component_files']} files, "
            f"{result['commands']} commands, {result['sections']} sections\n"
        )
        stream.write(
            f"manifest sha256: {manual['manifest_sha256']}\n"
            f"tree sha256: {manual['tree_sha256']}\n"
        )
    elif mode == "search":
        stream.write(f"status: {result['status']}\n")
        for item in result["results"]:
            stream.write(
                f"- {item['id']} [{item['kind']}] score={item['score']}\n"
                f"  {item['title']} — {item['path']}\n"
                f"  source sha256: {item['source_sha256']}\n"
                f"  {item['snippet']}\n"
            )
        if result["unmatched_terms"]:
            stream.write("unmatched terms: " + ", ".join(result["unmatched_terms"]) + "\n")
    elif mode == "command":
        documentation = result["documentation"]
        policy = result["execution_policy"]
        stream.write(f"command: {result['command']} ({policy['state']})\n")
        stream.write(f"scriptable: {str(documentation['scriptable']).lower()}\n")
        stream.write(f"source sha256: {documentation['source_sha256']}\n")
        stream.write(f"policy sha256: {policy['policy_sha256']}\n")
        stream.write(f"usage: {documentation['usage']}\n")
        stream.write(documentation["description"] + "\n")
        stream.write("allowed protocols: " + ", ".join(policy["allowed_protocols"]) + "\n")
    elif mode == "read":
        stream.write(
            f"{result['kind']}: {result.get('id', result.get('path', ''))}\n"
        )
        stream.write(f"source sha256: {result['source_sha256']}\n")
        if result["kind"] == "section":
            stream.write(f"slice sha256: {result['slice_sha256']}\n")
        else:
            stream.write(f"resolved sha256: {result['resolved_sha256']}\n")
        stream.write(result["content"])
        if result["content"] and not result["content"].endswith("\n"):
            stream.write("\n")


def _parser() -> _ArgumentParser:
    parser = _ArgumentParser(
        prog="query_siril_manual.py",
        description="Query the pinned, integrity-checked Siril 1.4.4 manual.",
    )
    parser.add_argument("query", nargs="?", help="English or reviewed Chinese search text")
    parser.add_argument("--command", metavar="NAME", help="look up one exact Siril command")
    parser.add_argument("--read", metavar="DOC_OR_SECTION", help="read doc/... or section:<id>")
    parser.add_argument("--verify-bundle", action="store_true", help="verify the complete offline bundle")
    parser.add_argument("--top", type=int, help="maximum search results, from 1 to 10 (default: 5)")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--output", help="atomically create one absolute JSON evidence file")
    return parser


def _parse_args(argv: Sequence[str]) -> tuple[argparse.Namespace, str]:
    args = _parser().parse_args(list(argv))
    selected = [
        args.query is not None,
        args.command is not None,
        args.read is not None,
        args.verify_bundle,
    ]
    if sum(selected) != 1:
        raise QueryUsageError(
            "choose exactly one of a search query, --command, --read, or --verify-bundle"
        )
    if args.top is not None and args.query is None:
        raise QueryUsageError("--top is valid only with a search query")
    if args.top is not None and not 1 <= args.top <= 10:
        raise QueryUsageError("--top must be from 1 to 10")
    if args.output is not None and args.format != "json":
        raise QueryUsageError("--output is available only with --format json")
    mode = (
        "search"
        if args.query is not None
        else "command"
        if args.command is not None
        else "read"
        if args.read is not None
        else "verify_bundle"
    )
    return args, mode


def run(
    argv: Sequence[str],
    *,
    skill_root: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Execute one query.  skill_root exists for isolated tests, not as a CLI option."""

    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    output_format = "json"
    try:
        args, mode = _parse_args(argv)
        output_format = args.format
        snapshot = verify_bundle(skill_root)
        indexes = _load_indexes(snapshot)
        policy = _load_policy(snapshot, indexes)
        used_paths = set(EXPECTED_INDEX_PATHS.values())
        if mode == "search":
            result = _search(
                indexes,
                args.query,
                args.top if args.top is not None else 5,
                used_paths,
            )
        elif mode == "command":
            result = _command_result(indexes, policy, args.command, used_paths)
        elif mode == "read":
            result = _read_result(snapshot, indexes, args.read, used_paths)
        else:
            used_paths.update(snapshot.captures)
            result = bundle_verification_document(snapshot)["result"]
        snapshot.reverify(used_paths)
        current_policy = read_skill_file(snapshot.skill_root, POLICY_PATH)
        if not captures_match(policy.capture, current_policy):
            raise BundleError("command policy changed during the query")
        document = _envelope(snapshot, mode, result)
        if args.output is not None:
            _atomic_write_json_exclusive(args.output, document)
            _emit_json(document, stdout)
        elif output_format == "text":
            _emit_text(document, stdout)
        else:
            _emit_json(document, stdout)
        return 0
    except QueryUsageError as exc:
        document = _error_envelope("usage_error", str(exc))
        (_emit_text if output_format == "text" else _emit_json)(document, stderr)
        return 2
    except ExactNotFound as exc:
        document = _error_envelope("not_found", str(exc))
        (_emit_text if output_format == "text" else _emit_json)(document, stderr)
        return 3
    except BundleError as exc:
        document = _error_envelope("bundle_integrity_error", str(exc))
        (_emit_text if output_format == "text" else _emit_json)(document, stderr)
        return 1


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
