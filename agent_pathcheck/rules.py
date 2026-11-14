"""Policy rule identity and independently versioned explanatory metadata."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Level(StrEnum):
    DOCUMENTED = 'documented'
    GUIDED = 'guided'
    CHECKED = 'checked'
    TESTED = 'tested'


@dataclass(frozen=True)
class Rule:
    rule_id: str
    summary: str
    level: Level
    owner: str
    source: str
    why: str
    review_by: str | None = None

    def __post_init__(self):
        object.__setattr__(self, 'level', Level(self.level))
        if not all((self.rule_id, self.summary, self.owner, self.source, self.why)):
            raise ValueError('Rule metadata must be nonempty')
        if '\n' in self.summary:
            raise ValueError('Rule summary must fit on one line')
        if self.review_by:
            date.fromisoformat(self.review_by)


def shared_rules():
    descriptions = {
        'claim.attempt_limit': 'Final claims have a bounded construction budget',
        'abstain.unjustified': 'Refusal requires an unresolved task-required block',
        'evidence.required': 'Claims require completed observations',
        'operation.single_result': 'Scalar operations return exactly one row',
        'replay.required': 'Required counterfactuals must pass or qualify for exemption',
        'data.integrity': 'Host data must satisfy declared relationships',
    }
    for name in ('max_queries', 'max_rows', 'max_sql_bytes', 'sql_instructions', 'max_sql_value_bytes',
                 'max_source_chars', 'max_memory', 'max_feed_duration_secs', 'max_suspensions',
                 'max_total_sleep_secs', 'max_constructions'):
        descriptions[f'limit.{name}'] = f'Execution respects the {name} budget'
    for name in ('literal_error', 'int_type', 'float_type', 'string_type', 'list_type', 'missing',
                 'extra_forbidden', 'less_than_equal', 'greater_than_equal', 'finite_number',
                 'model_type'):
        descriptions[f'pydantic.{name}'] = f'Typed values satisfy the {name} constraint'
    return {key: Rule(key, text, Level.CHECKED, 'reference-example', 'engine contract',
                      'The host checks this boundary before publishing a validated answer.')
            for key, text in descriptions.items()}
