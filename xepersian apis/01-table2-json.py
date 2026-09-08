#!/usr/bin/env python3

import json
import re
from pathlib import Path


INVISIBLE_RE = re.compile(
    r"[\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]"
)

SEPARATOR_RE = re.compile(r"^[-=_*]{3,}[\s|:=-_]*$")

COLUMN_RE = re.compile(r"^(\\\S+)(?:\t+| {2,})(.+)$")


def clean_text(text: str) -> str:
    """Remove invisible Unicode characters and surrounding whitespace."""
    return INVISIBLE_RE.sub("", text).strip()


def remove_leading_backslash(text: str) -> str:
    """Remove one or more backslashes from the beginning of a value."""
    return text.lstrip("\\")


def is_header(line: str) -> bool:
    """Return True for table title and header lines."""
    text = clean_text(line).casefold()

    return (
        text.startswith("table ")
        or text.startswith("command in xepersian")
        or text.startswith("equivalent persian command")
        or text.startswith("command")
        or text.startswith("description")
        or text.startswith("دستور")
        or text.startswith("معادل")
    )


def is_separator(line: str) -> bool:
    """Return True for separator lines."""
    return bool(SEPARATOR_RE.fullmatch(clean_text(line)))


def parse_table(text: str) -> list[dict[str, str]]:
    """Parse 00-table2.txt into a list of dictionaries."""
    entries = []
    seen_commands = set()

    for raw_line in text.splitlines():
        line = clean_text(raw_line)

        if not line:
            continue

        if is_header(line):
            continue

        if is_separator(line):
            continue

        match = COLUMN_RE.match(line)

        if not match:
            raise ValueError(
                "malformed line (expected command and equivalent): %r"
                % line
            )

        command = match.group(1).strip()
        description = clean_text(match.group(2))

        command = remove_leading_backslash(command)
        description = remove_leading_backslash(description)

        if not command:
            raise ValueError("empty command")

        if not description:
            raise ValueError(
                "empty description for command: %s"
                % command
            )

        if command in seen_commands:
            raise ValueError(
                "duplicate command: %s"
                % command
            )

        entries.append(
            {
                "command": command,
                "description": description,
            }
        )

        seen_commands.add(command)

    return entries


def validate_entries(entries: list[dict[str, str]]) -> None:
    """Validate commands and descriptions."""
    seen_commands = set()

    for index, entry in enumerate(entries, start=1):
        if "command" not in entry:
            raise ValueError(
                "entry %d is missing the command key"
                % index
            )

        if "description" not in entry:
            raise ValueError(
                "entry %d is missing the description key"
                % index
            )

        command = entry["command"]
        description = entry["description"]

        if not command:
            raise ValueError(
                "entry %d has an empty command"
                % index
            )

        if not description:
            raise ValueError(
                "entry %d has an empty description"
                % index
            )

        if command.startswith("\\"):
            raise ValueError(
                "entry %d still contains a leading backslash"
                % index
            )

        if description.startswith("\\"):
            raise ValueError(
                "entry %d description still contains a leading backslash"
                % index
            )

        if command in seen_commands:
            raise ValueError(
                "duplicate command: %s"
                % command
            )

        seen_commands.add(command)


def main() -> None:
    base_path = Path(__file__).resolve().parent

    input_path = base_path / "00-table2.txt"
    output_path = base_path / "01-table2.json"

    print("input path: ", input_path)
    print("output path:", output_path)

    if not input_path.exists():
        raise FileNotFoundError(
            "input file does not exist: %s"
            % input_path
        )

    text = input_path.read_text(encoding="utf-8")

    entries = parse_table(text)

    validate_entries(entries)

    payload = {
        "entry_count": len(entries),
        "entries": entries,
    }

    output_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print("entry_count:", len(entries))
    print("validation: OK")
    print("wrote:", output_path)


if __name__ == "__main__":
    main()
