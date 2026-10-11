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
    implements = frozenset({"should_post_item_send"})

    def should_post_item_send(
        self, ctx: HandlerContext, send: SendEvent
    ) -> PostDecision | None:
        return PostDecision(
            post=True,
            reason="puzzle completions are the seed's content, filler item or not",
            dedupe_key=ctx.dedupe_key("stppc", send.location_name),
        )


handler = SimonTathamsHandler()
