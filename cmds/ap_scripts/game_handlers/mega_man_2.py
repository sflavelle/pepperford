"""Mega Man 2 — game-specific handling extracted from utils.handle_location_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_location_tracking "Mega Man 2"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class MegaMan2Handler:
    game = "Mega Man 2"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_location_tracking", "handle_item_tracking"})

    def handle_location_tracking(self, itemlog, player, item, use_everywhere):
        location = item.location.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        game_name = player.game
        if location.endswith(' - Defeated') and (not use_everywhere):
            count = len([l for l in spoiler.values() if l.location.name.endswith(' - Defeated') and l.found is True])
            required = 8
            return f'{location} ({count}/{required})'


    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        if item.endswith('Access Codes'):
            total = 8
            count = len([i for i in player.inventory if str(i).endswith('Access Codes')])
            return f'{item} ({count}/{total})'


handler = MegaMan2Handler()
