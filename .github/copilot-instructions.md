# Copilot Instructions for SplatsunesLittleHelper

## Project Overview
This is a Discord bot (`Splatbot`) built with discord.py for managing Archipelago multiworld randomizer games. It tracks player progress, item collections, and provides commands for game management. The bot integrates with a PostgreSQL database for persistent storage and uses an event-driven architecture for handling game events.

## Architecture
- **Main Bot**: `splatbot.py` - Discord bot setup, extension loading, and global commands
- **Commands**: `cmds/` directory with cogs for different features
  - `archipelago.py`: Core AP game management commands
  - `raocow.py`: YouTube playlist integration
  - `quotes.py`: Quote management
- **AP Scripts**: `cmds/ap_scripts/` - Archipelago-specific utilities
  - `utils.py`: Game, Player, Item classes and database operations
  - `emitter.py`: Event emitter for decoupling event handling
  - `name_translations.py`: Game-specific name mappings

## Key Components
- **Game Class** (`utils.py`): Represents an AP multiworld game with players, locations, items
- **Event System**: Uses `EventEmitter` for item sends, player joins, milestones
- **Database Schema**:
  - `games.{room_id}`: Game settings and classifications
  - `{room_id}_locations`: Location data
  - `{room_id}_items`: Item data  
  - `{room_id}_p_{player}`: Player-specific data

## Critical Workflows
- **Bot Startup**: `python splatbot.py` loads config from `config.yaml` and extensions
- **Extension Reload**: Use `/reload_ext` command for hot-reloading cogs during development
- **Logging**: Check `logs/` directory; adjust log level with `/settings log_level debug`
- **Database**: Requires PostgreSQL connection; bot gracefully degrades if DB unavailable
- **AP Integration**: Fetches logs from AP webhost URLs, parses spoiler logs for item/location data

## Project-Specific Patterns
- **Config Loading**: Global `cfg` dict loaded from `config.yaml` in each module
- **Database Connections**: `sqlcon` established per module with autocommit enabled
- **Role Checks**: `@is_aphost()` and `@is_classifier()` decorators for permission gating
- **Item Classification**: Items categorized in DB for filtering/searching
- **Milestone Tracking**: Automatic 25%/50%/75%/100% completion notifications
- **Event Handling**: Register listeners with `event_emitter.on()` for decoupling
- **Player Slots**: Link Discord users to AP player slots for personalized tracking

## Integration Points
- **Discord API**: Slash commands with `app_commands`, role-based permissions
- **PostgreSQL**: Persistent game state, item classifications, player data
- **AP Webhost**: Log file fetching, spoiler log parsing
- **Home Assistant**: MQTT integration for smart home notifications (optional)
- **External APIs**: YouTube API for raocow playlist fetching

## Development Conventions
- **Error Handling**: Commands use `cog_command_error` for centralized error responses
- **Async Patterns**: Use `asyncio.to_thread()` for blocking DB operations
- **Caching**: Classification cache with 1-hour timeout in `utils.py`
- **Threading**: `ThreadPoolExecutor` for concurrent log processing
- **Pickle Storage**: Temporary log caching in `itemlog-{room_id}-log.pickle`

## Common Tasks
- **Add New Command**: Create method in appropriate cog with `@app_commands.command()`
- **Database Migration**: Update table schemas in `Game.init_db()` method
- **Event Integration**: Emit events via `event_emitter.emit()` with typed event classes
- **AP Data Parsing**: Extend regex patterns in `SpoilerLog.parse_log()` for new games
- **Testing**: Run bot locally, use `/archipelago room status` to verify game tracking</content>
<parameter name="filePath">/home/lily/Documents/Projects/Discord-SplatsunesLittleHelper/.github/copilot-instructions.md