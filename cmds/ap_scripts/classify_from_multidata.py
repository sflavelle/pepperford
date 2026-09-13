#!/usr/bin/env python3
"""
Propose (or apply) item-classification updates for the item log, using a seed's own multidata.

Dry-run by default - it prints what it would change and why, and writes nothing.

Examples
--------
    python classify_from_multidata.py AP_<seed>.zip --summary
    python classify_from_multidata.py AP_<seed>.zip --areas

    # compare against the DB (psycopg2 via PGHOST/PGUSER/PGPASSWORD, or a DSN)
    python classify_from_multidata.py AP_<seed>.zip --dry-run

    # or from a CSV dump, for a machine without psycopg2:
    psql -d bots -c "\\copy (SELECT game, rtrim(item) AS item, classification, datapackage_checksum FROM archipelago.item_classifications) TO 'ic.csv' CSV HEADER"
    python classify_from_multidata.py AP_<seed>.zip --dry-run --db-csv ic.csv

    # record the seed's census into the evidence table (see sql/2026-09-13_room_item_flags.sql)
    python classify_from_multidata.py AP_<seed>.zip --evidence-csv evidence.csv
    psql -d bots -c "\\copy archipelago.room_item_flags (room_id, seed_name, game, item, item_id, flags, instances, datapackage_checksum) FROM 'evidence.csv' CSV HEADER"

    # write the safe decisions (fills, world-version changes, upgrades) - needs write access
    python classify_from_multidata.py AP_<seed>.zip --apply
"""
from __future__ import annotations

import argparse
import collections
import csv
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import multidata  # noqa: E402  (sibling module)


def read_db_csv(path: str) -> dict:
    rows = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r["game"], (r["item"] or "").rstrip())
            rows[key] = (r.get("classification") or None, r.get("datapackage_checksum") or None)
    return rows


def read_db_sql(dsn: str | None = None) -> dict:
    import psycopg2  # only needed on this path
    conn = psycopg2.connect(dsn) if dsn else psycopg2.connect()
    rows = {}
    with conn, conn.cursor() as cur:
        cur.execute("SELECT game, rtrim(item), classification, datapackage_checksum "
                    "FROM archipelago.item_classifications;")
        for game, item, classification, checksum in cur.fetchall():
            rows[(game.strip(), item)] = (classification, checksum)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("multidata", help="path to a .archipelago file or an AP output zip")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--areas", action="store_true", help="per-player area groupings and sphere sizes")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true", help="write the decisions (needs write access)")
    ap.add_argument("--db-csv")
    ap.add_argument("--db-dsn")
    ap.add_argument("--evidence-csv", help="write the seed's census for the evidence table")
    ap.add_argument("--min-instances", type=int, default=1,
                    help="only propose values for items seen at least this many times in the seed")
    ap.add_argument("--limit", type=int, default=25, help="examples to print per category")
    a = ap.parse_args()

    md = multidata.load(a.multidata)
    slots = multidata.slots(md)
    census = multidata.classification_census(md)

    if a.summary or not (a.dry_run or a.apply or a.areas or a.evidence_csv):
        print(f"seed_name      : {md.get('seed_name')}")
        print(f"generator      : {'.'.join(map(str, md.get('version') or ()))}")
        for slot, (name, game) in sorted(slots.items()):
            print(f"  slot {slot}: {name:22} {game:26} locations="
                  f"{len((md.get('locations') or {}).get(slot, {}))} checksum="
                  f"{(multidata.game_checksum(md, game) or '')[:8]}")
        total = sum(sum(c.values()) for c in census.values())
        print(f"items placed   : {total} instances across {len(census)} (game, item) pairs")
        print(f"census verdicts: {multidata.summarise(census)}")
        print(f"spheres        : {len(multidata.spheres(md))}   "
              f"players with area data: {len(multidata.areas(md))}")

    if a.areas:
        for slot, (name, game) in sorted(slots.items()):
            for area, locs in list((multidata.areas(md).get(slot) or {}).items())[:10]:
                print(f"  {name:22} {area:26} {len(locs)} checks")

    if a.evidence_csv:
        names = {slot: {v: k for k, v in multidata.item_names(md, game).items()}
                 for slot, (_n, game) in slots.items()}
        with open(a.evidence_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["room_id", "seed_name", "game", "item", "item_id", "flags",
                        "instances", "datapackage_checksum"])
            for (game, item), counter in sorted(census.items()):
                item_id = next((names[s].get(item) for s, (_n, g) in slots.items() if g == game
                                and names[s].get(item) is not None), "")
                for flags, count in sorted(counter.items()):
                    w.writerow([md.get("seed_name"), md.get("seed_name"), game, item, item_id, flags,
                                count, multidata.game_checksum(md, game)])
        print(f"\nwrote evidence CSV: {a.evidence_csv}")

    if not (a.dry_run or a.apply):
        return 0

    db = read_db_csv(a.db_csv) if a.db_csv else read_db_sql(a.db_dsn)
    print(f"\nDB rows loaded: {len(db)}  (min instances per item: {a.min_instances})")

    fills, upgrades, world_changes, held, unknown, skipped = [], [], [], [], [], []
    for (game, item), counter in sorted(census.items()):
        if sum(counter.values()) < a.min_instances:
            skipped.append((game, item, counter))
            continue
        current, checksum = db.get((game, item), (None, None))
        if (game, item) not in db:
            unknown.append((game, item, counter))
            continue
        derived, _ = multidata.derive(counter)
        propose, why = multidata.decide(current, checksum, counter, multidata.game_checksum(md, game))
        if propose is None:
            held.append((game, item, current, derived, why))
            continue
        (fills if current is None else
         (world_changes if "world version changed" in why else upgrades)).append(
            (game, item, current, propose, why))

    print("\n=== proposed changes ===")
    print(f"  unclassified rows to fill       : {len(fills)}")
    print(f"  world-version changes           : {len(world_changes)}")
    print(f"  upgrades                        : {len(upgrades)}")
    print(f"  held back (no auto-downgrade)   : {len(held)}")
    print(f"  items with no DB row            : {len(unknown)}")
    if skipped:
        print(f"  skipped (fewer than {a.min_instances} instances): {len(skipped)}")
    for label, bucket in (("FILL", fills), ("WORLD", world_changes), ("UPGRADE", upgrades)):
        for game, item, current, propose, why in bucket[:a.limit]:
            print(f"  [{label:7}] {game[:24]:24} {item[:30]:30} {current} -> {propose}   ({why})")

    # group the held conflicts so a human can review a whole class at once
    groups = collections.defaultdict(lambda: {"n": 0, "games": collections.Counter(), "examples": []})
    for game, item, current, derived, why in held:
        if "no automatic downgrade" not in why:
            continue
        g = groups[(current, derived)]
        g["n"] += 1
        g["games"][game] += 1
        if len(g["examples"]) < 3:
            g["examples"].append(item)
    if groups:
        print("\n=== held conflicts by class (review these, don't auto-apply) ===")
        for (current, derived), g in sorted(groups.items(), key=lambda kv: -kv[1]["n"]):
            games = ", ".join(f"{k} x{v}" for k, v in g["games"].most_common(3))
            print(f"  {current} -> {derived}: {g['n']} items   [{games}]")
            print(f"      e.g. {', '.join(g['examples'])}")
    review = [h for h in held if "review by hand" in h[4]]
    for game, item, current, derived, why in review[:a.limit]:
        print(f"  [REVIEW ] {game[:24]:24} {item[:30]:30} keeps {current}   ({why})")

    if a.apply:
        import psycopg2
        conn = psycopg2.connect(a.db_dsn) if a.db_dsn else psycopg2.connect()
        changed = 0
        with conn, conn.cursor() as cur:
            for bucket in (fills, world_changes, upgrades):
                for game, item, _c, propose, _w in bucket:
                    cur.execute("UPDATE archipelago.item_classifications SET classification = %s "
                                "WHERE game = %s AND rtrim(item) = %s;", (propose, game, item))
                    changed += cur.rowcount
        print(f"\napplied: {changed} rows updated")
    else:
        print("\n(dry run - nothing written)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
