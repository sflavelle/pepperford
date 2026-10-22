"""DOOM II — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "DOOM II"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class DOOMIIHandler:
    game = "DOOM II"
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
        if item.endswith(' - Complete'):
            count = len([i for i in player.inventory if str(i).endswith(' - Complete')])
            required = 0
            if settings['Episode 1'] is True:
                required = required + 11
            if settings['Episode 2'] is True:
                required = required + 9
            if settings['Episode 3'] is True:
                required = required + 10
            if settings['Secret Levels'] is True:
                required = required + 2
            return f'{item} ({count}/{required})'

handler = DOOMIIHandler()
