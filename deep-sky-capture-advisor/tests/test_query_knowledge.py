from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from knowledge_common import is_valid_human_verification


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "query_knowledge.py"
KNOWLEDGE_ROOT = SKILL_ROOT / "references" / "knowledge"
CATALOG = SKILL_ROOT / "references" / "catalog.json"
MANIFEST = SKILL_ROOT / "references" / "manifest.json"
TOTAL_INDEX = KNOWLEDGE_ROOT / "index.md"


def run_query(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), *arguments],
        cwd=SKILL_ROOT.parent,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_city_balcony_first_session_routes_to_actionable_pages() -> None:
    completed = run_query("城市阳台第一次深空拍摄如何搭建并安全运行", "--top", "5")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    titles = {item["title"] for item in payload["results"]}
    assert titles & {"城市阳台首次深空拍摄", "现场搭建流程", "供电、线缆与现场运行安全"}


def test_smart_telescope_siril_query_finds_the_specific_workflow() -> None:
    completed = run_query("智能望远镜导出 FITS 以后如何用 Siril 后期", "--top", "5")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    titles = [item["title"] for item in payload["results"]]
    assert "智能望远镜导出数据的 Siril 工作流" in titles
    assert all(item["path"].startswith("references/knowledge/") for item in payload["results"])
    assert payload["guidance"]["requires_web_verification"] is True
    assert (
        "version_sensitive_claim_uses_unbundled_source_ledger"
        in payload["guidance"]["web_verification_reasons"]
    )


def test_bundle_integrity_and_authority_state() -> None:
    completed = run_query("--verify-bundle")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["bundle"]["content_page_count"] == 51
    assert payload["bundle"]["human_verified_page_count"] == 0
    assert payload["markdown_file_count"] == 52
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["bundle"]["navigation_page_count"] == 1
    assert manifest["bundle"]["markdown_file_count"] == 52


def test_read_cannot_escape_the_bundled_root() -> None:
    completed = run_query("--read", "../../README.md")
    assert completed.returncode == 2
    assert "inside references/knowledge" in completed.stderr


def test_read_accepts_knowledge_root_links() -> None:
    completed = run_query("--read", "/03-拍摄SOP/现场搭建流程.md", "--format", "text")
    assert completed.returncode == 0, completed.stderr
    assert "# 现场搭建流程" in completed.stdout


def test_catalog_preserves_scope_sources_and_explicit_raw_boundary() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = catalog["entries"]
    unbundled = {item["path"] for item in catalog["unbundled_internal_sources"]}
    assert len(entries) == 51
    assert len(unbundled) == 7
    assert all(entry["applies_to"] and entry["sources"] for entry in entries)
    for entry in entries:
        for link in entry["links"]:
            if link.startswith("raw/"):
                assert link in unbundled
            else:
                assert (KNOWLEDGE_ROOT / link).exists(), (entry["path"], link)


def test_current_weather_and_visibility_query_requires_web_verification() -> None:
    completed = run_query("今晚上海云量和 M31 可见时间", "--top", "3")
    assert completed.returncode == 0, completed.stderr
    guidance = json.loads(completed.stdout)["guidance"]
    assert guidance["requires_web_verification"] is True
    assert "current_observing_conditions_or_visibility" in guidance["web_verification_reasons"]


def test_explicit_weather_opt_out_does_not_force_web_or_uncovered_aps_c() -> None:
    completed = run_query(
        "我用 80mm APO、APS-C 相机和赤道仪，在城市阳台第一次拍 M31。不要查今晚天气，请给通用准备和拍摄 SOP。",
        "--top",
        "5",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    guidance = payload["guidance"]
    assert guidance["skill_scope"] == "in_scope"
    assert guidance["bundle_coverage"] == "sufficient"
    assert guidance["requires_web_verification"] is False
    assert guidance["web_verification_reasons"] == []
    assert "天文相机选型" in guidance["matched_core_terms"]
    assert "观测天气" not in guidance["matched_core_terms"]
    assert "aps-c" not in guidance["unmatched_core_terms"]


def test_english_equipment_intent_is_normalized_and_each_core_intent_is_covered() -> None:
    completed = run_query("How should I choose a telescope and mount for M31?", "--top", "5")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    titles = {item["title"] for item in payload["results"]}
    guidance = payload["guidance"]
    assert {"望远镜选型", "赤道仪选型"} <= titles
    assert titles & {"四季目标推荐", "经典目标案例与参数起点"}
    assert guidance["skill_scope"] == "in_scope"
    assert guidance["bundle_coverage"] == "sufficient"
    assert {"M31仙女座", "望远镜选型", "赤道仪选型"} <= set(
        guidance["matched_core_terms"]
    )
    assert guidance["unmatched_core_terms"] == []


def test_english_current_weather_gate_uses_original_and_normalized_intent() -> None:
    completed = run_query("What is the weather tonight in Shanghai for M31?", "--top", "3")
    assert completed.returncode == 0, completed.stderr
    guidance = json.loads(completed.stdout)["guidance"]
    assert guidance["skill_scope"] == "in_scope"
    assert guidance["requires_web_verification"] is True
    assert "current_observing_conditions_or_visibility" in guidance["web_verification_reasons"]


def test_transit_and_explicit_date_require_web_verification() -> None:
    completed = run_query("M31 transit on 2026-09-01", "--top", "3")
    assert completed.returncode == 0, completed.stderr
    guidance = json.loads(completed.stdout)["guidance"]
    assert guidance["skill_scope"] == "in_scope"
    assert guidance["requires_web_verification"] is True
    assert "current_observing_conditions_or_visibility" in guidance["web_verification_reasons"]


def test_in_scope_spectroscopy_without_strong_coverage_returns_no_results_and_requires_web() -> None:
    completed = run_query("如何用光谱仪拍摄类星体光谱？", "--top", "5")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    guidance = payload["guidance"]
    assert payload["results"] == []
    assert guidance["skill_scope"] == "in_scope"
    assert guidance["bundle_coverage"] == "insufficient"
    assert "光谱观测" in guidance["unmatched_core_terms"]
    assert guidance["requires_web_verification"] is True
    assert "no_bundled_coverage" in guidance["web_verification_reasons"]


def test_partial_core_coverage_fails_closed_instead_of_returning_generic_pages() -> None:
    completed = run_query("深空光谱仪波长校准", "--top", "5")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    guidance = payload["guidance"]
    assert payload["results"] == []
    assert guidance["bundle_coverage"] == "insufficient"
    assert "校准与叠加" in guidance["matched_core_terms"]
    assert "光谱观测" in guidance["unmatched_core_terms"]


def test_unknown_astronomy_model_is_in_scope_but_not_covered_by_generic_camera_page() -> None:
    completed = run_query("ASI2600MC Pro current specs", "--top", "5")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    guidance = payload["guidance"]
    assert payload["results"] == []
    assert guidance["skill_scope"] == "in_scope"
    assert guidance["bundle_coverage"] == "insufficient"
    assert "ASI2600MC PRO" in guidance["unmatched_core_terms"]
    assert guidance["requires_web_verification"] is True
    assert "current_product_or_software_state" in guidance["web_verification_reasons"]


def test_unknown_format_with_known_software_is_not_hidden_by_partial_match() -> None:
    completed = run_query("How do I open FooRAW format in Siril?", "--top", "5")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    guidance = payload["guidance"]
    assert payload["results"] == []
    assert guidance["skill_scope"] == "in_scope"
    assert guidance["bundle_coverage"] == "insufficient"
    assert "Siril" in guidance["matched_core_terms"]
    assert "fooraw" in guidance["unmatched_core_terms"]
    assert guidance["requires_web_verification"] is True


def test_out_of_scope_query_exits_without_turning_empty_results_into_web_research() -> None:
    completed = run_query("How do I make pizza?", "--top", "5")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    guidance = payload["guidance"]
    assert payload["results"] == []
    assert guidance["skill_scope"] == "out_of_scope"
    assert guidance["bundle_coverage"] == "insufficient"
    assert guidance["recommended_action"] == "exit_skill"
    assert guidance["should_exit_skill"] is True
    assert guidance["requires_web_verification"] is False
    assert guidance["web_verification_reasons"] == []


def test_lunar_imaging_is_out_of_scope_but_moon_phase_planning_is_not() -> None:
    lunar = run_query("How do I do lucky imaging of the Moon?", "--top", "3")
    assert lunar.returncode == 0, lunar.stderr
    lunar_guidance = json.loads(lunar.stdout)["guidance"]
    assert lunar_guidance["skill_scope"] == "out_of_scope"
    assert "excluded_lunar_imaging" in lunar_guidance["scope_reasons"]

    moon_phase = run_query("月相如何影响 M31 深空拍摄窗口？", "--top", "3")
    assert moon_phase.returncode == 0, moon_phase.stderr
    moon_payload = json.loads(moon_phase.stdout)
    assert moon_payload["guidance"]["skill_scope"] == "in_scope"
    assert moon_payload["results"]


def test_file_backed_analysis_exits_to_adjacent_advisor() -> None:
    completed = run_query("Analyze this FITS image file for gradients", "--top", "3")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    guidance = payload["guidance"]
    assert payload["results"] == []
    assert guidance["skill_scope"] == "out_of_scope"
    assert guidance["recommended_route"] == "$deep-sky-advisor"
    assert guidance["requires_web_verification"] is False


def test_static_bortle_explanation_uses_local_bundle_without_forcing_web() -> None:
    completed = run_query("What is Bortle and how should I use it for site selection?", "--top", "3")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert "光污染地图与Bortle" in {item["title"] for item in payload["results"]}
    assert payload["guidance"]["bundle_coverage"] == "sufficient"
    assert payload["guidance"]["requires_web_verification"] is False


def test_focused_m31_query_finds_target_and_framing_pages() -> None:
    completed = run_query("M31 仙女座 目标 季节 构图", "--top", "5")
    assert completed.returncode == 0, completed.stderr
    titles = {item["title"] for item in json.loads(completed.stdout)["results"]}
    assert "四季目标推荐" in titles
    assert "经典目标案例与参数起点" in titles


def test_text_output_displays_web_verification_gate() -> None:
    completed = run_query("今晚上海云量", "--top", "3", "--format", "text")
    assert completed.returncode == 0, completed.stderr
    assert "Web verification required:" in completed.stdout


def test_human_verification_requires_actor_time_and_scope() -> None:
    assert is_valid_human_verification({"by": "human:reviewer", "at": "2026-08-28", "scope": "SOP"})
    assert not is_valid_human_verification({"by": "model:reviewer", "at": "2026-08-28", "scope": "SOP"})
    assert not is_valid_human_verification({"by": "human:reviewer", "at": "2026-08-28"})
    assert not is_valid_human_verification({"by": "human:reviewer", "scope": "SOP"})


def test_only_generated_total_index_remains() -> None:
    indexes = sorted(KNOWLEDGE_ROOT.rglob("index.md"))
    assert indexes == [TOTAL_INDEX]


def test_total_index_covers_every_catalog_page_once() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    content = TOTAL_INDEX.read_text(encoding="utf-8")
    assert not content.startswith("---\n")
    assert "仅供人工浏览" in content
    assert "非权威参考" in content
    for entry in catalog["entries"]:
        marker = f"](<{entry['path']}>)"
        assert content.count(marker) == 1, entry["path"]
        assert (KNOWLEDGE_ROOT / entry["path"]).is_file()
