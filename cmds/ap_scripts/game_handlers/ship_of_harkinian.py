"""Ship of Harkinian — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "Ship of Harkinian"`) runs the old arm
against this handler.
"""

from __future__ import annotations

from cmds.ap_scripts.game_handlers import ClassificationProposal

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class ShipOfHarkinianHandler:
    game = "Ship of Harkinian"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_item_tracking", "handle_location_hinting", "resolve_classification"})

    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        if item == 'Triforce Piece' and settings['Triforce Hunt'] is True:
            if game == 'Ocarina of Time':
                required = settings['Required Triforce Pieces']
            elif game == 'Ship of Harkinian':
                required = round(settings['Triforce Hunt Pieces Total'] * (settings['Triforce Hunt Pieces Required Percentage'] / 100))
            else:
                pass
            return f'{item} ({count}/{required})'
        if item == 'Gold Skulltula Token':
            required = 50
            return f'{item} ({count}/{required})'
        if item == 'Progressive Wallet':
            capacities = ['200', '500', '999']
            if game == 'Ship of Harkinian' and settings["Shuffle Child's Wallet"] is True:
                capacities.insert(0, '99')
            return f'{item} ({capacities[player.get_item_count(item) - 1]} Capacity)'
        if item == 'Progressive Scale':
            tiers = ['Silver Scale', 'Gold Scale']
            if game == 'Ship of Harkinian' and settings['Shuffle Swim'] is True:
                tiers.insert(0, 'Bronze Scale')
            return f'{item} ({tiers[player.get_item_count(item) - 1]})'
        if item == 'Piece of Heart':
            if count % 4 == 0:
                return f'{item} (+1 Heart Container)'
            else:
                return f'{item} ({count % 4}/4)'
        if item.endswith(' Small Key'):
            return f'{item} ({count})'
        if item.startswith('Ocarina ') and item.endswith(' Button'):
            collected_str = ''
            if player.has_item('Ocarina A Button'):
                collected_str += '🅰️'
            if player.has_item('Ocarina C Down Button') or player.has_item('Ocarina C down Button'):
                collected_str += '🔽'
            if player.has_item('Ocarina C Left Button') or player.has_item('Ocarina C left Button'):
                collected_str += '◀️️'
            if player.has_item('Ocarina C Right Button') or player.has_item('Ocarina C right Button'):
                collected_str += '▶️'
            if player.has_item('Ocarina C Up Button') or player.has_item('Ocarina C up Button'):
                collected_str += '🔼'
            return f'{item} ({collected_str})'


    def handle_location_hinting(self, player, location):
        settings = player.settings
        slot_data = player.slot_data
        requirements = {}
        costs = {}
        extra_info = ""
        l = location
        location = location.name
        def add_requirement(text):
            from cmds.ap_scripts.requirements import parse_requirement_string

            parsed = parse_requirement_string(text)
            if parsed is None:
                requirements[str(text)] = None
            else:
                name, count = parsed
                prev = requirements.get(name)
                requirements[name] = count if prev is None else prev + count
        if bool(player.slot_data) and bool(player.slot_data.get('shop_prices')):
            shop_prices = player.slot_data['shop_prices']
            if location in shop_prices:
                add_requirement(f'{shop_prices[location]} Rupees')
        return (requirements, extra_info, costs)

    def resolve_classification(self, ctx, item, stored=None):
        setting = item.receiver.settings
        response = stored
        if item.name == "Gold Skulltula Token":
            if item.count > 50 and setting["Shuffle 100 GS Reward"] is False:  # no checks after 50
                response = "filler"
            else:
                response = "progression"
        if item.name.startswith("Bottle") or item.name == "Empty Bottle":
            # Bottles are progression when an overworld/big-poe/open-Zora setting needs one:
            # shuffled overworld Skulltulas, the Big Poe sidequest, or an open Zora's Fountain.
            if (
                setting["Shuffle Tokens"] in ["Overworld", "All"]
                or setting["Big Poe Target Count"] > 0
                or setting["Zora's Domain"] == "Open"
            ):
                response = "progression"
            else:
                response = "useful"
        if response == stored:
            return None
        return ClassificationProposal(classification=response, reason="Ship of Harkinian bottle/Skulltula rules")


handler = ShipOfHarkinianHandler()
