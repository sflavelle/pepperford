"""TMStadium — map details for a Trackmania-family slot.

The inline arm in `utils.handle_location_tracking` was one line:

    case "TMStadium":
        url = f'https://trackmania.exchange/mapshow/{mapid}'

`mapid` is never assigned in that function except inside Trackmania's own arm, so on its own the arm
raises NameError; both worlds read the same uploaded OpenPlanet JSON, so TMStadium gets the same
rendering as Trackmania (map name, exchange link chosen from the map's TitlePack, medal time).
"""

from __future__ import annotations

from cmds.ap_scripts.game_handlers.trackmania import TrackmaniaHandler


class TMStadiumHandler(TrackmaniaHandler):
    game = "TMStadium"
    implements = frozenset({"handle_location_tracking"})


handler = TMStadiumHandler()
