"""Aggregation boundaries and cross-checks against both private reference exports."""

from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from valcoach.__main__ import main
from valcoach.normalize import load_match, reconstruct_scoreboard
from valcoach.overview import query_overview, summarize_round
from valcoach.query import DEFAULTS, player_fights, query_round, select_player
from valcoach.render import render_overview
from valcoach.source import Export, QueryError

ROOT = Path(__file__).resolve().parents[1]
OLD_EXPORT = Path(os.environ.get("VALCOACH_TEST_EXPORT", ROOT / "output/first-replay/raw"))
NEW_EXPORT = Path(os.environ.get("VALCOACH_TEST_EXPORT_1308", ROOT / "output/replay-5c31718d/raw"))
SUBJECT = "380875cd-9d67-5e65-a6b4-c11422aae222"


def example():
    selected = {"id": "selected", "state_id": 11, "pawn": 1, "team": 100, "agent": "Jett"}
    players = [selected, {"id": "mate", "state_id": 12, "pawn": 2, "team": 100, "agent": "Fade"},
               {"id": "opponent", "state_id": 13, "pawn": 3, "team": 200, "agent": "Clove"}]
    round_ = {"number": 1, "buy_ms": 0, "live_ms": 1000, "end_ms": 3000, "result_time_ms": 2000,
              "selected_side": "defender", "result": {"WinningTeamRole": "attacker", "RoundResult": "elimination"}}
    hit = {"id": "hit", "kind": "damage", "time_ms": 1200, "combat_time_ms": 1200,
           "attacker": 1, "victim": 3, "actual_damage": 50, "raw_damage": 64, "lethal": False}
    # One response is context for two teammate groups; it is still only one hit.
    fights = [{"id": f"fight:{n}", "kind": "teammate_fight", "time_ms": 1100+n, "end_ms": 1300+n,
               "teammate": 2, "opponent": 3, "teammate_died_in_observed_exchange": False,
               "selected_response": {"damage_ids": ["hit"], "actual_damage": 50,
                                     "status": "recorded_damage_contribution"}} for n in (1, 2)]
    model = {"match_id": "synthetic", "revision": "revision", "replay_build": "test", "source_quality": {},
             "players": players, "rounds": [round_], "timeline": [], "scoreboard": {}}
    report = {"round": round_, "selected_player": selected, "timeline": [], "evidence": {"hit": hit},
              "teammate_fights": fights, "player_fights": [], "trades": [],
              "coverage": {"death_events": 0, "deaths_matched_to_damage": 0, "damage_events": 1, "limitations": []}}
    return model, report, selected


class OverviewTests(unittest.TestCase):
    def test_scoreboard_uses_latest_observation_and_preserves_zero_and_unknown(self):
        model, _, _ = example()
        def row(time, name, value):
            return {"time_ms": time, "source_row": time, "actor_net_guid": 11,
                    "field_name": name, "value_i64": value}
        rows = [row(20, "AggregateKills", 0), row(10, "AggregateKills", 9),
                row(10, "AggregateDeaths", 2), row(20, "AggregateDeaths", None)]
        stats = reconstruct_scoreboard(rows, model["players"])
        self.assertEqual(stats["1"]["kills"]["value"], 0)
        self.assertEqual(stats["1"]["kills"]["source"]["row"], 20)
        self.assertIsNone(stats["1"]["deaths"]["value"])
        self.assertNotIn("assists", stats["1"])

    def test_overlapping_response_windows_do_not_duplicate_damage(self):
        model, report, selected = example()
        with patch("valcoach.overview.query_round", return_value=report):
            result = query_overview(model, None, selected)
        totals = result["totals"]
        self.assertEqual(totals["teammate_fights"]["with_recorded_response"], 2)
        self.assertEqual(totals["teammate_fights"]["unique_response_damage"]["hits"], 1)
        self.assertEqual(totals["teammate_fights"]["unique_response_damage"]["actual"], 50)
        self.assertEqual(totals["damage_dealt"]["actual"], 50)
        self.assertEqual(totals["damage_dealt"]["raw"], 64)

    def test_missing_damage_and_scoreboard_remain_unknown(self):
        model, report, selected = example()
        report["evidence"]["hit"]["actual_damage"] = None
        with patch("valcoach.overview.query_round", return_value=report):
            result = query_overview(model, None, selected)
        self.assertIsNone(result["totals"]["damage_dealt"]["actual"])
        self.assertEqual(result["totals"]["damage_dealt"]["unknown_actual_hits"], 1)
        self.assertIsNone(result["totals"]["teammate_fights"]["unique_response_damage"]["actual"])
        self.assertTrue(all(result["scoreboard"][key] is None for key in ("kills", "deaths", "assists")))

    def test_round_outcome_uses_current_side_and_missing_side_stays_unknown(self):
        model, report, _ = example()
        self.assertEqual(summarize_round(report, {3}).summary["outcome"], "loss")
        report["round"]["selected_side"] = "attacker"
        self.assertEqual(summarize_round(report, {3}).summary["outcome"], "win")
        report["round"]["selected_side"] = None
        self.assertEqual(summarize_round(report, {3}).summary["outcome"], "unknown")

    def test_post_round_deaths_retained_without_fake_kill_or_trade(self):
        _, report, _ = example()
        report["timeline"] = [{"id": "self", "kind": "death", "time_ms": 2400, "victim": 1,
            "killer": 1, "weapon": None, "damage_id": None, "post_round": True,
            "alive": {"selected_team": 1, "opponents": 1}},
            {"id": "opponent", "kind": "death", "time_ms": 2090, "victim": 1,
            "killer": 3, "weapon": "Classic", "damage_id": None, "post_round": True,
            "alive": {"selected_team": 1, "opponents": 1}}]
        round_data = summarize_round(report, {3})
        summary, events = round_data.summary, round_data.review_events
        self.assertEqual(summary["death_events"], 2)
        self.assertEqual(summary["opponent_kills"], 0)
        self.assertEqual(summary["self_attributed_deaths"], 1)
        self.assertEqual(summary["post_round_deaths"], 2)
        self.assertTrue(all(e["details"]["trade_status"] == "outside_trade_scope"
                            for e in events if e["kind"] == "death"))

    def test_pagination_and_filters_preserve_totals_and_report_empty_pages(self):
        model, report, selected = example()
        with patch("valcoach.overview.query_round", return_value=report):
            first = query_overview(model, None, selected, limit=1)
            second = query_overview(model, None, selected, limit=1, offset=1)
            empty = query_overview(model, None, selected, event_kind="death")
            beyond = query_overview(model, None, selected, offset=100)
        self.assertEqual(first["pagination"]["total"], 2)
        self.assertEqual(first["pagination"]["next_offset"], 1)
        self.assertNotEqual(first["review_events"][0]["id"], second["review_events"][0]["id"])
        self.assertEqual(first["totals"], empty["totals"])
        self.assertEqual(first["rounds"], beyond["rounds"])
        self.assertEqual(empty["pagination"]["total"], 0)
        self.assertEqual(beyond["pagination"]["returned"], 0)
        self.assertIsNone(beyond["pagination"]["next_offset"])

    def test_invalid_pagination_is_rejected_before_expensive_round_queries(self):
        model, _, selected = example()
        for limit, offset in [(0, 0), (101, 0), (20, -1)]:
            with self.assertRaises(QueryError) as error:
                query_overview(model, None, selected, limit=limit, offset=offset)
            self.assertEqual(error.exception.code, "invalid_pagination")

    def test_non_jett_ability_summary_is_unknown_not_zero(self):
        model, report, selected = example()
        selected["agent"] = "Fade"
        with patch("valcoach.overview.query_round", return_value=report):
            result = query_overview(model, None, selected)
        self.assertIsNone(result["jett_abilities"]["counts"])
        self.assertIsNone(result["rounds"][0]["jett_abilities"])
        self.assertIn("unsupported", render_overview(result))

    def test_player_groups_reuse_gap_rule_and_exclude_self_damage(self):
        model, report, selected = example()
        hit = report["evidence"]["hit"]
        later = {**hit, "id": "later", "time_ms": 6000, "combat_time_ms": 6000}
        self_hit = {**hit, "id": "self_damage", "victim": 1}
        groups = player_fights(model, report["round"], selected, DEFAULTS, [hit, later, self_hit], [])
        self.assertEqual(len(groups), 2)
        self.assertEqual([g["damage_ids"] for g in groups], [["hit"], ["later"]])

    def test_overview_cli_json_and_pagination_error(self):
        model, report, _ = example()
        for tail, code in [([], 0), (["--limit", "0"], 1)]:
            stdout = io.StringIO()
            with patch("valcoach.__main__.Export"), patch("valcoach.__main__.load_match", return_value=(model, True)), \
                 patch("valcoach.overview.query_round", return_value=report), redirect_stdout(stdout):
                status = main(["overview", "--export", "unused", "--player", "11", "--json", *tail])
            self.assertEqual(status, code)
            response = json.loads(stdout.getvalue())
            if code:
                self.assertEqual(response["error"]["code"], "invalid_pagination")
            else:
                self.assertEqual(response["query"], "overview")
                self.assertTrue(response["cache_reused"])


class PrivateOverviewTests(unittest.TestCase):
    def check_export(self, path, scoreboard, score, death_events):
        source = Export(path)
        model, _ = load_match(source)
        selected = select_player(model, SUBJECT)
        original = copy.deepcopy(model)
        reports = {}
        def collect(model, export, number, player, settings):
            reports[number] = query_round(model, export, number, player, settings)
            return reports[number]
        with patch("valcoach.overview.query_round", side_effect=collect):
            result = query_overview(model, source, selected, limit=100)
        self.assertEqual(tuple(result["scoreboard"][k] for k in ("kills", "deaths", "assists")), scoreboard)
        self.assertEqual((result["score"]["wins"], result["score"]["losses"]), score)
        self.assertEqual(result["totals"]["death_events"], death_events)
        self.assertEqual(result["totals"]["opponent_kills"], scoreboard[0])
        self.assertEqual(result["coverage"]["rounds_total"], len(reports))
        self.assertEqual(model, original)
        events = list(result["review_events"])
        with patch("valcoach.overview.query_round", side_effect=lambda m, e, n, p, s: reports[n]):
            next_offset = result["pagination"]["next_offset"]
            while next_offset is not None:
                page = query_overview(model, source, selected, limit=100, offset=next_offset)
                self.assertEqual(page["totals"], result["totals"])
                events.extend(page["review_events"])
                next_offset = page["pagination"]["next_offset"]
        self.assertEqual(len({(e["kind"], e["id"]) for e in events}), len(events))
        for event in events:
            report = reports[event["round"]]
            self.assertEqual(event["round_time_ms"], event["time_ms"] - report["round"]["live_ms"])
            for ref in event["evidence"]["ids"]:
                self.assertIn(ref, report["evidence"])
        for row in result["rounds"]:
            report = reports[row["number"]]
            hits = [d for d in report["evidence"].values() if d["kind"] == "damage" and d["attacker"] == selected["pawn"]
                    and any(p["pawn"] == d["victim"] and p["team"] != selected["team"] for p in model["players"])]
            self.assertAlmostEqual(row["damage_dealt"]["actual"], sum(d["actual_damage"] for d in hits), places=3)
        json.dumps(result, allow_nan=False)
        rendered = render_overview(result)
        self.assertIn("Round index", rendered)
        self.assertIn("Other participation and support opportunity remain unknown", rendered)
        return result, events

    @unittest.skipUnless((OLD_EXPORT / "manifest.json").is_file(), "Original private export unavailable")
    def test_original_match_scoreboard_rounds_and_retrievable_evidence(self):
        result, _ = self.check_export(OLD_EXPORT, (33, 16, 3), (9, 13), 16)
        self.assertEqual(result["coverage"]["rounds_total"], 22)
        self.assertEqual(result["rounds"][0]["side"], "attacker")
        self.assertEqual(result["rounds"][12]["side"], "defender")

    @unittest.skipUnless((NEW_EXPORT / "manifest.json").is_file(), "Second private export unavailable")
    def test_new_match_preserves_scoreboard_difference_and_confirmed_trade(self):
        result, events = self.check_export(NEW_EXPORT, (30, 16, 4), (11, 13), 17)
        self.assertEqual(result["coverage"]["rounds_total"], 24)
        self.assertEqual(result["scoreboard"]["event_count_difference"]["deaths"], 1)
        self.assertEqual(result["totals"]["self_attributed_deaths"], 1)
        self.assertEqual(result["totals"]["post_round_deaths"], 2)
        fight = next(e for e in events if e["kind"] == "teammate_fight" and e["round"] == 2
                     and e["details"]["teammate"] == 1168 and e["details"]["opponent"] == 968)
        self.assertEqual(fight["details"]["opponent_death"]["weapon"], "Classic")
        self.assertIn(fight["details"]["opponent_death"]["id"], fight["evidence"]["ids"])
        trade = next(e for e in events if e["kind"] == "trade" and e["round"] == 14 and e["details"]["responder"] == 1270)
        self.assertEqual(trade["details"]["delay_ms"], 203)
        death = next(e for e in events if e["kind"] == "death" and e["details"]["self_attributed"])
        self.assertTrue(death["post_round"])
        self.assertEqual(death["details"]["trade_status"], "outside_trade_scope")


if __name__ == "__main__":
    unittest.main()
