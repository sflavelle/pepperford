"""
Simon Tatham's Portable Puzzle Collection — every location posts, even for filler items.

Migrated from the inline rule in ap_itemlog.py (`process_new_log_lines`):

    elif Item.location.game == "Simon Tatham's Portable Puzzle Collection":
        pass

The world is curated so that puzzle completions are the point of the seed (the item log shows
"Puzzle <n> Reward" locations carrying everything from coins to traps), so suppressing the filler
ones hides most of a player's progress. Every send is posted; nothing else about the send changes.
"""

from __future__ import annotations

from cmds.ap_scripts.game_handlers import (
    PROTOCOL_VERSION,
    HandlerContext,
    PostDecision,
    SendEvent,
)


class SimonTathamsHandler:
    game = "Simon Tatham's Portable Puzzle Collection"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_item_tracking", "handle_location_tracking", "is_location_checkable", "should_post_item_send"})

    def should_post_item_send(
        self, ctx: HandlerContext, send: SendEvent
    ) -> PostDecision | None:
        return PostDecision(
            post=True,
            reason="puzzle completions are the seed's content, filler item or not",
            dedupe_key=ctx.dedupe_key("stppc", send.location_name),
        )


    def is_location_checkable(self, location):
        return True


    def handle_location_tracking(self, itemlog, player, item, use_everywhere):
        location = item.location.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        game_name = player.game
        puzzle_id = int(location.split(' ')[1])
        puzzle_setting = slot_data['puzzles'][puzzle_id - 1].split(':')
        if not use_everywhere:
            required = round(settings['puzzle count'] * (settings['Target Completion Percentage'] / 100))
            count = player.collected_locations - settings['starting puzzles']
            return f'{location} ({puzzle_setting[0].title()} {puzzle_setting[1]}) ({count}/{required})'
        else:
            return f'{location} ({puzzle_setting[0]} {puzzle_setting[1]})'


    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        total = settings['puzzle count']
        count = len(player.inventory)
        return f'{item} ({count}/{total})'


handler = SimonTathamsHandler()
