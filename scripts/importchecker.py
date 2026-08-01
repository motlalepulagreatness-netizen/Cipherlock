"""
Check whether a submission only imports allowed modules.
"""

from __future__ import annotations

import argparse
import ast
import importlib.metadata
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ALLOWED_DISTRIBUTIONS = {"su-stdlibs-python"}


@dataclass
class ImportViolation:
    file_path: Path
    line_number: int
    module_name: str
    reason: str


def normalize_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def local_module_names(src_dir: Path) -> set[str]:
    names: set[str] = set()

    for path in src_dir.iterdir():
        if path.is_file() and path.suffix == ".py":
            names.add(path.stem)
        elif path.is_dir() and (path / "__init__.py").is_file():
            names.add(path.name)

    return names


def stdlib_module_names() -> set[str]:
    return set(sys.builtin_module_names) | set(sys.stdlib_module_names)


def distribution_top_level_names(distribution_name: str) -> set[str]:
    names: set[str] = set()

    try:
        dist = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return names

    top_level_text = dist.read_text("top_level.txt")
    if top_level_text is not None:
        for line in top_level_text.splitlines():
            line = line.strip()
            if line:
                names.add(line)

    for file_path in dist.files or []:
        parts = file_path.parts
        if not parts:
            continue

        first = parts[0]
        if first.endswith(".dist-info") or first.endswith(".egg-info"):
            continue
        if first == "__pycache__":
            continue

        if len(parts) == 1 and first.endswith(".py"):
            names.add(first[:-3])
        elif len(parts) > 1 and parts[1] == "__init__.py":
            names.add(first)

    return names


def allowed_distribution_modules() -> set[str]:
    modules: set[str] = set()

    for distribution_name in ALLOWED_DISTRIBUTIONS:
        normalized_name = normalize_distribution_name(distribution_name)
        modules.update(distribution_top_level_names(normalized_name))

    return modules


def allowed_modules(src_dir: Path) -> set[str]:
    return (
        stdlib_module_names()
        | local_module_names(src_dir)
        | allowed_distribution_modules()
    )


def top_level_module(module_name: str) -> str:
    return module_name.split(".", 1)[0]


def is_allowed(module_name: str, allowed: set[str]) -> bool:
    return top_level_module(module_name) in allowed


def dynamic_import_name(node: ast.Call) -> str | None:
    function = node.func

    is_dunder_import = (
        isinstance(function, ast.Name) and function.id == "__import__"
    )
    is_importlib_import_module = (
        isinstance(function, ast.Attribute)
        and function.attr == "import_module"
        and isinstance(function.value, ast.Name)
        and function.value.id == "importlib"
    )

    if not is_dunder_import and not is_importlib_import_module:
        return None

    if not node.args:
        return None

    first_arg = node.args[0]
    if (
        isinstance(first_arg, ast.Constant)
        and isinstance(first_arg.value, str)
    ):
        return first_arg.value

    return "<dynamic>"


def check_file(py_file: Path, allowed: set[str]) -> list[ImportViolation]:
    source = py_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(py_file))
    violations: list[ImportViolation] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not is_allowed(alias.name, allowed):
                    violations.append(
                        ImportViolation(
                            py_file,
                            node.lineno,
                            alias.name,
                            "third-party or unapproved module",
                        )
                    )

        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                continue
            if node.module is None:
                continue
            if not is_allowed(node.module, allowed):
                violations.append(
                    ImportViolation(
                        py_file,
                        node.lineno,
                        node.module,
                        "third-party or unapproved module",
                    )
                )

        elif isinstance(node, ast.Call):
            module_name = dynamic_import_name(node)
            if module_name is None:
                continue
            if module_name == "<dynamic>":
                violations.append(
                    ImportViolation(
                        py_file,
                        node.lineno,
                        module_name,
                        "dynamic import cannot be verified",
                    )
                )
            elif not is_allowed(module_name, allowed):
                violations.append(
                    ImportViolation(
                        py_file,
                        node.lineno,
                        module_name,
                        "third-party or unapproved dynamic import",
                    )
                )

    return violations


def check_imports(src_dir: Path) -> list[ImportViolation]:
    allowed = allowed_modules(src_dir)
    violations: list[ImportViolation] = []

    for py_file in sorted(src_dir.rglob("*.py")):
        violations.extend(check_file(py_file, allowed))

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check that a src directory only imports Python standard library "
            "modules, local src modules, and approved university libraries."
        )
    )
    parser.add_argument(
        "src_dir",
        type=Path,
        help="Path to the src directory.",
    )
    args = parser.parse_args()

    if not args.src_dir.is_dir():
        parser.exit(1, f"error: source directory not found: {args.src_dir}\n")

    violations = check_imports(args.src_dir)
    if not violations:
        print("Import check passed: no unapproved imports found.")
        return 0

    print("Import check failed: unapproved imports found.")
    for violation in violations:
        print(
            f"- {violation.file_path}:{violation.line_number}: "
            f"{violation.module_name} ({violation.reason})"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
