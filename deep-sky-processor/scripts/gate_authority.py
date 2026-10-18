"""门禁授权契约：把「授权字段」与「诊断字段」分开，并封印前者。

## 要解决的问题

`collect_quality_gates`（本模块）与 `build_quality_state`（本模块）都用
``gate.get('escalate', True) and gate.get('status') in ('warning','failed')``
决定一条门禁是否升级为 issue、是否阻断 ``accept``。而 ``status`` 与
``escalate`` **都由门禁模块自己填**（``agent_protocol.add`` /
``artifact_gates._gate`` / ``fidelity_gates._gate``）。这构成一个循环依赖：

    门禁自己填 status/escalate  →  质量层据此决定升级  →  issue 阻断 accept

链条上任何一环把 ``status`` 从 ``failed`` 改成 ``passed``（或把 ``escalate``
改成 ``False``），整条质量继承链会**静默失效** —— 没有任何机制会发现。

本模块借鉴 starun 侧 ``stage8_handoff.py:20`` 的思路
（``_UNBOUND_DIAGNOSTIC_FIELDS = frozenset({"final_quality"})``，
「可变的诊断标签不得参与授权决策」），用**封印 + 复验**把授权字段钉住。

## 授权字段 vs 诊断字段

**授权字段**（进入 digest，任何改动都会被检出）：

    code / status / escalate / value / threshold

**诊断字段**（**不**进入 digest，允许自由改写 —— 它们是给人看的）：

    message / evidence / summary / context / attribution

这正是 starun 侧的划分标准：*every actual safety and identity field is bound*，
而可变的诊断字符串被显式排除。

## 状态的三分类

- **授权状态** ``warning`` / ``failed`` —— 唯一能产生 issue 的状态。
- **诊断状态** ``degraded`` / ``unavailable`` / ``uncalibrated`` /
  ``skipped`` / ``passed`` —— **按定义不授权**。它们记录事实（包括
  「测不到」这种不等于健康的事实），但不升级、不阻断。
  ``degraded`` 尤其重要：它必须可见但不可授权，否则「阈值偏紧」会变成
  「门禁失效」。
- 未知状态一律按**不授权**处理（``DIAGNOSTIC_STATUSES`` 之外的落到
  ``escalate`` 判定），避免新状态默认放行。

## 保护范围（诚实声明）

**能检出**：门禁在其``_gate()`` 构造之后被改写 ``status`` / ``escalate`` /
``value`` / ``threshold``；跨 run 的 ``issues`` 被事后编辑。

**不能检出**：门禁**首次构造时**就填错（例如阈值算错导致本该 failed 却填
passed）。那需要「按合同阈值重算判定」的阈值注册表（starun 侧的
``STAGE3_GATE_PROFILES`` 模式），当前 A 侧的阈值分散在
``fidelity_gates.THRESHOLDS`` / ``artifact_gates`` 的模块常量里，
强行集中会引入「两份阈值」的新耦合 —— 留给后续，本次不做。

**也不试图防御**：同一进程内、在聚合点之前就完成的有意改动。封印能证明
「这条门禁从产出到落盘没被改过」，不能证明「产出时就是对的」。
"""

import hashlib
import json


#: 进入授权 digest 的字段。改动其中任何一个都会被 verify 检出。
AUTHORIZATION_FIELDS = (
    'code', 'status', 'escalate', 'value', 'threshold',
)

#: 明确排除在 digest 之外的诊断字段。允许自由改写（措辞、补充证据、
#: 标注归因），不影响授权。
DIAGNOSTIC_FIELDS = frozenset({
    'message', 'evidence', 'summary', 'context', 'attribution',
    'authorization_digest',      # 自身，不参与自己的计算
})

#: 唯一授权的状态。其余状态只记录事实。
AUTHORIZING_STATUSES = frozenset({'warning', 'failed'})

#: 按定义不授权的状态。``degraded`` 在此 —— 它必须可见但不可升级。
DIAGNOSTIC_STATUSES = frozenset({
    'degraded', 'unavailable', 'uncalibrated', 'skipped', 'passed', 'error',
})


def _canonical(value):
    """稳定的 JSON 序列化（排序键、无空格、保留中文）。"""
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, default=str)


def gate_authorization_digest(gate):
    """计算门禁授权字段的 digest。

    只覆盖 :data:`AUTHORIZATION_FIELDS`；``evidence`` 里的数值改动不影响
    授权判定（证据是给人核对的，判定依据是 ``value`` 与 ``threshold``）。
    """
    payload = {key: gate.get(key) for key in AUTHORIZATION_FIELDS}
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()[:20]


def seal_gate(gate):
    """在门禁**创建时**封印授权字段。

    幂等但**会复验**：已带封印的门禁先核对 digest，匹配才原样返回。
    这一步是「篡改后重新流经聚合点」的拦截点 —— 若这里无条件返回，
    上游把 ``status`` 从 ``failed`` 改成 ``passed`` 的门禁会因为
    「已封印」而被 ``gate_authorizes`` 放行，机制形同虚设。

    digest 不匹配时**重新封印**并把 ``authorization_tampered`` 置 True。
    之所以不直接抛异常：门禁流经多个聚合层（evaluate_quality_gates →
    collect_quality_gates → build_quality_state），在其中一层抛出会让
    整条管线失败；而正确行为是**该门禁不授权 + 留下可查痕迹**。
    ``verify_quality_state`` 在落盘后复验时会硬失败（见quality_state）。
    """
    if 'authorization_digest' not in gate:
        return {**gate, 'authorization_digest': gate_authorization_digest(gate)}
    if gate_authorization_digest(gate) == gate['authorization_digest']:
        return gate
    # 封印与内容不符 —— 授权字段被改过。重新封印并标记，让 gate_authorizes
    # 看到标记后拒绝授权。
    return {
        **gate,
        'authorization_digest': gate_authorization_digest(gate),
        'authorization_tampered': True,
    }


def verify_gate_authorization(gate):
    """复验门禁授权字段是否与封印一致。

    返回 ``(ok, reason)``。``ok`` 为 False 时该门禁**不得参与授权决策**。
    """
    sealed = gate.get('authorization_digest')
    if sealed is None:
        return False, 'gate carries no authorization digest'
    if gate_authorization_digest(gate) != sealed:
        return False, 'authorization fields changed after sealing'
    return True, ''


def gate_authorizes(gate, *, sealed_here=False):
    """该门禁是否授权升级。

    判定顺序（任一不满足即不授权）：

    0. ``authorization_tampered`` 为真 ⇒ **不授权**（``seal_gate`` 检测到
       授权字段与原封印不符）
    1. 授权字段与封印一致（``sealed_here=True`` 表示刚由本函数补封印，
       跳过复验—— 它是**聚合入口**，此刻还没有「中途改动」可言）
    2. ``status`` 属于 :data:`AUTHORIZING_STATUSES`
    3. ``escalate`` 为真

    未知状态落到第 2 条 ⇒ 不授权。宁可漏报也不误放行。
    """
    if gate.get('authorization_tampered'):
        return False, 'authorization fields were modified after sealing'
    ok, reason = (True, '') if sealed_here else verify_gate_authorization(gate)
    if not ok:
        return False, reason
    status = gate.get('status')
    if status not in AUTHORIZING_STATUSES:
        if status not in DIAGNOSTIC_STATUSES:
            return False, 'unknown gate status %r does not authorize' % (status,)
        return False, 'diagnostic status %r does not authorize' % (status,)
    if not gate.get('escalate', True):
        return False, 'gate is escalate=False'
    return True, ''


def annotate_authorization(gates):
    """给聚合结果附上授权判定，供报告与人工审查核对。

    每条门禁增``authorization`` 字段：``{authorizes, reason, sealed}``。
    纯诊断用途 —— 下游是否采信由调用方决定，但报告里能看到「为什么这条
    没升级」，避免静默。
    """
    annotated = []
    for gate in gates or []:
        sealed = 'authorization_digest' in gate
        sealed_here = False
        if not sealed:
            gate = seal_gate(gate)
            sealed_here = True
        authorizes, reason = gate_authorizes(gate, sealed_here=sealed_here)
        annotated.append({
            **gate,
            'authorization': {
                'authorizes': authorizes,
                'reason': reason,
                'sealed': True,
                'was_sealed_upstream': sealed,
            },
        })
    return annotated
