"""
That K Report -- CLI entry.

Three subcommands now, still a single module so the workflow can
invoke any mode with one shell line:

    # Nightly projections (default).  Same as before.
    python -m edge_equation.that_k projections --sample

    # Yesterday's Results Card + ledger update.
    python -m edge_equation.that_k results --sample

    # Supporting content for the day (K of the Night / Stat Drop /
    # Throwback).  Auto-picks 1-2 types rotated by date.
    python -m edge_equation.that_k supporting --sample

Every subcommand accepts --date YYYY-MM-DD, --out <path>, and --sample
(for dry-run fixtures).  The projections command additionally takes
--intro-70s to prepend the tasteful personality line.

Back-compat: `python -m edge_equation.that_k --sample` (no subcommand)
still runs the projections path.  The workflow can keep calling the
old flag layout while it's migrated to the subcommand form.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import List, Optional

from edge_equation.that_k.config import (
    TargetAccount,
    assert_account_separation,
    resolve_x_credentials,
)
from edge_equation.that_k.ledger import DEFAULT_LEDGER_PATH, Ledger
from edge_equation.that_k.report import DEFAULT_TOP_N, render_report
from edge_equation.that_k.results import build_results, render_results_card
from edge_equation.that_k.runner import build_projections
from edge_equation.that_k.sample_results import (
    sample_last_night_standout,
    sample_results,
    sample_slate_hooks,
)
from edge_equation.that_k.metrics import build_metrics_payload, write_metrics
from edge_equation.that_k.runner import (
    build_ab_entries,
    build_feature_importance,
)
from edge_equation.that_k.sample_slate import sample_slate
from edge_equation.that_k.simulator import DEFAULT_N_SIMS
from edge_equation.that_k.spotlight import (
    SpotlightSubject,
    render_spotlight,
    sample_spotlight,
)
from edge_equation.that_k.supporting import (
    generate_supporting,
    render_supporting,
)


def _resolve_target(value: str) -> TargetAccount:
    try:
        return TargetAccount(value)
    except ValueError as e:
        raise SystemExit(
            f"Invalid --target-account {value!r}. Expected 'k_guy' or 'main'."
        ) from e


def _preflight_account(account: TargetAccount) -> None:
    """Log (stderr) any account-separation warnings + completeness
    summary before the subcommand runs. Never prints secret values."""
    warnings = assert_account_separation(account)
    for w in warnings:
        sys.stderr.write(f"[that_k] warn: {w}\n")
    creds = resolve_x_credentials(account)
    if not creds.is_complete():
        # Informational only -- current workflow doesn't post to X
        # from these subcommands (projections + results are manual,
        # supporting is text-artifact only).  When a future poster
        # step IS added it will hard-fail on incomplete creds; for
        # now we just print the missing names so the operator can
        # configure them ahead of time.
        sys.stderr.write(
            "[that_k] info: target_account="
            f"{account.value} missing X secrets: {list(creds.missing)}\n"
        )


def _today() -> str:
    return dt.date.today().isoformat()


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _emit(text: str, out_path: Optional[Path]) -> None:
    """Write to file if `out_path` is set; otherwise stdout."""
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


# ---------------------------------------------------------------- subcommands

def _cmd_projections(args) -> int:
    account = _resolve_target(args.target_account)
    _preflight_account(account)
    if args.sample:
        slate = sample_slate()
    else:
        if not args.slate:
            raise SystemExit("projections: --slate or --sample required")
        slate = _load_json(args.slate)
    rows = build_projections(slate, n_sims=args.n_sims)
    text = render_report(
        rows, date_str=args.date, top_n=args.top_n, intro_70s=args.intro_70s,
        target_account=account,
    )
    _emit(text, args.out)
    # Optional Testing-Ground debug artifact.  Opt-in so routine
    # manual runs stay lightweight; the testing-ground workflow
    # wires it in every time.
    metrics_out = getattr(args, "metrics_out", None)
    if metrics_out is not None:
        ab_entries = build_ab_entries(rows)
        feature_rows = build_feature_importance(rows)
        payload = build_metrics_payload(
            rows=rows, ab_entries=ab_entries, feature_rows=feature_rows,
            date_str=args.date, target_account=account,
        )
        write_metrics(Path(metrics_out), payload)
    return 0


def _cmd_spotlight(args) -> int:
    account = _resolve_target(args.target_account)
    _preflight_account(account)
    if args.sample:
        subject = sample_spotlight()
    else:
        if not args.subject:
            raise SystemExit("spotlight: --subject or --sample required")
        raw = _load_json(args.subject)
        subject = SpotlightSubject(
            pitcher=raw["pitcher"],
            team=raw["team"],
            opponent=raw.get("opponent"),
            throws=raw.get("throws", "R"),
            arsenal=raw.get("arsenal"),
            movement=raw.get("movement"),
            edge_read=raw.get("edge_read"),
            projection_mean=raw.get("projection_mean"),
            projection_line=raw.get("projection_line"),
            projection_grade=raw.get("projection_grade"),
            clip=raw.get("clip"),
        )
    text = render_spotlight(
        subject, week_of=args.week_of, target_account=account,
    )
    _emit(text, args.out)
    return 0


def _cmd_results(args) -> int:
    account = _resolve_target(args.target_account)
    _preflight_account(account)
    if args.sample:
        rows = sample_results()
    else:
        if not args.results:
            raise SystemExit("results: --results or --sample required")
        rows = _load_json(args.results)
    results = build_results(rows)
    # Ledger: --no-ledger skips persistence entirely (useful for
    # dry-runs that shouldn't touch the on-disk season totals).
    ledger: Optional[Ledger] = None
    if not args.no_ledger:
        ledger = Ledger(args.ledger or DEFAULT_LEDGER_PATH)
    text = render_results_card(
        results=results,
        date_str=args.date,
        ledger=ledger,
        intro_70s=args.intro_70s,
        update_ledger=not args.no_ledger,
        commentary=not args.no_commentary,
        target_account=account,
    )
    _emit(text, args.out)
    return 0


def _cmd_supporting(args) -> int:
    account = _resolve_target(args.target_account)
    _preflight_account(account)
    last_night = None
    slate_hooks = None
    if args.sample:
        last_night = sample_last_night_standout()
        slate_hooks = sample_slate_hooks()
    else:
        if args.last_night:
            last_night = _load_json(args.last_night)
        if args.slate_hooks:
            slate_hooks = _load_json(args.slate_hooks)
    posts = generate_supporting(
        date_str=args.date,
        types=args.types or None,
        last_night=last_night,
        slate_hooks=slate_hooks,
        n=args.n,
    )
    text = render_supporting(posts)
    _emit(text, args.out)
    return 0


# ---------------------------------------------------------------- arg parsing

def _build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="edge_equation.that_k",
        description=(
            "That K Report pipeline -- projections, results, and "
            "supporting content generation."
        ),
    )
    # Back-compat: top-level --sample still means "projections --sample".
    root.add_argument(
        "--sample", action="store_true",
        help=argparse.SUPPRESS,
    )
    root.add_argument(
        "--slate", type=Path, help=argparse.SUPPRESS,
    )
    root.add_argument(
        "--date", default=_today(), help=argparse.SUPPRESS,
    )
    root.add_argument(
        "--top-n", type=int, default=DEFAULT_TOP_N, help=argparse.SUPPRESS,
    )
    root.add_argument(
        "--n-sims", type=int, default=DEFAULT_N_SIMS, help=argparse.SUPPRESS,
    )
    root.add_argument(
        "--out", type=Path, default=None, help=argparse.SUPPRESS,
    )
    root.add_argument(
        "--intro-70s", action="store_true", help=argparse.SUPPRESS,
    )
    root.add_argument(
        "--target-account", default=TargetAccount.KGUY.value,
        help=argparse.SUPPRESS,
    )
    # Back-compat defaults so a top-level `--sample` invocation (no
    # subcommand) doesn't AttributeError on subcommand-only flags.
    root.set_defaults(no_commentary=False, metrics_out=None)

    sub = root.add_subparsers(dest="cmd")

    # Shared default for --target-account across subparsers. K Report
    # module is @ThatK_Guy by default; operator can force the main
    # account on the top-level --target-account flag.
    TARGET_HELP = (
        "Account identity for artifacts + credential resolution. "
        "'k_guy' -> @ThatK_Guy (X_*_KGUY secrets). "
        "'main'  -> @EdgeEquation (X_* secrets). Default k_guy."
    )

    # --- projections -----------------------------------------------
    pp = sub.add_parser(
        "projections",
        help="Generate the nightly pitcher K projections.",
    )
    pp.add_argument("--sample", action="store_true")
    pp.add_argument("--slate", type=Path)
    pp.add_argument("--date", default=_today())
    pp.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    pp.add_argument("--n-sims", type=int, default=DEFAULT_N_SIMS)
    pp.add_argument("--out", type=Path, default=None)
    pp.add_argument(
        "--intro-70s", action="store_true",
        help="Prepend a light 70s personality line above the section "
             "header.",
    )
    pp.add_argument(
        "--target-account", default=TargetAccount.KGUY.value,
        choices=[a.value for a in TargetAccount], help=TARGET_HELP,
    )
    pp.add_argument(
        "--metrics-out", type=Path, default=None,
        help="Optional path to write a debug metrics JSON "
             "(calibration + feature importance + A/B variants).",
    )

    # --- spotlight -------------------------------------------------
    wp = sub.add_parser(
        "spotlight",
        help="Render the weekly Pitcher Spotlight card.",
    )
    wp.add_argument("--sample", action="store_true")
    wp.add_argument(
        "--subject", type=Path,
        help="JSON file with a single SpotlightSubject dict "
             "(pitcher/arsenal/movement/edge_read/...).",
    )
    wp.add_argument(
        "--week-of", default=_today(),
        help="Monday of the feature week, YYYY-MM-DD.",
    )
    wp.add_argument("--out", type=Path, default=None)
    wp.add_argument(
        "--target-account", default=TargetAccount.KGUY.value,
        choices=[a.value for a in TargetAccount], help=TARGET_HELP,
    )

    # --- results ---------------------------------------------------
    rp = sub.add_parser(
        "results",
        help="Render yesterday's Results card + update the season ledger.",
    )
    rp.add_argument("--sample", action="store_true")
    rp.add_argument(
        "--results", type=Path,
        help="Path to a JSON file: list of "
             "{'pitcher': str, 'line': float, 'actual': int}.",
    )
    rp.add_argument("--date", default=_today())
    rp.add_argument("--out", type=Path, default=None)
    rp.add_argument(
        "--ledger", type=Path, default=None,
        help=f"Ledger file path (default: {DEFAULT_LEDGER_PATH}).",
    )
    rp.add_argument(
        "--no-ledger", action="store_true",
        help="Skip ledger persistence (dry-run mode).",
    )
    rp.add_argument("--intro-70s", action="store_true")
    rp.add_argument(
        "--no-commentary", action="store_true",
        help="Skip the 70s-flair day commentary footer.",
    )
    rp.add_argument(
        "--target-account", default=TargetAccount.KGUY.value,
        choices=[a.value for a in TargetAccount], help=TARGET_HELP,
    )

    # --- supporting ------------------------------------------------
    sp = sub.add_parser(
        "supporting",
        help="Generate 1-2 supporting posts (K_OF_THE_NIGHT / STAT_DROP "
             "/ THROWBACK_K), rotated by date.",
    )
    sp.add_argument("--sample", action="store_true")
    sp.add_argument(
        "--last-night", type=Path,
        help="JSON file: previous evening's standout payload.",
    )
    sp.add_argument(
        "--slate-hooks", type=Path,
        help="JSON file: tonight's slate hooks (umpire trend, "
             "lineup leader, arsenal edge, form streak).",
    )
    sp.add_argument("--date", default=_today())
    sp.add_argument("--out", type=Path, default=None)
    sp.add_argument(
        "--n", type=int, default=1,
        help="How many supporting posts to generate (1 or 2, "
             "capped by brand rule).",
    )
    sp.add_argument(
        "--types", nargs="*",
        help="Explicit post types to generate (default: auto-rotate "
             "by date).",
    )
    sp.add_argument(
        "--target-account", default=TargetAccount.KGUY.value,
        choices=[a.value for a in TargetAccount], help=TARGET_HELP,
    )

    return root


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        # Back-compat: no subcommand -> projections path.
        if args.sample or args.slate:
            return _cmd_projections(args)
        parser.print_help()
        return 2
    if args.cmd == "projections":
        return _cmd_projections(args)
    if args.cmd == "results":
        return _cmd_results(args)
    if args.cmd == "supporting":
        return _cmd_supporting(args)
    if args.cmd == "spotlight":
        return _cmd_spotlight(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
