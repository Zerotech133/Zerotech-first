"""Synthetic offline contracts for the minimal split-unit model.

These tests are intentionally not executed while the repository's dynamic-test
safety approval is outstanding. Every fixture is synthetic and constructed in
memory; no network, no database writes, and no dynamic execution are required
to understand or review them.
"""

from __future__ import annotations

import hashlib
import unittest

from quant_backtest import xq_reverse_store, xq_split_units


class SplitUnitsOfflineTests(unittest.TestCase):
    """Splitting, reversibility, determinism and fail-closed rejection."""

    @staticmethod
    def post(
        post_id: str,
        raw_json: dict,
        created_at_ms: int = 1_600_000_000_000,
        source: str = "xueqiu",
    ) -> xq_reverse_store.RawPostInput:
        """Build one complete synthetic original post fixture."""
        return xq_reverse_store.RawPostInput(
            post_id=post_id,
            original_url=f"https://xueqiu.com/test/{post_id}",
            created_at_ms=created_at_ms,
            raw_json=raw_json,
            source=source,
        )

    @staticmethod
    def relation(
        post_id: str,
        symbol: str,
        excerpt: str,
        source_field: str = "text",
    ) -> xq_reverse_store.PostSymbolInput:
        """Build exact original-text evidence for one stock mention."""
        return xq_reverse_store.PostSymbolInput(
            post_id=post_id,
            symbol=symbol,
            evidence_type="post_content",
            evidence_json={"source_field": source_field, "excerpt": excerpt},
        )

    def test_full_post_splits_all_unit_types_with_reversible_lineage(self) -> None:
        """A complete original yields post/author/time/mention/content units."""
        raw = {
            "id": "p1",
            "user": {"id": 7, "screen_name": "trader_seven"},
            "created_at": 1_600_000_000_000,
            "text": "plain lead $SH600000 plain tail",
        }
        post = self.post("p1", raw)
        relation = self.relation("p1", "SH600000", "$SH600000")
        units = xq_split_units.split_post(post, [relation])
        by_type = {unit.unit_type: unit for unit in units}
        self.assertEqual(
            sorted(by_type),
            ["author", "content", "mention", "post", "time"],
        )
        # Post unit keeps the original identity facts.
        self.assertEqual(by_type["post"].raw_post_id, "p1")
        self.assertEqual(by_type["post"].source, "xueqiu")
        self.assertEqual(by_type["post"].payload["created_at_ms"],
                         1_600_000_000_000)
        self.assertEqual(by_type["post"].payload["original_url"],
                         "https://xueqiu.com/test/p1")
        # Author unit records the exact source field it was taken from.
        self.assertEqual(by_type["author"].payload,
                         {"field_name": "user.screen_name",
                          "value": "trader_seven"})
        self.assertNotIn("author_missing", by_type["author"].payload)
        # Time unit cites the original's own time field name.
        self.assertEqual(by_type["time"].payload["created_at_ms"],
                         1_600_000_000_000)
        self.assertEqual(by_type["time"].payload["time_source"], "created_at")
        # Mention offsets re-read the excerpt from the original text.
        mention = by_type["mention"]
        self.assertEqual(mention.payload["symbol"], "SH600000")
        self.assertEqual(mention.payload["evidence_type"], "post_content")
        self.assertEqual(mention.payload["source_field"], "text")
        self.assertEqual(mention.payload["excerpt"], "$SH600000")
        start, end = mention.payload["start"], mention.payload["end"]
        self.assertEqual(raw["text"][start:end], mention.payload["excerpt"])
        self.assertIn("SH600000", raw["text"][start:end])
        # Content unit keeps field name, length and a stable content hash.
        content = by_type["content"]
        self.assertEqual(content.payload["field_name"], "text")
        self.assertEqual(content.payload["length"], len(raw["text"]))
        expected_hash = hashlib.sha256(
            raw["text"].encode("utf-8")).hexdigest()[:32]
        self.assertEqual(content.payload["content_sha256"], expected_hash)
        # Lineage groups units back to the raw post and its symbols.
        chain = xq_split_units.lineage(units)
        self.assertEqual(list(chain), ["p1"])
        self.assertEqual(chain["p1"]["unit_ids"],
                         [unit.unit_id for unit in units])
        self.assertEqual(chain["p1"]["symbols"], ["SH600000"])
        self.assertEqual(chain["p1"]["source"], "xueqiu")
        self.assertEqual(chain["p1"]["downstream"], "not_linked")
        # Origin reverses every unit back to raw post and source field.
        for unit in units:
            back = xq_split_units.origin(units, unit.unit_id)
            self.assertEqual(back["raw_post_id"], "p1")
            self.assertEqual(back["unit_type"], unit.unit_type)
            self.assertEqual(back["source"], "xueqiu")
            self.assertEqual(back["unit_id"], unit.unit_id)
        mention_back = xq_split_units.origin(units, mention.unit_id)
        self.assertEqual(mention_back["source_field"], "text")
        self.assertEqual(raw["text"][mention_back["start"]:mention_back["end"]],
                         mention_back["excerpt"])

    def test_missing_author_is_explicitly_marked_without_invention(self) -> None:
        """No author field yields one author_missing unit, never a fake name."""
        raw = {"id": "p2", "text": "anonymous body $SH600000"}
        units = xq_split_units.split_post(
            self.post("p2", raw), [self.relation("p2", "SH600000", "$SH600000")])
        authors = [unit for unit in units if unit.unit_type == "author"]
        self.assertEqual(len(authors), 1)
        self.assertEqual(authors[0].payload, {"author_missing": True})
        # The marker is discoverable through the reverse lookup as well.
        back = xq_split_units.origin(units, authors[0].unit_id)
        self.assertTrue(back["author_missing"])
        self.assertEqual(back["raw_post_id"], "p2")

    def test_multi_symbol_post_yields_independent_reversible_mentions(self) -> None:
        """Two mentions split apart, each keeping its own original offsets."""
        raw = {"id": "p3", "text": "$SH600000 lead gap $SZ000001 tail"}
        relations = [
            self.relation("p3", "SH600000", "$SH600000"),
            self.relation("p3", "SZ000001", "$SZ000001"),
        ]
        units = xq_split_units.split_post(self.post("p3", raw), relations)
        mentions = [unit for unit in units if unit.unit_type == "mention"]
        self.assertEqual(len(mentions), 2)
        self.assertEqual([unit.payload["symbol"] for unit in mentions],
                         ["SH600000", "SZ000001"])
        for mention in mentions:
            start, end = mention.payload["start"], mention.payload["end"]
            self.assertEqual(raw["text"][start:end], mention.payload["excerpt"])
            self.assertIn(mention.payload["symbol"],
                          raw["text"][start:end])
        self.assertNotEqual(mentions[0].payload["start"],
                            mentions[1].payload["start"])
        chain = xq_split_units.lineage(units)
        self.assertEqual(sorted(chain["p3"]["symbols"]),
                         ["SH600000", "SZ000001"])

    def test_splitting_the_same_input_twice_is_deterministic(self) -> None:
        """Identical inputs reproduce the identical unit_id sequence."""
        raw = {
            "id": "p4",
            "user": {"screen_name": "steady"},
            "created_at": 1_600_000_000_001,
            "text": "$SH600000 and $SZ000001",
        }
        relations = [
            self.relation("p4", "SH600000", "$SH600000"),
            self.relation("p4", "SZ000001", "$SZ000001"),
        ]
        first = xq_split_units.split_post(self.post("p4", raw), relations)
        second = xq_split_units.split_post(self.post("p4", raw), relations)
        self.assertEqual([unit.unit_id for unit in first],
                         [unit.unit_id for unit in second])
        self.assertEqual([(unit.unit_type, unit.payload) for unit in first],
                         [(unit.unit_type, unit.payload) for unit in second])

    def test_invalid_inputs_are_rejected_without_fabrication(self) -> None:
        """Closed on failure: every identity or time defect raises."""
        # raw_json top-level id must equal post_id exactly.
        with self.assertRaises(ValueError):
            xq_split_units.split_post(
                self.post("p5", {"id": "other", "text": "body"}))
        # Boolean and negative timestamps are illegal.
        with self.assertRaises(ValueError):
            xq_split_units.split_post(
                self.post("p5", {"id": "p5", "text": "body"},
                          created_at_ms=True))
        with self.assertRaises(ValueError):
            xq_split_units.split_post(
                self.post("p5", {"id": "p5", "text": "body"},
                          created_at_ms=-1))
        # Blank post ids are illegal.
        with self.assertRaises(ValueError):
            xq_split_units.split_post(
                self.post("   ", {"id": "   ", "text": "body"}))
        # Only the xueqiu source is accepted by this splitter.
        with self.assertRaises(ValueError):
            xq_split_units.split_post(
                self.post("p5", {"id": "p5", "text": "body"}, source="tdx"))
        # A relation pointing at another post is rejected, not merged.
        with self.assertRaises(ValueError):
            xq_split_units.split_post(
                self.post("p5", {"id": "p5", "text": "body $SH600000"}),
                [self.relation("p6", "SH600000", "$SH600000")])

    def test_no_event_units_and_downstream_stays_not_linked(self) -> None:
        """Event extraction is not wired; lineage never claims downstream."""
        raw = {
            "id": "p7",
            "user": {"screen_name": "nobody"},
            "text": "body $SH600000",
            "title": "title text",
            "description": "description text",
        }
        units = xq_split_units.split_post(
            self.post("p7", raw), [self.relation("p7", "SH600000", "$SH600000")])
        self.assertEqual([unit.unit_type for unit in units].count("event"), 0)
        for unit in units:
            self.assertIn(unit.unit_type, xq_split_units.UNIT_TYPES)
        self.assertNotIn("event", xq_split_units.UNIT_TYPES)
        chain = xq_split_units.lineage(units)
        self.assertEqual(chain["p7"]["downstream"], "not_linked")
        # Every nonempty content field becomes its own content unit.
        contents = [unit.payload["field_name"] for unit in units
                    if unit.unit_type == "content"]
        self.assertEqual(contents, ["text", "title", "description"])

    def test_lineage_rejects_mixed_sources_under_one_raw_post(self) -> None:
        """Units of one raw_post_id may never mix source labels."""
        # Unit construction does not validate source; the lineage merge does.
        first = xq_split_units.Unit(
            unit_id="unit-xq-1",
            unit_type="post",
            source="xueqiu",
            raw_post_id="p9",
            payload={"original_url": "https://xueqiu.com/test/p9",
                     "created_at_ms": 1_600_000_000_000},
        )
        second = xq_split_units.Unit(
            unit_id="unit-tdx-1",
            unit_type="post",
            source="tdx",
            raw_post_id="p9",
            payload={"original_url": "https://example.test/p9",
                     "created_at_ms": 1_600_000_000_000},
        )
        with self.assertRaises(ValueError):
            xq_split_units.lineage([first, second])

    def test_origin_rejects_unknown_and_ambiguous_unit_ids(self) -> None:
        """Reverse lookup never guesses an unknown or duplicated unit."""
        raw = {"id": "p10", "user": {"screen_name": "steady"},
               "text": "body $SH600000"}
        units = xq_split_units.split_post(
            self.post("p10", raw),
            [self.relation("p10", "SH600000", "$SH600000")])
        # An unknown unit_id is closed on failure.
        with self.assertRaises(ValueError):
            xq_split_units.origin(units, "no-such-unit")
        # Two units sharing one unit_id make the lookup ambiguous.
        duplicated = list(units) + [units[0]]
        with self.assertRaises(ValueError):
            xq_split_units.origin(duplicated, units[0].unit_id)

    def test_dict_raw_json_rejects_non_serializable_values(self) -> None:
        """Dict raw_json with non-JSON values is closed on failure, store-aligned."""
        # Top-level id matches post_id, so only the serialization check fires.
        with self.assertRaises(ValueError) as error:
            xq_split_units.split_post(
                self.post("p1", {"id": "p1", "text": float("nan")}))
        self.assertEqual(str(error.exception),
                         "raw_json must be serializable as JSON")
        # A non-JSON object value takes the same store-aligned rejection path.
        with self.assertRaises(ValueError) as error:
            xq_split_units.split_post(
                self.post("p1", {"id": "p1", "text": object()}))
        self.assertEqual(str(error.exception),
                         "raw_json must be serializable as JSON")

    def test_malformed_string_raw_json_is_rejected(self) -> None:
        """A raw_json string that fails JSON parsing raises the complete-JSON-object ValueError."""
        for malformed in ("{incomplete", "", '{"id": "p1"} extra'):
            post_input = xq_reverse_store.RawPostInput(
                post_id="p1",
                original_url="https://xueqiu.com/test/p1",
                created_at_ms=1_600_000_000_000,
                raw_json=malformed,
            )
            with self.assertRaises(ValueError) as error:
                xq_split_units.split_post(post_input)
            self.assertEqual(str(error.exception),
                             "raw_json must contain a complete JSON object")

    def test_dict_raw_json_rejects_nested_infinity_and_nan_values(self) -> None:
        """Infinity or NaN at any depth in dict raw_json is rejected as non-serializable."""
        for raw in (
            {"id": "p1", "score": float("inf")},
            {"id": "p1", "score": float("-inf")},
            {"id": "p1", "meta": {"score": float("nan")}},
            {"id": "p1", "meta": {"deep": {"score": float("inf")}}},
            {"id": "p1", "tags": [float("nan"), 1]},
        ):
            with self.assertRaises(ValueError) as error:
                xq_split_units.split_post(self.post("p1", raw))
            self.assertEqual(str(error.exception),
                             "raw_json must be serializable as JSON")


if __name__ == "__main__":
    unittest.main()
