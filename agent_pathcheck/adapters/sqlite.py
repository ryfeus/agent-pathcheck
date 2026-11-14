"""Bounded, read-only SQLite access; initialization belongs to the host domain."""

from contextlib import closing, contextmanager
import math
import sqlite3

from agent_pathcheck.violations import ResourceLimitExceeded


MAX_QUERIES = 10
MAX_ROWS = 100
MAX_SQL_BYTES = 10_000
MAX_SQL_VALUE_BYTES = 1_000_000
SQL_PROGRESS_INTERVAL = 1_000
SQL_INSTRUCTION_BUDGET = 100_000


def authorize_read_only(action, _arg1, arg2, _database, _trigger):
    """Deny everything except reads, SELECTs, recursion and safe functions."""
    if action == sqlite3.SQLITE_FUNCTION:
        return (
            sqlite3.SQLITE_DENY
            if (arg2 or "").lower() == "load_extension"
            else sqlite3.SQLITE_OK
        )
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _trace_scalar(value):
    """Keep ordinary SQLite values intact and encode non-JSON trace values."""
    if value is None or type(value) in (str, bool):
        return value
    if type(value) is int:
        # SQLite rejects huge bound integers; avoid serializing unbounded digits.
        return value if value.bit_length() <= 1024 else {"integer_bits": value.bit_length()}
    if type(value) is float:
        return value if math.isfinite(value) else {"float": str(value)}
    if type(value) is bytes:
        return {"bytes_hex": value.hex()}
    return {"unsupported_type": type(value).__name__}


def _trace_params(params):
    if isinstance(params, dict):
        return {str(key): _trace_scalar(value) for key, value in params.items()}
    if isinstance(params, (tuple, list)):
        return [_trace_scalar(value) for value in params]
    return _trace_scalar(params)


def make_query(connection, trace):
    def query(sql: str, params=()) -> list[dict]:
        entry = {"sql": _trace_scalar(sql), "params": _trace_params(params)}
        trace.append(entry)
        instructions = 0

        def progress():
            nonlocal instructions
            instructions += SQL_PROGRESS_INTERVAL
            return int(instructions >= SQL_INSTRUCTION_BUDGET)

        try:
            if len(trace) > MAX_QUERIES:
                raise ResourceLimitExceeded("max_queries", MAX_QUERIES, f"SQL query limit exceeded ({MAX_QUERIES})")
            if isinstance(sql, str) and len(sql.encode()) > MAX_SQL_BYTES:
                raise ResourceLimitExceeded("max_sql_bytes", MAX_SQL_BYTES, "SQL query too large")
            connection.set_progress_handler(progress, SQL_PROGRESS_INTERVAL)
            with closing(connection.cursor()) as cursor:
                cursor.execute(sql, params)
                if cursor.description is None:
                    raise ValueError("SQL must return a result set")
                rows = [dict(row) for row in cursor.fetchmany(MAX_ROWS + 1)]
            if len(rows) > MAX_ROWS:
                raise ResourceLimitExceeded("max_rows", MAX_ROWS, f"SQL row limit exceeded ({MAX_ROWS})")
            entry["rows"] = [
                {name: _trace_scalar(value) for name, value in row.items()} for row in rows
            ]
            return rows
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if instructions >= SQL_INSTRUCTION_BUDGET:
                message = "SQLite instruction budget exceeded"
            entry["error"] = message
            if instructions >= SQL_INSTRUCTION_BUDGET:
                raise ResourceLimitExceeded("sql_instructions", SQL_INSTRUCTION_BUDGET, message) from None
            if isinstance(exc, ResourceLimitExceeded):
                raise
            if isinstance(exc, sqlite3.DataError) and "too big" in str(exc):
                raise ResourceLimitExceeded("max_sql_value_bytes", MAX_SQL_VALUE_BYTES, message) from None
            # Built-in exceptions cross the Monty boundary and can be caught.
            raise ValueError(message) from None
        finally:
            connection.set_progress_handler(None, 0)

    return query


@contextmanager
def open_database(initialize, trace):
    """Create a fresh database, initialize it, then expose only bounded reads."""
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.row_factory = sqlite3.Row
        initialize(connection)
        connection.commit()
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_BYTES)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_SQL_VALUE_BYTES)
        connection.execute("PRAGMA query_only = ON")
        connection.set_authorizer(authorize_read_only)
        yield make_query(connection, trace)


def adapter_limits():
    return dict(max_queries=MAX_QUERIES, max_rows=MAX_ROWS, max_sql_bytes=MAX_SQL_BYTES,
                max_sql_value_bytes=MAX_SQL_VALUE_BYTES, sql_instructions=SQL_INSTRUCTION_BUDGET)
