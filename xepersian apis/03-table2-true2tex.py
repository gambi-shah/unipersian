#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Generate a XeLaTeX report from checked JSON data.

Supported JSON structure:

{
  "results": [
    {
      "status": "defined",
      "name": "...",
      "kind": "command",
      "description": "...",
      "definitions": [
        {
          "package": "...",
          "definition_type": "...",
          "file": "...",
          "line": 123,
          "priority": 10
        }
      ]
    }
  ]
}

The script also supports the legacy form in which the definition
fields are located directly in each result object.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


DEFAULT_INPUT_NAMES = (
    "02-table2-checked.json",
    "test-02.json",
    "02-table2.json",
)

DEFAULT_OUTPUT_NAME = "03-table2-true.tex"

PACKAGE_ALIASES = {
    "persian": "unipersian",
}

DEFAULT_PACKAGE_ORDER = [
    "unipersian",
    "babel",
    "bidi",
    "luabidi",
    "fontspec",
]


LATEX_SPECIAL_CHARACTERS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape_latex(value: Any) -> str:
    """
    Escape ordinary text for LaTeX.

    This function must only be used for ordinary text, not for
    strings that already contain LaTeX commands.
    """
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ")

    return "".join(
        LATEX_SPECIAL_CHARACTERS.get(character, character)
        for character in text
    )


def lr(value: str) -> str:
    """Put an already formatted value in a left-to-right region."""
    return r"\lr{" + value + "}"


def first_value(
    mapping: Any,
    keys: tuple[str, ...],
    default: Any = None,
) -> Any:
    """Return the first non-empty value among several dictionary keys."""
    if not isinstance(mapping, dict):
        return default

    for key in keys:
        value = mapping.get(key)

        if value is not None and value != "":
            return value

    return default


def normalize_package(value: Any) -> str:
    """Normalize package names, especially old 'persian' labels."""
    if value is None:
        return "unknown"

    package = str(value).strip()

    if not package:
        return "unknown"

    return PACKAGE_ALIASES.get(package, package)


def normalize_kind(value: Any) -> str:
    """Normalize command/environment labels."""
    if value is None:
        return "command"

    kind = str(value).strip().lower()

    if kind in {"environment", "env", "محیط"}:
        return "environment"

    if kind in {"command", "macro", "cmd", "دستور"}:
        return "command"

    return kind


def normalize_line(value: Any) -> int | str | None:
    """Normalize line numbers while allowing missing line information."""
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        return str(value)

    if isinstance(value, int):
        return value

    if isinstance(value, float) and value.is_integer():
        return int(value)

    text = str(value).strip()

    if text.isdigit():
        return int(text)

    return text


def normalize_priority(value: Any) -> float | None:
    """Convert priorities to numbers where possible."""
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    try:
        return float(str(value).strip())
    except ValueError:
        return None


def format_name(name: Any, kind: str) -> str:
    """Format a command or environment name."""
    text = str(name).strip()

    # Avoid showing two leading backslashes for commands.
    text = text.lstrip("\\")

    escaped_name = escape_latex(text)

    if kind == "environment":
        return lr(escaped_name)

    return lr(r"\textbackslash{}" + escaped_name)


def format_description(value: Any) -> str:
    """Format a description as ordinary escaped text."""
    if value is None:
        return "---"

    if isinstance(value, list):
        text = "؛ ".join(str(item) for item in value)
    elif isinstance(value, dict):
        text = "؛ ".join(
            f"{key}: {item}" for key, item in value.items()
        )
    else:
        text = str(value)

    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())

    if not text:
        return "---"

    return escape_latex(text)


def format_definition_type(value: Any) -> str:
    """Format the type of a definition."""
    if value is None or str(value).strip() == "":
        return lr("unknown")

    text = str(value).strip().lstrip("\\")
    return lr(escape_latex(text))


def format_line(value: Any) -> str:
    """Format a line number."""
    if value is None or value == "":
        return "---"

    return lr(escape_latex(value))


def format_source_file(value: Any) -> str:
    """
    Format source paths using xurl.

    Paths without braces are passed to \\nolinkurl so xurl can
    create line-breaking points. Paths containing braces are
    rendered as escaped ordinary text.
    """
    if value is None or str(value).strip() == "":
        return "---"

    text = str(value).replace("\r", " ").replace("\n", " ").strip()

    if not text:
        return "---"

    if "{" in text or "}" in text:
        return lr(escape_latex(text))

    # xurl/nolinkurl handles underscores, %, #, &, spaces, etc.
    return lr(r"\nolinkurl{" + text + "}")


def load_json(input_path: Path) -> tuple[dict[str, Any], list[Any]]:
    """Load the JSON document and locate its result list."""
    try:
        text = input_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(
            f"Cannot read input file:\n{input_path}\n{error}"
        ) from error

    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON file:\n{input_path}\n{error}"
        ) from error

    if not isinstance(data, dict):
        raise ValueError(
            "The top-level JSON value must be an object."
        )

    results = data.get("results")

    if results is None:
        results = data.get("entries")

    if not isinstance(results, list):
        raise ValueError(
            "The JSON file must contain a 'results' or 'entries' list."
        )

    return data, results


def candidate_from(
    raw: dict[str, Any],
    entry: dict[str, Any],
    package_hint: Any = None,
) -> dict[str, Any]:
    """
    Build a normalized definition candidate.

    Several possible field names are accepted so the script works
    with both the current and legacy JSON formats.
    """
    package = first_value(
        raw,
        ("package", "package_name", "source_package"),
    )

    if package is None:
        package = first_value(
            entry,
            ("package", "package_name", "source_package"),
        )

    if package is None:
        package = package_hint

    definition_type = first_value(
        raw,
        (
            "definition_type",
            "definition",
            "type",
            "definition_kind",
        ),
    )

    if definition_type is None:
        definition_type = first_value(
            entry,
            (
                "definition_type",
                "definition",
                "definition_kind",
            ),
            default="unknown",
        )

    source_file = first_value(
        raw,
        (
            "file",
            "source_file",
            "path",
            "file_path",
        ),
    )

    if source_file is None:
        source_file = first_value(
            entry,
            (
                "file",
                "source_file",
                "path",
                "file_path",
            ),
            default="",
        )

    line = first_value(
        raw,
        ("line", "lineno", "line_number"),
    )

    if line is None:
        line = first_value(
            entry,
            ("line", "lineno", "line_number"),
        )

    priority = first_value(
        raw,
        ("priority", "score", "rank"),
    )

    if priority is None:
        priority = first_value(
            entry,
            ("priority", "score", "rank"),
        )

    return {
        "package": normalize_package(package),
        "definition_type": definition_type,
        "file": source_file,
        "line": normalize_line(line),
        "priority": normalize_priority(priority),
    }


def definition_list(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Return definitions as a normalized list."""
    definitions = entry.get("definitions")

    if isinstance(definitions, list):
        return [
            item for item in definitions
            if isinstance(item, dict)
        ]

    if isinstance(definitions, dict):
        return [definitions]

    return []


def candidate_is_better(
    candidate: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    """
    Decide whether candidate should replace current.

    A missing priority never replaces an existing candidate.
    Equal priorities preserve the first candidate.
    """
    candidate_priority = candidate["priority"]
    current_priority = current["priority"]

    if candidate_priority is None:
        return False

    if current_priority is None:
        return True

    return candidate_priority > current_priority


def entry_name(entry: dict[str, Any]) -> str:
    """Find the name of a command/environment."""
    value = first_value(
        entry,
        (
            "normalized_name",
            "name",
            "command",
            "environment",
        ),
    )

    if value is None:
        return ""

    return str(value).strip()


def entry_description(entry: dict[str, Any]) -> Any:
    """Find the description field."""
    return first_value(
        entry,
        ("description", "doc", "documentation", "comment"),
        default=None,
    )


def legacy_candidates(
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Build candidates for old JSON records without definitions[].

    If defining_packages exists, one legacy row is created for each
    package. Otherwise the package field of the entry is used.
    """
    selected = entry.get("selected_definition")

    if isinstance(selected, dict):
        merged = dict(entry)
        merged.update(selected)
    else:
        merged = dict(entry)

    packages = entry.get("defining_packages")

    if isinstance(packages, list) and packages:
        return [
            candidate_from(merged, entry, package_hint=package)
            for package in packages
        ]

    return [candidate_from(merged, entry)]


def collect_rows(
    results: list[Any],
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """
    Select defined entries and group them by package.

    For each entry and package, only the highest-priority definition
    is emitted.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    defined_count = 0

    for entry_number, raw_entry in enumerate(results, start=1):
        if not isinstance(raw_entry, dict):
            print(
                f"Warning: result {entry_number} is not an object; "
                "skipped.",
                file=sys.stderr,
            )
            continue

        status = str(raw_entry.get("status", "")).strip().lower()

        if status != "defined":
            continue

        defined_count += 1

        name = entry_name(raw_entry)

        if not name:
            print(
                f"Warning: defined result {entry_number} has no name; "
                "skipped.",
                file=sys.stderr,
            )
            continue

        kind = normalize_kind(raw_entry.get("kind"))
        description = entry_description(raw_entry)

        definitions = definition_list(raw_entry)

        if definitions:
            candidates = [
                candidate_from(item, raw_entry)
                for item in definitions
            ]
        else:
            candidates = legacy_candidates(raw_entry)

        selected_by_package: dict[str, dict[str, Any]] = {}

        for candidate in candidates:
            package = candidate["package"]
            previous = selected_by_package.get(package)

            if previous is None:
                selected_by_package[package] = candidate
            elif candidate_is_better(candidate, previous):
                selected_by_package[package] = candidate

        for package, candidate in selected_by_package.items():
            row = {
                "name": name,
                "kind": kind,
                "description": description,
                "definition_type": candidate["definition_type"],
                "file": candidate["file"],
                "line": candidate["line"],
            }

            grouped.setdefault(package, []).append(row)

    return grouped, defined_count


def package_order(
    data: dict[str, Any],
    grouped: dict[str, list[dict[str, Any]]],
) -> list[str]:
    """Create a stable package order."""
    packages = list(DEFAULT_PACKAGE_ORDER)

    def add(value: Any) -> None:
        if value is None:
            return

        package = normalize_package(value)

        if package not in packages:
            packages.append(package)

    for package in grouped:
        add(package)

    for key in ("scan", "overview"):
        section = data.get(key)

        if not isinstance(section, dict):
            continue

        roots = section.get("roots")

        if not isinstance(roots, list):
            continue

        for root in roots:
            if isinstance(root, dict):
                add(root.get("package"))

    return packages


def heading_row() -> str:
    """Return the table heading row."""
    headings = [
        "ردیف",
        "نام دستور / محیط",
        "توضیحات",
        "نوع",
        "نوع تعریف",
        "فایل منبع",
        "خط",
    ]

    formatted = [
        r"\textbf{" + heading + "}"
        for heading in headings
    ]

    return " & ".join(formatted) + r" \\"


def kind_text(kind: str) -> str:
    """Translate kind labels."""
    if kind == "command":
        return "دستور"

    if kind == "environment":
        return "محیط"

    return escape_latex(kind)


def make_document(
    data: dict[str, Any],
    grouped: dict[str, list[dict[str, Any]]],
) -> str:
    """Generate the complete standalone XeLaTeX document."""
    packages = package_order(data, grouped)

    lines: list[str] = [
        r"% !TeX program = lualatex",
        r"\documentclass[a4paper,landscape]{article}",
        r"\usepackage{array}",
        r"\usepackage{ragged2e}",
        r"\usepackage{xltabular}",
        r"\usepackage{booktabs}",
        r"\usepackage{hyperref}",
        r"\usepackage{unipersian}",
        "",
        r"% Valid flexible column for xltabular.",
        r"% Do not use X[1.2] or X[1.6] here.",
        r"\newcolumntype{Y}{>{\hspace{0pt}\RaggedRight\arraybackslash}X}",
        r"\renewcommand{\arraystretch}{1.2}",
        r"\setlength{\tabcolsep}{4pt}",
        r"\urlstyle{same}",
        "",
        r"\begin{document}",
        r"\title{دستورها و محیط‌های تعریف‌شده}",
        r"\author{}",
        r"\date{}",
        r"\maketitle",
        "",
    ]

    for package in packages:
        rows = grouped.get(package, [])

        package_text = lr(escape_latex(package))

        lines.append(
            r"\section*{بسته: " + package_text + "}"
        )

        lines.append(
            r"\textbf{تعداد: " + lr(str(len(rows))) + "}"
        )

        lines.append("")

        if not rows:
            lines.append(
                "برای این بسته دستور یا محیط تعریف‌شده‌ای یافت نشد."
            )
            lines.append("")
            continue

        # The second argument contains only valid xltabular columns:
        # c, c, c, l, l, c, c
        lines.extend(
            [
                r"\begin{xltabular}{\textwidth}{c c c l l c c}",
                r"\hline",
                heading_row(),
                r"\hline",
                r"\endfirsthead",
                r"\hline",
                heading_row(),
                r"\hline",
                r"\endhead",
                r"\hline",
                r"\endfoot",
                r"\hline",
                r"\endlastfoot",
            ]
        )

        for number, row in enumerate(rows, start=1):
            cells = [
                lr(str(number)),
                format_name(row["name"], row["kind"]),
                format_description(row["description"]),
                kind_text(row["kind"]),
                format_definition_type(row["definition_type"]),
                format_source_file(row["file"]),
                format_line(row["line"]),
            ]

            lines.append(" & ".join(cells) + r" \\")

        lines.extend(
            [
                r"\end{xltabular}",
                "",
            ]
        )

    lines.extend(
        [
            r"\end{document}",
            "",
        ]
    )

    return "\n".join(lines)


def find_default_input(script_directory: Path) -> Path:
    """Find the first existing default input file."""
    for filename in DEFAULT_INPUT_NAMES:
        candidate = script_directory / filename

        if candidate.exists():
            return candidate

    # Return the main expected name so the error message is meaningful.
    return script_directory / DEFAULT_INPUT_NAMES[0]


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    script_directory = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(
        description=(
            "Generate a XeLaTeX xltabular report from checked JSON."
        )
    )

    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=find_default_input(script_directory),
        help="Input JSON file.",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output TEX file.",
    )

    return parser.parse_args()


def main() -> int:
    """Program entry point."""
    arguments = parse_arguments()

    input_path = arguments.input.expanduser().resolve()

    if arguments.output is None:
        output_path = input_path.parent / DEFAULT_OUTPUT_NAME
    else:
        output_path = arguments.output.expanduser().resolve()

    if input_path == output_path:
        print(
            "Error: input and output files must be different.",
            file=sys.stderr,
        )
        return 1

    try:
        data, results = load_json(input_path)
        grouped, defined_count = collect_rows(results)
        document = make_document(data, grouped)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(document, encoding="utf-8")

    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    row_count = sum(len(rows) for rows in grouped.values())

    print(f"Input:         {input_path}")
    print(f"Defined items: {defined_count}")
    print(f"Generated rows:{row_count}")
    print(f"Output:        {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
