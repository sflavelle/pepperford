"""Celeste (Open World) — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "Celeste (Open World)"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class CelesteOpenWorldHandler:
    game = "Celeste (Open World)"
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
        if item == 'Strawberry':
            total = settings['Total Strawberries']
            required = round(total * (settings['Strawberries Required Percentage'] / 100))
            return f'{item} *({count}/{required})*'
        if item.startswith('The Summit A - Gem'):
            total = 6
            return f'{item} ({count}/{total})'
        if item in ['Left Climb', 'Right Climb']:
            collected = ''
            if player.has_item('Left Climb'):
                collected += '←'
            else:
                collected += '🟥'
            if player.has_item('Right Climb'):
                collected += '→'
            else:
                collected += '🟥'
            return f'{item} ({collected})'
        if item in [f'{dir} Dash' for dir in ['Up', 'Down', 'Left', 'Right', 'Up-Left', 'Up-Right', 'Down-Left', 'Down-Right']]:
            if settings['Dash Shuffle'] == 'Cardinal Loose':
                collected = ''
                for dir, arrow in [('Up', '↑'), ('Down', '↓'), ('Left', '←'), ('Right', '→')]:
                    if player.has_item(f'{dir} Dash'):
                        collected += arrow
                    else:
                        collected += '🟥'
                return f'{item} ({collected})'


    def handle_state_tracking(self, player, game):
        settings = player.settings
        slot_data = player.slot_data
        spoiler = game.spoiler_log[str(player)]
        player_game = player.game
        goal = ""
        goal_str = ""
        goal = settings['Goal Area']
        required_strawberries = settings['Total Strawberries'] * (settings['Strawberries Required Percentage'] / 100)
        goal_with_strawbs = lambda string: string + f' (with {int(required_strawberries)} Strawberries)'
        match goal:
            case 'The Summit A':
                goal_str = goal_with_strawbs('Reach the Summit of Mount Celeste')
            case 'The Summit B':
                goal_str = goal_with_strawbs("Take a Harder Path to Mount Celeste's Summit")
            case 'The Summit C':
                goal_str = goal_with_strawbs("Reach Celeste's Hardest Peak")
            case 'Core A':
                goal_str = goal_with_strawbs('Reach the Heart of the Mountain')
            case 'Core B':
                goal_str = goal_with_strawbs('Understand the Heart of the Mountain')
            case 'Core C':
                goal_str = goal_with_strawbs('Conquer the Heart of the Mountain')
            case 'Empty Space':
                goal_str = goal_with_strawbs('Reach Acceptance?')
            case 'Farewell':
                goal_str = goal_with_strawbs('Bid Farewell')
            case 'Farewell Golden':
                goal_str = goal_with_strawbs("Conquer Farewell's Hardest Challenge")
        player.stats.goal_str = goal_str


handler = CelesteOpenWorldHandler()
