"""Read a vrfkit export, preserving source row identities before filtering."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


class QueryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def value(row: dict):
    for key in ("value_i64", "value_f64", "value_str", "value_bool"):
        if row.get(key) is not None:
            return row[key]
    return None


def packed_guid(raw: bytes | None) -> int | None:
    """Unreal packed integer: low bit means another byte follows."""
    if not raw:
        return None
    result = 0
    for index, byte in enumerate(raw):
        if index >= 5:
            return None
        result |= (byte >> 1) << (index * 7)
        if not byte & 1:
            return result
    return None


def provenance(row: dict, table: str = "fields") -> dict:
    result = {"table": table, "row": row["source_row"]}
    for key in ("packet_id", "actor_net_guid", "object_net_guid", "field_name"):
        if row.get(key) is not None:
            result[key] = row[key]
    return result


def read_rows(path: Path, columns: list[str] | None = None) -> list[dict]:
    table = pq.read_table(path, columns=columns)
    table = table.append_column("source_row", pa.array(range(len(table)), type=pa.int64()))
    return table.to_pylist()


FIELD_PATTERN = (
    r"^(Subject|AssignedTeamState|SpawnedCharacter|PlayerState|TeamRole|InitialRole|"
    r"ClientBuyPhaseEnd|CurrentDefuser|Owner|CurrentEquippable|NewCurrentEquippable|"
    r"bUltimateActive)$|^RoundResults\[|^MulticastNotifyDamage|"
    r"^BombPlantedRPC\.|^BombDefusedRPC\.|^MulticastReceivePlayerResurrectEvent\.|"
    r"^AbilityCastsThisRound\["
)

# Explicitly exercised exports; new parser branches are not automatically enabled.
SUPPORTED_BUILDS = {"++Ares-Core+release-13.06", "++Ares-Core+release-13.08"}


class Export:
    TABLES = ("fields", "actors", "events", "net_guids", "movement")

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        expected = [self.root / "manifest.json"] + [self.root / f"{n}.parquet" for n in self.TABLES]
        missing = [p.name for p in expected if not p.is_file()]
        if missing:
            raise QueryError("incomplete_export", f"Missing export files: {', '.join(missing)}")
        try:
            self.manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise QueryError("invalid_manifest", f"Cannot read export manifest: {exc}") from exc
        if self.manifest.get("replay_build") not in SUPPORTED_BUILDS:
            raise QueryError("unsupported_build", "This normalizer accepts only release-13.06 and release-13.08 exports; other builds need explicit validation.")
        # Content hashes, rather than filenames/mtime, define the export revision.
        digest = hashlib.sha256()
        for path in expected:
            digest.update(path.name.encode())
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        self.fingerprint = digest.hexdigest()

    def fields(self) -> list[dict]:
        columns = ["time_ms", "packet_id", "actor_net_guid", "object_net_guid", "group_path",
                   "field_name", "raw_bits", "value_i64", "value_f64", "value_str", "value_bool"]
        table = pq.read_table(self.root / "fields.parquet", columns=columns)
        table = table.append_column("source_row", pa.array(range(len(table)), type=pa.int64()))
        names = table["field_name"].cast(pa.string())
        return table.filter(pc.fill_null(pc.match_substring_regex(names, FIELD_PATTERN), False)).to_pylist()

    def rows(self, name: str) -> list[dict]:
        return read_rows(self.root / f"{name}.parquet")

    def movement(self, start_ms: int, end_ms: int, pawns: list[int]) -> list[dict]:
        columns = ["time_ms", "packet_id", "character_net_guid", "pos_x", "pos_y", "pos_z",
                   "vel_x", "vel_y", "vel_z"]
        table = pq.read_table(self.root / "movement.parquet", columns=columns)
        table = table.append_column("source_row", pa.array(range(len(table)), type=pa.int64()))
        mask = pc.and_(pc.and_(pc.greater_equal(table["time_ms"], start_ms),
                              pc.less(table["time_ms"], end_ms)),
                       pc.is_in(table["character_net_guid"], value_set=pa.array(pawns, type=pa.uint32())))
        return table.filter(mask).to_pylist()
