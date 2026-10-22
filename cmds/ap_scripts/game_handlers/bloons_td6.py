"""Bloons TD6 — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "Bloons TD6"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class BloonsTD6Handler:
    game = "Bloons TD6"
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
        if item == 'Medal':
            total = settings['Total Medals']
            required = round(total * (settings['Medal Requirement Percentage'] / 100))
            return f'{item} *({count}/{required})*'


    def handle_state_tracking(self, player, game):
        settings = player.settings
        slot_data = player.slot_data
        spoiler = game.spoiler_log[str(player)]
        player_game = player.game
        goal = ""
        goal_str = ""
        required_medals = settings['Total Medals'] * (settings['Medal Requirement Percentage'] / 100)
        goal_with_medals = lambda string: f'Unlock your final map with {int(required_medals)} Medals, then ' + string
        match settings['Goal']:
            case 'Default':
                goal_str = goal_with_medals(f'Complete it')
            case 'Boss':
                goal_str = goal_with_medals(f'Defeat a randomly chosen Boss Bloon event')
            case 'Elite Boss':
                goal_str = goal_with_medals(f'Defeat a randomly chosen Elite Boss Bloon event')
        player.stats.goal_str = goal_str


handler = BloonsTD6Handler()
