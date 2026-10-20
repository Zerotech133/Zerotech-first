"""部署与监控清单离线校验（工位R 初建，工位U 扩编，不要求本机 docker）。

校验范围：deploy/docker-compose.yaml、deploy/k8s.yaml、
monitoring/prometheus.yml、monitoring/alerts.yml 的对齐与可解析性。

骨架状态披露：compose/k8s 中 os-legion/*:PLACEHOLDER_TAG 均为占位镜像，
须替换后才能构建/部署；本测试只做对齐与可解析性校验，不承诺部署。
YAML 解析依赖 pyyaml（经 PYTHONPATH 挂载用户 site-packages），
safe_load_all 兼容多文档。

端口约定说明（部署侧口径：9400 + 10×序号）：
- 通用席位：9400 + 10×角色在 entrypoint.KNOWN_ROLES 中的序号
  （message_bus=9400 … os_commander=9470），见 _expected_port。
- 平台席六实例（角色均为 platform_agent）：quant=9440（基准端口），
  其余五平台按 PLATFORM_REGISTRY 注册顺序自 9480 起步长 10 连续排布
  （stock=9480 rd=9490 ppt=9500 biz=9510 review=9520）；
  9450–9470 段已被 risk_guard/approval_gate/os_commander 占用故跳过，
  9480–9520 为连续不冲突专用段，见 _expected_service_port。
TODO(挂账)：entrypoint.py 的 PORTS 常量为 9400+序号（步长1），
与部署侧步长10不一致；同波次工位S 正在统一（非本工位文件），
统一合入后 test_entrypoint_ports_aligned_when_unified 自动转为实质断言。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import entrypoint  # noqa: E402  # 纯常量模块，无副作用（main 有 __main__ 保护）
from roles.platform_agent import PLATFORM_REGISTRY  # noqa: E402  # 纯注册表常量


def _load_docs(rel: str) -> list:
    with open(ROOT / rel, encoding="utf-8") as f:
        return [d for d in yaml.safe_load_all(f) if d is not None]


def _compose() -> dict:
    docs = _load_docs("deploy/docker-compose.yaml")
    assert docs, "docker-compose.yaml 至少应有一个文档"
    return docs[0]


def _command_ports() -> dict[str, int]:
    """从 compose 各服务 command 的 --port 提取 {服务名: 端口}。"""
    services = _compose()["services"]
    ports = {}
    for name, svc in services.items():
        cmd = svc.get("command") or []
        assert "--port" in cmd, f"服务 {name} command 缺 --port"
        ports[name] = int(cmd[cmd.index("--port") + 1])
    return ports


def _expected_port(role: str) -> int:
    """部署侧端口约定：9400 + 10×角色在 KNOWN_ROLES 中的序号。

    message_bus=9400 … os_commander=9470（与 compose 头注释、prometheus
    采集目标、派工单既有事实一致）。
    """
    return 9400 + 10 * entrypoint.KNOWN_ROLES.index(role)


# 平台席六实例端口（部署侧扩编口径，见模块 docstring；键为 compose 服务名）。
_PLATFORM_PORTS: dict[str, int] = {
    "platform-quant": 9440,
    "platform-stock": 9480,
    "platform-rd": 9490,
    "platform-ppt": 9500,
    "platform-biz": 9510,
    "platform-review": 9520,
}

# compose 服务名 → entrypoint --platform 平台标识（与 PLATFORM_REGISTRY 对齐）。
_PLATFORM_FLAGS: dict[str, str] = {
    "platform-quant": "quant_trading",
    "platform-stock": "stock_picking",
    "platform-rd": "r_and_d",
    "platform-ppt": "ppt_studio",
    "platform-biz": "biz_negotiation",
    "platform-review": "review_audit",
}


def _expected_service_port(service: str) -> int:
    """服务级端口约定：平台实例走扩编段，其余席位按 9400+10×角色序号。"""
    if service in _PLATFORM_PORTS:
        return _PLATFORM_PORTS[service]
    return _expected_port(service.replace("-", "_"))


def test_compose_yaml_parseable() -> None:
    """compose YAML 可解析（safe_load_all），services 为字典。"""
    compose = _compose()
    services = compose.get("services")
    assert isinstance(services, dict) and services, "services 须为非空字典"


def test_seat_commands_match_seat_order_ports() -> None:
    """每服务 --port 符合部署侧端口约定：平台实例走扩编段
    （quant=9440、stock=9480…review=9520），其余席位 9400+10×角色序号。
    """
    services = _compose()["services"]
    for name, svc in services.items():
        cmd = svc["command"]
        role = cmd[cmd.index("--role") + 1]
        port = int(cmd[cmd.index("--port") + 1])
        assert role in entrypoint.KNOWN_ROLES, (
            f"服务 {name} 的角色 {role} 不在 KNOWN_ROLES"
        )
        expected = _expected_service_port(name)
        assert port == expected, (
            f"服务 {name} 端口 {port} 与端口约定 {expected} 不一致"
        )


def test_each_seat_command_contains_serve() -> None:
    """每个席位服务 command 均含 --serve。"""
    services = _compose()["services"]
    missing = [n for n, s in services.items()
               if "--serve" not in (s.get("command") or [])]
    assert not missing, f"缺 --serve 的服务：{missing}"


def test_os_commander_service_exists() -> None:
    """os_commander 服务在列（command 含 --role os_commander）。"""
    services = _compose()["services"]
    found = [n for n, s in services.items()
             if "--role" in (s.get("command") or [])
             and s["command"][s["command"].index("--role") + 1] == "os_commander"]
    assert found, "compose 缺 os_commander 服务"
    assert found[0] == "os-commander", f"os_commander 服务名应为 os-commander，实际 {found[0]}"


def test_six_platform_services_present() -> None:
    """六平台服务在列，--platform 取值与 PLATFORM_REGISTRY 平台标识逐一对应。"""
    services = _compose()["services"]
    for svc_name, flag in _PLATFORM_FLAGS.items():
        assert svc_name in services, f"compose 缺平台服务 {svc_name}"
        cmd = services[svc_name]["command"]
        assert cmd[cmd.index("--role") + 1] == "platform_agent", (
            f"{svc_name} 角色应为 platform_agent"
        )
        actual = cmd[cmd.index("--platform") + 1]
        assert actual == flag, f"{svc_name} --platform 应为 {flag}，实际 {actual}"
        assert flag in {e["name"] for e in PLATFORM_REGISTRY.values()}, (
            f"--platform 值 {flag} 不在 PLATFORM_REGISTRY 平台标识内"
        )


def test_platform_ports_extension_rule() -> None:
    """平台席扩编段：quant=9440（基准），其余五平台 9480–9520 步长 10
    连续不冲突，且不与通用席位段（9400–9470）重叠。
    """
    expected = {"platform-stock": 9480, "platform-rd": 9490,
                "platform-ppt": 9500, "platform-biz": 9510,
                "platform-review": 9520}
    actual = {n: _command_ports()[n] for n in expected}
    assert actual == expected, f"平台扩编段端口不符：{actual}"
    common_ports = {_expected_port(r) for r in entrypoint.KNOWN_ROLES}
    assert not set(actual.values()) & common_ports, (
        "平台扩编段端口不得与通用席位段 9400-9470 重叠"
    )


def test_compose_ports_match_prometheus_targets() -> None:
    """compose 服务端口集合与 prometheus.yml 采集目标集合逐项一致。"""
    compose_ports = _command_ports()
    prom = _load_docs("monitoring/prometheus.yml")[0]
    targets: set[tuple[str, int]] = set()
    for job in prom["scrape_configs"]:
        for static in job.get("static_configs", []):
            for t in static.get("targets", []):
                host, _, port = t.rpartition(":")
                targets.add((host, int(port)))
    compose_set = set(compose_ports.items())
    assert compose_set == targets, (
        f"compose 独有：{sorted(compose_set - targets)}；"
        f"prometheus 独有：{sorted(targets - compose_set)}"
    )


def test_alerts_all_have_owner() -> None:
    """alerts.yml 每条 alert 均有 owner（labels.owner 或 annotations.owner）。"""
    docs = _load_docs("monitoring/alerts.yml")
    missing: list[str] = []
    for doc in docs:
        for group in doc.get("groups", []):
            for rule in group.get("rules", []):
                owner = (rule.get("labels", {}).get("owner")
                         or rule.get("annotations", {}).get("owner"))
                if not owner:
                    missing.append(rule.get("alert", "<unnamed>"))
    assert not missing, f"以下 alert 缺 owner 责任人：{missing}"


def _k8s_deployments() -> list[dict]:
    return [d for d in _load_docs("deploy/k8s.yaml")
            if d.get("kind") == "Deployment"]


def _k8s_component_roles() -> set[str]:
    """从 k8s Deployment component 标签归位角色名（platform-* → platform_agent）。"""
    roles: set[str] = set()
    for dep in _k8s_deployments():
        component = dep["metadata"]["labels"]["component"]
        roles.add("platform_agent" if component.startswith("platform-")
                  else component.replace("-", "_"))
    return roles


def test_k8s_yaml_multidoc_required_fields() -> None:
    """k8s.yaml 多文档可解析；每文档 apiVersion/kind/metadata.name 齐全，
    Deployment 另查 spec（含 template.spec.containers）与 replicas=1。"""
    docs = _load_docs("deploy/k8s.yaml")
    assert len(docs) >= 2, f"k8s.yaml 应为多文档，实际 {len(docs)} 个"
    for i, doc in enumerate(docs):
        assert doc.get("apiVersion"), f"第 {i} 文档缺 apiVersion"
        assert doc.get("kind"), f"第 {i} 文档缺 kind"
        name = doc.get("metadata", {}).get("name")
        assert name, f"第 {i} 文档缺 metadata.name"
        if doc["kind"] == "Deployment":
            spec = doc.get("spec")
            assert spec, f"Deployment {name} 缺 spec"
            assert spec.get("replicas") == 1, f"Deployment {name} 副本数应为 1"
            containers = (spec.get("template", {}).get("spec", {})
                          .get("containers"))
            assert containers, f"Deployment {name} 缺 template.spec.containers"


def test_k8s_covers_all_eight_roles() -> None:
    """k8s Deployment 覆盖全部 8 席位（KNOWN_ROLES 角色逐一在列）。"""
    roles = _k8s_component_roles()
    assert roles == set(entrypoint.KNOWN_ROLES), (
        f"k8s 缺席位：{sorted(set(entrypoint.KNOWN_ROLES) - roles)}；"
        f"k8s 多出：{sorted(roles - set(entrypoint.KNOWN_ROLES))}"
    )


def test_k8s_deployments_match_compose_services() -> None:
    """k8s Deployment 与 compose 服务一一对齐（component 集合 == 服务名集合）。"""
    components = {d["metadata"]["labels"]["component"]
                  for d in _k8s_deployments()}
    services = set(_compose()["services"])
    assert components == services, (
        f"k8s 独有：{sorted(components - services)}；"
        f"compose 独有：{sorted(services - components)}"
    )


def test_k8s_platform_deployments_args_match_compose() -> None:
    """k8s 平台 Deployment 的 --role/--platform/--port 与 compose 同名服务一致，
    且 platform_agent 实例均带 --platform（经入口既有参数区分平台）。"""
    compose_services = _compose()["services"]
    for dep in _k8s_deployments():
        component = dep["metadata"]["labels"]["component"]
        if not component.startswith("platform-"):
            continue
        k8s_args = dep["spec"]["template"]["spec"]["containers"][0]["args"]
        compose_cmd = compose_services[component]["command"]
        for flag in ("--role", "--platform", "--port"):
            k8s_v = k8s_args[k8s_args.index(flag) + 1]
            cmp_v = compose_cmd[compose_cmd.index(flag) + 1]
            assert k8s_v == cmp_v, (
                f"{component} 的 {flag}：k8s={k8s_v} 与 compose={cmp_v} 不一致"
            )


def test_placeholder_images_disclosed() -> None:
    """骨架披露：占位镜像（PLACEHOLDER_TAG）在列且无真实镜像名混入，
    提醒占位镜像须替换后才能构建/部署。"""
    placeholders: list[str] = []
    for name, svc in _compose()["services"].items():
        image = svc.get("image", "")
        assert "PLACEHOLDER_TAG" in image, (
            f"compose 服务 {name} 镜像 {image} 未用占位标记（骨架披露口径）"
        )
        placeholders.append(image)
    for dep in _k8s_deployments():
        image = dep["spec"]["template"]["spec"]["containers"][0]["image"]
        assert "PLACEHOLDER_TAG" in image, (
            f"k8s Deployment {dep['metadata']['name']} 镜像 {image} 未用占位标记"
        )
    assert placeholders, "应存在占位镜像（骨架状态）"


def test_prometheus_notes_recheck_before_launch() -> None:
    """prometheus.yml 保留披露注释：实例上线前 targets 与实际部署须再核对。"""
    text = (ROOT / "monitoring" / "prometheus.yml").read_text(encoding="utf-8")
    assert "实例上线前 targets 与实际部署须再核对" in text, (
        "prometheus.yml 缺上线前核对披露注释"
    )


def test_entrypoint_ports_aligned_when_unified() -> None:
    """工位S 端口统一挂账的守卫断言：entrypoint.PORTS 一旦统一为部署侧
    口径（步长10），立即校验导入值与 _expected_port 逐一相等；
    未合入前如实 skip，不假绿。"""
    if entrypoint.PORTS["zhuge_liang"] == 9401:
        pytest.skip("工位S 端口统一尚未合入：entrypoint.PORTS 仍为步长1，"
                    "合入后本测试自动转为实质断言")
    for role in entrypoint.KNOWN_ROLES:
        assert entrypoint.PORTS[role] == _expected_port(role), (
            f"entrypoint.PORTS[{role}] 与部署侧口径不一致"
        )
