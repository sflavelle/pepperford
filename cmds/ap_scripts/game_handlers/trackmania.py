"""Trackmania — game-specific handling extracted from utils.handle_location_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_location_tracking "Trackmania"`) runs the old arm
against this handler.
"""

from __future__ import annotations

from cmds.ap_scripts.game_handlers import ClassificationProposal

import logging
import math
import re

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")

# TitlePack -> the community map repository that hosts maps of that game. The uploaded JSON carries
# the map's TitlePack ("which game was this map made for") next to its MapId, so the link follows from
# the two of them (splatsune, 2026-10-22). TMStadium is the TM2020 stadium titlepack; the ManiaPlanet
# environments share a host. TMNF/TMUF are not supported by OpenPlanet yet, so their hosts are not
# guessed here.
EXCHANGE_HOSTS = {
    "Trackmania": "trackmania.exchange",
    "TMStadium": "trackmania.exchange",
    "TMValley": "tm.mania-exchange.com",
    "TMCanyon": "tm.mania-exchange.com",
    "TMLagoon": "tm.mania-exchange.com",
}


def exchange_url(titlepack, mapid):
    """The map's page on its community repository, or None when the titlepack is not known."""
    host = EXCHANGE_HOSTS.get(str(titlepack))
    if not host:
        return None
    return f"https://{host}/mapshow/{mapid}"


class TrackmaniaHandler:
    game = "Trackmania"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_item_tracking", "handle_location_tracking", "resolve_classification"})

    def handle_location_tracking(self, itemlog, player, item, use_everywhere):
        location = item.location.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        game_name = player.game
        if player.has_uploaded_data():
            match_regex = re.compile('^Series (\\d+) Map (\\d+) - ([a-zA-Z]+) Time$')
            if (match := match_regex.match(location)):
                seriesnum = int(match.groups()[0])
                mapnum = int(match.groups()[1])
                medal = match.groups()[2]
                try:
                    mapinfo = player.upload_data['world'][seriesnum - 1]['maps'][mapnum - 1]['mapInfo']
                    mapname = mapinfo['Name']
                    mapid = mapinfo['MapId']
                    title = mapinfo['TitlePack']
                    url = exchange_url(title, mapid)
                    if medal == 'Target':
                        medaltime_raw = player.upload_data['world'][seriesnum - 1]['maps'][mapnum - 1]['targetTime']
                    else:
                        medaltime_raw = mapinfo['Medals'][medal]
                    seconds: float = float(medaltime_raw / 1000) % 60
                    minutes: int = math.floor(medaltime_raw / 1000 / 60)
                    if url is not None:
                        return f'S{seriesnum}M{mapnum}: [{mapname}](<{url}>) - {medal} Time ({minutes}:{seconds:06.3f})'
                    else:
                        logger.warning(f'Map name {mapname} has no URL! Titlepack: {title}')
                        return f"S{seriesnum}M{mapnum}: {mapname} - {medal} Time ({(f'{minutes}:' if minutes > 0 else '')}{seconds:06.3f})"
                except IndexError:
                    logger.warning(f'{player.name} ({location}): Uploaded data does not cover this series')
                    return location


    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        medals = ['Bronze Medal', 'Silver Medal', 'Gold Medal', 'Author Medal']
        if itemlog.has_spoiler:
            target_difficulty = settings['Target Time Difficulty']
        else:
            target_difficulty = slot_data['TargetTimeSetting'] * 100
        progression_medal_lookup = target_difficulty // 100
        progression_medal = medals[progression_medal_lookup]
        if item == progression_medal:
            total = len([l for l in itemlog.spoiler_log[str(player)].values() if l.location.name.endswith('Target Time')])
            required = sum([t['MedalTotal'] for t in slot_data['SeriesData']])
            next_requirement: int = 0
            for series in slot_data['SeriesData']:
                next_requirement += series['MedalTotal']
                if next_requirement >= count:
                    break
            return f"{item} ({count}{(f'/{next_requirement}' if next_requirement < required else '')}/{required})"


    def resolve_classification(self, ctx, item, stored=None):
        setting = item.receiver.settings
        response = stored
        medals = ["Bronze Medal", "Silver Medal", "Gold Medal", "Author Medal"]
        # TMAP: "the quickest medal equal to or below target difficulty is the progression medal".
        if getattr(ctx.itemlog, "has_spoiler", False):
            target_difficulty = setting.get("Target Time Difficulty")
        else:
            target_difficulty = item.receiver.slot_data.get("TargetTimeSetting")
            target_difficulty = int(target_difficulty * 100) if target_difficulty is not None else None
        if target_difficulty is None:
            return None  # no target difficulty known: leave the row for the generic fallback
        lookup = target_difficulty // 100
        progression_medal = medals[lookup]
        filler_medals = [m for i, m in enumerate(medals) if i != lookup]
        if item.name == progression_medal:
            response = "progression"
        elif item.name in filler_medals:
            response = "filler"
        if response == stored:
            return None
        return ClassificationProposal(classification=response, reason="only the target-difficulty medal is progression")

def _selftest() -> int:
    """`python -m cmds.ap_scripts.game_handlers.trackmania` — exchange links and one rendering."""
    import types

    failures = []
    cases = {
        "Trackmania": "https://trackmania.exchange/mapshow/123",
        "TMStadium": "https://trackmania.exchange/mapshow/123",
        "TMValley": "https://tm.mania-exchange.com/mapshow/123",
        "TMCanyon": "https://tm.mania-exchange.com/mapshow/123",
        "TMLagoon": "https://tm.mania-exchange.com/mapshow/123",
    }
    for titlepack, expected in cases.items():
        got = exchange_url(titlepack, 123)
        if got != expected:
            failures.append(f"{titlepack}: {got} != {expected}")
    if exchange_url("TMNationsForever", 1) is not None:
        failures.append("an unsupported titlepack must not invent a host")

    def render(titlepack):
        upload = {
            "world": [
                {
                    "maps": [
                        {
                            "mapInfo": {
                                "Name": "Tech Valley",
                                "MapId": "42",
                                "TitlePack": titlepack,
                                "Medals": {"Gold": 95000},
                            },
                            "targetTime": 90000,
                        }
                    ]
                }
            ]
        }
        player = types.SimpleNamespace(
            name="TM", game="Trackmania", settings={}, slot_data={}, upload_data=upload,
            has_uploaded_data=lambda: True,
        )
        item = types.SimpleNamespace(
            location=types.SimpleNamespace(name="Series 1 Map 1 - Target Time")
        )
        log = types.SimpleNamespace(spoiler_log={str(player): {}})
        return TrackmaniaHandler().handle_location_tracking(log, player, item, False)

    linked = render("TMValley")
    expected = (
        "S1M1: [Tech Valley](<https://tm.mania-exchange.com/mapshow/42>)"
        " - Target Time (1:30.000)"
    )
    if linked != expected:
        failures.append(f"rendering with a link: {linked!r} != {expected!r}")

    plain = render("SomethingElse")
    expected_plain = "S1M1: Tech Valley - Target Time (1:30.000)"
    if plain != expected_plain:
        failures.append(f"rendering without a link: {plain!r} != {expected_plain!r}")

    for f in failures:
        print("FAIL:", f)
    print(f"trackmania self-test: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    import sys

    sys.exit(_selftest())

handler = TrackmaniaHandler()
