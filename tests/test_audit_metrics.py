import unittest
from examples.aws_finops.benchmark import audit_metrics, summarize, HELD_OUT_MODES


class AuditTests(unittest.TestCase):
    def test_synthetic_reports_count_claims_lookups_and_inconclusive(self):
        failed=dict(claim_attempts_rejected={'Answer':2},policy_events=[{'errors':[{'kind':'arithmetic'}]}],
                    replays=[{'verdict':'inconclusive','exemption_reason':'explicit'}])
        abstained=dict(contract_check='passed',result={'kind':'abstained'})
        rows=[dict(scenario='a',reports=[failed,abstained],last_report=abstained,lookups=2),
              dict(scenario='b',reports=[],last_report=None,lookups=0)]
        result=audit_metrics(rows)
        self.assertEqual(result['claim_attempts_rejected'],{'total':2,'per_scenario':{'a':2}})
        self.assertEqual(result['abstention_rate'],0.5)
        self.assertEqual(result['replay_inconclusive'],1)
        self.assertEqual(result['docs_lookups_per_trial'],[2,0])
        self.assertEqual(result['violation_kinds'],{'arithmetic':1})
        self.assertEqual(result['held_out_modes'],HELD_OUT_MODES)
        self.assertEqual(audit_metrics([])['abstention_rate'],0)
