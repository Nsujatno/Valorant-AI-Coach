"""Run with the existing environment: python -m valcoach --help."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .normalize import load_match
from .overview import EVENT_KINDS, query_overview
from .query import DEFAULTS, query_round, select_player
from .render import render_overview, render_round, render_spatial
from .render.spatial import render_spatial_svg
from .source import Export, QueryError
from .spatial import SpatialSettings, query_spatial
from .fight import FightSettings, query_fight
from .render.fight import render_fight
from .resources import query_resources
from .render.resources import render_resources


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Local evidence queries over existing vrfkit Parquet exports. No downloads.")
    commands = result.add_subparsers(dest="command", required=True)
    for name, help_ in [("players", "Discover unambiguous player selectors"),
                        ("overview", "Summarize the full match and discover round evidence to investigate"),
                        ("spatial", "Inspect bounded positions/facing and annotated map sight/access estimates"),
                        ("fight", "List fights or inspect compact fight context and individual evidence"),
                        ("resources", "Inspect ammo, ability resources and weapon states at a round time"),
                        ("round", "Inspect one round, Jett abilities, teammate fights and trade context")]:
        command = commands.add_parser(name, help=help_)
        if name in {"round", "spatial", "fight", "resources"}:
            command.add_argument("number", type=int, help="One-based round number")
        if name in {"round", "overview", "spatial", "fight", "resources"}:
            command.add_argument("--player", required=True, help="Subject UUID, state ID or pawn ID; a unique agent name also works")
        if name in {"round", "overview", "fight"}:
            for option in ("engagement_gap_ms", "trade_window_ms", "response_before_ms", "response_after_ms", "movement_max_age_ms"):
                command.add_argument("--" + option.replace("_", "-"), type=int, default=DEFAULTS[option])
        if name == "spatial":
            _spatial_arguments(command)
        if name == "fight":
            _fight_arguments(command)
        if name == "resources":
            command.add_argument("--at-ms", type=int, required=True, help="Snapshot time in milliseconds since live start")
            command.add_argument("--before-ms", type=int, default=2000)
            command.add_argument("--after-ms", type=int, default=3000)
            command.add_argument("--limit", type=int, default=40, help="Resource observations per page (1-200)")
            command.add_argument("--offset", type=int, default=0)
        if name == "overview":
            command.add_argument("--limit", type=int, default=20, help="Review events per page (1-100); round totals always cover the full match")
            command.add_argument("--offset", type=int, default=0, help="Zero-based review-event offset")
            command.add_argument("--event-kind", choices=EVENT_KINDS, help="Filter review-event details without changing match totals")
        command.add_argument("--export", required=True, type=Path, help="Completed vrfkit export directory containing manifest.json")
        command.add_argument("--cache-dir", type=Path, help="Optional normalized-cache directory")
        command.add_argument("--json", action="store_true", help="Emit structured JSON instead of a readable view")
        command.add_argument("--output", type=Path, help="Write the result to a NEW file (does not overwrite existing files)")
    return result


def _fight_arguments(command):
    selector = command.add_mutually_exclusive_group()
    selector.add_argument("--id", dest="fight_id", help="Fight ID from the index or overview")
    selector.add_argument("--at-ms", type=int, help="Round-live-relative time; choose a unique overlapping/nearest fight within 1000 ms")
    selector.add_argument("--evidence-id", help="Retrieve one normalized evidence record from this round/player")
    command.add_argument("--before-ms", type=int, default=2000)
    command.add_argument("--after-ms", type=int, default=3000)
    command.add_argument("--limit", type=int, default=20, help="Fight index page size (1-40)")
    command.add_argument("--offset", type=int, default=0)
    command.add_argument("--allow-candidate-map", action="store_true")
    command.add_argument("--smoke-radius-cm", type=float, help="Optional assumed uniform smoke disk radius, not measured game geometry")


def _spatial_arguments(command):
    command.add_argument("--start-ms", type=int, required=True, help="Round-live-relative start, inclusive (may include buy phase)")
    command.add_argument("--end-ms", type=int, required=True, help="Round-live-relative end, exclusive; maximum span 10000 ms")
    command.add_argument("--step-ms", type=int, default=250, help="Snapshot spacing (default 250 ms)")
    command.add_argument("--limit", type=int, default=50, help="Maximum snapshots (1-200)")
    command.add_argument("--movement-max-age-ms", type=int, default=250)
    command.add_argument("--target", action="append", default=[], help="Target player selector; repeat to focus on several players")
    command.add_argument("--map-model", type=Path, help="Optional versioned local map model JSON")
    command.add_argument("--allow-candidate-map", action="store_true", help="Opt into explicitly unverified, build-matched candidate map estimates")
    command.add_argument("--smoke-radius-cm", type=float, help="Optional uniform candidate smoke-disk radius (1-2000 cm); not a decoded/validated radius")
    command.add_argument("--diagram", type=Path, help="Write a NEW diagnostic SVG showing the first snapshot")
    command.add_argument("--map-image", type=Path, help="Optional local PNG matching the model's recorded image checksum")


def _validate_paths(arguments) -> None:
    raw = arguments.export.resolve()
    if arguments.cache_dir and (arguments.cache_dir.resolve() == raw or raw in arguments.cache_dir.resolve().parents):
        raise QueryError("invalid_cache", "Keep the normalized cache outside the parser's raw export directory.")
    outputs = [p.resolve() for p in (arguments.output,getattr(arguments,"diagram",None)) if p]
    if len(set(outputs)) != len(outputs):
        raise QueryError("invalid_output", "Report and diagram must use different new paths.")
    for output in outputs:
        if output == raw or raw in output.parents:
            raise QueryError("invalid_output", "Keep generated output outside the parser's raw export directory.")
        if output.exists():
            raise QueryError("output_exists", f"Output already exists: {output}. Choose a new path.")
    if getattr(arguments,"map_image",None) and not arguments.diagram:
        raise QueryError("invalid_spatial_settings", "Use --map-image together with --diagram.")


def _query(arguments, export: Export, model: dict) -> dict:
    if arguments.command == "players":
        return {"schema_version": 1, "match_id": model["match_id"], "revision": model["revision"],
                "query": "players", "players": model["players"], "replay_build": model["replay_build"],
                "rounds": [r["number"] for r in model["rounds"]]}
    selected = select_player(model, arguments.player)
    if arguments.command == "resources":
        return query_resources(model, arguments.number, selected, arguments.at_ms, arguments.before_ms,
                               arguments.after_ms, arguments.limit, arguments.offset)
    if arguments.command == "spatial":
        config = SpatialSettings(arguments.step_ms,arguments.limit,arguments.movement_max_age_ms,arguments.allow_candidate_map,arguments.smoke_radius_cm)
        targets = [select_player(model,s) for s in arguments.target] if arguments.target else None
        return query_spatial(model,export,arguments.number,selected,arguments.start_ms,arguments.end_ms,config,targets,arguments.map_model)
    settings = {key: getattr(arguments, key) for key in DEFAULTS if hasattr(arguments, key)}
    if arguments.command == "fight":
        config = FightSettings(arguments.before_ms, arguments.after_ms, arguments.allow_candidate_map, arguments.smoke_radius_cm)
        return query_fight(model, export, arguments.number, selected, arguments.fight_id, arguments.at_ms,
                           arguments.evidence_id, config, arguments.limit, arguments.offset, settings)
    if arguments.command == "overview":
        return query_overview(model, export, selected, settings, arguments.limit, arguments.offset, arguments.event_kind)
    return query_round(model, export, arguments.number, selected, settings)


def _render(response: dict, as_json: bool) -> str:
    if as_json:
        if response["query"] in {"fight", "fight_index", "evidence", "resources"}:
            return json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return json.dumps(response, indent=2, ensure_ascii=False, allow_nan=False)
    if response["query"] == "players":
        return "\n".join(f"state {p['state_id']} / pawn {p['pawn']} | team {p['team']} | {p['agent']} | {p['id']}"
                         for p in response["players"])
    renderer = {"overview":render_overview,"round":render_round,"spatial":render_spatial,
                "fight":render_fight,"fight_index":render_fight,"evidence":render_fight,"resources":render_resources}[response["query"]]
    return renderer(response)


def _write(rendered: str, output: Path | None) -> None:
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered.rstrip() + "\n")
        print(f"Wrote {output}", file=sys.stderr)
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(rendered)


def main(argv=None) -> int:
    arguments = parser().parse_args(argv)
    try:
        _validate_paths(arguments)
        export = Export(arguments.export)
        model, reused = load_match(export, arguments.cache_dir)
        response = _query(arguments, export, model)
        response["cache_reused"] = reused
        diagram_path = getattr(arguments,"diagram",None)
        diagram = render_spatial_svg(response,arguments.map_image) if diagram_path else None
        _write(_render(response, arguments.json), arguments.output)
        if diagram:
            _write(diagram,diagram_path)
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
