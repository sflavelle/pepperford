"""Jigsaw — game-specific handling extracted from utils.fetch_islocation_checkable.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check is_location_checkable "Jigsaw"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class JigsawHandler:
    game = "Jigsaw"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_item_tracking", "handle_location_tracking", "is_location_checkable"})

    def is_location_checkable(self, location):
        return True


    def handle_location_tracking(self, itemlog, player, item, use_everywhere):
        location = item.location.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        game_name = player.game
        if location.startswith('Merge'):
            count = player.collected_locations
            dimensions = settings['Puzzle dimension'].split('×')
            required = int(dimensions[0]) * int(dimensions[1])
            return f'{location} (of {required})'


    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        if item.endswith('Puzzle Pieces'):
            starting_pieces: int = int(settings['Precollected pieces']) if settings['Precollected pieces'] else 0
            pieces_per_item: int = int(item.split()[0])
            item_count: int = player.get_item_count(item)
            total_pieces = starting_pieces + pieces_per_item * item_count
            return f'{item} ({total_pieces} Available)'


handler = JigsawHandler()
