"""席位启动入口骨架。

TODO(领域实现): 各席位实际业务逻辑（任务解析、执行、七态流转）；
本入口已支持真实实例化与 --serve 启动，但席位业务逻辑待实现，不可用于生产。
os_commander 已接入归集最小实现（collect）；OS 归集层界面另立。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict, Optional

KNOWN_ROLES = (
    "message_bus", "zhuge_liang", "tian_feng", "zhao_yun",
    "platform_agent", "risk_guard", "approval_gate", "os_commander",
)

# 端口约定（S6 工位S，2026-10-20 统一口径）：9400 + 10×角色在 KNOWN_ROLES 中的序号，
# 即 message_bus=9400、zhuge_liang=9410、…、os_commander=9470。
# 口径来源：部署配置工程师约定（deploy/docker-compose.yaml、monitoring/prometheus.yml
# 及 tests/test_deploy_config.py 的 _expected_port 均按 9400+10×序号 编号），
# 本入口 PORTS 与部署侧保持一致；旧步长1口径（9401 等）已废弃。
PORTS = {role: 9400 + 10 * i for i, role in enumerate(KNOWN_ROLES)}

# 各席位上报层级目标（report_to）；业务语义由总线核验。
REPORT_TO = {
    "zhuge_liang": "principal",
    "tian_feng": "principal",
    "zhao_yun": "principal",
    "platform_agent": "command",
    "risk_guard": "command",
    "approval_gate": "principal",
    "os_commander": "principal",
}

# 角色 -> (模块路径, 席位类名)
SEAT_CLASSES = {
    "zhuge_liang": ("roles.zhuge_liang", "ZhugeLiang"),
    "tian_feng": ("roles.tian_feng", "TianFeng"),
    "zhao_yun": ("roles.zhao_yun", "ZhaoYun"),
    "platform_agent": ("roles.platform_agent", "PlatformAgent"),
    "risk_guard": ("roles.risk_guard", "RiskGuard"),
    "approval_gate": ("roles.approval_gate", "ApprovalGate"),
    # os_commander：归集最小实现已接入；OS 归集层界面另立，不在本入口实现界面。
    "os_commander": ("roles.os_commander", "OsCommander"),
}


def _build_bus_url() -> str:
    return os.environ.get("BUS_URL", "http://127.0.0.1:9400").rstrip("/")


# ---------------- 总线席位凭证注入（LEGION_SEAT_TOKENS） ----------------
#
# 契约（与 roles/message_bus.py 的鉴权本体对齐，见该文件头 AUTH 段）：
#   - 变量未设置 / 空串 / 纯空白  → seat_credentials=None → 鉴权关闭（既有行为，
#     零破坏：未配凭证的部署与加鉴权前逐字节一致）；
#   - 变量已设置且合法            → 传入 dict → 鉴权启用且强制；
#   - 变量已设置但内容非法        → 抛 SeatCredentialsError，**启动失败**。
#
# 为什么非法配置必须启动失败，而不是"解析失败就当作未配置"：
# 后者是 fail-open——运维以为配了凭证，实际总线裸奔，服务照常起来、探活照常
# 通过，没有任何人会发现问题，直到有人白拿数据。凭证配置的失败模式必须朝
# 「起不来」的方向倒，让人在部署那一刻就知道，而不是在事故复盘时才知道。
#
# 为什么格式定 JSON 对象而不是 `seat=token,seat2=token2`：
# token 由密钥系统生成，可能含 `=`、`,`、`:`、引号等字符；逗号分隔 + 等号
# 分隔必然需要转义规则，而"同一个字符串有两种切法"就是解析歧义。鉴权凭据的
# 解析歧义是安全风险（歧义的一侧往往是放行），不是便利性问题。JSON 对象有
# 明确的引号边界与转义定义，标准库直接给解析器，无需自造格式。
#
# 为什么 `{}`（合法 JSON 但零席位）也判启动失败：
# 这是明显的配置遗漏（例如模板占位忘了替换、环境变量写成了空对象）。若放行，
# 总线会以「鉴权启用但无人可进」的状态起来：健康检查通过、所有业务请求 401，
# 变成一场"服务活着但全员进不去"的排查。启动即失败能把问题钉在部署步骤上，
# 且与"配错就起不来"的整体取向一致；放宽它等于给配置遗漏开一条静默通道。
#
# token 明文禁令：异常消息与自检打印都不得回显 token（含前缀/长度），
# 只允许回显席位名与错误位置（席位名本身在部署清单里是公开信息）。
SEAT_TOKENS_ENV = "LEGION_SEAT_TOKENS"


# 挂起队列快照路径注入（LEGION_PENDING_PATH，WO-LEGION-S16-02）：
#   - 变量未设置 / 空串 / 纯空白 → pending_path=None → 纯内存（既有行为，
#     零持久化零产物，既有部署与此前逐字节一致）；
#   - 变量已设置 → 传入路径 → 挂起队列变更点同步落快照、启动加载恢复，
#     总线重启不再无声丢失未送达报告（漏报比重复危险）；
#   - 指向的快照文件损坏 → MessageBus 构造抛 PendingSnapshotCorrupt，
#     启动失败（fail-visible：坏账宁可拒绝启动，不静默当空队列）。
# 与 LEGION_SEAT_TOKENS 同风格：缺省不启用，启用即生效，无第三方开关。
PENDING_PATH_ENV = "LEGION_PENDING_PATH"


def _load_pending_path(env_value: Optional[str]) -> Optional[Path]:
    """把 LEGION_PENDING_PATH 的原始值解析成快照路径。

    返回 None 表示「未配置 → 纯内存」（未设置 / 空串 / 纯空白）；
    返回 Path 表示「已配置 → 快照持久化启用」。
    纯函数：只读入参、不读环境，便于单测直接覆盖。
    """
    if env_value is None or not env_value.strip():
        return None
    return Path(env_value.strip())


class SeatCredentialsError(ValueError):
    """LEGION_SEAT_TOKENS 配置非法。继承 ValueError 便于调用方粗粒度捕获，
    独立类型便于精确区分「凭证配置错」与其它启动期错误。"""


def _load_seat_credentials(
    env_value: Optional[str],
) -> Optional[Dict[str, str]]:
    """把 LEGION_SEAT_TOKENS 的原始值解析成凭证表。

    返回 None 表示「未配置 → 鉴权关闭」（未设置 / 空串 / 纯空白）；
    返回非空 dict 表示「已配置 → 鉴权启用」；
    其余一切情况抛 SeatCredentialsError（fail-close，绝不放行）。

    纯函数：只读入参、不读环境、不打印，便于单测直接覆盖各坏法。
    """
    # 未设置、空串、纯空白：等价于未配置（运维清空变量 = 关掉鉴权，
    # 这是显式且可审计的动作，与"配了但内容烂"必须区别对待）。
    if env_value is None or not env_value.strip():
        return None

    try:
        parsed = json.loads(env_value)
    except json.JSONDecodeError as exc:
        # 只回显错误类型与行列位置；不回显原文（原文即凭证）。
        raise SeatCredentialsError(
            f"{SEAT_TOKENS_ENV} 不是合法 JSON：{exc.msg}"
            f"（第 {exc.lineno} 行第 {exc.colno} 列）"
        ) from None

    if not isinstance(parsed, dict):
        raise SeatCredentialsError(
            f"{SEAT_TOKENS_ENV} 必须是 JSON 对象（形如 "
            f'{{"tian_feng": "token"}}），实际是 {type(parsed).__name__}'
        )

    credentials: Dict[str, str] = {}
    for name, token in parsed.items():
        # JSON 对象的键必然是字符串，此判断只防御性保留；仍单独报错，
        # 避免"键类型不对"被静默归入空键分支。
        if not isinstance(name, str):
            raise SeatCredentialsError(
                f"{SEAT_TOKENS_ENV} 的席位名必须是字符串，实际是 "
                f"{type(name).__name__}"
            )
        if not name.strip():
            raise SeatCredentialsError(
                f"{SEAT_TOKENS_ENV} 含空席位名（键为空串或纯空白）"
            )
        if not isinstance(token, str):
            # 不回显 token 值：数字/布尔/嵌套结构里可能藏着配置原文。
            raise SeatCredentialsError(
                f"{SEAT_TOKENS_ENV} 中席位 {name!r} 的 token 必须是字符串，"
                f"实际是 {type(token).__name__}"
            )
        if not token.strip():
            raise SeatCredentialsError(
                f"{SEAT_TOKENS_ENV} 中席位 {name!r} 的 token 为空或纯空白"
                f"（空 token 会变成免凭证通道，必须拒绝）"
            )
        credentials[name] = token

    if not credentials:
        raise SeatCredentialsError(
            f"{SEAT_TOKENS_ENV} 解析出 0 个席位：空对象通常是配置遗漏"
            f"（占位未替换），放行会导致总线起来但全部请求 401"
        )
    return credentials


# 监听地址：鉴权关闭时禁止对外监听（bind 与鉴权状态联动，见 _resolve_bind_host）。
BIND_HOST_AUTH = "0.0.0.0"        # 已强制鉴权，可对外提供服务
BIND_HOST_NO_AUTH = "127.0.0.1"   # 未配置凭证，只允许本机访问


def _resolve_bind_host(auth_enabled: bool) -> str:
    """按鉴权状态决定 uvicorn 的 bind 地址。

    **鉴权关闭时不允许监听对外地址。** 这是结构性保证，不是部署纪律：
    未配 LEGION_SEAT_TOKENS 时总线没有任何身份校验，若同时绑 0.0.0.0，
    任何能连到端口的人都能 /route 伪造席位上报、读 /board 拿走全部状态——
    「功能可用」与「默认裸奔」被绑在一起，是比功能本身更糟的交付物。
    把两者联动后：本机自检/开发照旧跑（既有语义零破坏），
    而"对外暴露"必然带上鉴权，不依赖谁记得配环境变量。

    为什么不提供 --bind 之类的覆盖参数：安全默认值一旦配上"我知道我在做什么"
    的逃生门，第一个赶着上线的人就会用它，然后裸奔。要对外，唯一路径是配凭证。
    """
    return BIND_HOST_AUTH if auth_enabled else BIND_HOST_NO_AUTH


def _run_message_bus(serve: bool, port: int) -> int:
    """构造 MessageBus（含席位凭证注入）；serve=True 时以 uvicorn 启动。

    导入/构造/配置失败直接抛出（closed on failure）：
    降级占位路径已移除（S2 收口，工位H），不允许入口静默降级掩盖故障；
    凭证配置非法同样抛错（见 _load_seat_credentials），不会退化成关闭鉴权。

    serve 路径说明：message_bus 此前只实例化、不监听端口，--serve 对它无效，
    结果是根本没有调用方会打到总线上——鉴权配了也永远不会被触发。
    这里补上真正的服务启动，鉴权才有生效的场域；同时 bind 地址与鉴权状态
    联动（见 _resolve_bind_host）：没配凭证就只监听本机，绝不对外裸奔。
    """
    from roles.message_bus import MessageBus, create_app  # noqa: PLC0415

    # 先解析凭证与快照路径再构造总线：坏配置在创建任何对象之前就失败，
    # 不留半启动状态（快照损坏由 MessageBus 构造抛 PendingSnapshotCorrupt）。
    credentials = _load_seat_credentials(os.environ.get(SEAT_TOKENS_ENV))
    pending_path = _load_pending_path(os.environ.get(PENDING_PATH_ENV))
    bus = MessageBus(seat_credentials=credentials, pending_path=pending_path)

    # 自检打印如实反映鉴权与持久化状态（运维靠这几行确认配置真的进了总线）；
    # 只报开关与席位数，不报任何 token 内容。
    if bus.auth_enabled:
        auth_desc = f"auth=enabled seats={len(credentials or {})}"
    else:
        auth_desc = "auth=disabled"
    if pending_path is not None:
        pending_desc = f"pending=snapshot path={pending_path}"
    else:
        pending_desc = "pending=memory"

    if serve:
        import uvicorn  # noqa: PLC0415
        # 应用用官方工厂构造（MessageBus 本体不持有 .app，工厂才是应用入口）。
        app = create_app(bus)
        # bind 与鉴权联动：未配凭证只监听本机，避免「0.0.0.0 + 无鉴权」的裸奔态。
        bind_host = _resolve_bind_host(bus.auth_enabled)
        print(f"[skeleton] message_bus 启动服务 bind={bind_host}:{port} "
              f"{auth_desc} {pending_desc} —— 业务逻辑待实现")
        uvicorn.run(app, host=bind_host, port=port)
    else:
        print(f"[skeleton] message_bus 实例化成功：{type(bus).__name__} "
              f"{auth_desc} {pending_desc} —— 业务逻辑待实现")
    return 0


def _run_seat(role: str, platform: str | None, serve: bool,
              port: int) -> int:
    """实例化对应席位类；--serve 时以 uvicorn 启动其 FastAPI 应用。"""
    if role == "platform_agent" and not platform:
        print("[skeleton] platform_agent 需要 --platform 指定平台名")
        return 2
    module_path, class_name = SEAT_CLASSES[role]
    module = __import__(module_path, fromlist=[class_name])
    seat_cls = getattr(module, class_name)
    kwargs: dict = {
        "report_to": REPORT_TO[role],
        "bus_url": _build_bus_url(),
        "metrics_port": port,
    }
    if role == "platform_agent":
        kwargs["platform"] = platform
    seat = seat_cls(**kwargs)

    if serve:
        import uvicorn  # noqa: PLC0415
        print(f"[skeleton] {role} 启动服务 port={port} —— 业务逻辑待实现")
        uvicorn.run(seat.app, host="0.0.0.0", port=port)
    else:
        print(f"[skeleton] 自检 role={role} platform={platform} "
              f"seat={type(seat).__name__} port={port} "
              f"bus={_build_bus_url()} —— 业务逻辑待实现")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="个人 OS 数字军团席位入口（骨架）")
    parser.add_argument("--role", required=True, choices=KNOWN_ROLES)
    parser.add_argument("--platform", default=None,
                        help="platform_agent 专用：平台名")
    parser.add_argument("--serve", action="store_true",
                        help="以 uvicorn 启动席位 FastAPI 应用（默认仅自检）")
    parser.add_argument("--port", type=int, default=None,
                        help="服务端口，默认 9400+10×角色序号（与部署侧口径一致）")
    args = parser.parse_args(argv)

    port = args.port if args.port is not None else PORTS[args.role]

    if args.role == "message_bus":
        return _run_message_bus(args.serve, port)
    # os_commander 与六席同构走 _run_seat（OS 归集层界面另立，见 SEAT_CLASSES 注释）。
    return _run_seat(args.role, args.platform, args.serve, port)


if __name__ == "__main__":
    raise SystemExit(main())
