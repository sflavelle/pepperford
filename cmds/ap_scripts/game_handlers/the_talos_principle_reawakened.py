"""The Talos Principle Reawakened — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "The Talos Principle Reawakened"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class TheTalosPrincipleReawakenedHandler:
    game = "The Talos Principle Reawakened"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_item_tracking"})

    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        tetrominos = {'Golden': ['I', 'J', 'L', 'O', 'S', 'T', 'Z'], 'Green': ['I', 'J', 'L', 'T', 'Z'], 'Red': ['I', 'J', 'L', 'O', 'S', 'T', 'Z'], 'White': ['L', 'O', 'S']}
        if item == 'Star':
            required = 10
            return f'{item} ({count}/{required})'
        for t, pieces in tetrominos.items():
            for p in pieces:
                if item == f'{t} {p}':
                    count = sum([player.get_item_count(f'{t} {piece}') for piece in pieces])
                    total = sum([player.get_item_worldtotal(f'{t} {piece}') for piece in pieces])
                    return f'{item} ({count}/{total})'
        if item == 'Purple Sigil':
            required = 24
            return f'{item} ({count}/{required})'

handler = TheTalosPrincipleReawakenedHandler()
