"""
Spyro 3 — Skill Point checks are goal-bearing, so their sends are posted even when the item is filler.

Migrated from the inline rule in ap_itemlog.py (`process_new_log_lines`):

    # By vote of spotzone: if it's filler, don't post it
    # 2026-01-15 amendment: specific locations might be important (goal-bearing),
    # We'll handle individual cases for now but this might become a function
    if Item.location.game == "Spyro 3" and Item.location.name.endswith("(Skill Point)"):
        pass

The logline this protects (real example, #item-log 2026-01-03):

    SaturnTheDragon sent **<:filler:…> Coin (*60/65*)** to **HereComesTheThey**
        (Bentley's Outpost: Push box off the cliff (Skill Point))

Skill Point checks are what the 20-per-level goal counts, so suppressing them as filler hides
player progress. Everything else about the send is unchanged — the release/collect buffers still
apply, and no icon is forced, exactly as when this rule was inline.
"""

from __future__ import annotations

from cmds.ap_scripts.game_handlers import (
    PROTOCOL_VERSION,
    HandlerContext,
    PostDecision,
    SendEvent,
)

SKILL_POINT_SUFFIX = "(Skill Point)"


class Spyro3Handler:
    game = "Spyro 3"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_item_tracking", "handle_location_tracking", "should_post_item_send"})

    def should_post_item_send(
        self, ctx: HandlerContext, send: SendEvent
    ) -> PostDecision | None:
        if send.location_name.endswith(SKILL_POINT_SUFFIX):
            return PostDecision(
                post=True,
                reason="Skill Point checks gate the 20-per-level goal",
                dedupe_key=ctx.dedupe_key("skill-point", send.location_name),
            )
        return None


    def handle_location_tracking(self, itemlog, player, item, use_everywhere):
        location = item.location.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        game_name = player.game
        if 'Skill Point' in location and settings['Completion Goal'] == 'All Skillpoints' and (not use_everywhere):
            total = 20
            count = len([l for l in spoiler.values() if 'Skill Point' in l.location.name and l.found is True])
            return f'{location} (*{count}/{total}*)'


    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        if item == 'Egg':
            total = 150
            return f'{item} ({count}/{total})'
        if item == 'Progressive Sparx Health Upgrade':
            return f'{item} ({count})'


handler = Spyro3Handler()
