from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smartwatch_clank.core.models import Observation
from smartwatch_clank.core.store import SQLiteStore
from smartwatch_clank.intelligence.samsung import (
    CandidateState, SamsungRelationship, persist_samsung_reconciliation, reconcile_samsung,
)


T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


def product(region: str, base: str, sku: str) -> Observation:
    return Observation("samsung_product_catalogue", f"p:{region}:{sku}", f"https://samsung/{region}/p/{sku}", T0,
                       source_kind="product_catalogue", region=region, model_number=base,
                       regional_model_number=sku, classification_state="known_smartwatch")


def support(region: str, base: str, sku: str, state: str = "known_smartwatch", when: datetime = T0) -> Observation:
    return Observation(f"samsung_support_{region.lower()}", f"s:{region}:{sku}", f"https://samsung/{region}/s/{sku}", when,
                       source_kind="support", region=region, model_number=base, regional_model_number=sku,
                       classification_state=state, classification_evidence=("official_support_sitemap",))


class ReconciliationTests(unittest.TestCase):
    def test_required_relationships_and_global_semantics(self):
        products = (
            product("IN", "SM-L100", "SM-L100AAAIN"),
            product("US", "SM-L200", "SM-L200AAAUS"),
            product("IN", "SM-L400", "SM-L400AAAIN"),
        )
        supports = (
            support("IN", "SM-L100", "SM-L100AAAIN"),
            support("IN", "SM-L200", "SM-L200BBBIN"),
            support("IN", "SM-L300", "SM-L300AAAIN"),
            support("IN", "SM-L999", "SM-L999ZZZIN", "ambiguous"),
        )
        records = reconcile_samsung(products, supports)
        relationships = {(item.base_model, item.relationship) for item in records}
        self.assertIn(("SM-L100", SamsungRelationship.CATALOGUE_AND_SUPPORT), relationships)
        self.assertIn(("SM-L200", SamsungRelationship.SUPPORT_ONLY_REGIONAL_SKU), relationships)
        self.assertIn(("SM-L300", SamsungRelationship.SUPPORT_ONLY_BASE_MODEL), relationships)
        self.assertIn(("SM-L999", SamsungRelationship.AMBIGUOUS_SUPPORT_ONLY), relationships)
        self.assertIn(("SM-L400", SamsungRelationship.CATALOGUE_ONLY), relationships)

    def test_deterministic_regardless_of_order_and_regions_do_not_collapse(self):
        products = (product("IN", "SM-L100", "SM-L100AAA"),)
        supports = (support("IN", "SM-L100", "SM-L100AAA"), support("GB", "SM-L100", "SM-L100AAA"))
        forward = reconcile_samsung(products, supports)
        reverse = reconcile_samsung(tuple(reversed(products)), tuple(reversed(supports)))
        self.assertEqual(forward, reverse)
        self.assertEqual(len([item for item in forward if item.support_identity]), 2)
        gb = next(item for item in forward if item.region == "GB")
        self.assertEqual(gb.relationship, SamsungRelationship.SUPPORT_ONLY_REGIONAL_SKU)

    def test_candidate_lifecycle_preserves_support_first_timestamp(self):
        with tempfile.TemporaryDirectory() as directory, SQLiteStore(Path(directory) / "r.sqlite3") as store:
            first_records = reconcile_samsung((), (support("IN", "SM-L305", "SM-L305FZEAINS"),))
            first = persist_samsung_reconciliation(store, first_records, reconciled_at=T0)
            self.assertTrue(first["baseline"])
            self.assertEqual(first["events"], 0)
            later = T0 + timedelta(days=9)
            second_records = reconcile_samsung(
                (product("IN", "SM-L305", "SM-L305FZEAINS"),),
                (support("IN", "SM-L305", "SM-L305FZEAINS", when=later),),
            )
            second = persist_samsung_reconciliation(store, second_records, reconciled_at=later)
            self.assertEqual(second["events"], 1)
            row = store.connection.execute("SELECT * FROM prelaunch_candidates").fetchone()
            self.assertEqual(row["state"], CandidateState.COMMERCIALLY_CONFIRMED.value)
            self.assertEqual(datetime.fromisoformat(row["first_seen"]), T0)
            self.assertEqual(datetime.fromisoformat(row["catalogue_first_seen"]), later)

    def test_newly_enabled_region_is_silent_baseline(self):
        with tempfile.TemporaryDirectory() as directory, SQLiteStore(Path(directory) / "r.sqlite3") as store:
            persist_samsung_reconciliation(
                store, reconcile_samsung((), (support("IN", "SM-L100", "SM-L100IN"),)), reconciled_at=T0
            )
            result = persist_samsung_reconciliation(
                store, reconcile_samsung((), (
                    support("IN", "SM-L100", "SM-L100IN"),
                    support("GB", "SM-L777", "SM-L777GB"),
                )), reconciled_at=T0 + timedelta(days=1)
            )
            self.assertEqual(result["new_source_regions"], ["GB"])
            self.assertEqual(result["events"], 0)
            row = store.connection.execute(
                "SELECT onboarding_baseline FROM prelaunch_candidates WHERE region='GB'"
            ).fetchone()
            self.assertEqual(row[0], 1)

    def test_genuine_new_regional_support_sku_after_onboarding_is_detected(self):
        with tempfile.TemporaryDirectory() as directory, SQLiteStore(Path(directory) / "r.sqlite3") as store:
            products = (product("US", "SM-L200", "SM-L200US"),)
            persist_samsung_reconciliation(
                store, reconcile_samsung(products, (support("IN", "SM-L100", "SM-L100IN"),)), reconciled_at=T0
            )
            later = T0 + timedelta(days=1)
            result = persist_samsung_reconciliation(
                store, reconcile_samsung(products, (
                    support("IN", "SM-L100", "SM-L100IN"),
                    support("IN", "SM-L200", "SM-L200IN", when=later),
                )), reconciled_at=later
            )
            self.assertEqual(result["events"], 1)
            event = store.connection.execute("SELECT event_type FROM samsung_candidate_events").fetchone()[0]
            self.assertEqual(event, "SUPPORT_REGIONAL_SKU_FIRST_SEEN")

    def test_sm_l305_like_multi_region_case_is_one_global_base_and_six_regional_candidates(self):
        supports = tuple(
            support(region, "SM-L305", sku)
            for region, sku in (
                ("IN", "SM-L305FZEAINS"), ("IN", "SM-L305FZGAINS"),
                ("GB", "SM-L305FZEAEUA"), ("GB", "SM-L305FZGAEUA"),
                ("DE", "SM-L305FZEADBT"), ("DE", "SM-L305FZGADBT"),
            )
        )
        records = reconcile_samsung((), supports)
        self.assertEqual({item.base_model for item in records}, {"SM-L305"})
        self.assertEqual(len(records), 6)
        with tempfile.TemporaryDirectory() as directory, SQLiteStore(Path(directory) / "r.sqlite3") as store:
            first = persist_samsung_reconciliation(store, records, reconciled_at=T0)
            second = persist_samsung_reconciliation(store, records, reconciled_at=T0 + timedelta(hours=1))
            self.assertEqual((first["events"], second["events"]), (0, 0))
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM prelaunch_candidates").fetchone()[0], 6)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM samsung_candidate_events").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
