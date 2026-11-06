"""Starting-side discovery is tied to round one, never final team roles."""

from copy import deepcopy
import unittest

from valcoach.query import query_players


def match():
    return {
        "match_id": "synthetic", "revision": "synthetic-revision", "replay_build": "synthetic-build",
        "players": [{"id": "attack-jett", "team": 100, "agent": "Jett", "state_id": 11, "pawn": 1},
                    {"id": "defense-jett", "team": 200, "agent": "Jett", "state_id": 22, "pawn": 2}],
        "rounds": [{"number": 1, "live_ms": 1000}, {"number": 13, "live_ms": 9000}],
        "team_roles": [{"team": 100, "time_ms": 50, "role": "attacker"},
                       {"team": 200, "time_ms": 50, "role": "defender"},
                       {"team": 100, "time_ms": 9000, "role": "defender"},
                       {"team": 200, "time_ms": 9000, "role": "attacker"}]
    }


class PlayerDiscoveryTests(unittest.TestCase):
    def test_mirror_agents_keep_distinct_identities_and_starting_sides(self):
        model = match()
        original = deepcopy(model)
        result = query_players(model)
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual([p["id"] for p in result["players"]], ["attack-jett", "defense-jett"])
        self.assertEqual([p["starting_side"] for p in result["players"]], ["attacker", "defender"])
        evidence = result["players"][0]["starting_side_evidence"]
        self.assertEqual(evidence, {"round_number": 1, "at_ms": 1000, "observation_time_ms": 50,
                                   "normalized_team_role_indices": [0], "reason": "observed"})
        self.assertEqual(model, original, "Player discovery must not mutate cached normalized facts")
        self.assertEqual(result["revision"], model["revision"])

    def test_latest_observation_at_live_start_is_used_regardless_of_row_order(self):
        model = match()
        model["team_roles"] = [{"team": 100, "time_ms": 1000, "role": "defender"},
                               {"team": 200, "time_ms": 1000, "role": "attacker"},
                               {"team": 100, "time_ms": 500, "role": "attacker"}]
        result = query_players(model)
        self.assertEqual(result["players"][0]["starting_side"], "defender")
        self.assertEqual(result["players"][0]["starting_side_evidence"]["observation_time_ms"], 1000)

    def test_missing_role_is_not_inferred_from_the_opposing_team_or_halftime(self):
        model = match()
        model["team_roles"] = [role for role in model["team_roles"] if role["team"] == 100 or role["time_ms"] > 1000]
        result = query_players(model)
        self.assertEqual(result["players"][0]["starting_side"], "attacker")
        self.assertIsNone(result["players"][1]["starting_side"])
        self.assertEqual(result["players"][1]["starting_side_evidence"]["reason"], "not_observed")

    def test_later_unknown_does_not_fall_back_to_an_older_known_role(self):
        model = match()
        model["team_roles"].append({"team": 100, "time_ms": 900, "role": None})
        player = query_players(model)["players"][0]
        self.assertIsNone(player["starting_side"])
        self.assertEqual(player["starting_side_evidence"]["reason"], "unmapped_role")

    def test_same_time_conflicts_stay_unknown_with_both_normalized_references(self):
        model = match()
        model["team_roles"].extend([{"team": 100, "time_ms": 500, "role": "attacker"},
                                    {"team": 100, "time_ms": 500, "role": "defender"}])
        player = query_players(model)["players"][0]
        self.assertIsNone(player["starting_side"])
        self.assertEqual(player["starting_side_evidence"]["normalized_team_role_indices"], [4, 5])
        self.assertEqual(player["starting_side_evidence"]["reason"], "conflicting_observations")

    def test_same_role_on_distinct_teams_is_not_a_valid_starting_side_filter(self):
        model = match()
        model["team_roles"][1]["role"] = "attacker"
        players = query_players(model)["players"]
        self.assertTrue(all(p["starting_side"] is None for p in players))
        self.assertTrue(all(p["starting_side_evidence"]["reason"] == "conflicting_teams" for p in players))

    def test_missing_or_ambiguous_round_one_does_not_use_the_first_available_round(self):
        for rounds in ([{"number": 13, "live_ms": 9000}],
                       [{"number": 1, "live_ms": 1000}, {"number": 1, "live_ms": 2000}],
                       [{"number": 1, "live_ms": None}]):
            with self.subTest(rounds=rounds):
                model = match()
                model["rounds"] = rounds
                result = query_players(model)
                self.assertTrue(all(p["starting_side"] is None for p in result["players"]))
                self.assertTrue(all(p["starting_side_evidence"]["reason"] == "round_one_unavailable" for p in result["players"]))

    def test_unknown_team_and_unmapped_agent_identifier_are_preserved(self):
        model = match()
        model["players"][0].update(team=None, agent="Unknown", agent_internal="UnmappedAgent")
        result = query_players(model)["players"][0]
        self.assertEqual(result["id"], "attack-jett")
        self.assertEqual(result["agent_internal"], "UnmappedAgent")
        self.assertIsNone(result["starting_side"])
        self.assertEqual(result["starting_side_evidence"]["reason"], "unknown_team")


if __name__ == "__main__":
    unittest.main()
