"""白名单特征、成熟标签时间切分与仅供描述的统计。"""

import hashlib
import json
import math
import statistics
from collections import Counter
from datetime import datetime, timedelta

from .contracts import canonical_json, normalize_time
from .shadow import _POSTERIOR_FIELDS


_ROW_FIELDS = frozenset({
    "sample_id", "code", "content_family_id", "decision_at", "feature_available_at",
    "label_end_at", "label_available_at", "features", "target", "target_kind", "horizon", "label_policy",
})
_TIME_FIELDS = ("feature_available_at", "decision_at", "label_end_at", "label_available_at")
_PARTITIONS = ("train", "validation", "test")
_POLICY_FIELDS = frozenset({
    "train_end", "validation_end", "test_end", "as_of", "embargo_seconds",
    "target_kind", "horizon", "label_policy", "group_policy",
})
_SPLIT_FIELDS = frozenset({
    "schema_version", "policy", "feature_allowlist", "train", "validation", "test",
    "excluded", "input_hash", "split_hash", "production_eligible",
})
_REASONS = frozenset({
    "immature", "purge_train", "purge_validation", "embargo", "outside_window", "group_overlap",
})
_TARGET_KIND = "forward_close_return"
_GROUP_POLICY = "family_or_code_utc_day_connected"


def _fields(value, expected, name):
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{name} 必须是字段完整且无未知字段的 JSON 对象")


def _nonempty(value, name):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")


def _time(value, name):
    if type(value) is not str:
        raise ValueError(f"{name} 必须是带时区的 ISO 字符串")
    return normalize_time(value)


def _number(value, name):
    if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
        raise ValueError(f"{name} 必须是有限数值且不能是布尔值")


def _horizon(value):
    if type(value) is not int or not 1 <= value <= 250:
        raise ValueError("horizon 必须是 1 到 250 的整数且不能是布尔值")


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _allowlist(value):
    if type(value) is not list or not value:
        raise ValueError("feature_allowlist 必须是非空 JSON 列表")
    for name in value:
        _nonempty(name, "白名单特征名")
    if len(set(value)) != len(value):
        raise ValueError("白名单特征名不能重复")
    if set(value) & _POSTERIOR_FIELDS:
        raise ValueError("白名单不能包含已知后验字段")
    return sorted(value)


def _policy(train_end, validation_end, test_end, as_of, embargo_seconds):
    bounds = {name: _time(value, name) for name, value in (
        ("train_end", train_end), ("validation_end", validation_end), ("test_end", test_end), ("as_of", as_of),
    )}
    if not bounds["train_end"] < bounds["validation_end"] < bounds["test_end"] <= bounds["as_of"]:
        raise ValueError("边界必须满足 train_end < validation_end < test_end <= as_of")
    if type(embargo_seconds) is not int or embargo_seconds < 0:
        raise ValueError("embargo_seconds 必须是非负整数且不能是布尔值")
    try:
        starts = tuple((datetime.fromisoformat(bounds[name]) + timedelta(seconds=embargo_seconds))
                       .isoformat(timespec="microseconds") for name in ("train_end", "validation_end"))
    except (OverflowError, ValueError) as exc:
        raise ValueError("embargo_seconds 超出支持的时间范围") from exc
    return dict(bounds, embargo_seconds=embargo_seconds, target_kind=_TARGET_KIND,
                horizon=None, label_policy=None, group_policy=_GROUP_POLICY), starts


def _rows(rows, allowlist):
    if type(rows) is not list:
        raise ValueError("rows 必须是 JSON 列表")
    frozen = json.loads(canonical_json(rows))
    ids = set()
    horizon = label_policy = None
    for item in frozen:
        _fields(item, _ROW_FIELDS, "样本行")
        for name in ("sample_id", "code", "label_policy"):
            _nonempty(item[name], name)
        if item["sample_id"] in ids:
            raise ValueError("sample_id 必须唯一")
        ids.add(item["sample_id"])
        if item["content_family_id"] is not None:
            _nonempty(item["content_family_id"], "content_family_id")
        _fields(item["features"], set(allowlist), "features")
        for name, value in item["features"].items():
            _number(value, f"features.{name}")
        _number(item["target"], "target")
        if item["target_kind"] != _TARGET_KIND:
            raise ValueError("target_kind 必须为 forward_close_return")
        _horizon(item["horizon"])
        if horizon is not None and (horizon != item["horizon"] or label_policy != item["label_policy"]):
            raise ValueError("全部样本必须具有相同 horizon 和 label_policy")
        horizon, label_policy = item["horizon"], item["label_policy"]
        for name in _TIME_FIELDS:
            item[name] = _time(item[name], name)
        if not item["feature_available_at"] <= item["decision_at"] <= item["label_end_at"] <= item["label_available_at"]:
            raise ValueError("样本时间必须满足 feature_available_at <= decision_at <= label_end_at <= label_available_at")
    return sorted(frozen, key=lambda item: (item["decision_at"], item["sample_id"])), horizon, label_policy


def _candidate(item, policy, starts):
    decision = item["decision_at"]
    if item["label_available_at"] > policy["as_of"]:
        return None, "immature"
    if decision < policy["train_end"]:
        if item["label_end_at"] >= policy["train_end"] or item["label_available_at"] >= policy["train_end"]:
            return None, "purge_train"
        return "train", None
    if decision < policy["validation_end"]:
        if decision < starts[0]:
            return None, "embargo"
        if item["label_end_at"] >= policy["validation_end"] or item["label_available_at"] >= policy["validation_end"]:
            return None, "purge_validation"
        return "validation", None
    if decision < policy["test_end"]:
        if decision < starts[1]:
            return None, "embargo"
        return "test", None
    return None, "outside_window"


def _groups(candidates):
    parents = list(range(len(candidates)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    seen = {}
    for index, (item, _) in enumerate(candidates):
        keys = [("code_day", item["code"], item["decision_at"][:10])]
        if item["content_family_id"] is not None:
            keys.append(("family", item["content_family_id"]))
        for key in keys:
            if key in seen:
                parents[root(index)] = root(seen[key])
            else:
                seen[key] = index
    return [root(index) for index in range(len(candidates))]


def split_dataset(rows, feature_allowlist, train_end, validation_end, test_end, as_of, embargo_seconds=0) -> dict:
    allowlist = _allowlist(feature_allowlist)
    policy, starts = _policy(train_end, validation_end, test_end, as_of, embargo_seconds)
    items, policy["horizon"], policy["label_policy"] = _rows(rows, allowlist)
    candidates = []
    excluded = []
    for item in items:
        partition, reason = _candidate(item, policy, starts)
        if reason is None:
            candidates.append((item, partition))
        else:
            excluded.append((item, reason))
    # 家族与证券 UTC 日构成连通关系；同组只保留最晚时间折，禁止移动样本。
    groups = _groups(candidates)
    latest = {}
    for group, (_, partition) in zip(groups, candidates):
        latest[group] = max(latest.get(group, -1), _PARTITIONS.index(partition))
    partitions = {name: [] for name in _PARTITIONS}
    for group, (item, partition) in zip(groups, candidates):
        if _PARTITIONS.index(partition) == latest[group]:
            partitions[partition].append(item)
        else:
            excluded.append((item, "group_overlap"))
    excluded.sort(key=lambda pair: (pair[0]["decision_at"], pair[0]["sample_id"]))
    result = dict(schema_version=1, policy=policy, feature_allowlist=allowlist, **partitions,
                  excluded=[{"sample_id": item["sample_id"], "reason": reason} for item, reason in excluded],
                  input_hash=_hash(items), production_eligible=False)
    result["split_hash"] = _hash(result)
    return result


def _validate_split(split):
    _fields(split, _SPLIT_FIELDS, "split")
    value = json.loads(canonical_json(split))
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("split 仅支持 schema_version=1")
    if value["production_eligible"] is not False:
        raise ValueError("split 的 production_eligible 必须为 false")
    expected = _hash({key: child for key, child in value.items() if key != "split_hash"})
    if type(value["split_hash"]) is not str or value["split_hash"] != expected:
        raise ValueError("split_hash 不匹配，内容可能被篡改")
    input_hash = value["input_hash"]
    if type(input_hash) is not str or len(input_hash) != 64 or any(char not in "0123456789abcdef" for char in input_hash):
        raise ValueError("input_hash 必须是小写 64 位 SHA256")
    policy = value["policy"]
    _fields(policy, _POLICY_FIELDS, "policy")
    if policy["target_kind"] != _TARGET_KIND or policy["group_policy"] != _GROUP_POLICY:
        raise ValueError("policy 的目标口径或分组政策不受支持")
    if policy["horizon"] is None:
        if policy["label_policy"] is not None:
            raise ValueError("空集政策的 horizon 与 label_policy 必须同时为 null")
    else:
        _horizon(policy["horizon"])
        _nonempty(policy["label_policy"], "label_policy")
    retained = []
    for name in _PARTITIONS:
        if type(value[name]) is not list:
            raise ValueError(f"{name} 必须是 JSON 列表")
        retained.extend(value[name])
    rebuilt = split_dataset(retained, value["feature_allowlist"], policy["train_end"], policy["validation_end"],
                            policy["test_end"], policy["as_of"], policy["embargo_seconds"])
    # 哈希保护内容完整性；重跑切分保护时间、成熟度、分组与折归属语义。
    for name in (*_PARTITIONS, "feature_allowlist"):
        if canonical_json(value[name]) != canonical_json(rebuilt[name]):
            raise ValueError(f"{name} 不符合规范时间切分或排序语义")
    expected_policy = dict(rebuilt["policy"])
    if not retained:
        expected_policy.update(horizon=policy["horizon"], label_policy=policy["label_policy"])
    if canonical_json(policy) != canonical_json(expected_policy):
        raise ValueError("policy 与样本或规范时间边界不一致")
    if type(value["excluded"]) is not list:
        raise ValueError("excluded 必须是 JSON 列表")
    ids = {item["sample_id"] for item in retained}
    for item in value["excluded"]:
        _fields(item, {"sample_id", "reason"}, "excluded 项")
        _nonempty(item["sample_id"], "excluded.sample_id")
        if type(item["reason"]) is not str or item["reason"] not in _REASONS:
            raise ValueError("excluded.reason 不受支持")
        if item["sample_id"] in ids:
            raise ValueError("excluded 的 ID 不能重复或与保留样本重叠")
        ids.add(item["sample_id"])
    if not retained and bool(value["excluded"]) != (policy["horizon"] is not None):
        raise ValueError("空集或全剔除样本的标签政策不一致")
    if not value["excluded"] and input_hash != rebuilt["input_hash"]:
        raise ValueError("input_hash 与全部保留样本重建的输入不一致")
    return value


def summarize_split(split: dict) -> dict:
    value = _validate_split(split)
    partitions = {}
    for name in _PARTITIONS:
        targets = [item["target"] for item in value[name]]
        mean = rate = None
        if targets:
            try:
                mean = statistics.mean(targets)
                _number(mean, "mean_target")
                rate = sum(target > 0 for target in targets) / len(targets)
            except (OverflowError, ValueError) as exc:
                raise ValueError("目标数值过大，无法生成有限摘要统计") from exc
        partitions[name] = {"n": len(targets), "mean_target": mean, "positive_rate": rate}
    result = {"split_hash": value["split_hash"], "partitions": partitions,
              "excluded_by_reason": dict(sorted(Counter(item["reason"] for item in value["excluded"]).items())),
              "production_eligible": False}
    canonical_json(result)
    return result
