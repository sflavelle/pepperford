"""Marble It Up! Ultra — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "Marble It Up! Ultra"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class MarbleItUpUltraHandler:
    game = "Marble It Up! Ultra"
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
        if item == 'Completion Medal':
            chapters = ['Get Moving', 'The Subtle Joy Of Rolling', 'Focus On Flow', 'Kick It Up A Notch', 'Show Me What You Got', 'Play For Keeps']
            final_chapter = settings['Final Chapter']
            chapters_in_play = chapters[:chapters.index(final_chapter)]
            medals_per_unlock = settings['Medals Per Chapter']
            goal_required = len(chapters_in_play[1:]) * medals_per_unlock + 5
            next_required: int
            current_chapter: int
            current_chapter_str: str
            next_chapter: int
            next_chapter_str: str
            for idx, chapter in enumerate(chapters_in_play):
                next_required = idx * medals_per_unlock + 5
                if count >= next_required:
                    continue
                elif count < next_required:
                    current_chapter = idx - 1
                    next_chapter = idx
                    current_chapter_str = chapters_in_play[current_chapter]
                    next_chapter_str = chapters_in_play[next_chapter]
                    break
            next_required_string = f' ({count}/{next_required} to {next_chapter_str})' if current_chapter < len(chapters) - 1 else ''
            return f'{item} (*{count}/{goal_required}*){next_required_string}'
        if item == 'Gold Completion Medal':
            bonus_chapters = ['Keep On Rolling', 'The Way Of The Marble', 'Keep Your Cool', 'Challenge Accepted']
            final_bonus = settings['Bonus Arc Chapters']
            chapters_in_play = bonus_chapters[:bonus_chapters.index(final_bonus)]
            medals_per_unlock = settings['Medals Per Chapter']
            total_required = len(chapters_in_play[0:]) * medals_per_unlock
            next_required: int
            current_chapter: int = 0
            current_chapter_str: str
            next_chapter: int
            next_chapter_str: str
            for idx, chapter in enumerate(chapters_in_play):
                next_required = idx * medals_per_unlock
                if count >= next_required:
                    continue
                elif count < next_required:
                    current_chapter = idx
                    next_chapter = idx + 1
                    current_chapter_str = chapters_in_play[current_chapter]
                    next_chapter_str = chapters_in_play[next_chapter]
                    break
            next_required_string = f' ({count}/{next_required} to {next_chapter_str})' if current_chapter < len(chapters_in_play) - 1 else ''
            return f'{item} (*{count}/{total_required}*){next_required_string}'

handler = MarbleItUpUltraHandler()
