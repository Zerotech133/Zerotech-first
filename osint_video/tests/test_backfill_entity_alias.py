"""``scripts/backfill_entity_alias.py`` 的确定性规则测试。

## 这份测试要守住什么

回填脚本直接往 ``osint_entity_alias`` 写行，一行错别名就会把两个主体合并成
一个，抬高独立来源数（热度分母），且表现为"数字偶尔偏大"、极难定位。所以
这里不只测"该配的配上了"，更重要的是测**不该配的坚决不配**：

- 分布包含不成立时拒绝（``欧盟`` vs ``欧盟委员会`` 的真实反例）
- 通用词永不配对（``银行`` / ``中国`` / ``协会``）
- 长名从未在正文出现时不做断言，只出候选

以及一条端到端的不变式：两条规则产出的别名与既有别名表**混在一起**时，
``resolve`` 必须把同一主体的所有写法收敛到同一个 canonical。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import backfill_entity_alias as bf  # noqa: E402


# ------------------------------------------------------------ 分布包含 --


class TestDeriveContainmentAliases:
    def test_pairs_when_short_name_docs_are_fully_covered(self):
        """含短名的文档全部也含长名 → 配对。

        这是真机语料 ``中国人民银行`` / ``人民银行`` 的形态：4 条含"人民银行"
        的事实，4 条全部同时含"中国人民银行"。
        """
        documents = [
            "央行宣布下调存款准备金率 中国人民银行宣布，自下周起下调准备金率",
            "降准落地 中国人民银行下调存款准备金率0.5个百分点",
            "央行开展中期借贷便利操作 中国人民银行开展中期借贷便利操作",
        ]
        aliases, candidates = bf.derive_containment_aliases(
            documents, ["中国人民银行", "人民银行"]
        )

        assert len(aliases) == 1
        row = aliases[0]
        assert row["alias"] == "人民银行"
        assert row["canonical_id"] == "中国人民银行"  # 无既有 canonical 时长名作 ID
        assert candidates == []

    def test_rejects_when_coverage_is_partial(self):
        """含短名的文档不全含长名 → 拒绝。

        这是本规则能否安全上线的分水岭：放宽成"只要有交集"就会把上位概念与
        其下属机构合并。用 ``光伏行业协会`` / ``中国光伏行业协会`` 构造——
        第二个名字长度过关（6 字），确保拦截来自**分布证据不足**而不是长度
        门槛，否则测的是另一条规则。
        """
        documents = [
            "中国光伏行业协会呼吁企业停止低价竞争",
            "光伏行业协会发布《行业自律倡议书》，未署上级单位名称",
        ]
        aliases, candidates = bf.derive_containment_aliases(
            documents, ["中国光伏行业协会", "光伏行业协会"]
        )

        assert aliases == []
        assert len(candidates) == 1
        assert candidates[0]["reason"] == "containment_unproven"
        assert "分布包含不成立" in candidates[0]["evidence"]

    def test_rejects_names_shorter_than_min_length(self):
        """2 字短名一律不参与，哪怕分布包含成立。

        ``欧盟`` 是真机语料里的真实形态：它确实是 ``欧盟委员会`` 的子串，且
        4 条含"欧盟"的文档里有 1 条含"欧盟委员会"。但 2 字短名绝大多数是通用
        词，让其进入比较只会制造噪音。这里断言它根本不进入候选流程（连候选
        都不产生），与"证据不足"区分开。
        """
        documents = [
            "欧盟决定对中国电动汽车加征反补贴税 欧盟委员会发布公告",
            "欧盟对华电动车加征关税落地，最高35.3%",
        ]
        aliases, candidates = bf.derive_containment_aliases(
            documents, ["欧盟", "欧盟委员会"]
        )

        assert aliases == []
        assert candidates == []

    def test_rejects_generic_tokens_even_when_covered(self):
        """通用词即使分布包含完全成立也必须拒绝。

        用 3 字的 ``委员会`` 构造：长度过关、分布包含也成立，唯一能挡住它的
        就是黑名单。若这条红，说明黑名单没生效。
        """
        documents = [
            "欧盟委员会发布公告，对中国电动汽车加征反补贴税",
            "欧盟委员会启动调查程序，委员会称将依法推进",
        ]
        aliases, _ = bf.derive_containment_aliases(documents, ["欧盟委员会", "委员会"])
        assert aliases == []

    def test_requires_minimum_support_documents(self):
        """只出现在 1 条文档里 → 证据不足，不配对。"""
        documents = ["中国人民银行下调存款准备金率", "财政部发布国债发行计划"]
        aliases, _ = bf.derive_containment_aliases(
            documents, ["中国人民银行", "人民银行"]
        )
        assert aliases == []

    def test_long_name_never_appearing_yields_candidate_not_alias(self):
        """长名在正文里从未出现 → 无法证伪也无法证实，只出候选。

        真机语料里 ``宁德时代新能源科技股份有限公司`` 就是这种：实体字段有它，
        正文里只写"宁德时代"。这类必须交人工，脚本不能自作主张。
        """
        documents = ["宁德时代发布麒麟电池，续航突破一千公里"] * 2
        aliases, candidates = bf.derive_containment_aliases(
            documents, ["宁德时代", "宁德时代新能源科技股份有限公司"]
        )

        assert aliases == []
        assert candidates[0]["reason"] == "containment_unproven"
        assert "未出现" in candidates[0]["evidence"]

    def test_reuses_existing_canonical(self):
        """既有别名表里有 canonical 时必须复用，不得另造一个。

        真机形态：人工已录 ``中国人民银行 -> pboc``，脚本若推
        ``人民银行 -> 中国人民银行``，两者混用会让同一主体落到两个 canonical。
        复用后两条都指向 ``pboc``。
        """
        documents = [
            "中国人民银行下调存款准备金率",
            "中国人民银行开展中期借贷便利操作",
        ]
        aliases, _ = bf.derive_containment_aliases(
            documents,
            ["中国人民银行", "人民银行"],
            existing_canonical={"中国人民银行": "pboc"},
        )

        assert aliases[0]["canonical_id"] == "pboc"

    def test_skips_when_alias_equals_canonical(self):
        """短名恰好就是 canonical 自身时不需要建行。"""
        documents = ["中国人民银行下调存款准备金率", "中国人民银行开展逆回购"]
        aliases, _ = bf.derive_containment_aliases(
            documents,
            ["中国人民银行", "人民银行"],
            existing_canonical={"中国人民银行": "人民银行"},
        )
        assert aliases == []


# ------------------------------------------------------------ 孤立实体 --


class TestSuggestSingletonEntities:
    def test_lists_parallel_cross_language_names(self):
        """干净语料上，中英平行写法必须全部出现在待核对清单里。

        这是本功能存在的理由：``TSMC`` 那条事实的正文写的是"台积电"，正文里
        根本没有 ``TSMC`` 这个串。若按文档出现次数判定，它会被漏掉；按实体
        频次判定才会浮出来。
        """
        units = [
            {"entities": ["台积电"], "title": "台积电亚利桑那厂开始量产"},
            {"entities": ["TSMC"], "title": "台积电美国工厂量产4nm"},
            {"entities": ["中芯国际"], "title": "中芯国际产能利用率回升"},
            {"entities": ["SMIC"], "title": "中芯国际产能利用率超90%"},
            {"entities": ["台积电"], "title": "台积电上调资本开支"},
            {"entities": ["中芯国际"], "title": "中芯国际公告新建产线"},
        ]
        singletons = bf.suggest_singleton_entities(units, ["台积电", "TSMC", "中芯国际", "SMIC"])

        assert {item["name"] for item in singletons} == {"TSMC", "SMIC"}
        assert all(item["facts"] == 1 for item in singletons)

    def test_skips_names_already_covered(self):
        """已在别名表里的名字不再提示。"""
        units = [
            {"entities": ["台积电"], "title": "台积电量产"},
            {"entities": ["台积电"], "title": "台积电扩产"},
            {"entities": ["TSMC"], "title": "台积电量产4nm"},
        ]
        singletons = bf.suggest_singleton_entities(
            units, ["台积电", "TSMC"], known_keys=["tsmc"]
        )
        assert singletons == []

    def test_handles_dict_entities(self):
        """``entities`` 为 ``[{"name": ...}]`` 形态时也要能计数。"""
        units = [
            {"entities": [{"name": "英伟达"}], "title": "英伟达发布显卡"},
            {"entities": [{"name": "NVIDIA"}], "title": "NVIDIA 发布 GPU"},
        ]
        singletons = bf.suggest_singleton_entities(units, ["英伟达", "NVIDIA"])
        assert {item["name"] for item in singletons} == {"英伟达", "NVIDIA"}


class TestSuggestShortFormCandidates:
    """剥后缀产生的"中间形态 canonical"必须能反查出前缀短名。

    真机形态：``华为技术有限公司`` 剥出 canonical ``华为技术``，而 ``华为``
    是它的前缀。``华为技术`` 在正文中一次都不出现，故分布包含规则永远判它
    ``containment_unproven``——这类实体只能靠本函数浮出水面。
    """

    ALIASES = [
        {"alias": "华为技术有限公司", "canonical_id": "华为技术", "alias_type": "cn_full"},
        {"alias": "贵州茅台酒股份有限公司", "canonical_id": "贵州茅台酒", "alias_type": "cn_full"},
    ]

    def test_finds_prefix_short_forms(self):
        candidates = bf.suggest_short_form_candidates(
            ["华为技术有限公司", "华为", "贵州茅台酒股份有限公司", "贵州茅台"],
            self.ALIASES,
        )
        assert {item["left"]["key"] for item in candidates} == {"华为", "贵州茅台"}
        assert all(item["reason"] == "short_form_suspect" for item in candidates)
        # 提示里必须给出对照写法，否则人工无从判断
        assert any("华为技术有限公司" in item["right"]["names"] for item in candidates)

    def test_skips_already_normalized(self):
        """已归一的短名不再提示。"""
        candidates = bf.suggest_short_form_candidates(
            ["华为", "华为技术有限公司"],
            [*self.ALIASES, {"alias": "华为", "canonical_id": "huawei", "alias_type": "variant"}],
        )
        assert candidates == []

    def test_skips_when_canonical_is_not_longer(self):
        """canonical 不比短名长时不构成"前缀短名"。"""
        candidates = bf.suggest_short_form_candidates(
            ["比亚迪"],
            [{"alias": "比亚迪股份有限公司", "canonical_id": "比亚迪", "alias_type": "cn_full"}],
        )
        assert candidates == []

    def test_skips_generic_tokens(self):
        """通用词即使是指面前缀也不提示（``中国`` ⊂ ``中国人民银行``）。"""
        candidates = bf.suggest_short_form_candidates(
            ["中国", "中国人民银行"],
            [{"alias": "中国人民银行", "canonical_id": "中国人民银行", "alias_type": "cn_full"}],
            existing_canonical={"中国人民银行": "pboc"},
        )
        assert candidates == []


# ---------------------------------------------------------------- 端到端 --


def test_resolve_converges_across_rules_and_existing_table():
    """两条规则 + 既有别名表混在一起，同一主体的所有写法必须收敛到同一 canonical。

    这是本轮修掉的那个真实缺陷的回归测试：脚本产出 ``人民银行 -> 中国人民银行``
    而人工表里是 ``中国人民银行 -> pboc``，若不压平链，``resolve({'人民银行'})``
    与 ``resolve({'中国人民银行'})`` 会得到两个不同结果，实体交集判空。
    """
    from knowledge.entity_alias import _build_alias_map, resolve

    paired, _ = bf.derive_containment_aliases(
        ["中国人民银行下调准备金率", "中国人民银行开展逆回购"],
        ["中国人民银行", "人民银行"],
        existing_canonical={"中国人民银行": "pboc"},
    )
    rows = [("中国人民银行", "pboc"), ("央行", "中国人民银行")]
    rows += [(row["alias"], row["canonical_id"]) for row in paired]

    alias_map = _build_alias_map(rows)
    resolved = {
        name: resolve({name}, alias_map)
        for name in ("中国人民银行", "人民银行", "央行")
    }

    assert resolved["中国人民银行"] == {"pboc"}
    assert resolved["人民银行"] == {"pboc"}
    assert resolved["央行"] == {"pboc"}


def test_filter_resolved_candidates_drops_noise():
    """两侧都已被归一覆盖的候选必须剔除，否则清单失去可信度。"""
    candidates = [
        {"left": {"key": "中国人民银行"}, "right": {"key": "人民银行"}, "reason": "substring"},
        {"left": {"key": "宁德时代"}, "right": {"key": "宁德时代新能源科技"}, "reason": "substring"},
    ]
    remaining = bf._filter_resolved_candidates(
        candidates,
        aliases=[{"alias": "人民银行", "canonical_id": "pboc", "alias_type": "variant"}],
        existing_keys=["中国人民银行"],
    )

    assert len(remaining) == 1
    assert remaining[0]["left"]["key"] == "宁德时代"


@pytest.mark.parametrize(
    "short,long,documents,expected",
    [
        (
            "人民银行",
            "中国人民银行",
            [
                "中国人民银行下调存款准备金率，释放长期资金约一万亿",
                "中国人民银行开展中期借贷便利操作，利率持平",
            ],
            True,
        ),
        (
            "光伏行业协会",
            "中国光伏行业协会",
            [
                "中国光伏行业协会呼吁企业停止低价竞争",
                "中国光伏行业协会发布行业自律倡议书",
            ],
            True,
        ),
        (
            # 黑名单：长度过关、分布包含成立，唯一拦截来自 GENERIC_TOKENS
            "委员会",
            "欧盟委员会",
            [
                "欧盟委员会发布公告，对中国电动汽车加征反补贴税",
                "欧盟委员会启动调查程序，委员会称将依法推进",
            ],
            False,
        ),
        (
            # 长度门槛：2 字短名不进入比较
            "欧盟",
            "欧盟委员会",
            [
                "欧盟委员会发布公告加征反补贴税",
                "欧盟对华电动车加征关税落地",
            ],
            False,
        ),
    ],
)
def test_containment_boundary_matrix(short, long, documents, expected):
    """四类边界压成一张表，任何一条回归都会立刻红。"""
    aliases, _ = bf.derive_containment_aliases(documents, [short, long])
    assert bool(aliases) is expected
