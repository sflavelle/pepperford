"""Haste — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "Haste"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class HasteHandler:
    game = "Haste"
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
        if item == 'Progressive Speed Upgrade':
            base_speed = 0.6
            speed_per_item = 0.1
            total_speed = base_speed + speed_per_item * count
            return f'{item} ({total_speed:.0%} Speed)'
        if item == 'Progressive Shard':
            total = 10
            count = 1 + count
            return f'{item} ({count}/{total})'
        if item == 'Item Rarity Upgrade':
            pass
        if item == 'Max Energy Upgrade':
            pass
        if item == 'Max Health Upgrade':
            pass
        if item == 'Max Lives Upgrade':
            pass
        if item == 'Sparks in Fragments Upgrade':
            pass
        if item == 'Starting Sparks Upgrade':
            pass

handler = HasteHandler()
