"""原子文本写：先写同目录临时文件，再 ``replace`` 覆盖目标。

为什么单独抽出这个模块
----------------------
项目里有一类文件是**人手维护、丢了不可重建**的：人工裁决台账
（``story_borderline_overrides.json``）、待确认队列（``*.jsonl``）、
标定新鲜度戳。对它们直接 ``write_text`` 是不安全的——断电、cron 被 kill、
两个人同时跑，都会留下半截 JSON。而这些文件的加载器一律**对损坏拒绝运行**
（见 ``knowledge/borderline_review.OverrideError``），所以一次意外写入能把
整条聚类链路停掉，且故障现象（"启动就报错"）与真实原因（"上次写一半"）
相距很远。

Windows 特有的一点
------------------
``os.replace`` 在目标文件被杀毒/索引器/编辑器持有句柄时会抛
``PermissionError``（``WinError 5``）。2026-11-04 在真机上遇到：同一调用原样
重跑就成功，属瞬时竞态而非权限配置错误。所以这里做**有界重试**。

重试**不是吞错**：次数用完仍然抛出，并且保留 ``.tmp`` 不删——里面是这次要写的
内容，删了连事后取证都没了。需要承认的限度：重试只覆盖不可重现的竞态，
它带来的是"可恢复"，不等于"已修好"。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: 替换重试次数与退避基数（秒）。5 次 × 递增退避 ≈ 0.75 秒，足以跨过
#: 杀毒扫描的瞬时句柄，又不至于让调用方误以为卡死。
REPLACE_ATTEMPTS: int = 5
REPLACE_BACKOFF: float = 0.05


def atomic_write_text(target: Path | str, text: str) -> None:
    """把 ``text`` 原子写入 ``target``（UTF-8，无 BOM）。

    Args:
        target: 目标文件；父目录不存在会自动创建。
        text: 完整内容。**调用方负责结尾换行**——本函数不做任何内容加工，
            避免"到底加没加换行"变成隐式约定。

    Raises:
        PermissionError: 连续 ``REPLACE_ATTEMPTS`` 次替换都因文件锁失败。
            此时 ``target`` 未被修改，``<target>.tmp`` 保留最新内容。
    """
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")

    last: OSError | None = None
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            tmp.replace(path)
            return
        except PermissionError as exc:  # Windows 瞬时文件锁，非权限配置问题
            last = exc
            time.sleep(REPLACE_BACKOFF * (attempt + 1))

    logger.error("原子替换连续 %d 次失败，保留临时文件 %s", REPLACE_ATTEMPTS, tmp)
    assert last is not None
    raise last


__all__ = ["atomic_write_text", "REPLACE_ATTEMPTS", "REPLACE_BACKOFF"]
