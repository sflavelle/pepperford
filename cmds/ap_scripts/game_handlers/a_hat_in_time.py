"""A Hat in Time — game-specific handling extracted from utils.handle_location_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_location_tracking "A Hat in Time"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class AHatInTimeHandler:
    game = "A Hat in Time"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_location_tracking", "handle_item_tracking"})

    def handle_location_tracking(self, itemlog, player, item, use_everywhere):
        location = item.location.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        game_name = player.game
        if location.startswith('Tasksanity') and settings['Tasksanity'] is True:
            total = settings['Tasksanity Check Count']
            return f'{location}/{total}'


    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        if item == 'Time Piece' and (not settings['Death Wish Only']):
            required = 0
            match settings['End Goal']:
                case 'Finale':
                    required = settings['Chapter 5 Cost']
                case 'Rush Hour':
                    required = settings['Chapter 7 Cost']
            return f'{item} (*{count}/{required}*)'
        if item == 'Progressive Painting Unlock':
            required = 3
            return f'{item} ({count}/{required})'
        if item.startswith('Metro Ticket'):
            required = 4
            tickets = ['Yellow', 'Green', 'Blue', 'Pink']
            collected = [player.get_collected_items([f'Metro Ticket - {ticket}' for ticket in tickets])]
            logger.debug(f'Collected tickets: {collected}')
            return f'{item} ({len(collected)}/{required})'
        if item.startswith('Relic'):
            relics = {'Burger': ['Relic (Burger Cushion)', 'Relic (Burger Patty)'], 'Cake': ['Relic (Cake Stand)', 'Relic (Chocolate Cake Slice)', 'Relic (Chocolate Cake)', 'Relic (Shortcake)'], 'Crayon': ['Relic (Blue Crayon)', 'Relic (Crayon Box)', 'Relic (Green Crayon)', 'Relic (Red Crayon)'], 'Necklace': ['Relic (Necklace Bust)', 'Relic (Necklace)'], 'Train': ['Relic (Mountain Set)', 'Relic (Train)'], 'UFO': ['Relic (Cool Cow)', 'Relic (Cow)', 'Relic (Tin-foil Hat Cow)', 'Relic (UFO)']}
            for relic, parts in relics.items():
                if any((part == item for part in parts)):
                    required = len(parts)
                    count = len(player.get_collected_items(parts))
                    return f'{item} ({relic} {count}/{required})'


    def handle_state_tracking(self, player, game):
        settings = player.settings
        slot_data = player.slot_data
        spoiler = game.spoiler_log[str(player)]
        player_game = player.game
        goal = ""
        goal_str = ""
        hats = ['Sprint Hat', 'Brewing Hat', 'Ice Hat', 'Dweller Mask', 'Time Stop Hat']
        collected_hats = player.get_collected_items(hats)
        time_pieces = player.get_item_count('Time Piece')
        time_pieces_required: int
        goal = settings['End Goal']
        match goal:
            case 'Finale':
                goal_str = 'Defeat Mustache Girl'
                time_pieces_required = settings['Chapter 5 Cost']
            case 'Rush Hour':
                goal_str = "Escape Nyakuza Metro's Rush Hour"
                time_pieces_required = settings['Chapter 7 Cost']
            case 'Seal The Deal':
                goal_str = 'Seal the Deal with Snatcher'
            case _:
                goal_str = goal
        if goal == 'Finale' or goal == 'Rush Hour':
            player.stats.set_stat('time_pieces', time_pieces)
            player.stats.set_stat('time_pieces_required', time_pieces_required)
        player.stats.set_stat('found_hats', [hat.name for hat in collected_hats])
        if goal == 'Rush Hour':
            metro_tickets = ['Yellow', 'Pink', 'Green', 'Blue']
            player.stats.set_stat('collected_tickets', [item.name for item in player.get_collected_items([f'Metro Ticket - {color}' for color in metro_tickets])])
        world_costs = {'Kitchen': settings['Chapter 1 Cost'], 'Machine Room': settings['Chapter 2 Cost'], 'Bedroom': settings['Chapter 3 Cost'], 'Boiler Room': settings['Chapter 4 Cost'], 'Attic': settings['Chapter 5 Cost'], 'Laundry': settings['Chapter 6 Cost'], 'Lab': settings['Chapter 7 Cost']}
        player.stats.set_stat('accessible_worlds', [k for k, v in world_costs.items() if time_pieces >= v])
        player.stats.goal_str = goal_str


handler = AHatInTimeHandler()
