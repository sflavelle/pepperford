"""Wario Land 4 — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "Wario Land 4"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class WarioLand4Handler:
    game = "Wario Land 4"
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
        if (item.startswith('Golden') and (not item.startswith('Golden Jewel'))) and 'Treasure Hunt' in settings['Goal']:
            count = len([i for i in player.inventory if str(i).startswith('Golden') and (not str(i).startswith('Golden Jewel'))])
            required = settings['Golden Treasure Count']
            return f'{item} ({count}/{required})'
        if item.endswith('Piece'):
            jewels = ['Emerald', 'Entry Jewel', 'Golden Jewel', 'Ruby', 'Sapphire', 'Topaz']
            parts = ['Bottom Left', 'Bottom Right', 'Top Left', 'Top Right']
            jewel = next((j for j in jewels if j in item))
            jewel_count = len(player.get_collected_items([f'{part} {jewel} Piece' for part in parts]))
            jewel_pieces = 4

            def determine_complete_jewels() -> int:
                """Helper function for WL4 jewels. Returns how many jewels have all pieces collected."""
                completed = 0
                for j in jewels:
                    bottom_left = player.get_item_count(f'Bottom Left {j} Piece')
                    bottom_right = player.get_item_count(f'Bottom Right {j} Piece')
                    top_left = player.get_item_count(f'Top Left {j} Piece')
                    top_right = player.get_item_count(f'Top Right {j} Piece')
                    if all([bottom_left > 0, bottom_right > 0, top_left > 0, top_right > 0]):
                        completed += min([bottom_left, bottom_right, top_left, top_right])
                return completed
            jewels_complete = determine_complete_jewels()
            jewels_required = settings['Required Jewels']
            return f'{item} ({jewels_complete}/{jewels_required}C)'

handler = WarioLand4Handler()
