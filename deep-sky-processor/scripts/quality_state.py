"""Artifact-bound quality findings shared by the pipeline and Agent workflow."""

import hashlib
import json
from pathlib import Path

from gate_authority import gate_authorizes, seal_gate, verify_gate_authorization


def artifact_digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read_quality_state(path):
    sidecar = Path(str(path) + '.meta.json')
    state = json.loads(sidecar.read_text()).get('quality_state') if sidecar.exists() else None
    if state is not None:
        verify_quality_state(path, state)
    return state


def verify_quality_state(path, state):
    if (state.get('artifact') != str(Path(path).resolve())
            or state.get('artifact_sha256') != artifact_digest(path)):
        raise ValueError('Quality state artifact has changed or does not match')
    if (not isinstance(state.get('issues'), list) or any(not isinstance(issue, dict)
            or not all(key in issue for key in ('id', 'code', 'status', 'origin_artifact', 'origin_step'))
            for issue in state['issues'])):
        raise ValueError('Quality state issues must contain identified findings')
    # 逐条复验 issue 所携带门禁的封印 —— 落盘后被改写 status/escalate 的
    # issue 会被检出，而不是静默参与 accept 判定。见 gate_authority 文档。
    for issue in state['issues']:
        evidence = issue.get('evidence')
        if not isinstance(evidence, dict) or 'authorization_digest' not in evidence:
            continue
        ok, reason = verify_gate_authorization(evidence)
        if not ok:
            raise ValueError(
                'Quality state issue %r was modified after sealing: %s'
                % (issue.get('code'), reason))


def collect_quality_gates(payload):
    gates = list(payload.get('quality_gates') or [])
    gates += (payload.get('star_artifact_gates') or {}).get('gates', [])
    diagnostics = payload.get('stretch_diagnostics') or {}
    gates += (diagnostics.get('display_gates') or {}).get('gates', [])
    plateau = diagnostics.get('core_plateau') or {}
    if plateau.get('escalate'):
        gates.append(plateau)
    # 授权判定走 gate_authorizes()，不再直接读 gate 自填的 status/escalate。
    # 门禁构造器已封印授权字段；此处对未封印者（第三方 / 遗留）兜底封印，
    # 之后任何改写都会被 verify 检出。见 gate_authority 模块文档的
    # 「保护范围」一节 —— 首次构造即填错的情况不在本机制覆盖内。
    gates = [gate for gate in gates if gate_authorizes(seal_gate(gate))[0]]
    visual = payload.get('visual_review') or {}
    if isinstance(visual, dict) and visual.get('verdict') in ('reject', 'rejected'):
        gates.append({'code': 'VISUAL_REVIEW_REJECTED', 'status': 'failed', 'evidence': visual})
    if not gates and payload.get('status', 'success') not in ('success', 'ok', 'skipped'):
        gates.append({'code': 'PROCESSING_REVIEW_REQUIRED', 'status': 'warning',
                      'evidence': {'status': payload['status'], 'warnings': payload.get('warnings', [])}})
    return gates


def derive_status(issues, degraded_stages=()):
    """由 issues 与降级阶段推导 status。

    优先级（与 starun 侧``outcome.summarize_outcome`` 的判定链同序）：

        failed            任一阶段声明了 stop
        review_required   有未解决的 issue
        degraded          无 issue，但有阶段走了回退/preserve 路径
        success           干净出图

    **degraded 不阻断 accept** —— 它是「可见、可审、可接受」的中间态。
    此前 status 只有二值，导致「拉伸第 2 级 masked 成功」与「一次成功的
    pi_mtf」完全无法区分（见 failure_policy 模块文档）。
    """
    if 'stop' in set(degraded_stages or ()):
        return 'failed'
    if issues:
        return 'review_required'
    if degraded_stages:
        return 'degraded'
    return 'success'


def build_quality_state(path, parent=None, parent_state=None, step='input',
                        gates=(), degraded_stages=(), config_fingerprint=None):
    """构建 quality_state。

    ``config_fingerprint``
        累计配置指纹（``config_fingerprint.stage_fingerprint``）。**它的存在
        本身就是本次改造的要点** —— `artifact_sha256` 只能证明「文件没被改」，
        证明不了「配置没被改」。改一个上游参数重跑，旧配置的 issues 会被
        ``parent_state`` 继承到新产物上，除非有配置指纹把关。
        见 config_fingerprint 模块文档。
    """
    artifact = str(Path(path).resolve())
    digest = artifact_digest(path)
    inherited_config = (parent_state or {}).get('config_fingerprint')
    # 配置指纹变化 ⇒ 父状态的 issues 不再适用于当前产物。
    config_changed = bool(
        inherited_config and config_fingerprint
        and inherited_config != config_fingerprint)
    if parent_state and not config_changed:
        issues = {item['id']: dict(item)
                  for item in parent_state.get('issues', [])}
    else:
        # 无父状态，或配置已变 ⇒ 从零开始（旧的 findings 属于旧配置）
        issues = {}
    for gate in gates:
        # 授权判定统一走 gate_authorizes（见 collect_quality_gates 的注释）。
        # degraded / unavailable / uncalibrated / skipped 等诊断状态在此
        # **不产生 issue** —— 它们可见但不升级，这是 degraded 语义的前提。
        sealed = seal_gate(gate)
        if not gate_authorizes(sealed)[0]:
            continue
        code = gate.get('code', 'QUALITY_REVIEW_REQUIRED')
        issue_id = hashlib.sha256(f'{artifact}:{digest}:{code}'.encode()).hexdigest()[:20]
        issues.setdefault(issue_id, {'id': issue_id, 'code': code, 'status': gate['status'],
                                    'origin_artifact': artifact, 'origin_step': step,
                                    'evidence': gate})
    # 父状态的降级记录向后继承：本阶段干净不代表整条链路干净。
    inherited = list((parent_state or {}).get('degraded_stages') or ())
    stages = sorted(set(inherited) | set(degraded_stages or ()))
    return {'artifact': artifact, 'artifact_sha256': digest,
            'parent_artifact': str(Path(parent).resolve()) if parent else None,
            'status': derive_status(list(issues.values()), stages),
            'issues': list(issues.values()),
            'degraded_stages': stages,
            'config_fingerprint': config_fingerprint,
            'config_changed': bool(config_changed),
            'resolutions': list((parent_state or {}).get('resolutions', []))}


def persist_quality_state(path, state):
    from agent_protocol import write_json_atomic
    sidecar = Path(str(path) + '.meta.json')
    metadata = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    metadata['quality_state'] = state
    write_json_atomic(metadata, sidecar)


def resolve_quality_state(path, state, review):
    verify_quality_state(path, state)
    if (review.get('artifact') != str(Path(path).resolve())
            or review.get('artifact_sha256') != state['artifact_sha256']):
        raise ValueError('Review must identify the current artifact and SHA-256')
    pending = {item['id']: item for item in state['issues']}
    resolutions = list(state.get('resolutions', []))
    for resolution in review.get('resolutions', []):
        issue = pending.get(resolution.get('issue_id'))
        if issue is None:
            raise ValueError('Resolution must identify an unresolved issue')
        outcome = resolution.get('outcome')
        evidence = resolution.get('evidence')
        if outcome not in ('fixed', 'false_positive', 'inherited') or not resolution.get('reason'):
            raise ValueError('Resolution requires an outcome and a reason')
        if not isinstance(evidence, list) or not evidence or any(not Path(p).is_file() for p in evidence):
            raise ValueError('Resolution requires existing evidence files')
        if outcome == 'fixed':
            checks = [json.loads(Path(p).read_text()) for p in evidence if Path(p).suffix == '.json']
            if not any(check.get('artifact_sha256') == state['artifact_sha256']
                       and any(g.get('code') == issue['code'] and g.get('status') == 'passed'
                               for g in check.get('quality_gates', [])) for check in checks):
                raise ValueError('Fixed defects require a passing check bound to the current artifact')
        elif issue['status'] == 'failed':
            checks = [json.loads(Path(p).read_text()) for p in evidence if Path(p).suffix == '.json']
            if not any(check.get('artifact_sha256') == state['artifact_sha256']
                       and check.get('issue_id') == issue['id'] and check.get('outcome') == outcome
                       for check in checks):
                raise ValueError('Failed findings require artifact-bound reclassification evidence')
        resolutions.append({**resolution, 'artifact': str(Path(path).resolve()),
                            'artifact_sha256': state['artifact_sha256']})
        del pending[issue['id']]
    return {**state, 'issues': list(pending.values()), 'resolutions': resolutions,
            'status': derive_status(list(pending.values()),
                                    state.get('degraded_stages') or ())}
