"""Static checks for policy source shown to a Monty guest."""

import ast


# Monty materializes generator expressions as lists, which next() rejects.
BANNED_CONSTRUCTS = {"next_generator": "Monty generator expressions are lists, not iterators."}


def unsupported_constructs(source):
    return ["next_generator" for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "next" and node.args and isinstance(node.args[0], ast.GeneratorExp)]
