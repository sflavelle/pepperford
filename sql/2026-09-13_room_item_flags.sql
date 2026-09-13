-- Evidence table for item classifications observed in real seeds.
--
-- item_classifications holds ONE current value per (game, item), which cannot represent an item whose
-- classification varies by count or by settings (Skulltula Tokens, coins, bottles, TM/HMs). Those seeds
-- are the place the truth lives, so record every seed's census here and derive the aggregate from it.
--
-- Populated by: cmds/ap_scripts/classify_from_multidata.py --evidence-csv <file>
--   \copy archipelago.room_item_flags (room_id, seed_name, game, item, flags, instances, first_seen, last_seen, datapackage_checksum) FROM '<file>' CSV HEADER

CREATE TABLE IF NOT EXISTS archipelago.room_item_flags (
    room_id             varchar(64)  NOT NULL,      -- e.g. the room's seed name
    seed_name           varchar(32),                -- multidata seed_name
    game                bpchar,
    item                bpchar,
    item_id             integer,
    flags               smallint,                   -- ItemClassification.as_flag(): 0 filler 1 progression 2 useful 4 trap
    instances           integer,                    -- how many placements carried that flag in this seed
    first_seen          timestamptz DEFAULT now(),
    last_seen           timestamptz DEFAULT now(),
    datapackage_checksum varchar(64)                -- the world version this observation came from
);

CREATE UNIQUE INDEX IF NOT EXISTS room_item_flags_unique
    ON archipelago.room_item_flags (room_id, game, item, flags);
CREATE INDEX IF NOT EXISTS room_item_flags_lookup
    ON archipelago.room_item_flags (game, item);

-- Handy: the seed-level view of any item, next to what the aggregate currently claims.
CREATE OR REPLACE VIEW archipelago.item_classification_evidence AS
SELECT f.game, f.item, f.flags, sum(f.instances) AS instances,
       count(DISTINCT f.room_id) AS seeds,
       max(f.last_seen) AS last_seen,
       c.classification AS current_classification,
       CASE
         WHEN c.classification IS NULL THEN 'unclassified'
         WHEN c.classification IN ('currency', 'conditional progression', 'mcguffin') THEN 'annotated'
         WHEN f.flags = 1 AND c.classification <> 'progression' THEN 'seed says progression'
         WHEN f.flags <> 1 AND c.classification = 'progression' THEN 'seed disagrees with progression'
         ELSE 'consistent'
       END AS verdict
FROM archipelago.room_item_flags f
LEFT JOIN archipelago.item_classifications c ON c.game = f.game AND rtrim(c.item) = rtrim(f.item)
GROUP BY f.game, f.item, f.flags, c.classification;
