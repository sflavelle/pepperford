"""Dark Souls Remastered — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "Dark Souls Remastered"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class DarkSoulsRemasteredHandler:
    game = "Dark Souls Remastered"
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
        if item == 'Progressive Soul Multiplier':
            start_mult = settings['Soul Multiplier - Base Percentage']
            max_mult = settings['Soul Multiplier - Maximum Percentage']
            mult_steps = settings['Soul Multiplier Steps']
            mult_per_step = 1 / mult_steps * (max_mult - start_mult)
            current_mult = start_mult + mult_per_step * count
            return f'{item} ({current_mult}% Soul Gain)'
        if item == 'Progressive Weight Reducer':
            start_weight = settings['Weight Multiplier - Base Percentage']
            min_weight = settings['Weight Multiplier - Minimum Percentage']
            weight_steps = settings['Weight Multiplier Steps']
            reduction_per_step = 1 / weight_steps * (min_weight - start_weight)
            old_weight = start_weight - reduction_per_step * (count - 1)
            current_weight = start_weight - reduction_per_step * count
            return f'{item} ({old_weight}%→{current_weight}% Equipment Weight)'

handler = DarkSoulsRemasteredHandler()
