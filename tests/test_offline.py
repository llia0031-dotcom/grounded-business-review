"""Offline checks. No model is called and nothing is spent.

  python -m unittest discover -s tests -v

A small database is generated once (fixed seed, one planted event) and every test reads it.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bizagent import baseline, config as C, db, decompose as D, metrics as M, monitor, score, world  # noqa: E402
from bizagent.evidence import Evidence, check_blocks, row  # noqa: E402
from bizagent.tools import Toolbox  # noqa: E402

WEEK = "2026-W36"
EVENT = {"lever": "traffic", "segment": {"channel": "直播"}, "size": -0.4}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = db.build(world.generate(7, 36, [EVENT]), Path(cls.tmp.name) / "t.duckdb")
        cls.con = db.connect(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()


class TestData(Base):
    def test_same_seed_same_data(self):
        a, b = world.generate(3, 22), world.generate(3, 22)
        for name in a:
            self.assertTrue(a[name].equals(b[name]), name)

    def test_tables_agree(self):
        """Every redemption and refund belongs to a paid order, and no order has both."""
        q = lambda s: self.con.execute(s).fetchone()[0]
        self.assertEqual(q("SELECT COUNT(*) FROM fact_redeem r LEFT JOIN fact_order o USING (order_id) WHERE o.order_id IS NULL"), 0)
        self.assertEqual(q("SELECT COUNT(*) FROM fact_refund f LEFT JOIN fact_order o USING (order_id) WHERE o.order_id IS NULL"), 0)
        self.assertEqual(q("SELECT COUNT(*) FROM fact_redeem r JOIN fact_refund f USING (order_id)"), 0)
        self.assertEqual(q("SELECT COUNT(*) FROM fact_redeem r JOIN fact_order o USING (order_id) WHERE r.redeem_date < o.pay_date"), 0)
        self.assertEqual(q("SELECT COUNT(*) FROM fact_order o JOIN dim_store s USING (store_id) WHERE o.pay_date < s.open_date"), 0)

    def test_event_is_in_the_data(self):
        df = D.breakdown(self.con, "visits", WEEK, "channel")
        live = df[df["slice"] == "直播"].iloc[0]
        self.assertAlmostEqual(live["change_pct"], -40, delta=4)
        others = df[df["slice"] != "直播"]["change_pct"].abs().max()
        self.assertLess(others, 6)


class TestMetricSql(Base):
    def test_hand_written_sql_matches_catalog(self):
        """sql/metrics/01..04 compute each metric from the detail tables; the catalog computes it from the cubes."""
        got = {}
        for f in sorted((ROOT / "sql" / "metrics").glob("0[1-4]_*.sql")):
            df = self.con.execute(f.read_text(encoding="utf-8")).fetchdf()
            for col in df.columns[1:]:
                got[col] = dict(zip(df["week_id"], df[col]))
        cat = self.con.execute("SELECT metric, week_id, value FROM metric_weekly WHERE dim = 'total'").fetchall()
        self.assertEqual({m["name"] for m in M.catalog()["metrics"]}, set(got))
        for name, week, value in cat:
            self.assertAlmostEqual(float(got[name][week]), float(value), places=1, msg=f"{name} {week}")

    def test_other_sql_files_run(self):
        for f in ["05_cohort_redeem.sql", "06_repeat_purchase.sql"]:
            df = self.con.execute((ROOT / "sql" / "metrics" / f).read_text(encoding="utf-8")).fetchdf()
            self.assertGreater(len(df), 5, f)


class TestDecomposition(Base):
    def test_additive_contributions_sum_to_100(self):
        for metric in ("gmv", "visits", "refund_amt"):
            df = D.breakdown(self.con, metric, WEEK, "category")
            self.assertAlmostEqual(df["contribution_pct"].sum(), 100.0, places=6)

    def test_ratio_effects_sum_to_the_change(self):
        for metric, dim in (("redeem_rate", "category"), ("conversion_rate", "channel"), ("aov", "city"), ("take_rate", "category")):
            df = D.breakdown(self.con, metric, WEEK, dim)
            self.assertAlmostEqual(df["effect"].sum(), df.attrs["total_change"], places=8, msg=metric)

    def test_kitagawa_example(self):
        """The two-city example in the method notes: 3 points lost, 1.575 of them rate effect in city B."""
        import numpy as np
        w0, w1 = np.array([0.6, 0.4]), np.array([0.5, 0.5])
        r0, r1 = np.array([85.0, 72.5]), np.array([85.0, 69.0])
        R0, R1 = (w0 * r0).sum(), (w1 * r1).sum()
        rate = (w0 + w1) / 2 * (r1 - r0)
        mix = (w1 - w0) * ((r0 + r1) / 2 - (R0 + R1) / 2)
        self.assertAlmostEqual(rate[1], -1.575)
        self.assertAlmostEqual(mix[0], -0.65)
        self.assertAlmostEqual((rate + mix).sum(), R1 - R0)

    def test_sql_decomposition_matches_python(self):
        sql = (ROOT / "sql" / "analysis" / "rate_mix.sql").read_text(encoding="utf-8")
        s = self.con.execute(sql, {"week": WEEK}).fetchdf().set_index("category")
        p = D.breakdown(self.con, "redeem_rate", WEEK, "category").set_index("slice")
        for cat in p.index:
            self.assertAlmostEqual(s.loc[cat, "rate_effect"], p.loc[cat, "rate_effect"], places=2)
            self.assertAlmostEqual(s.loc[cat, "mix_effect"], p.loc[cat, "mix_effect"], places=2)
        sql = (ROOT / "sql" / "analysis" / "contribution.sql").read_text(encoding="utf-8")
        s = self.con.execute(sql, {"week": WEEK}).fetchdf().set_index("category")
        p = D.breakdown(self.con, "gmv", WEEK, "category").set_index("slice")
        for cat in p.index:
            self.assertAlmostEqual(s.loc[cat, "contribution_pct"], p.loc[cat, "contribution_pct"], places=2)

    def test_adtributor_finds_the_channel(self):
        top = D.adtributor(self.con, "visits", WEEK)[0]
        self.assertEqual((top["dim"], top["slices"]), ("channel", ["直播"]))


class TestMonitorAndBaseline(Base):
    def test_monitor_sees_the_event(self):
        al = monitor.alerts(self.con, WEEK)
        self.assertTrue(((al["metric"] == "visits") & (al["dim"] == "channel") & (al["slice"] == "直播")).any())

    def test_baseline_names_the_cause_and_its_report_is_released(self):
        with tempfile.TemporaryDirectory() as d:
            r = baseline.analyse(self.con, WEEK, Path(d))
            self.assertEqual(r["causes"], [{"segment": {"channel": "直播"}, "lever": "traffic", "direction": "down"}])
            self.assertTrue(check_blocks(r["blocks"], Path(d), r["has_anomaly"])["released"])
            case = {"id": "x", "family": "traffic_channel", "week": WEEK,
                    "truth": {"causes": [{"segment": {"channel": "直播"}, "lever": "traffic", "direction": "down"}]}}
            s = score.score(self.con, case, r)
            self.assertTrue(s["located_all"] and s["levers_right"])


class TestScore(Base):
    def test_relation(self):
        chain = self.con.execute("SELECT merchant FROM cube_trade WHERE merchant_type = '连锁' AND category = '火锅' GROUP BY 1 ORDER BY SUM(orders) DESC LIMIT 1").fetchone()[0]
        self.assertEqual(score.relation(self.con, WEEK, {"category": "火锅", "merchant": chain}, {"merchant": chain}), "same")
        self.assertEqual(score.relation(self.con, WEEK, {"category": "火锅"}, {"merchant": chain}), "coarser")
        self.assertEqual(score.relation(self.con, WEEK, {"category": "烧烤"}, {"merchant": chain}), "other")
        self.assertEqual(score.relation(self.con, WEEK, {}, {"channel": "直播"}), "coarser")


class TestNumberCheck(Base):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ev = Evidence(Path(self.dir.name))
        self.tb = Toolbox(self.con, WEEK, self.ev)
        self.tb.breakdown("visits", "channel")
        df = D.table(D.breakdown(self.con, "visits", WEEK, "channel"))
        self.line = row(df, 0)
        self.value = df.iloc[0, 2]

    def tearDown(self):
        self.dir.cleanup()

    def check(self, kind, text, evidence, anomaly=True):
        blocks = [{"kind": kind, "text": text, "evidence": evidence}]
        return check_blocks(blocks, self.ev.dir, anomaly)

    def test_copied_number_passes(self):
        r = self.check("finding", f"直播的访问次数本周是 {self.value:.0f} 次。", [{"source": "q01", "quote": self.line}])
        self.assertTrue(r["released"])

    def test_changed_number_is_caught(self):
        r = self.check("finding", f"直播的访问次数本周是 {self.value + 1234:.0f} 次。", [{"source": "q01", "quote": self.line}])
        self.assertFalse(r["released"])
        self.assertEqual(r["blocks"][0]["status"], "numbers_not_in_quote")

    def test_edited_quote_is_caught(self):
        r = self.check("finding", f"直播的访问次数本周是 {self.value:.0f} 次。", [{"source": "q01", "quote": self.line.replace("直播", "搜索")}])
        self.assertEqual(r["blocks"][0]["status"], "quote_not_found")

    def test_number_without_evidence_is_caught(self):
        r = self.check("advice", "建议把直播场次恢复到每周 30 场。", [])
        self.assertEqual(r["blocks"][0]["status"], "no_evidence")

    def test_finding_must_state_a_number(self):
        r = self.check("finding", "直播的访问次数明显下降。", [{"source": "q01", "quote": self.line}])
        self.assertEqual(r["blocks"][0]["status"], "finding_without_number")

    def test_anomaly_report_needs_a_finding(self):
        r = self.check("summary", "本周直播流量下滑。", [])
        self.assertIn("no_finding", r["report_problems"])
        self.assertTrue(self.check("summary", "本周没有异动。", [], anomaly=False)["released"])

    def test_percentage_points_are_not_read_as_100(self):
        change = self.line.split("|")[5].strip()
        r = self.check("finding", f"直播的访问次数变化率是 {change} 个百分点。", [{"source": "q01", "quote": self.line}])
        self.assertTrue(r["released"], r)


class TestSqlTool(Base):
    def test_only_one_select(self):
        for bad in ("DROP TABLE fact_order", "SELECT 1; SELECT 2", "COPY (SELECT 1) TO 'x.csv'", "INSTALL httpfs"):
            with self.assertRaises(db.SqlRefused, msg=bad):
                db.query(self.con, bad)
        self.assertEqual(db.query(self.con, "SELECT 1 AS a").iloc[0, 0], 1)

    def test_no_file_access_and_no_writes(self):
        with self.assertRaises(Exception):
            self.con.execute("SELECT * FROM read_csv('anything.csv')")
        with self.assertRaises(Exception):
            self.con.execute("CREATE TABLE t AS SELECT 1")


if __name__ == "__main__":
    unittest.main()
