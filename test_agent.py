"""Run:  python -m unittest discover -s tests -v   (no API key or network needed)."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["LLM_MODE"] = "offline"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import openpyxl  # noqa: E402
from agent import registry  # noqa: E402
from agent.agent import Agent  # noqa: E402
from agent.config import load_settings  # noqa: E402
from agent.loader import load_test_questions, load_workflows  # noqa: E402
from agent.router import keyword_route  # noqa: E402

XLSX = ROOT / "data" / "workflows.xlsx"


class TestEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = Agent()

    def run_req(self, req, **params):
        return self.agent.handle(req, params, log=False)

    # ---- Excel loading & tool coverage -------------------------------------------
    def test_loads_ten_workflows(self):
        self.assertEqual(len(self.agent.workflows), 10)

    def test_every_excel_step_has_a_tool(self):
        """Maintainability guard: a workflow row whose steps have no tool fails here."""
        for wf in self.agent.workflows.values():
            for s in wf.steps:
                fn, _, how = registry.resolve(s)
                self.assertIsNotNone(fn, f"{wf.id}: no tool for step '{s}'")

    # ---- Routing: the 10 questions from the Test_Questions sheet -----------------
    def test_router_on_excel_test_questions(self):
        for row in load_test_questions(XLSX):
            d = keyword_route(row["Test_Request"], self.agent.workflows)
            self.assertEqual(d.workflow_id, row["Workflow_ID"], row["Test_Request"])

    def test_unrelated_request_is_not_routed(self):
        d, run = self.run_req("What's the weather like in Paris?")
        self.assertIsNone(d.workflow_id)
        self.assertIsNone(run)

    # ---- Business rules -----------------------------------------------------------
    def test_wf001_threshold_rule(self):
        _, r = self.run_req("Which products need restocking?")
        self.assertEqual(r.status, "success")
        skus = set(r.result["skus"])
        self.assertIn("SKU-009", skus)          # stock 0 < 5
        self.assertNotIn("SKU-004", skus)       # stock 12 == min 12 -> NOT below
        self.assertNotIn("SKU-013", skus)       # non-numeric row skipped

    def test_wf002_flags_only_above_10_percent(self):
        _, r = self.run_req("Find products where vendor price differs by more than 10%.")
        self.assertEqual(set(r.result["skus"]), {"SKU-001", "SKU-003", "SKU-005", "SKU-007", "SKU-009"})

    def test_wf003_invalid_rows(self):
        _, r = self.run_req("Process this vendor spreadsheet", file="data/vendor_file_messy.csv")
        self.assertEqual((r.result["valid"], r.result["invalid"]), (4, 3))

    def test_wf003_missing_required_column_fails_cleanly(self):
        _, r = self.run_req("Process this vendor file", file="data/vendor_file_bad_columns.csv")
        self.assertEqual(r.status, "failed")
        self.assertEqual([s.status for s in r.steps][-2:], ["skipped", "skipped"])

    def test_wf004_does_not_invent_missing_attributes(self):
        _, r = self.run_req("Generate SEO content for this product.", product_name="Camping Headlamp")
        self.assertEqual(r.status, "success")
        self.assertIn("material", r.result["missing"])
        for bad in ("cotton", "leather", "red", "waterproof"):
            self.assertNotIn(bad, r.output.lower().split("missing information")[0])
        self.assertLessEqual(len(r.result["seo_title"]), 60)

    def test_wf004_asks_for_product_name(self):
        _, r = self.run_req("Generate SEO content for this product.")
        self.assertEqual(r.status, "needs_input")

    def test_wf005_found_not_found_and_retry(self):
        self.assertEqual(self.run_req("Where is order ORD-1001?")[1].status, "success")
        self.assertEqual(self.run_req("Where is order ORD-9999?")[1].status, "needs_input")
        _, r = self.run_req("Where is order ORD-5000?")     # simulated timeout -> auto retry
        self.assertEqual(r.status, "success")
        self.assertTrue(any(s.attempts > 1 for s in r.steps) or "ORD-5000" in r.output)

    def test_wf006_exact_sku_is_definite_and_attribute_match_is_possible(self):
        _, r = self.run_req("Find likely duplicate products in the catalog.")
        labels = [g["confidence"] for g in r.result["groups"]]
        self.assertTrue(any(l.startswith("DEFINITE") for l in labels))
        self.assertTrue(any("possible" in l for l in labels))

    def test_wf007_requires_goal_and_dates(self):
        _, r = self.run_req("Create a campaign brief for the new collection.")
        self.assertEqual(r.status, "needs_input")
        self.assertEqual(set(r.missing), {"campaign_goal", "dates"})
        self.assertEqual(r.steps[1].status, "skipped")      # nothing generated before asking

    def test_wf008_four_intents_and_dedupe(self):
        _, r = self.run_req("Classify these keywords and map them to pages.", keywords_file="data/keywords.csv")
        self.assertEqual(r.status, "success")
        self.assertIn("1 duplicates removed", r.output)
        for intent in ("informational", "commercial", "transactional", "navigational"):
            self.assertIn(intent, r.output)

    def test_wf009_assigns_and_escalates(self):
        _, r = self.run_req("Assign this urgent task to the best available developer.",
                            task="Fix the checkout bug in the React frontend that calls the Node API")
        self.assertEqual(r.status, "success")
        self.assertEqual(r.result["employee"], "Rahul Verma")
        _, r = self.run_req("Assign this task", task="Build a Rust service", required_skills="rust;kubernetes")
        self.assertEqual(r.status, "escalated")

    def test_wf010_flags_high_failure_workflows(self):
        _, r = self.run_req("Which workflows are failing most often?")
        self.assertIn("WF003", r.result["flagged"])

    # ---- THE scalability test: add an 11th workflow with NO Python changes -----------
    def test_eleventh_workflow_needs_only_an_excel_row(self):
        tmp = Path(tempfile.mkdtemp()) / "workflows.xlsx"
        shutil.copy(XLSX, tmp)
        wb = openpyxl.load_workbook(tmp)
        ws = wb["Workflows"]
        ws.append(["WF011", "Out of Stock Alert", "User asks which products are completely out of stock",
                   "Product inventory CSV",
                   "Load inventory → compare current stock with minimum threshold → identify low-stock products "
                   "→ generate restock list",
                   "Products below minimum are listed", "CSV reader", "List of products needing urgent attention"])
        wb.save(tmp)
        agent = Agent(workflows_xlsx=tmp)
        self.assertEqual(len(agent.workflows), 11)
        _, r = agent.handle("Run the out of stock alert", {}, workflow_id="WF011", log=False)
        self.assertEqual(r.status, "success")
        self.assertIn("SKU-009", r.output)

    def test_unknown_step_fails_gracefully(self):
        tmp = Path(tempfile.mkdtemp()) / "workflows.xlsx"
        shutil.copy(XLSX, tmp)
        wb = openpyxl.load_workbook(tmp)
        wb["Workflows"].append(["WF012", "Broken", "x", "x", "Load inventory → teleport the warehouse",
                                "x", "x", "x"])
        wb.save(tmp)
        _, r = Agent(workflows_xlsx=tmp).handle("x", {}, workflow_id="WF012", log=False)
        self.assertEqual(r.status, "failed")
        self.assertIn("No tool registered", r.message)


if __name__ == "__main__":
    unittest.main()
