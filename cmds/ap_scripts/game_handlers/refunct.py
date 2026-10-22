"""Refunct — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "Refunct"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class RefunctHandler:
    game = "Refunct"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_item_tracking", "handle_location_hinting"})

    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        if item == 'Grass':
            if len(player.slot_data) > 0:
                total = int(slot_data['amount_grass'])
                required = int(slot_data['required_grass'])
            else:
                total = int(settings['Amount Of Grass'])
                required = round(total * (int(settings['Required Grass Percentage']) / 100))
            return f'{item} ({count}/{required})'
        if item.startswith('Trigger Cluster') or item.startswith('Cluster '):
            count = len([i for i in player.inventory if str(i).startswith('Trigger Cluster ') or str(i).startswith('Cluster ')])
            total = 30
            return f'{item} ({count}/{total})'


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
        minigames = [game for game in settings['Likeliness of minigames'].keys()]
        cubes_setting = settings['Cubes']
        excubes_setting = settings['Extra Cubes']
        if any([location.startswith(minigame) for minigame in minigames]):
            for minigame in minigames:
                if location.startswith(minigame):
                    add_requirement(minigame)
        if location.startswith('Cube') and cubes_setting.endswith('Cubes Bag'):
            add_requirement(cubes_setting)
        if location.startswith('Extra Cube') and excubes_setting.endswith('Cubes Bag'):
            add_requirement(excubes_setting)
        return (requirements, extra_info, costs)


handler = RefunctHandler()
