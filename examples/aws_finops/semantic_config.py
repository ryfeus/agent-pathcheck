"""Semantic declarations beside the policy, with no fixture dependency."""

from agent_pathcheck.semantic import Relationship, Vocabulary
from .policy import Candidate, Evidence, ServiceCost


def declarations(policy):
    models = policy.parameters['models']
    if 'CostQuery' in models:
        request = models['CostQuery']
        relationships = [Relationship(f'cost_scope_{field}', request, field, 'costs', field,
                          'The scoped request selects this dimension of the cost rows.')
                         for field in ('account', 'month', 'metric')]
        relationships.append(Relationship('service_cost_service', ServiceCost, 'service', 'costs', 'service',
                                          'The operation aggregates source records by service within its request scope.'))
        vocabulary = [Vocabulary('prod', request, 'account', 'production'),
                      Vocabulary('amortized cost', request, 'metric', 'NetAmortizedCost'),
                      Vocabulary('last month', request, 'month', policy.parameters['months'][0])]
    else:
        relationships = [
            Relationship('candidate_evidence', Candidate, 'instance_id', Evidence, 'instance_id',
                         'Evidence belongs to the instance; each metric has its own observation.'),
            Relationship('candidate_target', Candidate, 'target_type', 'instance_types', 'instance_type',
                         'The target catalog supplies the observed target architecture.'),
            Relationship('candidate_instance', Candidate, 'instance_id', 'instances', 'instance_id',
                         'The candidate belongs to the scoped inventory instance.'),
        ]
        vocabulary = [Vocabulary('prod', models['CandidatesQuery'], 'account', 'production')]
    return relationships, vocabulary
