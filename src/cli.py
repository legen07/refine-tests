"""Entrypoint (plugin.toml `entrypoint = "src.cli:main"`).

Usage (§17):

    refine-tests                                   # audit whole repo (read-only)
    refine-tests --path "src/billing/**"           # scoped audit
    refine-tests --diff origin/main..HEAD          # scoped audit (changed files)
    refine-tests --offline                         # no CI history mining
    refine-tests --cleanup --dry-run               # show plan, no writes
    refine-tests --cleanup --yes                   # quarantine all gated removals
    refine-tests approve <id>                      # human sign-off: permanent remove
    refine-tests restore <id>                      # reverse a quarantine

Exit codes: audit = 0 always (findings are not failures, §9); cleanup missing
`--yes` = 2 with a diff preview; cleanup applied = 0; usage error = 1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import engine, plugin_loader
from .actions import QuarantineStore
from .config_paths import plugin_root

EPILOG = ("Every retained or proposed test must answer the north-star "
          "question: what real production bug would this catch?")
REPORT_BASE = engine.REPORT_BASE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="refine-tests",
        description="Forensic audit + curation of a project's test suite.",
        epilog=EPILOG,
    )
    parser.add_argument("target", nargs="?", default=None,
                        help="project root to audit (default: current "
                             "directory)")
    manifest = plugin_loader.identity()
    parser.add_argument("--version", action="version",
                        version=f"{manifest['name']} {manifest['version']}")
    parser.description = manifest["description"]
    # §9 modes
    parser.add_argument("--cleanup", action="store_true",
                        help="opt-in cleanup mode (writes quarantine/ and "
                             "proposed/ only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan; never write (§8)")
    parser.add_argument("--yes", action="store_true",
                        help="confirm the gated writes (required for cleanup)")
    parser.add_argument("--approve", action="append", default=[],
                        metavar="<id>",
                        help="approve one action (test id or quarantine id); "
                             "repeatable")
    parser.add_argument("--approve-all", action="store_true",
                        help="approve every gated removal in this run")
    parser.add_argument("--allow-replace-proposals", action="store_true",
                        help="allow Replace proposals in cleanup mode (§12)")
    # §9 scoped modifiers
    parser.add_argument("--path", metavar="<glob>",
                        help="audit only tests matching this path glob")
    parser.add_argument("--diff", metavar="base..head",
                        help="audit only tests touched by, or covering code "
                             "changed in, the given diff")
    parser.add_argument("--all-tiers", action="store_true",
                        help="scoped runs default to smoke+core; pass this to "
                             "include extended")
    # §2 / §9
    parser.add_argument("--offline", action="store_true",
                        help="never make a network call (skip CI history "
                             "mining)")
    parser.add_argument("--mutation", action="store_true",
                        help="enable the P4 mutation probe (feature flag "
                             "mutation=true)")
    parser.add_argument("--report-dir", metavar="<dir>",
                        help="where to write the reports (default: target root)")
    parser.add_argument("--language", action="append", default=None,
                        dest="languages", choices=["python", "javascript",
                                                   "typescript"],
                        help="restrict to these languages (repeatable)")
    return parser


def build_subcommand_parser(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"refine-tests {command}",
        description={"approve": "Human sign-off for permanent removal (§8).",
                     "restore": "Reverse a quarantine (§8).",
                     "bootstrap": "Provision the plugin's own .venv with "
                                  "pytest + pytest-cov + coverage (one-time, "
                                  "needs network; makes per-test coverage "
                                  "available to audits run by an installed "
                                  "copy)."}[command])
    if command == "bootstrap":
        parser.add_argument("--python", default="python3",
                            help="interpreter for the venv (default: python3)")
        return parser
    parser.add_argument("qid", help="quarantine id (qt-…)")
    parser.add_argument("target", nargs="?", default=None,
                        help="project root (default: current directory)")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Dispatch subcommands explicitly so the audit/cleanup positional target
    # is never mistaken for a subcommand name.
    if argv and argv[0] in {"approve", "restore", "bootstrap"}:
        sub_args = build_subcommand_parser(argv[0]).parse_args(argv[1:])
        if argv[0] == "approve":
            return _run_approve(sub_args)
        if argv[0] == "restore":
            return _run_restore(sub_args)
        return _run_bootstrap(sub_args)

    parser = build_parser()
    args_ns = parser.parse_args(argv)

    target = Path(args_ns.target or Path.cwd()).resolve()
    if not target.exists():
        print(f"refine-tests: target does not exist: {target}",
              file=sys.stderr)
        return 1
    cfg = engine.load_config(target)
    args = engine.AuditArgs(
        mode="cleanup" if args_ns.cleanup else "audit",
        path_glob=args_ns.path,
        diff=args_ns.diff,
        offline=args_ns.offline or
        bool((cfg.get("ci", {}) or {}).get("provider") == "none"),
        all_tiers=args_ns.all_tiers,
        yes=args_ns.yes,
        dry_run=args_ns.dry_run,
        approve=list(args_ns.approve or []),
        approve_all=args_ns.approve_all,
        allow_replace_proposals=args_ns.allow_replace_proposals,
        mutation=args_ns.mutation,
        report_dir=args_ns.report_dir,
        languages=args_ns.languages,
    )
    if args.diff and args.path_glob:
        print("refine-tests: --path and --diff are mutually exclusive (§9)",
              file=sys.stderr)
        return 1

    try:
        result = engine.analyze(target, cfg, args)
    except engine.RefineTestsError as exc:
        # Usage failures (bad --diff range, unreadable target, ...): a clean
        # one-liner, never a traceback (§16 quality bar).
        print(f"refine-tests: {exc}", file=sys.stderr)
        return 2

    if args.mode == "audit":
        md, js = engine.write_reports(result, target, args)
        _print_audit_summary(result, md, js)
        return 0

    # -- cleanup (§8) ---------------------------------------------------------
    if not args.yes or args.dry_run:
        # §14.3: zero writes, diff preview on stdout, non-zero without --yes.
        print(engine.render_plan(result, target, cfg))
        if args.dry_run:
            print()
            print("Dry run requested: no writes performed; exit 0.")
            return 0
        print()
        print("refine-tests: cleanup refused without --yes "
              "(no writes performed).", file=sys.stderr)
        return 2

    result = engine.apply_cleanup(result, target, cfg, args)
    md, js = engine.write_reports(result, target, args)
    _print_cleanup_summary(result, md, js)
    return 0


def _run_bootstrap(args_ns: argparse.Namespace) -> int:
    """Provision <plugin root>/.venv so audits get per-test coverage (§2).

    Installed copies (e.g. a profile's node_modules) ship no virtualenv: the
    venv is gitignored, and packing it would be platform-specific megabytes.
    Bootstrap is explicit and one-time; every probe below stays honest about
    what it found (§0).
    """
    import subprocess as _sp
    import sys as _sys
    from .config_paths import plugin_root as _root

    venv = _root() / ".venv"
    py = venv / "bin" / "python"
    if py.exists():
        probe = _sp.run([str(py), "-c", "import pytest, coverage, pytest_cov"],
                        capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            print(f"refine-tests bootstrap: nothing to do, {py} already has "
                  "pytest + pytest-cov + coverage.")
            return 0
    print(f"refine-tests bootstrap: creating {venv} ...")
    try:
        proc = _sp.run([args_ns.python, "-m", "venv", str(venv)],
                       capture_output=True, text=True, check=False)
    except OSError as exc:
        print(f"refine-tests bootstrap: venv creation failed: {exc}",
              file=_sys.stderr)
        return 1
    if proc.returncode != 0:
        print(f"refine-tests bootstrap: venv creation failed:\n"
              f"{(proc.stderr or '').strip()}", file=_sys.stderr)
        return 1
    print("refine-tests bootstrap: installing pytest + pytest-cov + "
          "coverage (network) ...")
    proc = _sp.run([str(py), "-m", "pip", "install", "-q", "--upgrade",
                    "pytest", "pytest-cov", "coverage"],
                   capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(f"refine-tests bootstrap: pip install failed:\n"
              f"{(proc.stderr or '').strip()}", file=_sys.stderr)
        return 1
    probe = _sp.run([str(py), "-c", "import pytest, coverage, pytest_cov"],
                    capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        print("refine-tests bootstrap: installed, but the import probe "
              "failed — audits will degrade to no-coverage mode (§2).",
              file=_sys.stderr)
        return 1
    print(f"refine-tests bootstrap: done. Audits now use {py} "
          "(per-test coverage available).")
    return 0

def _run_approve(args_ns: argparse.Namespace) -> int:
    target = Path(args_ns.target or Path.cwd()).resolve()
    cfg = engine.load_config(target)
    store = QuarantineStore(target / (cfg.get("quarantine", {}) or {}).get(
        "root", "quarantine"))
    try:
        record = store.approve(args_ns.qid, target)
    except KeyError as exc:
        print(f"refine-tests: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"refine-tests: {exc}", file=sys.stderr)
        return 1
    sign = record.human_signoff
    print(f"Approved permanent removal of `{record.test_id}` "
          f"(id {record.id}).")
    print(f"  human sign-off: {sign.get('name', 'unknown')} "
          f"<{sign.get('email', '')}> at {sign.get('signed_at', '')} "
          f"(commit {sign.get('commit', '—')})")
    print("  The original source remains archived at "
          f"quarantine/{record.id}/original.py (§8).")
    return 0


def _run_restore(args_ns: argparse.Namespace) -> int:
    target = Path(args_ns.target or Path.cwd()).resolve()
    cfg = engine.load_config(target)
    store = QuarantineStore(target / (cfg.get("quarantine", {}) or {}).get(
        "root", "quarantine"))
    try:
        record = store.restore(args_ns.qid, target)
    except (KeyError, ValueError) as exc:
        print(f"refine-tests: {exc}", file=sys.stderr)
        return 1
    print(f"Restored `{record.test_id}` from quarantine/{record.id}/ "
          f"back into {record.file}.")
    print("  The quarantine entry moved to quarantine/.restored/ for the "
          "audit trail.")
    return 0


def _print_audit_summary(result, md, js) -> None:
    summary = result.summary
    by_action = result.by_action()
    print("refine-tests audit complete (read-only mode, §9).")
    print(f"  tests analyzed : {summary.get('total_tests', 0)}")
    print("  findings by action: " +
          (", ".join(f"{k}={v}" for k, v in sorted(by_action.items()))
           if by_action else "none"))
    ci = result.run.get("ci", {})
    print(f"  CI history     : {ci.get('source', 'unavailable')} "
          f"({ci.get('details', '')})")
    cov = result.run.get("toolchain", {})
    print(f"  per-test coverage : "
          f"{'available' if cov.get('coverage_available') else 'UNAVAILABLE'}"
          f" ({cov.get('coverage_note', '')})")
    print(f"  report (md)    : {md}")
    print(f"  report (json)  : {js}")
    print("  Findings are not failures (exit code is always 0 in audit "
          "mode).")


def _print_cleanup_summary(result, md, js) -> None:
    applied = result.run.get("applied", {})
    print("refine-tests cleanup applied (writes limited to quarantine/ and "
          "proposed/, §9).")
    print(f"  quarantined    : {len(applied.get('removed', []))} "
          f"({', '.join(applied.get('removed', [])) or '—'})")
    print(f"  proposals      : {len(applied.get('proposals', []))}")
    skipped = applied.get("skipped") or []
    if skipped:
        print(f"  removals skipped: {len(skipped)} (quarantine is Python-only "
              f"for now; see the report for each test and reason)")
    print(f"  report (md)    : {md}")
    print(f"  report (json)  : {js}")


if __name__ == "__main__":
    raise SystemExit(main())
