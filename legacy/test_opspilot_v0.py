import unittest
from opspilot import add_event, connect, decide_action, draft_action, triage


class OpsTests(unittest.TestCase):
    def setUp(self):
        self.db = connect(":memory:")

    def tearDown(self):
        self.db.close()

    def test_cited_triage(self):
        eid = add_event(self.db, "payments", "payment_errors", 4)
        result = triage(self.db, "payments")
        self.assertEqual(result["claims"][0]["evidence"], f"event:{eid}")
        self.assertEqual(result["claims"][0]["runbook"], "RB-PAY")

    def test_approval(self):
        self.db.execute("INSERT INTO incidents VALUES('I','payments','test')")
        aid = draft_action(self.db, "I", "Add ticket note")
        self.assertEqual(self.db.execute("SELECT status FROM actions").fetchone()[0], "pending")
        decide_action(self.db, aid, True)
        with self.assertRaises(ValueError):
            decide_action(self.db, aid, False)


if __name__ == "__main__":
    unittest.main()
