"""
Count non-trivial classes in a src directory.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path


MIN_CLASS_SIGNIFICANT_LINES = 10
MIN_NON_INIT_METHODS = 2


@dataclass
class ClassReport:
    file_path: Path
    class_name: str
    counted: bool
    reason: str


def is_enum_base(base: ast.expr) -> bool:
    if isinstance(base, ast.Name):
        return base.id == "Enum"
    if isinstance(base, ast.Attribute):
        return base.attr == "Enum"
    return False


def is_docstring_stmt(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def significant_statements(statements: list[ast.stmt]) -> list[ast.stmt]:
    return [stmt for stmt in statements if not is_docstring_stmt(stmt)]


def is_trivial_return(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.Return):
        return False

    value = stmt.value
    return (
        isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id == "self"
    )


def is_trivial_setter(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
        return False

    target = stmt.targets[0]
    return (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    )


def is_nontrivial_method(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = significant_statements(node.body)
    if not body:
        return False

    if len(body) == 1:
        stmt = body[0]
        if isinstance(stmt, ast.Pass):
            return False
        if is_trivial_return(stmt):
            return False
        if is_trivial_setter(stmt):
            return False

    return True


def count_significant_class_lines(
    class_node: ast.ClassDef,
    source_lines: list[str],
) -> int:
    if class_node.end_lineno is None:
        return 0

    count = 0
    for line in source_lines[class_node.lineno - 1:class_node.end_lineno]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count


def evaluate_class(
    class_node: ast.ClassDef,
    file_path: Path,
    source_lines: list[str],
) -> ClassReport:
    if any(is_enum_base(base) for base in class_node.bases):
        return ClassReport(file_path, class_node.name, False, "enum")

    methods = [
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    non_init_methods = [
        method for method in methods if method.name != "__init__"
    ]

    if len(non_init_methods) < MIN_NON_INIT_METHODS:
        return ClassReport(
            file_path,
            class_node.name,
            False,
            "fewer than 2 non-__init__ methods",
        )

    if not any(is_nontrivial_method(method) for method in non_init_methods):
        return ClassReport(
            file_path,
            class_node.name,
            False,
            "no non-trivial non-__init__ method",
        )

    significant_lines = count_significant_class_lines(class_node, source_lines)
    if significant_lines < MIN_CLASS_SIGNIFICANT_LINES:
        return ClassReport(
            file_path,
            class_node.name,
            False,
            f"fewer than {MIN_CLASS_SIGNIFICANT_LINES} significant lines",
        )

    return ClassReport(file_path, class_node.name, True, "counted")


def analyze_src_dir(src_dir: Path) -> list[ClassReport]:
    reports: list[ClassReport] = []

    for py_file in sorted(src_dir.rglob("*.py")):
        try:
            source = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            reports.append(
                ClassReport(
                    py_file,
                    "<file>",
                    False,
                    f"could not read file: {error}",
                )
            )
            continue

        source_lines = source.splitlines()
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError as error:
            reports.append(
                ClassReport(
                    py_file,
                    "<file>",
                    False,
                    f"could not parse file: line {error.lineno}",
                )
            )
            continue

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                reports.append(evaluate_class(node, py_file, source_lines))

    return reports


def count_nontrivial_classes(src_dir: Path) -> tuple[int, list[ClassReport]]:
    reports = analyze_src_dir(src_dir)
    count = sum(1 for report in reports if report.counted)
    return count, reports


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Count non-trivial classes in a src directory."
    )
    parser.add_argument(
        "src_dir",
        type=Path,
        help="Path to the src directory.",
    )
    args = parser.parse_args()

    if not args.src_dir.is_dir():
        parser.exit(1, f"error: source directory not found: {args.src_dir}\n")

    count, reports = count_nontrivial_classes(args.src_dir)
    print(f"Non-trivial classes: {count}")

    if reports:
        print("\nClass analysis:")
        for report in reports:
            status = "counted" if report.counted else "ignored"
            print(
                f"- {report.file_path}:{report.class_name}: "
                f"{status} ({report.reason})"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
