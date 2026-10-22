"""Archipela-Go! — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "Archipela-Go!"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class ArchipelaGoHandler:
    game = "Archipela-Go!"
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
        if settings['Goal'] == 'Long Macguffin' and len(item) == 1:
            items = list('Archipela-Go!')
            collected = [player.get_collected_items(items)]
            collected_string = ''
            for i in items:
                if i in collected:
                    collected_string += i
                else:
                    collected_string += '_'
            return f'{item} ({collected_string})'

handler = ArchipelaGoHandler()
