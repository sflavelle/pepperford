"""SM64: Spicy Mycena 64 — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "SM64: Spicy Mycena 64"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class SM64SpicyMycena64Handler:
    game = "SM64: Spicy Mycena 64"
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
        if item == 'Power Star':
            required = round(settings['Total Power Stars'] * (settings['Endless Stairs Star %'] / 100))
            return f'{item} ({count}/{required})'
        if item == 'Progressive Bowser Arena Bomb':
            total = 5
            return f'{item} ({count}/{total})'
        if item.startswith('Bowser Arena Bomb'):
            total = 4
            count = len([i for i in player.inventory if str(i).startswith('Bowser Arena Bomb ')])
            return f'{item} ({count}/{total})'

handler = SM64SpicyMycena64Handler()
