"""Readable firing observations, without inferred aim or hit/miss judgments."""

from .common import amount,seconds,table


def render_gunfire(result):
    data=result["observations"]
    rows=[(seconds(r["round_time_ms"]),r["weapon"] or r["item_class"] or "unknown",r["item_kind"],
           amount(r["ammo_remaining"]),amount(r["num_projectiles"]),amount(r["burst_shot_number"]),len(r["attack_vectors"]),
           len(r["nearby_damage"]["damage_ids"]),r["id"]) for r in data["records"]]
    return "\n".join([f"# Firing observations — round {result['round']}","",
        *table(["Time","Item","Kind","Ammo after firing","Projectiles","Sequence index","Vectors","Nearby damage records","Evidence"],rows),"",
        f"Returned {data['returned']}/{data['total']}; next offset {data['next_offset']}.","",
        *("- "+line for line in result["limitations"])])+"\n"
