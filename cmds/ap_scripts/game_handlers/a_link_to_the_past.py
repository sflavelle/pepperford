"""A Link to the Past — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "A Link to the Past"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class ALinkToThePastHandler:
    game = "A Link to the Past"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_item_tracking", "handle_state_tracking"})

    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        if item == 'Triforce Piece' and 'Triforce Hunt' in settings['Goal']:
            required = settings['Triforce Pieces Required']
            return f'{item} (*{count}/{required}*)'
        if item == 'Piece of Heart':
            if count % 4 == 0:
                return f'{item} (+1 Heart Container)'
            else:
                return f'{item} ({count % 4}/4)'
        if item == 'Progressive Sword':
            swords: list[str] = ["Fighter's Sword", 'Master Sword', 'Tempered Sword', 'Golden Sword']
            return f'{item} ({swords[count - 1]})'


    def handle_state_tracking(self, player, game):
        settings = player.settings
        slot_data = player.slot_data
        spoiler = game.spoiler_log[str(player)]
        player_game = player.game
        goal = ""
        goal_str = ""
        if settings['Goal'].endswith('Triforce Hunt'):
            required_pieces = settings['Triforce Pieces Required']
            goal_str = f'Collect {required_pieces} Triforce Pieces'
        elif settings['Goal'] == 'Ganon':
            goal_str = 'Defeat Agahnim 2 and Ganon in the Dark World'
        else:
            if settings['Goal'] == 'Crystals':
                required = settings['Crystals for Ganon']
                goal_str = f'Obtain {required} Crystals, then defeat Ganon in the Dark World'
            elif 'Bosses' in settings['Goal']:
                goal_str = 'Purge Hyrule of dungeon bosses'
            elif 'Pedestal' in settings['Goal']:
                goal_str = 'Prove yourself worthy of pulling the Master Sword from its pedestal'
            if 'Ganon' in settings['Goal'] and settings['Goal'] != 'Ganon':
                goal_str += ', then Defeat Ganon'
        player.stats.goal_str = goal_str


handler = ALinkToThePastHandler()
