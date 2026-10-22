"""SMZ3 — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "SMZ3"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class SMZ3Handler:
    game = "SMZ3"
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
        if item == 'ETank':
            tank_capacity = 100
            current_capacity = count * tank_capacity + 100
            return f'{item} ({current_capacity} Capacity)'
        if item == 'Missile':
            capacity_per = 5
            current_capacity = count * capacity_per
            return f'{item} ({current_capacity} Capacity)'
        if item == 'Super':
            capacity_per = 5
            current_capacity = count * capacity_per
            return f'{item} ({current_capacity} Capacity)'
        if item == 'PowerBomb':
            capacity_per = 5
            current_capacity = count * capacity_per
            return f'{item} ({current_capacity} Capacity)'

handler = SMZ3Handler()
