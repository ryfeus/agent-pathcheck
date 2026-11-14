"""SQLAlchemy Core schema and three host-owned FinOps query builders."""

from sqlalchemy import Column, Float, ForeignKey, Integer, MetaData, String, Table, func, select
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateTable


metadata = MetaData()
costs = Table(
    "costs", metadata,
    Column("account", String, nullable=False),
    Column("month", String, nullable=False),
    Column("service", String, nullable=False),
    Column("metric", String, nullable=False),
    Column("cost_cents", Integer, nullable=False),
)
instances = Table(
    "instances", metadata,
    Column("instance_id", String, primary_key=True),
    Column("account", String, nullable=False),
    Column("instance_type", String, nullable=False),
    Column("architecture", String, nullable=False),
)
instance_types = Table(
    "instance_types", metadata,
    Column("instance_type", String, primary_key=True),
    Column("architecture", String, nullable=False),
)
recommendations = Table(
    "recommendations", metadata,
    Column("recommendation_id", String, primary_key=True),
    Column("instance_id", String, ForeignKey("instances.instance_id"), nullable=False),
    Column("target_type", String, ForeignKey("instance_types.instance_type"), nullable=False),
    Column("performance_risk", Float, nullable=False),
    Column("monthly_savings_cents", Integer, nullable=False),
)
observations = Table(
    "observations", metadata,
    Column("instance_id", String, ForeignKey("instances.instance_id"), primary_key=True),
    Column("metric", String, primary_key=True),
    Column("p95_percent", Float, nullable=False),
    Column("days", Integer, nullable=False),
)
SQLITE = sqlite.dialect()
CANDIDATE_COLUMNS = (recommendations.c.recommendation_id, instances.c.instance_id,
                     instances.c.instance_type, instances.c.architecture,
                     recommendations.c.target_type, instance_types.c.architecture.label("target_architecture"),
                     recommendations.c.performance_risk, recommendations.c.monthly_savings_cents)
EVIDENCE_COLUMNS = (observations.c.instance_id, observations.c.metric,
                    observations.c.p95_percent, observations.c.days)


def create_schema(connection):
    """Create the same declared relationships in SQLite, before loading data."""
    connection.execute("PRAGMA foreign_keys = ON")
    for table in metadata.sorted_tables:
        connection.execute(str(CreateTable(table).compile(dialect=SQLITE)))


def cost_statement(request):
    return (
        select(costs.c.service, func.sum(costs.c.cost_cents).label("cost_cents"))
        .where(costs.c.account == request.account,
               costs.c.month == request.month,
               costs.c.metric == request.metric)
        .group_by(costs.c.service)
        .order_by(costs.c.service)
    )


def candidates_statement(request):
    return (
        select(*CANDIDATE_COLUMNS)
        .select_from(recommendations.join(instances).join(instance_types))
        .where(instances.c.account == request.account)
        .order_by(recommendations.c.recommendation_id)
    )


def evidence_statement(request):
    return (
        select(*EVIDENCE_COLUMNS)
        .select_from(observations.join(instances))
        .where(instances.c.account == request.account, observations.c.metric == request.metric)
        .order_by(observations.c.instance_id)
    )


def execute_statement(sql_query, statement):
    """Use the existing bounded SQLite runner, keeping values out of SQL text.

    These three builders bind only strings; no SQLAlchemy type processors or
    expanding parameters are needed. This is not a general statement executor.
    """
    compiled = statement.compile(dialect=SQLITE)
    params = tuple(compiled.params[name] for name in compiled.positiontup)
    return sql_query(str(compiled), params)
