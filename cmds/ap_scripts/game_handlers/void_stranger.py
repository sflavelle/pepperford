"""Void Stranger — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "Void Stranger"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class VoidStrangerHandler:
    game = "Void Stranger"
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
        if item == 'Greed Coin':
            required = 15
            return f'{item} ({count}/{required})'
        if item == 'Locust Idol' or item == 'Lucky Locust Idol':
            OneCount = player.get_item_count('Locust Idol')
            ThreeCount = player.get_item_count('Lucky Locust Idol')
            count = OneCount + ThreeCount * 3
            return f'{item} ({count})'

handler = VoidStrangerHandler()
