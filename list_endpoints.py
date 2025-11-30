#!/usr/bin/env python3
"""List UI vs. Sentinel HTTP endpoints defined across the codebase."""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import Iterable, List, Tuple


ROOT = Path(__file__).resolve().parent


def _attr_to_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute):
        value = _attr_to_str(node.value)
        if value:
            return f"{value}.{node.attr}"
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _extract_route_args(decorator: ast.Call) -> Tuple[str | None, List[str]]:
    rule = None
    methods: List[str] = []

    args = decorator.args
    if args:
        first = args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            rule = first.value

    for keyword in decorator.keywords:
        if keyword.arg == "rule" and isinstance(keyword.value, ast.Constant):
            # Ensure we only assign string values to rule
            if isinstance(keyword.value.value, str):
                rule = keyword.value.value
        if keyword.arg == "methods" and isinstance(
            keyword.value, (ast.List, ast.Tuple)
        ):
            for elt in keyword.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    methods.append(elt.value)

    return rule, methods


def _gather_routes_from_file(
    path: Path,
) -> List[Tuple[str | None, List[str], str, int]]:
    with path.open("r", encoding="utf-8") as fh:
        source = fh.read()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    routes: List[Tuple[str | None, List[str], str, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call):
                    name = _attr_to_str(dec.func)
                    if name and name.endswith(".route"):
                        rule, methods = _extract_route_args(dec)
                        routes.append((rule, methods, name, node.lineno))
                    elif name == "route":
                        rule, methods = _extract_route_args(dec)
                        routes.append((rule, methods, name, node.lineno))
    return routes


def _collect_endpoints(
    root: Path,
) -> dict[str, List[Tuple[str | None, List[str], str, int]]]:
    endpoints = defaultdict(list)
    for py_path in root.rglob("*.py"):
        rel = py_path.relative_to(ROOT)
        routes = _gather_routes_from_file(py_path)
        if not routes:
            continue
        endpoints[str(rel)] = routes
    return endpoints


def _dump_group(
    name: str, endpoints: dict[str, List[Tuple[str | None, List[str], str, int]]]
) -> None:
    print(f"\n{name} ({len(endpoints)} files with routes)")
    for rel_path, routes in sorted(endpoints.items()):
        print(f"\n  {rel_path}")
        for rule, methods, decorator, lineno in sorted(
            routes, key=lambda x: (x[0] or "", x[3])
        ):
            method_info = f" [{', '.join(methods)}]" if methods else ""
            print(
                f"    Line {lineno}: {decorator} -> {rule or '<dynamic>'}{method_info}"
            )


def main() -> None:
    ui_root = ROOT / "ui"
    sentinel_root = ROOT / "src" / "tgsentinel"

    ui_endpoints = _collect_endpoints(ui_root)
    sentinel_endpoints = _collect_endpoints(sentinel_root)

    if not ui_endpoints and not sentinel_endpoints:
        print("No route decorators found.")
        return

    if ui_endpoints:
        _dump_group("UI Container Endpoints", ui_endpoints)
    else:
        print("No UI endpoints detected.")

    if sentinel_endpoints:
        _dump_group("Sentinel Container Endpoints", sentinel_endpoints)
    else:
        print("No Sentinel endpoints detected.")


if __name__ == "__main__":
    main()
