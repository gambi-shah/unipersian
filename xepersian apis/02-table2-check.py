#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Static LaTeX definition analyzer (table2 checker).

Reads a JSON table of LaTeX commands/environments, scans TeX distribution /
project trees for their definitions, selects the best definition per entity
(max role priority, stable first tie-break), and writes a checked JSON plus a
terminal report.

Scanned roots: unipersian, babel, bidi, luabidi and fontspec.  Every root is
scanned for every entity (no short-circuit): bidi and luabidi often provide
duplicate definitions of the same target, and the unipersian (babel-based)
package may re-define what bidi/luabidi define, so ALL definition candidates
from ALL roots are kept in ``definitions``; only one representative is
copied into the legacy top-level fields via the priority/stable rule.

Pure static analysis: no expansion, catcodes or runtime precedence modelled.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Match, Optional, Tuple

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

NAME: str = "A-Za-z@:_"
NAME_RE: str = "[" + NAME + "]+"
NAME_CLASS: str = "[" + NAME + "]"

PRIORITY: Dict[str, int] = {
    "body_definition": 100,
    "declaration": 80,
    "assignment": 50,
    "neutralizing_assignment": 10,
    "unknown": 0,
}

VALID_KINDS: Tuple[str, ...] = ("command", "environment")

# Named group n must appear at most ONCE per compiled pattern.  Where a
# pattern needs the control-sequence name twice (e.g. braced/unbraced
# alternatives) we use the bracketed form \\{?CS\\}? which covers both, and
# where the name is NOT preceded by a backslash (\\csname form) we use a
# distinct group name (cn).
CS: str = r"\\(?P<n>" + NAME_RE + r")"

PATTERN_SPECS: List[Tuple[str, str]] = [
    # TeX primitives \def \gdef \edef \xdef (incl. literal \csname)
    (r"\\(?P<t>gdef|edef|xdef|def)\s*" + CS, "primitive_def"),
    # NOTE: group renamed to cn (CS already owns group n in this module)
    (r"\\(?P<t>gdef|edef|xdef|def)\s*\\csname\s*(?P<cn>[^\\{}\s%]+)\s*\\endcsname",
     "primitive_def_csname"),
    # \let with optional '=' ; \futurelet
    (r"\\(?P<t>let)\s*" + CS + r"\s*(?P<eq>=)?\s*"
     r"(?:\\(?P<rhs>" + NAME_RE + r")|\\csname\s*(?P<rhs2>[^\\{}\s%]+)\s*\\endcsname)?",
     "let"),
    (r"\\(?P<t>futurelet)\s*" + CS, "futurelet"),
    # register defs and allocations
    (r"\\(?P<t>chardef|mathchardef|countdef|dimendef|skipdef|muskipdef|toksdef)"
     r"\s*" + CS, "register_def"),
    # braced or unbraced allocation: \{?CS\}? avoids duplicating group n
    (r"\\(?P<t>newcount|newdimen|newskip|newmuskip|newtoks|newbox|newread|"
     r"newwrite|newinsert|newlength)\s*\{?" + CS + r"\}?",
     "allocation"),
    (r"\\(?P<t>newif)\s*\\(?P<n>if" + NAME_RE + r")", "newif"),
    # LaTeX macro-definition commands
    (r"\\(?P<t>newcommand|renewcommand|providecommand)\*?\s*\{?" + CS + r"\}?",
     "latex_def"),
    (r"\\(?P<t>DeclareRobustCommand|DeclareMathOperator|DeclareTextSymbol|"
     r"DeclareTextAccent|DeclareTextCommand|DeclarePairedDelimiter|"
     r"DeclarePairedDelimiterX|DeclarePairedDelimiterXPP)\*?\s*\{?" + CS + r"\}?",
     "latex_declare"),
    (r"\\DeclareTextCompositeCommand\s*\{?" + CS + r"\}?", "latex_declare"),
    (r"\\(?P<t>NewDocumentCommand|RenewDocumentCommand|ProvideDocumentCommand|"
     r"DeclareDocumentCommand)\s*\{?" + CS + r"\}?", "xparse_command"),
    (r"\\DeclareCommandCopy\s*\{?" + CS + r"\}?\s*(?:\\(?P<rhs>" + NAME_RE + r"))?",
     "command_copy"),
    # expl3
    (r"\\cs_(?P<op>new|set|gset)(?P<prot>_protected)?_(?P<sig>Npn|Npx|Npe|Nn|Nx|NV|Nv)"
     r"\s*" + CS, "expl3_def"),
    (r"\\cs_(?P<op>new|set|gset)_eq:NN\s*" + CS +
     r"\s*(?:\\(?P<rhs>" + NAME_RE + r"))?", "expl3_eq"),
    # environments
    (r"\\(?P<t>newenvironment|renewenvironment|provideenvironment)\*?\s*"
     r"\{\s*(?P<n>[^{}%\n]+?)\s*\}", "environment_def"),
    (r"\\(?P<t>NewDocumentEnvironment|RenewDocumentEnvironment|"
     r"ProvideDocumentEnvironment|DeclareDocumentEnvironment)\s*"
     r"\{\s*(?P<n>[^{}%\n]+?)\s*\}", "xparse_environment"),
    (r"\\(?P<t>newtheorem|renewtheorem|declaretheorem)\*?\s*"
     r"\{\s*(?P<n>[^{}%\n]+?)\s*\}", "theorem_env"),
]

PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(rx), ptype) for rx, ptype in PATTERN_SPECS
]

ENV_PATTERN_TYPES: Tuple[str, ...] = (
    "environment_def", "xparse_environment", "theorem_env",
)

SELECTION_NOTE: str = (
    "prefer highest role priority (body_definition 100 > declaration 80 > "
    "assignment 50 > neutralizing_assignment 10); among equal-priority "
    "candidates choose the first in stable scan order (package order, file, line)."
    "Every root (unipersian, babel, bidi, luabidi, fontspec) is scanned "
    "without short-circuit; all candidates from all packages are kept "
    "in `definitions`"
)

STATIC_LIMITATION: str = (
    "Pure static analysis: TeX expansion, catcodes, package load order and "
    "runtime redefinitions are NOT modelled; results may differ from actual "
    "runtime behaviour."
)

# --------------------------------------------------------------------------
# Text normalisation
# --------------------------------------------------------------------------

def strip_comments(text: str) -> str:
    """Remove unescaped TeX comments, preserving every line and newline."""
    out: List[str] = []
    for line in text.split("\n"):
        cut: Optional[int] = None
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\\":
                i += 2  # skip escaped char (handles \% and \\)
                continue
            if ch == "%":
                cut = i
                break
            i += 1
        out.append(line if cut is None else line[:cut])
    return "\n".join(out)

def mask_first_braced_arg(text: str, container: str) -> Tuple[str, bool]:
    r"""Mask the FIRST braced argument of \container{...} with spaces
    (newlines preserved so line numbers stay valid)."""
    pat = re.compile(r"\\" + re.escape(container) + r"\s*\{")
    m = pat.search(text)
    if m is None:
        return text, False
    start = m.end()
    depth, i = 1, start
    while i < len(text) and depth > 0:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    end = i - 1
    if depth != 0 or end < start:
        return text, False
    inner = "".join(c if c == "\n" else " " for c in text[start:end])
    return text[:start] + inner + text[end:], True

def mask_all_containers(
    text: str, containers: Iterable[str]
) -> Tuple[str, Dict[str, int]]:
    """Repeatedly mask the first braced arg of each container until done."""
    containers = [c for c in containers if c]
    counts: Dict[str, int] = {c: 0 for c in containers}
    progress = True
    while progress:
        progress = False
        for c in containers:
            new_text, masked = mask_first_braced_arg(text, c)
            if masked:
                text = new_text
                counts[c] += 1
                progress = True
    return text, counts

# --------------------------------------------------------------------------
# Definition extraction
# --------------------------------------------------------------------------

def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1

def _clean_name(raw: Optional[str]) -> str:
    if not raw:
        return ""
    return raw.strip().lstrip("\\").strip("{} \t").strip()

def normalize_entity_name(raw: Any) -> str:
    """Single validated normalization used for every entity-name path."""
    if not isinstance(raw, str):
        return ""
    return raw.strip().lstrip("\\").strip("{} \t").strip()


def entity_name_and_kind(entry: Dict[str, Any]) -> Tuple[str, str]:
    """Read the entity name honoring the actual schema: the ``command`` key
    is primary; ``name`` is accepted as an alias. Kind is honored as
    ``environment`` only when explicitly so, else command."""
    raw = entry.get("command")
    if not isinstance(raw, str) or not raw.strip():
        raw = entry.get("name")
    normalized = normalize_entity_name(raw)
    raw_kind = entry.get("kind", "command")
    kind = raw_kind if raw_kind in VALID_KINDS else "command"
    return normalized, kind


def _classify(ptype: str, gd: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    """Return (role, definition_type, extras) for a pattern match."""
    role, dtype, extra = "body_definition", ptype, {}
    rhs = gd.get("rhs") or gd.get("rhs2") or ""
    rhs_name = _clean_name(rhs)
    # primitive_def_csname stores the name in group cn (CS owns group n)
    name = gd.get("n") or gd.get("cn") or ""
    if ptype == "primitive_def":
        dtype = "\\" + gd["t"]
    elif ptype == "primitive_def_csname":
        dtype = "\\" + gd["t"] + " (csname)"
    elif ptype == "let":
        role = ("neutralizing_assignment" if rhs_name in ("relax", "@empty")
                else "assignment")
        dtype = "\\let" + ("=" if gd.get("eq") else "")
        extra["rhs"] = rhs_name or "(unknown)"
    elif ptype == "futurelet":
        role, dtype = "unknown", "\\futurelet"
        extra["rhs"] = "(unknown: futurelet)"
    elif ptype in ("register_def", "allocation"):
        role, dtype = "declaration", "\\" + gd["t"]
    elif ptype == "newif":
        role, dtype = "declaration", "\\newif"
        base = _clean_name(name)
        if base.startswith("if") and len(base) > 2:
            stem = base[2:]
            extra["generated_names"] = [stem + "true", stem + "false"]
    elif ptype in ("latex_def", "latex_declare", "xparse_command",
                   "environment_def", "xparse_environment", "theorem_env"):
        dtype = "\\" + gd["t"]
    elif ptype == "command_copy":
        role, dtype = "assignment", "\\DeclareCommandCopy"
        extra["rhs"] = rhs_name or "(unknown)"
    elif ptype == "expl3_def":
        dtype = "\\cs_" + gd["op"] + (gd.get("prot") or "") + "_" + gd["sig"]
    elif ptype == "expl3_eq":
        role = ("neutralizing_assignment" if rhs_name in ("relax", "@empty")
                else "assignment")
        dtype = "\\cs_" + gd["op"] + "_eq:NN"
        extra["rhs"] = rhs_name or "(unknown)"
    return role, dtype, extra


def _strip_dt_slashes(dtype: Optional[str]) -> Optional[str]:
    """Output-boundary normalization: strip leading backslashes from a
    serialized definition_type while preserving None and internal text."""
    if not isinstance(dtype, str):
        return dtype
    return dtype.lstrip("\\")

def scan_text_spans(
    text: str, normalized: str, kind: str,
) -> Tuple[List[Dict[str, Any]], List[Tuple[int, int]]]:
    """Definitions of exactly ``normalized`` in text plus their spans."""
    defs: List[Dict[str, Any]] = []
    spans: List[Tuple[int, int]] = []
    want_env = kind == "environment"
    for pat, ptype in PATTERNS:
        for m in pat.finditer(text):
            gd = m.groupdict()
            name = _clean_name(gd.get("n") or gd.get("cn"))
            if not name:
                continue
            is_env = ptype in ENV_PATTERN_TYPES
            matched = False
            if name == normalized and is_env == want_env:
                matched = True
            elif (ptype == "newif" and not want_env
                  and name.startswith("if") and len(name) > 2):
                stem = name[2:]
                if normalized in (stem + "true", stem + "false"):
                    matched = True
            if not matched:
                continue
            role, dtype, extra = _classify(ptype, gd)
            defs.append({
                "type": ptype,
                "line": _line_of(text, m.start()),
                "entity_kind": "environment" if is_env else "command",
                "name": normalized,
                "role": role,
                "priority": PRIORITY[role],
                "definition_type": _strip_dt_slashes(dtype),
                "rhs": extra.get("rhs"),
                "generated_names": extra.get("generated_names"),
            })
            spans.append((m.start(), m.end()))
    defs.sort(key=lambda d: d["line"])
    return defs, spans

def find_mentions(
    text: str, name: str, kind: str,
    def_pos: Iterable[Tuple[int, int]],
) -> List[Dict[str, Any]]:
    """Non-definition occurrences of ``name``; def_pos = (start, end) spans."""
    if kind == "environment":
        pat = re.compile(
            r"\\(?:begin|end)\s*\{\s*" + re.escape(name) + r"\s*\}")
    else:
        pat = re.compile(r"\\" + re.escape(name) + "(?!" + NAME_CLASS + ")")
    spans = sorted(def_pos)
    mentions: List[Dict[str, Any]] = []
    for m in pat.finditer(text):
        if any(s <= m.start() < e for s, e in spans):
            continue
        mentions.append({
            "line": _line_of(text, m.start()),
            "snippet": text[max(0, m.start() - 20):m.end() + 30]
                       .replace("\n", " ").strip(),
        })
    return mentions

# --------------------------------------------------------------------------
# File traversal
# --------------------------------------------------------------------------

def iter_package_files(
    root: Path, extensions: List[str], follow_dir_symlinks: bool,
    warnings: List[str],
) -> Iterator[Tuple[str, Path]]:
    """Yield (relative_path, absolute_path) for matching files under root."""
    try:
        if root.is_file():
            if root.suffix.lower() in extensions:
                yield root.name, root
            return
        if not root.is_dir():
            warnings.append(f"root does not exist or is not a directory: {root}")
            return
        stack: List[Tuple[Path, str]] = [(root, "")]
        while stack:
            cur, rel = stack.pop(0)
            try:
                entries = sorted(cur.iterdir(), key=lambda p: p.name)
            except OSError as exc:
                warnings.append(f"cannot read {cur}: {exc}")
                continue
            dirs: List[str] = []
            files: List[str] = []
            for e in entries:
                try:
                    if e.is_dir():
                        if e.is_symlink() and not follow_dir_symlinks:
                            continue
                        dirs.append(e.name)
                    else:
                        files.append(e.name)
                except OSError as exc:
                    warnings.append(f"cannot stat {e}: {exc}")
            for fn in files:
                if Path(fn).suffix.lower() in extensions:
                    ap = cur / fn
                    r = (rel + "/" + fn) if rel else fn
                    yield r, ap
            for d in reversed(dirs):
                nd = cur / d
                stack.append((nd, (rel + "/" + d) if rel else d))
    except Exception as exc:  # robustness: never crash the whole scan
        warnings.append(f"error scanning {root}: {exc}")

# --------------------------------------------------------------------------
# Definition selection
# --------------------------------------------------------------------------

def select_definition(defs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Best definition: max priority; first on ties (stable)."""
    if not defs:
        return None
    best = defs[0]
    for d in defs[1:]:
        if d["priority"] > best["priority"]:
            best = d
    return best

# --------------------------------------------------------------------------
# Per-entity analysis
# --------------------------------------------------------------------------

FileText = Tuple[str, str, str, str]  # (package, rel, package_root, text)

def analyze_entry(
    entry: Dict[str, Any], file_texts: List[FileText],
) -> Dict[str, Any]:
    normalized, kind = entity_name_and_kind(entry)
    # Reject missing/empty names BEFORE any mention regex: an empty pattern
    # would match every backslash in the corpus.
    if not normalized:
        result = dict(entry)  # preserve original fields
        result.update({
            "kind": kind,
            "normalized_name": "",
            "status": "requires_manual_review",
            "requires_manual_review": True,
            "found_in_source": False,
            "has_direct_definition": False,
            "error": "missing or empty command/environment name",
            "definitions": [], "defining_packages": [], "first_mention": None,
            "mentions_count": 0, "mentions": [],
            "definition_type": None, "package": None, "file": None,
            "line": None, "role": None, "priority": None,
        })
        return result

    all_defs: List[Dict[str, Any]] = []
    all_mentions: List[Dict[str, Any]] = []
    first_mention: Optional[Dict[str, Any]] = None

    for package, rel, _root, text in file_texts:
        pat_defs, spans = scan_text_spans(text, normalized, kind)
        for d in pat_defs:
            rec = dict(d)
            rec["package"] = package
            rec["file"] = rel
            all_defs.append(rec)
        for mm in find_mentions(text, normalized, kind, spans):
            rec = {"package": package, "file": rel,
                   "line": mm["line"], "snippet": mm["snippet"]}
            all_mentions.append(rec)
            if first_mention is None:
                first_mention = rec

    selected = select_definition(all_defs)
    has_def, has_mention = bool(all_defs), bool(all_mentions)
    if has_def:
        status = "defined"
    elif has_mention and kind == "environment":
        status = "requires_manual_review"
    elif has_mention:
        status = "mentioned_only"
    else:
        status = "not_found"

    result: Dict[str, Any] = dict(entry)  # preserve original fields
    result.update({
        "kind": kind,
        "normalized_name": normalized,
        "found_in_source": has_def or has_mention,
        "has_direct_definition": has_def,
        "status": status,
        "requires_manual_review": (has_mention and not has_def)
                                  or status == "requires_manual_review",
        "definition_selection": SELECTION_NOTE,
        "definitions": all_defs,
        "defining_packages": sorted({d["package"] for d in all_defs}),
        "first_mention": first_mention,
        "mentions_count": len(all_mentions),
        "mentions": all_mentions,
    })
    if selected:
        result.update({
            "definition_type": _strip_dt_slashes(selected["definition_type"]),
            "package": selected["package"],
            "file": selected["file"],
            "line": selected["line"],
            "role": selected["role"],
            "priority": selected["priority"],
        })
    else:
        result.update({
            "definition_type": None, "package": None, "file": None,
            "line": None, "role": None, "priority": None,
        })
    return result

# --------------------------------------------------------------------------
# Loading and scanning
# --------------------------------------------------------------------------

def load_table(path: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    entries: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {}
    if isinstance(data, list):
        entries = [e for e in data if isinstance(e, dict)]
    elif isinstance(data, dict):
        meta = {k: v for k, v in data.items()
                if not isinstance(v, (list, dict))}
        for key in ("commands", "environments", "entries", "table", "items"):
            v = data.get(key)
            if isinstance(v, list):
                entries = [e for e in v if isinstance(e, dict)]
                break
        if not entries:
            for v in data.values():
                if isinstance(v, list) and v and isinstance(v[0], dict) \
                        and "name" in v[0]:
                    entries = [e for e in v if isinstance(e, dict)]
                    break
    if not entries:
        raise ValueError(f"no command/environment entries found in {path}")
    return entries, meta

def build_file_texts(
    roots: List[Tuple[str, Path]], extensions: List[str],
    follow_dir_symlinks: bool, ignore_containers: List[str],
) -> Tuple[List[FileText], Dict[str, Any]]:
    warnings: List[str] = []
    texts: List[FileText] = []
    scan_stats: Dict[str, Any] = {
        "roots": [], "files_scanned": 0, "files_read_errors": 0,
        "extension_filter": sorted(extensions),
        "follow_directory_symlinks": follow_dir_symlinks,
        "masked_containers": {},
    }
    for package, root in roots:
        n = 0
        for rel, ap in iter_package_files(
                root, extensions, follow_dir_symlinks, warnings):
            try:
                raw = ap.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                warnings.append(f"cannot read {ap}: {exc}")
                scan_stats["files_read_errors"] += 1
                continue
            text = strip_comments(raw)
            if ignore_containers:
                text, counts = mask_all_containers(text, ignore_containers)
                for c, k in counts.items():
                    scan_stats["masked_containers"][c] = \
                        scan_stats["masked_containers"].get(c, 0) + k
            texts.append((package, rel, str(root), text))
            n += 1
        scan_stats["roots"].append({
            "package": package, "root": str(root),
            "exists": root.exists(), "files_scanned": n,
        })
    scan_stats["files_scanned"] = len(texts)
    return texts, {"scan": scan_stats, "warnings": warnings,
                   "static_limitation": STATIC_LIMITATION}

# --------------------------------------------------------------------------
# Reporting / output
# --------------------------------------------------------------------------

def terminal_report(results: List[Dict[str, Any]], overview: Dict[str, Any],
                    warnings: List[str]) -> str:
    L: List[str] = []
    L.append("=" * 70)
    L.append("Static LaTeX definition analysis - table2 checker")
    L.append("=" * 70)
    L.append(f"entries analysed : {overview['entries']}")
    L.append(f"defined          : {overview['defined']}")
    L.append(f"mentioned_only   : {overview['mentioned_only']}")
    L.append(f"manual_review    : {overview['requires_manual_review']}")
    L.append(f"not_found        : {overview['not_found']}")
    L.append(f"files scanned    : {overview['files_scanned']}")
    for r in overview.get("roots", []):
        L.append(f"  root {r['package']:<10} {r['root']} "
                 f"(exists={r['exists']}, files={r['files_scanned']})")
    if warnings:
        L.append(f"warnings          : {len(warnings)}")
        for w in warnings[:10]:
            L.append(f"  - {w}")
    L.append("-" * 70)
    for r in results:
        L.append(
            f"[{r['status']:^21}] \\{r['normalized_name']} "
            f"({r['kind']}) -> "
            f"{r['definition_type']} @ {r['package']}:{r['file']}:"
            f"{r['line']} role={r['role']} prio={r['priority']} "
            f"(defs={len(r['definitions'])}, mentions={r['mentions_count']})"
        )
        pkgs = r.get("defining_packages") or []
        if pkgs:
            L.append(f"             defined in packages: {', '.join(pkgs)}")
    L.append("-" * 70)
    L.append("NOTE: " + STATIC_LIMITATION)
    return "\n".join(L)

def main(argv: Optional[List[str]] = None) -> int:
    here = Path(__file__).resolve().parent
    default_input = here / "01-table2.json"
    default_output = here / "02-table2-checked.json"
    tl = "/usr/local/texlive/2026/texmf-dist/tex"

    ap = argparse.ArgumentParser(
        description="Static analyzer of LaTeX command/environment definitions.")
    ap.add_argument("input", nargs="?", default=str(default_input),
                    help="input JSON table (default: 01-table2.json beside script)")
    ap.add_argument("-o", "--output", default=str(default_output),
                    help="output JSON (default: 02-table2-checked.json beside script)")
    ap.add_argument("--ignore-definition-container", action="append",
                    default=[], metavar="NAME",
                    help="mask first braced argument of \\NAME (repeatable)")
    ap.add_argument("--scan-ext", action="append", default=[],
                    metavar="EXT", help="extra file extension to scan")
    ap.add_argument("--follow-directory-symlinks", action="store_true",
                    help="follow directory symlinks when scanning")
    ap.add_argument("--no-default-roots", action="store_true",
                    help="do not add default scan roots")
    ap.add_argument("--unipersian-root", metavar="DIR",
                    help="override UniPersian input root (flat, recursive)")
    ap.add_argument("--babel-root", metavar="DIR", help="override babel root")
    ap.add_argument("--bidi-root", metavar="DIR", help="override bidi root")
    ap.add_argument("--luabidi-root", metavar="DIR",
                    help="override luabidi root (default: "
                         "/usr/local/texlive/2026/texmf-dist/tex/lualatex/luabidi)")
    ap.add_argument("--fontspec-root", metavar="DIR", help="override fontspec root")
    ap.add_argument("--quiet", action="store_true", help="suppress terminal report")
    args = ap.parse_args(argv)

    try:
        in_path = Path(args.input).resolve()
        if not in_path.is_file():
            print(f"error: input file not found: {in_path}", file=sys.stderr)
            return 2
        entries, meta = load_table(in_path)
    except Exception as exc:
        print(f"error: cannot load input JSON: {exc}", file=sys.stderr)
        return 2

    unipersian_root = Path(args.unipersian_root) if args.unipersian_root \
        else in_path.parent.parent
    roots: List[Tuple[str, Path]] = []
    if not args.no_default_roots:
        roots.append(("unipersian", unipersian_root))
        roots.append(("babel", Path(args.babel_root)
                      if args.babel_root else Path(tl + "/generic/babel")))
        roots.append(("bidi", Path(args.bidi_root)
                      if args.bidi_root else Path(tl + "/latex/bidi")))
        roots.append(("luabidi", Path(args.luabidi_root)
                      if args.luabidi_root
                      else Path(tl + "/lualatex/luabidi")))
        roots.append(("fontspec", Path(args.fontspec_root)
                      if args.fontspec_root else Path(tl + "/latex/fontspec")))
    else:
        overrides = [("unipersian", args.unipersian_root), ("babel", args.babel_root),
                     ("bidi", args.bidi_root), ("luabidi", args.luabidi_root),
                     ("fontspec", args.fontspec_root)]
        roots = [(p, Path(d)) for p, d in overrides if d]

    extensions = [".sty", ".def"]
    for ext in args.scan_ext:
        e = ext.lower()
        if not e.startswith("."):
            e = "." + e
        if e not in extensions:
            extensions.append(e)

    try:
        file_texts, scan_info = build_file_texts(
            roots, extensions, args.follow_directory_symlinks,
            args.ignore_definition_container)
    except Exception as exc:
        print(f"error: scan failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 3

    results: List[Dict[str, Any]] = []
    for entry in entries:
        try:
            results.append(analyze_entry(entry, file_texts))
        except Exception as exc:
            _fb_name, _fb_kind = entity_name_and_kind(entry)
            results.append({
                "name": entry.get("name", entry.get("command")),
                "kind": _fb_kind,
                "normalized_name": _fb_name,
                "status": "requires_manual_review",
                "error": f"analysis failed: {exc}",
                "definitions": [], "mentions": [], "mentions_count": 0,
                "has_direct_definition": False, "found_in_source": False,
            })

    overview = {
        "entries": len(results),
        "defined": sum(1 for r in results if r.get("status") == "defined"),
        "mentioned_only": sum(1 for r in results
                              if r.get("status") == "mentioned_only"),
        "requires_manual_review": sum(1 for r in results
                                      if r.get("requires_manual_review")),
        "not_found": sum(1 for r in results if r.get("status") == "not_found"),
        "files_scanned": scan_info["scan"]["files_scanned"],
        "roots": scan_info["scan"]["roots"],
    }
    out = {
        "overview": overview,
        "scan": scan_info["scan"],
        "warnings": scan_info["warnings"],
        "static_limitation": STATIC_LIMITATION,
        "input_meta": meta,
        "results": results,
    }
    out_path = Path(args.output).resolve()
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 4

    report = terminal_report(results, overview, scan_info["warnings"])
    print(report)
    if not args.quiet:
        try:
            rpt = out_path.with_name(out_path.stem + "-report.txt")
            rpt.write_text(report + "\n", encoding="utf-8")
            print(f"report file: {rpt}")
        except OSError:
            pass
    print(f"output JSON : {out_path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())