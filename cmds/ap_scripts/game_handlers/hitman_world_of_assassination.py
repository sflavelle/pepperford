"""HITMAN World of Assassination — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "HITMAN World of Assassination"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class HITMANWorldOfAssassinationHandler:
    game = "HITMAN World of Assassination"
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
        if item.startswith('Level - '):
            count = len([i for i in player.inventory if str(i).startswith('Level - ')])
            total = len([i for i in player.spoilers['items'] if str(i).startswith('Level - ')])
            return f'{item} ({count}/{total})'
        if item == 'Contract Piece':
            required = settings['Required Contract Pieces']
            return f'{item} (*{count}/{required}*)'

handler = HITMANWorldOfAssassinationHandler()
