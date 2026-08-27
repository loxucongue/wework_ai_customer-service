from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ai_paths" / "scripts" / "run_sop_pending_flow_simulation.py"
SPEC = importlib.util.spec_from_file_location("sop_pending_flow_simulation", SCRIPT)
assert SPEC and SPEC.loader
simulation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(simulation)


class SopPendingFlowSimulationTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_deterministic_scenarios_pass(self) -> None:
        results = [await simulation.run_scenario(case) for case in simulation.scenarios()]

        failures = [result for result in results if not result["passed"]]
        self.assertEqual(failures, [])

    def test_all_fixture_customers_are_isolated(self) -> None:
        for case in simulation.scenarios():
            for task in [*(case.get("online") or []), *(case.get("store") or [])]:
                self.assertTrue(str(task.get("customer_wechat_id") or "").startswith("sim_"))
                self.assertEqual(task.get("corp_id"), "sim_corp")
                self.assertEqual(task.get("user_wechat"), "SIM001")


if __name__ == "__main__":
    unittest.main()
