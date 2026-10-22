"""Muse Dash — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "Muse Dash"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class MuseDashHandler:
    game = "Muse Dash"
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
        if item == 'Music Sheet':
            song_count = settings['Starting Song Count'] + settings['Additional Song Count']
            total = round(song_count * (settings['Music Sheet Percentage'] / 100))
            required = round(total / (settings['Music Sheets Needed to Win'] / 100))
            return f'{item} ({count}/{required})'

handler = MuseDashHandler()
