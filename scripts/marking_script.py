"""
The official marking script for the Cipherlock CS144 project 2026.
@author Dylan James Reid
"""

##############################################################################
# Imports
##############################################################################

import sys
import argparse
import subprocess
import shutil
import tempfile
import secrets
from pathlib import Path
import csv
import re
import zipfile
from contextlib import contextmanager
from contextlib import nullcontext
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from classchecker import count_nontrivial_classes  # noqa: E402
from importchecker import check_imports  # noqa: E402

try:
    import pycodestyle
except ModuleNotFoundError:
    pycodestyle = None

##############################################################################
# Flags / globals
##############################################################################

log_output = True
SCRIPT_DIR = Path(__file__).resolve().parent

##############################################################################
# Constants
##############################################################################


# Directories that should be marked for each SH
# Note that all implicitly have SH1.
DIRS_TO_MARK = {
    "SH1": [],
    "SH2": ["SH2"],
    "SH3": ["SH3"],
    "SH4": ["SH4"],
    "SH5": ["SH5"],
    "SH6": ["SH6"],
    "SH7": ["SH7"],
    "SH8": ["SH8"],
    "FH1": ["SH2", "SH3", "SH5"],
    "FH2": ["SH6", "SH7", "SH8", "STYLE"],
}

# Weighting of test cases
SH_CAT_WEIGHTS = {
    "SH1": {},
    "SH2": {
        "arge": 2,
        # Valid argument combinations. These are for SH2 progress checks only
        # and are not included in FH1.
        "argc": 2,
    },
    "SH3": {
        "rsgn": 7,
        "game": 14,
    },
    "SH4": {
        # Note, this particular prefix will not be tested in the midterm
        # hand-in, and is here purely to serve as a progress indicator for
        # students.
        "deck": 1
    },
    "SH5": {
        "crck": 3,
        "revl": 3,
        "toss": 3,
        "extd": 3,
        "prmt": 3,
        "hdst": 3,
        "mapp": 3,
        "cont": 3,
        "alll": 3,
    },
    "SH6": {
        "cigs": 10,
    },
    "SH7": {
        "lkgs": 15,
    },
    "SH8": {
        "agsh": 4,
        "agsm": 4,
        "agsr": 4,
        "agsa": 3,
    },
    "FH1": {
        "arge": 2,
        "rsgn": 7,
        "game": 14,
        "crck": 3,
        "revl": 3,
        "toss": 3,
        "extd": 3,
        "prmt": 3,
        "hdst": 3,
        "mapp": 3,
        "cont": 3,
        "alll": 3,
    },
    "FH2": {
        "cigs": 10,
        "lkgs": 15,
        "agsh": 4,
        "agsm": 4,
        "agsr": 4,
        "agsa": 3,
        "STYLE": 10,
    },
}

# SH ARGS
SH_ARGS = {
    "SH2": ["0", "0"],
    "SH3": ["0", "0"],
    "SH4": ["1", "0"],
    "SH5": ["1", "0"],
    "SH6": ["0", "1"],
    "SH7": ["0", "2"],
    "SH8": ["1", "3"],
}

# Response types for each test case
TC_FAILED = "FAILED"
TC_PASSED = "PASSED"
TC_CRASHED = "CRASH"
TC_TOTAL = "TOTAL"

FINAL = "final"
DEFAULT_FEEDBACK = "All is as expected."
SANDBOX_NONE = "none"
SANDBOX_BWRAP = "bwrap"
SANDBOX_BWRAP_NO_NET = "bwrap-no-net"
SANDBOX_MODES = {SANDBOX_BWRAP, SANDBOX_BWRAP_NO_NET}
REQUIRED_PYTHON_VERSION = (3, 12, 3)
REQUIRED_PYCODESTYLE_VERSION = "2.14.0"

STYLE_PYCODESTYLE_POINTS = 50
STYLE_README_POINTS = 10
STYLE_GITIGNORE_POINTS = 10
STYLE_CLASS_POINTS = 30
STYLE_TOTAL_POINTS = (
    STYLE_PYCODESTYLE_POINTS
    + STYLE_README_POINTS
    + STYLE_GITIGNORE_POINTS
    + STYLE_CLASS_POINTS
)


def zero_mark(
    sh: str,
    feedback: list[str],
) -> tuple[dict[str, dict[str, int]], dict[str, float], list[str]]:
    """
    Build a result structure representing a zero for the whole hand-in.

    This is used for policy failures and missing submissions. It keeps the
    table/CSV shape the same as an ordinary marking run.
    """
    categories = SH_CAT_WEIGHTS[sh]
    results = {
        cat: {
            TC_PASSED: 0,
            TC_FAILED: 0,
            TC_CRASHED: 0,
            TC_TOTAL: 0,
        }
        for cat in categories
    }
    percentages = {cat: 0.0 for cat in categories}
    percentages[FINAL] = 0.0
    return results, percentages, feedback

##############################################################################
# Mark all projects
##############################################################################


def create_isolated_tests(
    tests_path: Path,
) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    """
    Copy the full test suite to a randomly named temporary directory.
    """
    if not tests_path.is_dir():
        raise RuntimeError(f"Test suite directory not found: {tests_path}")

    tmp_dir = tempfile.TemporaryDirectory(prefix=f"{secrets.token_hex(32)}-")
    isolated_tests_path = Path(tmp_dir.name) / "tests"
    shutil.copytree(tests_path, isolated_tests_path)
    return tmp_dir, isolated_tests_path


def missing_ref_path(handin: str) -> Path:
    """
    Return the optional missing-tag report for this hand-in.
    """
    return SCRIPT_DIR / "missing" / f"tag{handin}.txt"


def read_missing_ref_students(handin: str) -> set[str]:
    """
    Read students that could not be checked out to the requested hand-in tag.
    """
    path = missing_ref_path(handin)
    if not path.is_file():
        return set()

    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def restore_test_suite(source_tests_path: Path, target_tests_path: Path):
    """
    Restore the working tests directory from a pristine isolated copy.
    """
    if target_tests_path.exists():
        shutil.rmtree(target_tests_path)
    shutil.copytree(source_tests_path, target_tests_path)


def feedback_log_paths(sh: str, csv_path: Path) -> tuple[Path, Path]:
    """
    Return the feedback log directory and zip path for a batch CSV file.
    """
    stem = csv_path.stem
    if stem == f"{sh}_results":
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        stem = f"{sh}-{stamp}"

    feedback_dir = csv_path.parent / f"{stem}-feedback"
    return feedback_dir, feedback_dir.with_suffix(".zip")


def safe_filename_part(value: str) -> str:
    """
    Convert a CSV value into a conservative filename component.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return safe.strip("._-") or "student"


def write_feedback_logs(
    sh: str,
    csv_path: Path,
    headers: list[str],
    rows: list[dict[str, str]],
):
    """
    Write one human-readable feedback log per student and zip the directory.
    """
    feedback_dir, zip_path = feedback_log_paths(sh, csv_path)
    if feedback_dir.exists():
        shutil.rmtree(feedback_dir)
    if zip_path.exists():
        zip_path.unlink()

    feedback_dir.mkdir(parents=True, exist_ok=True)

    mark_headers = [
        h
        for h in headers
        if h not in ("StudentNumber", FINAL, "Feedback")
    ]

    for row in rows:
        snum = row.get("StudentNumber", "").strip()
        log_name = f"{safe_filename_part(snum)}-{safe_filename_part(sh)}.log"
        log_path = feedback_dir / log_name
        feedback_lines = [
            line.strip()
            for line in row.get("Feedback", "").splitlines()
            if line.strip()
        ]

        lines = [
            f"Student Number: {snum}",
            "",
            "Marks",
        ]
        for header in mark_headers:
            lines.append(f"{header}: {row.get(header, '')}")
        lines.extend(
            [
                f"{FINAL}: {row.get(FINAL, '')}",
                "",
                "Feedback",
            ]
        )
        if feedback_lines:
            lines.extend(f"- {line}" for line in feedback_lines)
        else:
            lines.append(f"- {DEFAULT_FEEDBACK}")

        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for log_path in sorted(feedback_dir.glob("*.log")):
            archive.write(
                log_path,
                arcname=f"{feedback_dir.name}/{log_path.name}",
            )

    print(f"Feedback logs written to {feedback_dir}")
    print(f"Feedback zip written to {zip_path}")


def mark_all(
    sh: str,
    vis_dirs: list[str],
    snums: list[str],
    batch_dir: Path,
    isolate_tests: bool = True,
    sandbox_mode: str = SANDBOX_NONE,
    output_csv: Path | None = None,
):
    """
    Mark a batch of submissions and write ``results/<sh>_results.csv``.

    CSV column order:
      1) categories (any order)
      2) final (second last)
      3) StudentNumber (third last)
      4) Feedback (last)

    Existing CSV behavior:
      - overwrite rows with same StudentNumber
      - append new students
    """
    # Don't print out
    global log_output
    log_output = False

    # 1) ordered, unique student numbers
    snums_ordered = sorted(set(snums))

    # Output location
    if output_csv is None:
        results_dir = Path("./results")
        results_dir.mkdir(parents=True, exist_ok=True)
        csv_path = results_dir / f"{sh}_results.csv"
    else:
        csv_path = output_csv
        csv_path.parent.mkdir(parents=True, exist_ok=True)

    # Helper: determine header order from percentages dict
    def build_headers(percentages: dict[str, float]) -> list[str]:
        # categories = all keys except "final"
        cats = [k for k in percentages.keys() if k != FINAL]
        return ["StudentNumber"] + cats + [FINAL, "Feedback"]

    # Load existing CSV rows so repeated batch runs can update students without
    # discarding unrelated rows already present in the result file.
    existing_headers: list[str] | None = None
    rows: list[dict[str, str]] = []
    snum_to_idx: dict[str, int] = {}

    if csv_path.exists():
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            existing_headers = reader.fieldnames or []
            for i, row in enumerate(reader):
                rows.append(row)
                sn = row.get("StudentNumber", "")
                if sn:
                    snum_to_idx[sn] = i

    # Write once at the end. This avoids rewriting the whole CSV after each
    # student while still keeping memory use small for normal class sizes.
    headers: list[str] | None = existing_headers

    tests_path = Path.cwd() / "tests"
    tmp_tests_dir: tempfile.TemporaryDirectory[str] | None = None
    isolated_tests_path: Path | None = None

    if isolate_tests:
        tmp_tests_dir, isolated_tests_path = create_isolated_tests(tests_path)

    missing_ref_students = read_missing_ref_students(sh)

    try:
        total_students = len(snums_ordered)
        for student_index, snum in enumerate(snums_ordered, start=1):
            print(f"[{student_index}/{total_students}] Marking {snum}")
            projdir = batch_dir / snum
            if snum in missing_ref_students:
                print(f"  missing tag for {sh}; assigning 0")
                feedback = [f"Student does not have tag for {sh}."]
                _, percentages, feedback = zero_mark(sh, feedback)
            else:
                try:
                    _, percentages, feedback = mark_project(
                        sh,
                        projdir,
                        vis_dirs,
                        isolated_tests_path,
                        sandbox_mode,
                    )
                finally:
                    if isolated_tests_path is not None:
                        restore_test_suite(isolated_tests_path, tests_path)
                print(f"  done: {percentages[FINAL]:.4f}")

            # New CSV files derive their headers from the first marked student.
            if headers is None or headers == []:
                headers = build_headers(percentages)

            # Existing CSV files may need new category columns after script
            # changes. Add missing columns while preserving the tail order.
            current_cats = [
                h
                for h in headers
                if h not in ("final", "StudentNumber", "Feedback")
            ]
            new_cats = [
                k
                for k in percentages.keys()
                if k != "final" and k not in current_cats
            ]
            if new_cats:
                headers = (
                    current_cats
                    + new_cats
                    + ["final", "StudentNumber", "Feedback"]
                )

            # Build row dict as strings for CSV
            row_out: dict[str, str] = {}
            for h in headers:
                if h == "StudentNumber":
                    row_out[h] = snum
                elif h == "Feedback":
                    row_out[h] = "\n".join(feedback or [DEFAULT_FEEDBACK])
                elif h == "final":
                    row_out[h] = f"{percentages.get('final', 0.0):.4f}"
                else:
                    row_out[h] = f"{percentages.get(h, 0.0):.4f}"

            # Overwrite if student exists, else append
            if snum in snum_to_idx:
                rows[snum_to_idx[snum]] = row_out
            else:
                snum_to_idx[snum] = len(rows)
                rows.append(row_out)
    finally:
        if tmp_tests_dir is not None:
            tmp_tests_dir.cleanup()

    # Ensure we have headers even if no students
    if headers is None:
        headers = ["final", "StudentNumber", "Feedback"]

    # --- Write back (overwrite file with merged content) ---
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    write_feedback_logs(sh, csv_path, headers, rows)


##############################################################################
# Mark one project
##############################################################################


def mark_project(
    sh: str,
    impl_path: Path | None = None,
    vis_dirs: list[str] | None = None,
    expected_tests_path: Path | None = None,
    sandbox_mode: str = SANDBOX_NONE,
):
    """
    Mark one submission directory.

    Args:
        sh: Hand-in identifier, such as ``SH5`` or ``FH2``.
        impl_path: Submission root. Defaults to the current directory.
        vis_dirs: Visibility directories to mark. If omitted, private tests
            are used for backwards compatibility with older internal calls.
        expected_tests_path: Optional isolated copy of the test suite from
            which expected outputs should be read.
        sandbox_mode: Execution sandbox for student code.

    Returns:
        A tuple containing raw results, percentages, and feedback messages.
    """
    if impl_path is None:
        impl_path = Path.cwd()

    if not impl_path.exists() or not impl_path.is_dir():
        feedback = ["Implementation directory not found"]
        return zero_mark(sh, feedback)

    if vis_dirs is None:
        vis_dirs = ["private"]

    setup_ok, feedback = mark_SH1(impl_path)
    cipherlock: Path = impl_path / "src" / "cipherlock.py"

    tests_path: Path = Path.cwd() / "tests"
    expected_path = expected_tests_path if expected_tests_path else tests_path

    sh_dirs = DIRS_TO_MARK[sh]
    marks: dict[str, dict[str, int]] = {}

    if sh == "SH1":
        if setup_ok:
            percentages = compute_final_mark(sh, marks, feedback)
        else:
            percentages = {FINAL: 0.0}
        return marks, percentages, feedback

    if sh != "SH1" and not cipherlock.is_file():
        feedback.append('Implementation for "src\\cipherlock.py" not found.')
        setup_ok = False

    if setup_ok:
        import_violations = check_imports(impl_path / "src")
        if import_violations:
            disallowed_libs = sorted(
                {
                    violation.module_name
                    for violation in import_violations
                }
            )
            feedback.append(
                "Detected the following disallowed libs: "
                f"{', '.join(disallowed_libs)}. "
                "This hand-in receives 0."
            )
            for violation in import_violations:
                feedback.append(
                    "POLICY: "
                    f"{violation.file_path}:{violation.line_number}: "
                    f"{violation.module_name} ({violation.reason})."
                )
            return zero_mark(sh, feedback)

        include_style_mark = "STYLE" in SH_CAT_WEIGHTS[sh]
        if sh != "SH1" or include_style_mark:
            style_mark = mark_style(
                impl_path,
                feedback,
                include_mark=include_style_mark,
            )
            if include_style_mark:
                marks.update(style_mark)

        sandbox_dir_ctx = (
            prepare_bwrap_submission(impl_path)
            if sandbox_mode in SANDBOX_MODES
            else nullcontext(None)
        )

        with sandbox_dir_ctx as sandbox_impl_path:
            run_impl_path = sandbox_impl_path or impl_path
            run_cipherlock = run_impl_path / "src" / "cipherlock.py"

            for sh_dir in sh_dirs:
                if sh_dir == "STYLE":
                    continue
                marks.update(
                    mark_cases(
                        sh_dir,
                        run_impl_path,
                        run_cipherlock,
                        tests_path / sh_dir,
                        expected_path / sh_dir,
                        vis_dirs,
                        set(SH_CAT_WEIGHTS[sh].keys()),
                        sandbox_mode,
                    )
                )

        percentages = compute_final_mark(sh, marks, feedback)
    else:
        percentages = {
            cat: 0.0 for cat in list(SH_CAT_WEIGHTS[sh].keys()) + [FINAL]
        }

    return marks, percentages, feedback


##############################################################################
# Different types of marking
##############################################################################


def mark_SH1(impl_path: Path) -> tuple[bool, list[str]]:
    """
    SH1 checks:
      - src/ exists
      - tests/ exists
      - scripts/ exists
      - README.md exists and is non-empty
      - .gitignore exists

    Returns: (setup_ok, feedback)
    """
    feedback: list[str] = []

    required_dirs = ("src", "tests", "scripts")
    for dirname in required_dirs:
        if not (impl_path / dirname).is_dir():
            feedback.append(f"Missing {dirname}/ directory.")

    readme_path = impl_path / "README.md"
    if not readme_path.is_file():
        feedback.append("Missing README.md file.")
    else:
        try:
            if not readme_path.read_text(encoding="utf-8").strip():
                feedback.append("README.md file is empty.")
        except UnicodeDecodeError:
            feedback.append(
                "README.md could not be read as UTF-8 text."
            )

    if not (impl_path / ".gitignore").is_file():
        feedback.append("Missing .gitignore file.")

    setup_ok = not feedback

    # Return
    return setup_ok, feedback


def has_gitignore(dir_path: Path, feedback: list[str]) -> bool:
    """
    Check whether the project has a top-level .gitignore file.
    """
    if (dir_path / ".gitignore").is_file():
        return True

    feedback.append("STYLE: missing .gitignore file.")
    return False


def has_nonempty_readme(dir_path: Path, feedback: list[str]) -> bool:
    """
    Check whether the project has a top-level non-empty README.md file.
    """
    readmes = [
        p
        for p in dir_path.iterdir()
        if p.is_file() and p.name.lower() == "readme.md"
    ]

    if not readmes:
        feedback.append("STYLE: missing README.md file.")
        return False

    try:
        if readmes[0].read_text(encoding="utf-8").strip():
            return True
    except UnicodeDecodeError:
        feedback.append("STYLE: README.md could not be read as UTF-8 text.")
        return False

    feedback.append("STYLE: README.md file is empty.")
    return False


def mark_style(
    dir_path: Path,
    feedback: list[str],
    include_mark: bool,
) -> dict[str, dict[str, int]]:
    """
    Check style for feedback, and optionally return a STYLE mark.
    """
    if pycodestyle is None:
        if include_mark:
            raise RuntimeError(
                "pycodestyle is not installed. Run "
                "`python -m pip install -r requirements.txt` first."
            )
        pycodestyle_points = 0
        feedback.append(
            "STYLE: pycodestyle is not installed; style feedback for "
            "pycodestyle could not be checked."
        )
    else:
        style = pycodestyle.StyleGuide(
            quiet=True,
        )
        report = style.check_files([str(dir_path / "src")])
        pycodestyle_points = STYLE_PYCODESTYLE_POINTS - min(
            STYLE_PYCODESTYLE_POINTS, report.total_errors
        )

        if report.total_errors > 0:
            feedback.append(
                f"STYLE: pycodestyle reported {report.total_errors} issue(s)."
            )

    readme_points = (
        STYLE_README_POINTS if has_nonempty_readme(dir_path, feedback) else 0
    )
    gitignore_points = (
        STYLE_GITIGNORE_POINTS if has_gitignore(dir_path, feedback) else 0
    )
    class_count, _ = count_nontrivial_classes(dir_path / "src")
    feedback.append(f"STYLE: non-trivial classes detected: {class_count}.")

    if class_count <= 0:
        class_points = 0
    elif class_count == 1:
        class_points = 5
    elif class_count == 2:
        class_points = 10
    elif class_count == 3:
        class_points = 15
    elif class_count in (4, 5):
        class_points = 20
    elif class_count == 6:
        class_points = 25
    else:
        class_points = STYLE_CLASS_POINTS

    style_points = (
        pycodestyle_points
        + readme_points
        + gitignore_points
        + class_points
    )

    if not include_mark:
        return {}

    return {
        "STYLE": {
            TC_PASSED: style_points,
            TC_FAILED: 0,
            TC_CRASHED: 0,
            TC_TOTAL: STYLE_TOTAL_POINTS,
        }
    }


def ensure_bwrap_available():
    """
    Ensure bubblewrap is installed before attempting to use it.
    """
    if shutil.which("bwrap") is None:
        raise RuntimeError(
            'bubblewrap ("bwrap") was requested for sandboxing, but it was '
            "not found on PATH."
        )


def probe_bwrap(mode: str):
    """
    Check whether the requested bubblewrap mode is usable on this host.
    """
    ensure_bwrap_available()

    system_bind_candidates = [
        "/usr",
        "/usr/local",
        "/bin",
        "/sbin",
        "/lib",
        "/lib64",
        "/etc",
        "/opt",
    ]

    command = [
        "bwrap",
        "--die-with-parent",
        "--new-session",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
    ]

    if mode == SANDBOX_BWRAP:
        command.append("--unshare-net")

    true_path = shutil.which("true")
    if true_path is None:
        raise RuntimeError(
            "Could not find `true` on PATH for bwrap preflight."
        )

    for candidate in system_bind_candidates:
        if Path(candidate).exists():
            command.extend(["--ro-bind", candidate, candidate])

    command.append(true_path)

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode == 0:
        return

    stderr = result.stderr.strip()
    if mode == SANDBOX_BWRAP:
        raise RuntimeError(
            "bubblewrap sandbox preflight failed in network-isolated mode. "
            "This host does not permit the requested namespace setup.\n"
            f"bwrap stderr:\n{stderr}\n"
            "Try `--sandbox bwrap-no-net` for filesystem-only isolation, or "
            "`--sandbox none` to disable sandboxing."
        )

    raise RuntimeError(
        "bubblewrap sandbox preflight failed in filesystem-only mode.\n"
        f"bwrap stderr:\n{stderr}"
    )


def require_supported_runtime(sh: str):
    """
    Warn when the marking script is not using the expected runtime.
    """
    if sys.version_info[:3] != REQUIRED_PYTHON_VERSION:
        expected = ".".join(str(part) for part in REQUIRED_PYTHON_VERSION)
        actual = ".".join(str(part) for part in sys.version_info[:3])
        print(
            f"warning: expected Python {expected}, but the marking script is "
            f"running under Python {actual}. Results may differ from the "
            "official marking environment.",
            file=sys.stderr,
        )

    if pycodestyle is not None:
        actual_pycodestyle = getattr(pycodestyle, "__version__", "unknown")
    else:
        actual_pycodestyle = None

    if (
        actual_pycodestyle is not None
        and actual_pycodestyle != REQUIRED_PYCODESTYLE_VERSION
    ):
        print(
            "warning: expected pycodestyle "
            f"{REQUIRED_PYCODESTYLE_VERSION}, but this environment has "
            f"{actual_pycodestyle}. Style results may differ from the "
            "official marking environment.",
            file=sys.stderr,
        )

    if "STYLE" not in SH_CAT_WEIGHTS[sh]:
        return

    if pycodestyle is None:
        raise RuntimeError(
            "FH2 style marking requires pycodestyle "
            f"{REQUIRED_PYCODESTYLE_VERSION}. Run "
            "`python -m pip install -r requirements.txt` in the Python 3.12.3 "
            "environment."
        )


@contextmanager
def prepare_bwrap_submission(
    impl_path: Path,
):
    """
    Create a temporary working copy of a submission for bubblewrap execution.
    """
    ensure_bwrap_available()

    with tempfile.TemporaryDirectory(prefix="cipherlock-bwrap-") as tmp_dir:
        sandbox_impl_path = Path(tmp_dir) / "submission"
        shutil.copytree(impl_path, sandbox_impl_path)
        yield sandbox_impl_path


def build_bwrap_command(
    impl_path: Path,
    cipherlock: Path,
    args: list[str],
    sandbox_mode: str,
) -> list[str]:
    """
    Build a bubblewrap command line for running a submission in a sandbox.
    """
    system_bind_candidates = [
        "/usr",
        "/usr/local",
        "/bin",
        "/sbin",
        "/lib",
        "/lib64",
        "/etc",
        "/opt",
    ]

    command = [
        "bwrap",
        "--die-with-parent",
        "--new-session",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/work",
        "--bind",
        str(impl_path),
        "/work",
        "--chdir",
        "/work",
    ]

    if sandbox_mode == SANDBOX_BWRAP:
        command.append("--unshare-net")

    for candidate in system_bind_candidates:
        path = Path(candidate)
        if path.exists():
            command.extend(["--ro-bind", candidate, candidate])

    python_executable = "python3.12"
    if sys.prefix != sys.base_prefix:
        command.extend(["--ro-bind", sys.prefix, "/venv"])
        python_executable = "/venv/bin/python"

    command.extend(
        [
            python_executable,
            str(Path("/work") / cipherlock.relative_to(impl_path)),
            *args,
        ]
    )

    return command


def mark_cases(
    sh: str,
    impl_path: Path,
    cipherlock: Path,
    tests_path: Path,
    expected_tests_path: Path,
    vis_dirs: list[str],
    allowed_categories: set[str] | None = None,
    sandbox_mode: str = SANDBOX_NONE,
) -> dict[
    str,
    dict[str, int],
]:
    """
    Mark by test cases.
    Args
        sh (str): The test cases to mark
        impl_path (Path): The path of the implementation
        cipherlock (Path): The path of cipherlock.py itself
        tests_path (Path): The path of the test cases
        expected_tests_path (Path): The path of the expected test outputs for
            this sh.
        vis_dirs (list[str]): The visibility directories to check.
        allowed_categories (set[str] | None): If given, only categories in
            this set are counted for the results of this run.
    """

    # Args used in general for this sh
    default_args = SH_ARGS[sh]

    # Category by test case name prefix.
    if allowed_categories is None:
        categories = list(SH_CAT_WEIGHTS[sh].keys())
    else:
        categories = [
            cat
            for cat in SH_CAT_WEIGHTS[sh].keys()
            if cat in allowed_categories
        ]
    # marks by category
    marks: dict[str, dict[str, int]] = {
        cat: {TC_PASSED: 0, TC_FAILED: 0, TC_CRASHED: 0, TC_TOTAL: 0}
        for cat in list(categories) + ["other"]
    }

    # Mark the tests based on the test dirs to check

    for visibility in vis_dirs:
        ins: Path = tests_path / visibility / "ins"
        outs: Path = expected_tests_path / visibility / "outs"

        if not ins.is_dir():
            raise RuntimeError(f"Input test directory not found: {ins}")
        if not outs.is_dir():
            raise RuntimeError(f"Expected-output directory not found: {outs}")

        # Loop through the test cases
        for in_file in sorted(ins.iterdir()):
            # Skip directories and non-text files
            if not in_file.is_file():
                continue

            test_name = in_file.name
            raw_cat = test_name[:4]

            # For aggregate hand-ins like FH1, skip test files whose category
            # is not actually part of the hand-in being marked.
            if allowed_categories is not None and raw_cat not in categories:
                continue

            # Get the actual file
            out_file = outs / test_name
            if not out_file.is_file():
                raise RuntimeError(
                    f"Expected-output file not found: {out_file}"
                )

            # Get the args
            args = sparg_check(test_name)
            if args is None:
                args = default_args

            # Get the category.
            cat = raw_cat
            if cat not in categories:
                cat = "other"

            # Add to the total
            marks[cat][TC_TOTAL] += 1

            # Try to get their program's output
            try:
                command = (
                    build_bwrap_command(
                        impl_path,
                        cipherlock,
                        args,
                        sandbox_mode,
                    )
                    if sandbox_mode in SANDBOX_MODES
                    else [sys.executable, cipherlock, *args]
                )
                result = subprocess.run(
                    command,
                    stdin=in_file.open("r"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=(
                        None
                        if sandbox_mode in SANDBOX_MODES
                        else impl_path
                    ),
                    timeout=40,
                    env=(
                        {
                            "PATH": "/usr/local/bin:/usr/bin:/bin",
                            "HOME": "/tmp",
                            "TMPDIR": "/tmp",
                        }
                        if sandbox_mode in SANDBOX_MODES
                        else None
                    ),
                )
            except subprocess.TimeoutExpired:
                log_outcome(test_name, TC_CRASHED, "Timed out.")
                marks[cat][TC_CRASHED] += 1
                continue
            except Exception as e:
                log_outcome(test_name, TC_CRASHED, f"Runtime error: {e}")
                marks[cat][TC_CRASHED] += 1
                continue

            # Treat non-zero exit code as crash
            if result.returncode != 0:
                log_outcome(
                    test_name,
                    TC_CRASHED,
                    f"Non-zero exit ({result.returncode}).\n{result.stderr}",
                )
                marks[cat][TC_CRASHED] += 1
                continue

            # Expected output
            expected = out_file.read_text()

            # If the output matches, then its a pass.
            if result.stdout == expected:
                log_outcome(test_name, TC_PASSED, "Correct")
                marks[cat][TC_PASSED] += 1
            # Otherwise its a fail.
            else:
                if log_output:
                    print_side_by_side(test_name, expected, result.stdout)
                marks[cat][TC_FAILED] += 1

    return marks


def sparg_check(filename: str):
    """
    If a special argument keyword appears in the filename, return the
    associated arguments as a list of characters.

    Supported keywords:
        - 'sparg'  : returns 2 characters
        - 'noargs' : returns an empty list
        - 'onearg' : returns 1 character
        - 'thrarg' : returns 3 characters

    If the keyword is present but not enough characters follow, or if no
    keyword is present, return None.
    """
    specs = {
        "sparg": 2,
        "noargs": 0,
        "onearg": 1,
        "thrarg": 3,
    }

    for key, count in specs.items():
        idx = filename.find(key)
        if idx == -1:
            continue

        start = idx + len(key)

        if count == 0:
            return []

        tail = filename[start:start + count]
        if len(tail) < count:
            return None

        return list(tail)

    return None


##############################################################################
# Mark grouping and feedback
##############################################################################


def compute_final_mark(
    sh: str,
    marks: dict[str, dict[str, int]],
    feedback: list[str],
):
    """
    Compute the categorical marks for this hand-in, using the prefixes for this
    sh.
    Args
        sh (str): The soft hand-in being marked.
        marks (dict[str, dict[str, int]]): The results.
        feedback (list[str]): Feedback that has already been given
    """
    # Get the weights
    cat_weights = SH_CAT_WEIGHTS[sh]

    # SH1 (or any case with no category weights):
    # SH1 has no test categories.
    if not cat_weights:
        percentages: dict[str, float] = {FINAL: 100.0}
        return percentages

    weighted_categories_with_no_tests: list[str] = []

    # Initialize fm
    percentages: dict[str, float] = {"final": 0.0}
    total_weight = 0.0

    # Get each categorical percentage
    for cat in cat_weights.keys():
        if marks[cat][TC_TOTAL] == 0:
            weighted_categories_with_no_tests.append(cat)
            continue

        mark: float = (
            float(marks[cat][TC_PASSED]) / float(marks[cat][TC_TOTAL])
        )
        # Save the percentage
        percentages[cat] = mark * 100

        # Add to final mark
        percentages[FINAL] += mark * cat_weights[cat]
        total_weight += cat_weights[cat]

        # Now apply feedback.
        # Check if all passed
        if marks[cat][TC_PASSED] == marks[cat][TC_TOTAL]:
            feedback.append(f"Program passed all {cat} test cases.")
        # Check if some failed
        if marks[cat][TC_FAILED] > 0:
            feedback.append(
                f"Program failed {marks[cat][TC_FAILED]} {cat} test cases."
            )
        # Check if some crashed.
        if marks[cat][TC_CRASHED] > 0:
            feedback.append(
                f"Program crashed {marks[cat][TC_CRASHED]} {cat} test cases."
            )

    if weighted_categories_with_no_tests:
        categories_text = ", ".join(weighted_categories_with_no_tests)
        raise RuntimeError(
            "No test cases were found for weighted categor"
            + ("y" if len(weighted_categories_with_no_tests) == 1 else "ies")
            + f": {categories_text}"
        )

    # Weigh down the percentage and multiply by 100
    percentages[FINAL] *= 100.0 / total_weight

    # Return these results
    return percentages


##############################################################################
# LOGS
##############################################################################


def log_outcome(test_name: str, outcome: str, reason: str):
    """
    Print a one-line test outcome when verbose logging is enabled.
    """
    if log_output:
        print(f"{test_name} - {outcome}: {reason}")


def print_side_by_side(test_name: str, expected: str, received: str):
    """
    Print expected and received output side by side.
    """
    exp_lines = expected.splitlines()
    rec_lines = received.splitlines()

    left_width = max([len("Expected")] + [len(line) for line in exp_lines])
    right_width = max([len("Received")] + [len(line) for line in rec_lines])

    print(f"\nTest case: {test_name}")
    print(f"┌{'─' * left_width}┬{'─' * right_width}┐")
    print(
        f"│{'Expected'.ljust(left_width)}│"
        f"{'Received'.ljust(right_width)}│"
    )
    print(f"├{'─' * left_width}┼{'─' * right_width}┤")

    for line_index in range(max(len(exp_lines), len(rec_lines))):
        expected_line = (
            exp_lines[line_index] if line_index < len(exp_lines) else ""
        )
        received_line = (
            rec_lines[line_index] if line_index < len(rec_lines) else ""
        )
        print(
            f"│{expected_line.ljust(left_width)}│"
            f"{received_line.ljust(right_width)}│"
        )

    print(f"└{'─' * left_width}┴{'─' * right_width}┘")


def print_percentages(percentages: dict[str, float]):
    """
    Prints percentages for the percentages dict
    """
    rows = [(k, v) for k, v in percentages.items() if k != "final"]
    rows.append(("final", percentages["final"]))

    def fmt(v: float) -> str:
        return f"{v:.2f}%"

    w1 = max([len("Category")] + [len(k) for k, _ in rows])
    w2 = max([len("Mark")] + [len(fmt(v)) for _, v in rows])

    print(f"┌{'─' * w1}┬{'─' * w2}┐")
    print(f"│{'Category'.ljust(w1)}│{'Mark'.ljust(w2)}│")
    print(f"├{'─' * w1}┼{'─' * w2}┤")

    for k, v in rows:
        print(f"│{k.ljust(w1)}│{fmt(v).rjust(w2)}│")

    print(f"└{'─' * w1}┴{'─' * w2}┘")


def print_feedback(feedback: list[str]):
    """
    Print feedback messages in a readable bullet list.
    """
    if not feedback:
        print(f"\nFeedback:\n • {DEFAULT_FEEDBACK}")
        return

    print("\nFeedback:")
    for msg in feedback:
        print(f" • {msg}")


def print_results_table(results: dict[str, dict[str, int]]):
    """
    Print raw pass/fail/crash counts as a boxed table.
    """
    headers = ["Category", "Passed", "Failed", "Crashed", "Total"]

    rows = []
    sums = [0, 0, 0, 0]

    for cat, vals in results.items():
        row = [
            cat,
            vals.get(TC_PASSED, 0),
            vals.get(TC_FAILED, 0),
            vals.get(TC_CRASHED, 0),
            vals.get(TC_TOTAL, 0),
        ]
        for i in range(4):
            sums[i] += row[i + 1]
        rows.append(row)

    rows.append(["TOTAL", *sums])

    widths = [
        max(len(str(r[i])) for r in ([headers] + rows)) for i in range(5)
    ]

    def border(left: str, middle: str, right: str) -> str:
        return left + middle.join("─" * width for width in widths) + right

    print(border("┌", "┬", "┐"))
    print("│" + "│".join(headers[i].ljust(widths[i]) for i in range(5)) + "│")
    print(border("├", "┼", "┤"))

    for r in rows:
        print(
            "│" + "│".join(str(r[i]).ljust(widths[i]) for i in range(5)) + "│"
        )

    print(border("└", "┴", "┘"))


##############################################################################
# Run from the command line
##############################################################################


def build_arg_parser() -> argparse.ArgumentParser:
    """
    Build the command-line interface for the marking script.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Mark a Cipherlock submission using the CS144 test-case layout."
        )
    )
    parser.add_argument(
        "handin",
        choices=sorted(DIRS_TO_MARK.keys()),
        help="Hand-in to mark.",
    )
    parser.add_argument(
        "-i",
        "--impl",
        type=Path,
        default=Path.cwd(),
        help=(
            "Path to one implementation directory. Defaults to the current "
            "directory. Ignored when --batch-dir is used."
        ),
    )
    parser.add_argument(
        "-v",
        "--visibility",
        nargs="+",
        choices=["public", "private"],
        default=None,
        help=(
            "Visibility directories to mark. Defaults to public for one "
            "submission and public private for batch marking."
        ),
    )
    parser.add_argument(
        "-b",
        "--batch-dir",
        type=Path,
        help=(
            "Path to a batch directory containing one subdirectory per "
            "student number."
        ),
    )
    parser.add_argument(
        "-s",
        "--students",
        nargs="+",
        help="Student numbers to mark in --batch-dir.",
    )
    parser.add_argument(
        "--students-file",
        type=Path,
        help=(
            "File containing student numbers to mark in --batch-dir. "
            "Whitespace and commas are both accepted as separators."
        ),
    )
    parser.add_argument(
        "--isolate-tests",
        action="store_true",
        help=(
            "For a single-submission run, copy tests/ to a random temporary "
            "directory, compare expected outputs from that copy, and restore "
            "tests/ afterwards. Batch runs do this by default."
        ),
    )
    parser.add_argument(
        "--no-isolate-tests",
        action="store_true",
        help="Disable the default random test-suite isolation in batch mode.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-test terminal logging.",
    )
    parser.add_argument(
        "--sandbox",
        choices=[SANDBOX_NONE, SANDBOX_BWRAP, SANDBOX_BWRAP_NO_NET],
        default=None,
        help=(
            "Execution sandbox to use for running student code. Defaults to "
            '"none" for one submission and "bwrap" for batch marking.'
        ),
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        help=(
            "CSV output path for batch marking. Defaults to "
            "results/<handin>_results.csv."
        ),
    )
    return parser


def require_cli_runtime(
    parser: argparse.ArgumentParser,
    sh: str,
    sandbox: str,
):
    """
    Validate the Python/pycodestyle runtime and requested sandbox mode.
    """
    try:
        require_supported_runtime(sh)
        if sandbox in SANDBOX_MODES:
            probe_bwrap(sandbox)
    except RuntimeError as error:
        parser.exit(1, f"error: {error}\n")


def run_single_submission(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> int:
    """
    Mark one submission and print the result tables to standard output.
    """
    if args.students or args.students_file:
        parser.error("--students and --students-file require --batch-dir.")
    if args.no_isolate_tests:
        parser.error("--no-isolate-tests only applies with --batch-dir.")

    visibility = args.visibility if args.visibility is not None else ["public"]
    sandbox_mode = args.sandbox if args.sandbox is not None else SANDBOX_NONE
    require_cli_runtime(parser, args.handin, sandbox_mode)

    tests_path = Path.cwd() / "tests"
    tmp_tests_dir: tempfile.TemporaryDirectory[str] | None = None
    isolated_tests_path: Path | None = None

    if args.isolate_tests:
        tmp_tests_dir, isolated_tests_path = create_isolated_tests(tests_path)

    try:
        try:
            results, percentages, feedback = mark_project(
                args.handin,
                args.impl,
                visibility,
                isolated_tests_path,
                sandbox_mode,
            )
        finally:
            if isolated_tests_path is not None:
                restore_test_suite(isolated_tests_path, tests_path)
    except RuntimeError as error:
        parser.exit(1, f"error: {error}\n")
    finally:
        if tmp_tests_dir is not None:
            tmp_tests_dir.cleanup()

    print_results_table(results)
    print_percentages(percentages)
    print_feedback(feedback)
    return 0


def read_student_numbers(args: argparse.Namespace) -> list[str]:
    """
    Collect student numbers from ``--students`` and ``--students-file``.
    """
    snums: list[str] = []
    if args.students:
        snums.extend(args.students)

    if args.students_file is not None:
        student_text = args.students_file.read_text(encoding="utf-8")
        student_text = student_text.replace(",", " ")
        snums.extend(student_text.split())

    return snums


def run_batch(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> int:
    """
    Mark a batch directory and write/update the corresponding results CSV.
    """
    snums = read_student_numbers(args)
    if not snums:
        parser.error("batch marking requires --students or --students-file.")

    visibility = (
        args.visibility
        if args.visibility is not None
        else ["public", "private"]
    )
    sandbox_mode = (
        args.sandbox if args.sandbox is not None else SANDBOX_BWRAP
    )
    require_cli_runtime(parser, args.handin, sandbox_mode)

    try:
        mark_all(
            args.handin,
            visibility,
            snums,
            args.batch_dir,
            not args.no_isolate_tests,
            sandbox_mode,
            args.output_csv,
        )
    except RuntimeError as error:
        parser.exit(1, f"error: {error}\n")

    output_csv = (
        args.output_csv or Path("results") / f"{args.handin}_results.csv"
    )
    print(
        f"Marked {len(set(snums))} submission(s). "
        f"Results written to {output_csv}"
    )
    return 0


def main():
    """
    Parse CLI arguments and dispatch to single-submission or batch marking.
    """
    parser = build_arg_parser()

    args = parser.parse_args()

    global log_output
    if args.quiet:
        log_output = False

    if args.batch_dir is None:
        return run_single_submission(parser, args)

    return run_batch(parser, args)


if __name__ == "__main__":
    sys.exit(main())
