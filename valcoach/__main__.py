"""Run with the existing environment: python -m valcoach --help."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .normalize import load_match
from .query import DEFAULTS, query_round, select_player
from .render import render_round
from .source import Export, QueryError


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Local evidence queries over existing vrfkit Parquet exports. No downloads.")
    commands = result.add_subparsers(dest="command", required=True)
    for name, help_ in [("players", "Discover unambiguous player selectors"),
                        ("round", "Inspect one round, Jett abilities, teammate fights and trade context")]:
        command = commands.add_parser(name, help=help_)
        if name == "round":
            command.add_argument("number", type=int, help="One-based round number")
            command.add_argument("--player", required=True, help="Subject UUID, state ID or pawn ID; a unique agent name also works")
            for option in ("engagement_gap_ms", "trade_window_ms", "response_before_ms", "response_after_ms", "movement_max_age_ms"):
                command.add_argument("--" + option.replace("_", "-"), type=int, default=DEFAULTS[option])
        command.add_argument("--export", required=True, type=Path, help="Completed vrfkit export directory containing manifest.json")
        command.add_argument("--cache-dir", type=Path, help="Optional normalized-cache directory")
        command.add_argument("--json", action="store_true", help="Emit structured JSON instead of a readable view")
        command.add_argument("--output", type=Path, help="Write the result to a NEW file (does not overwrite existing files)")
    return result


def main(argv=None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.cache_dir and (arguments.cache_dir.resolve() == arguments.export.resolve()
                                    or arguments.export.resolve() in arguments.cache_dir.resolve().parents):
            raise QueryError("invalid_cache", "Keep the normalized cache outside the parser's raw export directory.")
        if arguments.output and arguments.output.resolve() == arguments.export.resolve():
            raise QueryError("invalid_output", "The output must be a new file outside the raw export directory.")
        if arguments.output and arguments.export.resolve() in arguments.output.resolve().parents:
            raise QueryError("invalid_output", "Keep generated output outside the parser's raw export directory.")
        if arguments.output and arguments.output.exists():
            raise QueryError("output_exists", f"Output already exists: {arguments.output}. Choose a new path.")
        export = Export(arguments.export)
        model, reused = load_match(export, arguments.cache_dir)
        if arguments.command == "players":
            response = {"schema_version": 1, "match_id": model["match_id"], "revision": model["revision"],
                        "query": "players", "players": model["players"],
                        "rounds": [r["number"] for r in model["rounds"]], "cache_reused": reused,
                        "replay_build": model["replay_build"]}
            rendered = json.dumps(response, indent=2, ensure_ascii=False, allow_nan=False) if arguments.json else "\n".join(
                f"state {p['state_id']} / pawn {p['pawn']} | team {p['team']} | {p['agent']} | {p['id']}" for p in model["players"])
        else:
            selected = select_player(model, arguments.player)
            settings = {key: getattr(arguments, key) for key in DEFAULTS if hasattr(arguments, key)}
            response = query_round(model, export, arguments.number, selected, settings)
            response["cache_reused"] = reused
            rendered = json.dumps(response, indent=2, ensure_ascii=False, allow_nan=False) if arguments.json else render_round(response)
        if arguments.output:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            with arguments.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(rendered.rstrip() + "\n")
            print(f"Wrote {arguments.output}", file=sys.stderr)
        else:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            print(rendered)
        return 0
    except QueryError as exc:
        error = {"schema_version": 1, "error": {"code": exc.code, "message": str(exc)}}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        error = {"schema_version": 1, "error": {"code": "invalid_export_or_io", "message": str(exc)}}
    if arguments.json:
        print(json.dumps(error, ensure_ascii=True))
    else:
        print(f"{error['error']['code']}: {error['error']['message']}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
