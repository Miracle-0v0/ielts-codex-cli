"""ANSI pixel-art renderer for the pocket-adventure spelling game.

The game engine continues to reason in map tiles. This module expands every
logical tile into an 8 by 8 pixel canvas, then packs a 2 by 4 micro-pixel group
into one Unicode Braille cell. The game view combines a 56-column local scene
with a 20-column north-up minimap, so the complete viewport remains 77 columns
by 16 terminal rows while showing substantially more of the field.

The renderer is deliberately stateless and has no third-party dependencies. It
uses an original grassland, tree-canopy, path, and creature palette inspired by
classic handheld monster-adventure interfaces without using their assets.
Its public entry points are:

``PixelArtRenderer.render``
    Render an engine and snapshot into a list of terminal lines.
``render_pixel_viewport``
    Convenience wrapper around :class:`PixelArtRenderer`.
``camera_origin``
    Calculate the clamped top-left map tile for a snapshot.
``pet_sprite_for``
    Turn a ``PetProfile`` (or ``None`` for the built-in dog) into complete,
    animated pixel-sprite data.

Callers may supply :class:`PetSpriteData` directly when an API or a future
sprite editor provides custom pixel art. ANSI-free output uses Braille and block
shading so the map and silhouettes remain legible in redirected or monochrome
output.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import AbstractSet, Final

from .game_engine import GameEngine, GameSnapshot, Position, Tile
from .pet_api import DEFAULT_PET_PALETTE, DEFAULT_PET_SPRITE, PetProfile


TILE_PIXEL_WIDTH: Final = 8
TILE_PIXEL_HEIGHT: Final = 8
# Micro-pixel Braille cells encode two horizontal by four vertical artwork
# pixels. The 8-by-8 art remains intact while each pixel appears smaller.
MICRO_PIXEL_WIDTH: Final = 2
MICRO_PIXEL_HEIGHT: Final = 4
# Fourteen detailed tiles keep the local field at exactly 56 terminal columns;
# eight tiles high retain the 16-row map slot beneath the HUD in an 80-by-24
# shell. This is a two-times wider and taller map view than the full-block mode.
VIEWPORT_TILE_WIDTH: Final = 14
VIEWPORT_TILE_HEIGHT: Final = 8
SCENE_TILE_WIDTH: Final = VIEWPORT_TILE_WIDTH
SCENE_COLUMNS: Final = TILE_PIXEL_WIDTH * VIEWPORT_TILE_WIDTH // MICRO_PIXEL_WIDTH
MINIMAP_COLUMNS: Final = 20
MINIMAP_MAP_ROWS: Final = 10
VIEWPORT_COLUMNS: Final = SCENE_COLUMNS + 1 + MINIMAP_COLUMNS
VIEWPORT_ROWS: Final = TILE_PIXEL_HEIGHT * VIEWPORT_TILE_HEIGHT // MICRO_PIXEL_HEIGHT
ANSI_RESET: Final = "\x1b[0m"

_TRANSPARENT: Final = "."

# ANSI 256-colour indexes.  Restricting the renderer to the standard palette
# keeps output small and works in essentially every colour-capable terminal.
_FOG: Final = 17
_FOG_WISP: Final = 24
_EXPLORED: Final = 22
_EXPLORED_MARK: Final = 28
_VOID: Final = 16
_FLOOR: Final = 22
_FLOOR_LIGHT: Final = 34
_FLOOR_DARK: Final = 28
_PATH: Final = 179
_PATH_LIGHT: Final = 223
_FLOWER: Final = 213
_WALL: Final = 28
_WALL_LIGHT: Final = 70
_WALL_DARK: Final = 22
_TREE_TRUNK: Final = 94
_MINIMAP_VISIBLE_FLOOR: Final = 77
_MINIMAP_VISIBLE_WALL: Final = 28
_MINIMAP_PLAYER: Final = 196
_MINIMAP_PET: Final = 221
_MINIMAP_MONSTER: Final = 147
_PLAYER_CAP: Final = 196
_PLAYER_CAP_LIGHT: Final = 203
_PLAYER_SKIN: Final = 223
_PLAYER_HAIR: Final = 94
_PLAYER_JACKET: Final = 27
_PLAYER_JACKET_LIGHT: Final = 39
_PLAYER_PACK: Final = 94
_PLAYER_LEGS: Final = 67
_PLAYER_BOOTS: Final = 236
_MONSTER_BODY: Final = 99
_MONSTER_BODY_LIGHT: Final = 147
_MONSTER_OUTLINE: Final = 54
_MONSTER_LETTER: Final = 231
_MONSTER_SHADOW: Final = 60

SpriteFrame = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PixelViewport:
    """Viewport dimensions expressed in logical map tiles."""

    width_tiles: int = VIEWPORT_TILE_WIDTH
    height_tiles: int = VIEWPORT_TILE_HEIGHT

    def __post_init__(self) -> None:
        if self.width_tiles <= 0 or self.height_tiles <= 0:
            raise ValueError("Pixel viewport dimensions must be positive.")
        if self.width_tiles * TILE_PIXEL_WIDTH % MICRO_PIXEL_WIDTH:
            raise ValueError(
                "Pixel viewport width must contain complete micro-pixel cells."
            )
        if self.height_tiles * TILE_PIXEL_HEIGHT % MICRO_PIXEL_HEIGHT:
            raise ValueError(
                "Pixel viewport height must contain complete micro-pixel cells."
            )

    @property
    def columns(self) -> int:
        """Return the terminal-cell width of this viewport."""

        return self.width_tiles * TILE_PIXEL_WIDTH // MICRO_PIXEL_WIDTH

    @property
    def rows(self) -> int:
        """Return the packed terminal-row height of this viewport."""

        return self.height_tiles * TILE_PIXEL_HEIGHT // MICRO_PIXEL_HEIGHT


@dataclass(frozen=True, slots=True)
class PetSpriteData:
    """A validated animated pet sprite.

    Frames contain exactly eight strings of eight characters.  ``.`` is
    transparent; every other character must have an ANSI 256-colour entry in
    ``palette``.  Two or more frames can be supplied for animation, although a
    single static frame is also valid.
    """

    frames: tuple[SpriteFrame, ...]
    palette: Mapping[str, int]

    def __post_init__(self) -> None:
        if not self.frames:
            raise ValueError("A pet sprite needs at least one frame.")
        normalized_frames = tuple(tuple(frame) for frame in self.frames)
        for frame in normalized_frames:
            _validate_frame(frame)
            symbols = {pixel for row in frame for pixel in row if pixel != _TRANSPARENT}
            missing = symbols - set(self.palette)
            if missing:
                names = ", ".join(sorted(missing))
                raise ValueError(f"Pet sprite palette is missing symbols: {names}.")
        for symbol, colour in self.palette.items():
            if len(symbol) != 1 or symbol == _TRANSPARENT:
                raise ValueError("Pet sprite palette keys must be one visible symbol.")
            if isinstance(colour, bool) or not isinstance(colour, int):
                raise ValueError("Pet sprite colours must be integer ANSI indexes.")
            if not 0 <= colour <= 255:
                raise ValueError("Pet sprite colours must be between 0 and 255.")
        object.__setattr__(self, "frames", normalized_frames)


@dataclass(frozen=True, slots=True)
class PixelArtRenderer:
    """Render complete animated sprites and terrain into the local scene.

    ``colour`` selects ANSI 256-colour output.  Set it to ``False`` when output
    is redirected or the terminal does not support colour.  Rendering is a pure
    operation: the engine, snapshot, explored set, and pet are never mutated.
    The higher-level :func:`render_pixel_viewport` adds the minimap sidebar.
    """

    colour: bool = True
    viewport: PixelViewport = PixelViewport()

    def render(
        self,
        engine: GameEngine,
        snapshot: GameSnapshot | None = None,
        pet: PetProfile | PetSpriteData | None = None,
        *,
        explored: AbstractSet[Position] | None = None,
        animation_tick: int | None = None,
        fog_tick: int | None = None,
        player_frame: int | None = None,
    ) -> list[str]:
        """Return the current pixel-art viewport as terminal-ready lines.

        ``animation_tick`` defaults to the weather tick, synchronising fog,
        monster bobbing, the player's walking cycle, and the pet's wagging tail.
        ``fog_tick`` may be advanced more slowly to reduce terminal redraws.
        A caller that tracks actual movement can pass ``player_frame`` to choose
        the player's walking frame independently.
        """

        current = engine.snapshot() if snapshot is None else snapshot
        tick = current.weather.tick if animation_tick is None else int(animation_tick)
        mist_tick = tick if fog_tick is None else int(fog_tick)
        origin = camera_origin(engine, current, self.viewport)
        known = set(current.visible_positions)
        if explored is not None:
            known.update(explored)
        canvas = _terrain_canvas(
            engine,
            current,
            origin,
            self.viewport,
            known,
            mist_tick,
        )
        pet_pixels = _draw_entities(
            canvas,
            engine,
            current,
            origin,
            self.viewport,
            pet_sprite_for(pet),
            tick,
            tick if player_frame is None else int(player_frame),
        )
        if self.colour:
            return _pack_micro_ansi(canvas)
        return _pack_micro_monochrome(canvas, pet_pixels)


def camera_origin(
    engine: GameEngine,
    snapshot: GameSnapshot,
    viewport: PixelViewport = PixelViewport(),
) -> Position:
    """Return a player-centred camera origin clamped to the map boundaries."""

    wanted_x = snapshot.player_position.x - viewport.width_tiles // 2
    wanted_y = snapshot.player_position.y - viewport.height_tiles // 2
    max_x = max(0, engine.config.width - viewport.width_tiles)
    max_y = max(0, engine.config.height - viewport.height_tiles)
    return Position(
        min(max(wanted_x, 0), max_x),
        min(max(wanted_y, 0), max_y),
    )


def pet_sprite_for(
    pet: PetProfile | PetSpriteData | None,
) -> PetSpriteData:
    """Return full animated sprite data for a profile or custom sprite.

    The offline default is a small dog. Image-generated profiles supply a
    validated three-colour indexed sprite; the renderer converts those colours
    to the nearest ANSI 256-colour entries and creates a local mirrored walking
    frame. No API-provided terminal control data is ever interpreted.
    """

    if isinstance(pet, PetSpriteData):
        return pet
    indexed_palette = DEFAULT_PET_PALETTE if pet is None else pet.palette
    raw_frame = tuple(DEFAULT_PET_SPRITE if pet is None else pet.sprite)
    frame = _normalise_pet_frame(raw_frame)
    mirrored = tuple(row[::-1] for row in frame)
    return PetSpriteData(
        frames=(frame, mirrored),
        palette={
            str(index): _hex_to_ansi256(colour)
            for index, colour in enumerate(indexed_palette, start=1)
        },
    )


def _normalise_pet_frame(frame: Sequence[str]) -> SpriteFrame:
    """Return a current 8-by-8 frame, preserving compact legacy companions.

    Version 0.4 and 0.5 profiles used seven-by-six indexed sprites.  Rather
    than rewriting a saved profile (which could make later downgrades lose the
    original artwork), add a transparent top/bottom border and one left pixel
    while rendering.  New and API-created profiles already pass through
    unchanged.
    """

    normalized = tuple(frame)
    if (
        len(normalized) == TILE_PIXEL_HEIGHT
        and all(len(row) == TILE_PIXEL_WIDTH for row in normalized)
    ):
        return normalized
    if len(normalized) == 6 and all(len(row) == 7 for row in normalized):
        border = _TRANSPARENT * TILE_PIXEL_WIDTH
        return (border, *(f"{_TRANSPARENT}{row}" for row in normalized), border)
    raise ValueError(
        "Pet profiles must use an 8-by-8 sprite or a legacy 7-by-6 sprite."
    )


def render_pet_preview(
    pet: PetProfile | PetSpriteData | None,
    *,
    colour: bool = True,
    frame: int = 0,
) -> list[str]:
    """Render a complete eight-by-eight companion as four terminal rows."""

    sprite = pet_sprite_for(pet)
    canvas = _solid_tile(_VOID)
    selected = sprite.frames[frame % len(sprite.frames)]
    pet_pixels: set[tuple[int, int]] = set()
    for y, row in enumerate(selected):
        for x, symbol in enumerate(row):
            if symbol != _TRANSPARENT:
                canvas[y][x] = sprite.palette[symbol]
                pet_pixels.add((x, y))
    return (
        _pack_full_ansi(canvas)
        if colour
        else _pack_full_monochrome(canvas, pet_pixels)
    )


def render_pixel_viewport(
    engine: GameEngine,
    snapshot: GameSnapshot | None = None,
    pet: PetProfile | PetSpriteData | None = None,
    *,
    explored: AbstractSet[Position] | None = None,
    animation_tick: int | None = None,
    fog_tick: int | None = None,
    player_frame: int | None = None,
    colour: bool = True,
    viewport: PixelViewport = PixelViewport(
        SCENE_TILE_WIDTH,
        VIEWPORT_TILE_HEIGHT,
    ),
) -> list[str]:
    """Return the local light pool beside a persistent, fog-safe minimap."""

    current = engine.snapshot() if snapshot is None else snapshot
    known = set(current.visible_positions)
    if explored is not None:
        known.update(explored)
    scene = PixelArtRenderer(colour=colour, viewport=viewport).render(
        engine,
        current,
        pet,
        explored=known,
        animation_tick=animation_tick,
        fog_tick=fog_tick,
        player_frame=player_frame,
    )
    minimap = render_minimap(
        engine,
        current,
        explored=known,
        colour=colour,
    )
    if len(scene) != len(minimap):
        raise ValueError("Scene and minimap heights must match.")
    return [
        f"{scene_line} {map_line}"
        for scene_line, map_line in zip(scene, minimap)
    ]


def render_minimap(
    engine: GameEngine,
    snapshot: GameSnapshot | None = None,
    *,
    explored: AbstractSet[Position] | None = None,
    colour: bool = True,
) -> list[str]:
    """Render a north-up minimap without exposing unexplored terrain or actors."""

    current = engine.snapshot() if snapshot is None else snapshot
    visible = set(current.visible_positions)
    known = set(visible)
    if explored is not None:
        known.update(explored)
    known = {
        position
        for position in known
        if 0 <= position.x < engine.config.width
        and 0 <= position.y < engine.config.height
    }

    inner_width = MINIMAP_COLUMNS - 2
    inner_height = MINIMAP_MAP_ROWS
    grid: list[list[tuple[str, int]]] = [
        [("░", _FOG_WISP) for _ in range(inner_width)]
        for _ in range(inner_height)
    ]

    for mini_y in range(inner_height):
        world_y_start = mini_y * engine.config.height // inner_height
        world_y_end = max(
            world_y_start + 1,
            (mini_y + 1) * engine.config.height // inner_height,
        )
        for mini_x in range(inner_width):
            world_x_start = mini_x * engine.config.width // inner_width
            world_x_end = max(
                world_x_start + 1,
                (mini_x + 1) * engine.config.width // inner_width,
            )
            bucket = [
                Position(world_x, world_y)
                for world_y in range(world_y_start, world_y_end)
                for world_x in range(world_x_start, world_x_end)
                if world_x < engine.config.width
                and world_y < engine.config.height
            ]
            visible_bucket = [position for position in bucket if position in visible]
            known_bucket = [position for position in bucket if position in known]
            sample = visible_bucket or known_bucket
            if not sample:
                continue
            walls = sum(engine.tile_at(position) is Tile.WALL for position in sample)
            is_wall = walls * 2 >= len(sample)
            if visible_bucket:
                cell = (
                    "#",
                    _MINIMAP_VISIBLE_WALL
                    if is_wall
                    else _MINIMAP_VISIBLE_FLOOR,
                )
                if not is_wall:
                    cell = ("·", _MINIMAP_VISIBLE_FLOOR)
            else:
                cell = (
                    ("#", _WALL_DARK)
                    if is_wall
                    else ("·", _EXPLORED_MARK)
                )
            grid[mini_y][mini_x] = cell

    def mark(position: Position, glyph: str, colour_index: int) -> None:
        mini_x = min(
            inner_width - 1,
            position.x * inner_width // engine.config.width,
        )
        mini_y = min(
            inner_height - 1,
            position.y * inner_height // engine.config.height,
        )
        grid[mini_y][mini_x] = (glyph, colour_index)

    monster_cells: dict[tuple[int, int], list[str]] = {}
    for monster in engine.active_monsters:
        if monster.position not in visible:
            continue
        mini_x = min(
            inner_width - 1,
            monster.position.x * inner_width // engine.config.width,
        )
        mini_y = min(
            inner_height - 1,
            monster.position.y * inner_height // engine.config.height,
        )
        monster_cells.setdefault((mini_x, mini_y), []).append(
            monster.letter.upper()
        )
    for (mini_x, mini_y), letters in monster_cells.items():
        glyph = letters[0] if len(letters) == 1 else "M"
        grid[mini_y][mini_x] = (glyph, _MINIMAP_MONSTER)
    mark(current.pet_position, "p", _MINIMAP_PET)
    mark(current.player_position, "@", _MINIMAP_PLAYER)

    map_lines = [
        f"│{_render_minimap_row(row, colour=colour)}│"
        for row in grid
    ]
    total_tiles = engine.config.width * engine.config.height
    explored_percent = round(100 * len(known) / total_tiles) if total_tiles else 0
    sidebar = [
        "┌─── FIELD MAP ────┐",
        *map_lines,
        "├──────────────────┤",
        _minimap_panel_line("@ YOU  p PAL"),
        _minimap_panel_line(f"* WILD EXP {explored_percent:>3}%"),
        "└──────────────────┘",
        " " * MINIMAP_COLUMNS,
    ]
    return sidebar


def _render_minimap_row(
    row: Sequence[tuple[str, int]],
    *,
    colour: bool,
) -> str:
    if not colour:
        return "".join(glyph for glyph, _colour in row)
    pieces: list[str] = []
    previous: int | None = None
    for glyph, colour_index in row:
        if colour_index != previous:
            pieces.append(f"\x1b[38;5;{colour_index}m")
            previous = colour_index
        pieces.append(glyph)
    pieces.append(ANSI_RESET)
    return "".join(pieces)


def _minimap_panel_line(content: str) -> str:
    return f"│{content[:18].ljust(18)}│"


_PLAYER_FRAMES: Final[tuple[SpriteFrame, ...]] = (
    (
        "...RR...",
        "..rRRR..",
        "..HSSH..",
        "..HSSH..",
        "...JJ...",
        "..JjBJ..",
        "..L.L...",
        ".K...K..",
    ),
    (
        "...RR...",
        "..rRRR..",
        "..HSSH..",
        "..HSSH..",
        "...JJ...",
        "..JjBJ..",
        ".L...L..",
        "K.....K.",
    ),
)

_PLAYER_PALETTE: Final[Mapping[str, int]] = {
    "R": _PLAYER_CAP,
    "r": _PLAYER_CAP_LIGHT,
    "S": _PLAYER_SKIN,
    "H": _PLAYER_HAIR,
    "J": _PLAYER_JACKET,
    "j": _PLAYER_JACKET_LIGHT,
    "B": _PLAYER_PACK,
    "L": _PLAYER_LEGS,
    "K": _PLAYER_BOOTS,
}

_LETTER_BITMAPS: Final[Mapping[str, tuple[str, ...]]] = {
    "A": ("010", "101", "111", "101", "101"),
    "B": ("110", "101", "110", "101", "110"),
    "C": ("011", "100", "100", "100", "011"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "110", "100", "111"),
    "F": ("111", "100", "110", "100", "100"),
    "G": ("011", "100", "101", "101", "011"),
    "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"),
    "J": ("001", "001", "001", "101", "010"),
    "K": ("101", "101", "110", "101", "101"),
    "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "111", "101", "101"),
    "N": ("101", "111", "111", "111", "101"),
    "O": ("010", "101", "101", "101", "010"),
    "P": ("110", "101", "110", "100", "100"),
    "Q": ("010", "101", "101", "111", "011"),
    "R": ("110", "101", "110", "101", "101"),
    "S": ("011", "100", "010", "001", "110"),
    "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"),
    "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "111", "111", "101"),
    "X": ("101", "101", "010", "101", "101"),
    "Y": ("101", "101", "010", "010", "010"),
    "Z": ("111", "001", "010", "100", "111"),
}

_MONSTER_PALETTE: Final[Mapping[str, int]] = {
    "M": _MONSTER_OUTLINE,
    "m": _MONSTER_BODY,
    "n": _MONSTER_BODY_LIGHT,
    "L": _MONSTER_LETTER,
    "s": _MONSTER_SHADOW,
}


def _terrain_canvas(
    engine: GameEngine,
    snapshot: GameSnapshot,
    origin: Position,
    viewport: PixelViewport,
    explored: AbstractSet[Position],
    animation_tick: int,
) -> list[list[int]]:
    width = viewport.columns
    height = viewport.height_tiles * TILE_PIXEL_HEIGHT
    canvas = [[_VOID for _ in range(width)] for _ in range(height)]
    visible = snapshot.visible_positions
    for tile_y in range(viewport.height_tiles):
        for tile_x in range(viewport.width_tiles):
            world = Position(origin.x + tile_x, origin.y + tile_y)
            destination_x = tile_x * TILE_PIXEL_WIDTH
            destination_y = tile_y * TILE_PIXEL_HEIGHT
            if not (
                0 <= world.x < engine.config.width
                and 0 <= world.y < engine.config.height
            ):
                pixels = _solid_tile(_VOID)
            elif world not in visible:
                if world in explored:
                    pixels = _explored_tile(world, engine.tile_at(world))
                else:
                    pixels = _fog_tile(
                        world,
                        animation_tick,
                        # Keep density tied to the slower fog phase. The
                        # engine's finer-grained weather samples must not
                        # trigger a full terminal redraw on their own.
                        density=0.9 if snapshot.weather.is_storm else 0.35,
                        dense=snapshot.weather.is_storm,
                    )
            elif engine.tile_at(world) is Tile.WALL:
                pixels = _wall_tile(world)
            else:
                pixels = _floor_tile(world)
            _blit_pixels(canvas, pixels, destination_x, destination_y)
    return canvas


def _floor_tile(position: Position) -> list[list[int]]:
    is_path = (position.x - 2 * position.y) % 13 in {0, 1}
    tile = _solid_tile(_PATH if is_path else _FLOOR)
    if is_path:
        # Fine pebbles and alternating sunlit grains make a path read as a
        # continuous trail rather than a flat tan map tile.
        for offset, salt in enumerate((7, 29, 47, 83, 101, 131, 167)):
            noise = _noise(position.x, position.y, salt)
            x = noise % TILE_PIXEL_WIDTH
            y = (noise // TILE_PIXEL_WIDTH + offset) % TILE_PIXEL_HEIGHT
            tile[y][x] = _PATH_LIGHT if offset % 3 else _FLOOR_DARK
        return tile

    # Each blade is two pixels high where possible.  This deliberately leaves
    # breathing room between clumps so the trainer, pet, and letter-creatures
    # stay readable on the small terminal canvas.
    for offset, salt in enumerate((11, 31, 53, 79, 107, 137, 173)):
        noise = _noise(position.x, position.y, salt)
        x = noise % TILE_PIXEL_WIDTH
        y = 1 + (noise // TILE_PIXEL_WIDTH) % (TILE_PIXEL_HEIGHT - 1)
        tile[y][x] = _FLOOR_LIGHT if offset % 3 else _FLOOR_DARK
        if y > 1 and offset % 2 == 0:
            lean = -1 if noise & 1 else 1
            tile[y - 1][max(0, min(TILE_PIXEL_WIDTH - 1, x + lean))] = _FLOOR_LIGHT

    if _noise(position.x, position.y, 71) % 5 == 0:
        flower_x = 1 + _noise(position.x, position.y, 89) % (TILE_PIXEL_WIDTH - 2)
        flower_y = 1 + _noise(position.x, position.y, 97) % (TILE_PIXEL_HEIGHT - 2)
        for offset_x, offset_y in ((0, -1), (-1, 0), (1, 0), (0, 1)):
            tile[flower_y + offset_y][flower_x + offset_x] = _FLOWER
        tile[flower_y][flower_x] = _PATH_LIGHT
    return tile


def _wall_tile(position: Position) -> list[list[int]]:
    """Render an impassable wall as a clustered tree canopy and trunk."""

    tile = _solid_tile(_WALL_DARK)
    for y in range(TILE_PIXEL_HEIGHT - 2):
        for x in range(TILE_PIXEL_WIDTH):
            leaf_noise = _noise(position.x * 3 + x, position.y * 5 + y, 41)
            tile[y][x] = _WALL if leaf_noise % 5 else _WALL_LIGHT
            if leaf_noise % 11 == 0:
                tile[y][x] = _WALL_DARK
    # Bright upper-leaf edges and a narrow trunk give every blocked tile a
    # recognisable tree silhouette, even under the player light radius.
    for x in range(TILE_PIXEL_WIDTH):
        if _noise(position.x, position.y, 61 + x) % 3:
            tile[0][x] = _WALL_LIGHT
    trunk_x = 2 + _noise(position.x, position.y, 83) % 3
    for y in range(TILE_PIXEL_HEIGHT - 3, TILE_PIXEL_HEIGHT):
        for x in range(trunk_x, min(TILE_PIXEL_WIDTH, trunk_x + 2)):
            tile[y][x] = _TREE_TRUNK
    if trunk_x > 0:
        tile[TILE_PIXEL_HEIGHT - 2][trunk_x - 1] = _WALL
    if trunk_x + 2 < TILE_PIXEL_WIDTH:
        tile[TILE_PIXEL_HEIGHT - 2][trunk_x + 2] = _WALL
    return tile


def _fog_tile(
    position: Position,
    tick: int,
    *,
    density: float,
    dense: bool,
) -> list[list[int]]:
    tile = _solid_tile(_FOG)
    area = TILE_PIXEL_WIDTH * TILE_PIXEL_HEIGHT
    wisp_count = min(10, 3 + round(max(0.0, min(1.0, density)) * 5))
    if dense:
        wisp_count = min(10, wisp_count + 2)
    salts = (59, 83, 127, 149, 191, 233, 257, 293, 331, 373)
    for offset, salt in enumerate(salts[:wisp_count]):
        wisp = (
            _noise(position.x, position.y, salt)
            + tick * (offset + 1)
        ) % area
        tile[wisp // TILE_PIXEL_WIDTH][wisp % TILE_PIXEL_WIDTH] = _FOG_WISP
    return tile


def _explored_tile(position: Position, terrain: Tile) -> list[list[int]]:
    if terrain is Tile.WALL:
        tile = _solid_tile(_WALL_DARK)
        for x in range(TILE_PIXEL_WIDTH):
            tile[0][x] = _EXPLORED_MARK
        return tile
    tile = _solid_tile(_EXPLORED)
    mark = _noise(position.x, position.y, 71) % (
        TILE_PIXEL_WIDTH * TILE_PIXEL_HEIGHT
    )
    tile[mark // TILE_PIXEL_WIDTH][mark % TILE_PIXEL_WIDTH] = _EXPLORED_MARK
    return tile


def _solid_tile(colour: int) -> list[list[int]]:
    return [
        [colour for _ in range(TILE_PIXEL_WIDTH)]
        for _ in range(TILE_PIXEL_HEIGHT)
    ]


def _draw_entities(
    canvas: list[list[int]],
    engine: GameEngine,
    snapshot: GameSnapshot,
    origin: Position,
    viewport: PixelViewport,
    pet: PetSpriteData,
    animation_tick: int,
    player_frame: int,
) -> set[tuple[int, int]]:
    visible = snapshot.visible_positions
    pet_pixels: set[tuple[int, int]] = set()
    actor_tick = animation_tick
    for monster in engine.active_monsters:
        if monster.position not in visible:
            continue
        phase = (actor_tick + monster.position.x + monster.position.y) % 2
        _draw_world_sprite(
            canvas,
            _monster_frame(monster.letter, phase),
            _MONSTER_PALETTE,
            monster.position,
            origin,
            viewport,
        )
    if snapshot.pet_position in visible:
        pet_frame = pet.frames[actor_tick % len(pet.frames)]
        _draw_world_sprite(
            canvas,
            pet_frame,
            pet.palette,
            snapshot.pet_position,
            origin,
            viewport,
            visible_pixels=pet_pixels,
        )
    if snapshot.player_position in visible:
        _draw_world_sprite(
            canvas,
            _PLAYER_FRAMES[player_frame % len(_PLAYER_FRAMES)],
            _PLAYER_PALETTE,
            snapshot.player_position,
            origin,
            viewport,
        )
    return pet_pixels


def _monster_frame(letter: str, phase: int) -> SpriteFrame:
    bitmap = _LETTER_BITMAPS.get(letter.upper(), _LETTER_BITMAPS["X"])
    # A compact original letter-creature: ears, round outlined body, shadow,
    # and a five-pixel-tall glyph.  The two phases alternate its ears and feet
    # to create a readable idle bounce at the low redraw rate.
    rows = [
        list("..M..M.." if phase else ".M..M..."),
        list(".MmmmmM."),
        list("MnnnnnnM"),
        list("MnnnnnnM"),
        list("MnnnnnnM"),
        list("MnnnnnnM"),
        list(".M.M..M." if phase else ".M..M.M."),
        list("..s..s.." if phase else ".s....s."),
    ]
    for y, bits in enumerate(bitmap, start=2):
        for x, bit in enumerate(bits, start=2):
            if bit == "1":
                rows[y][x] = "L"
    return tuple("".join(row) for row in rows)


def _draw_world_sprite(
    canvas: list[list[int]],
    frame: Sequence[str],
    palette: Mapping[str, int],
    position: Position,
    origin: Position,
    viewport: PixelViewport,
    *,
    visible_pixels: set[tuple[int, int]] | None = None,
) -> None:
    tile_x = position.x - origin.x
    tile_y = position.y - origin.y
    if not (0 <= tile_x < viewport.width_tiles):
        return
    if not (0 <= tile_y < viewport.height_tiles):
        return
    destination_x = tile_x * TILE_PIXEL_WIDTH
    destination_y = tile_y * TILE_PIXEL_HEIGHT
    for y, row in enumerate(frame):
        for x, symbol in enumerate(row):
            if symbol != _TRANSPARENT:
                pixel = (destination_x + x, destination_y + y)
                canvas[pixel[1]][pixel[0]] = palette[symbol]
                if visible_pixels is not None:
                    visible_pixels.add(pixel)


def _blit_pixels(
    canvas: list[list[int]],
    pixels: Sequence[Sequence[int]],
    destination_x: int,
    destination_y: int,
) -> None:
    for y, row in enumerate(pixels):
        canvas[destination_y + y][
            destination_x : destination_x + len(row)
        ] = row


_BRAILLE_OFFSET: Final = 0x2800
_BRAILLE_BITS: Final[tuple[tuple[int, int], ...]] = (
    (0x01, 0x08),
    (0x02, 0x10),
    (0x04, 0x20),
    (0x40, 0x80),
)


def _pack_micro_ansi(canvas: Sequence[Sequence[int]]) -> list[str]:
    """Pack 2-by-4 artwork pixels into each coloured Unicode Braille cell.

    A terminal cell carries one foreground and one background colour. For a
    micro-cell containing more than two source colours, choose the two that
    best approximate the group in xterm RGB space. This keeps the source 8-by-8
    art intact while reducing each visible pixel's footprint enough to show a
    much wider field.
    """

    _validate_micro_canvas(canvas)
    lines: list[str] = []
    for y in range(0, len(canvas), MICRO_PIXEL_HEIGHT):
        pieces: list[str] = []
        previous: tuple[int, int] | None = None
        for x in range(0, len(canvas[y]), MICRO_PIXEL_WIDTH):
            colours = tuple(
                canvas[y + offset_y][x + offset_x]
                for offset_y in range(MICRO_PIXEL_HEIGHT)
                for offset_x in range(MICRO_PIXEL_WIDTH)
            )
            foreground, background = _micro_cell_colours(colours)
            mask = _micro_braille_mask(colours, foreground, background)
            pair = (foreground, background)
            if pair != previous:
                pieces.append(
                    f"\x1b[38;5;{foreground};48;5;{background}m"
                )
                previous = pair
            pieces.append(chr(_BRAILLE_OFFSET + mask))
        pieces.append(ANSI_RESET)
        lines.append("".join(pieces))
    return lines


def _pack_full_ansi(canvas: Sequence[Sequence[int]]) -> list[str]:
    """Pack vertical pairs at full size for the companion-preview panel."""

    lines: list[str] = []
    for y in range(0, len(canvas), 2):
        upper = canvas[y]
        lower = canvas[y + 1]
        pieces: list[str] = []
        previous: tuple[int, int] | None = None
        for upper_colour, lower_colour in zip(upper, lower):
            colours = (upper_colour, lower_colour)
            if colours != previous:
                pieces.append(
                    f"\x1b[38;5;{upper_colour};48;5;{lower_colour}m"
                )
                previous = colours
            pieces.append("▀")
        pieces.append(ANSI_RESET)
        lines.append("".join(pieces))
    return lines


def _validate_micro_canvas(canvas: Sequence[Sequence[int]]) -> None:
    if not canvas:
        return
    width = len(canvas[0])
    if (
        len(canvas) % MICRO_PIXEL_HEIGHT
        or width % MICRO_PIXEL_WIDTH
        or any(len(row) != width for row in canvas)
    ):
        raise ValueError("Micro-pixel canvas dimensions must divide into 2-by-4 cells.")


def _micro_cell_colours(colours: tuple[int, ...]) -> tuple[int, int]:
    """Return foreground/background ANSI colours with minimum RGB error."""

    unique = tuple(dict.fromkeys(colours))
    if len(unique) == 1:
        return unique[0], unique[0]
    counts = {colour: colours.count(colour) for colour in unique}
    selected: tuple[int, int] | None = None
    selected_key: tuple[int, int, int, int, int] | None = None
    for background in unique:
        for foreground in unique:
            error = sum(
                min(
                    _ansi_colour_distance(colour, background),
                    _ansi_colour_distance(colour, foreground),
                )
                for colour in colours
            )
            key = (
                error,
                -counts[background],
                -counts[foreground],
                background,
                foreground,
            )
            if selected_key is None or key < selected_key:
                selected = (foreground, background)
                selected_key = key
    assert selected is not None
    return selected


def _micro_braille_mask(
    colours: tuple[int, ...],
    foreground: int,
    background: int,
) -> int:
    """Return the Braille-dot mask for pixels assigned to foreground colour."""

    mask = 0
    for offset_y, bits in enumerate(_BRAILLE_BITS):
        for offset_x, bit in enumerate(bits):
            colour = colours[offset_y * MICRO_PIXEL_WIDTH + offset_x]
            if _ansi_colour_distance(colour, foreground) < _ansi_colour_distance(
                colour,
                background,
            ):
                mask |= bit
    return mask


@lru_cache(maxsize=256)
def _ansi256_rgb(colour: int) -> tuple[int, int, int]:
    """Return an ANSI 256-colour index as RGB for local micro-cell matching."""

    ansi_base = (
        (0, 0, 0),
        (128, 0, 0),
        (0, 128, 0),
        (128, 128, 0),
        (0, 0, 128),
        (128, 0, 128),
        (0, 128, 128),
        (192, 192, 192),
        (128, 128, 128),
        (255, 0, 0),
        (0, 255, 0),
        (255, 255, 0),
        (0, 0, 255),
        (255, 0, 255),
        (0, 255, 255),
        (255, 255, 255),
    )
    if 0 <= colour < len(ansi_base):
        return ansi_base[colour]
    if 16 <= colour <= 231:
        offset = colour - 16
        cube = (0, 95, 135, 175, 215, 255)
        return (
            cube[offset // 36],
            cube[(offset % 36) // 6],
            cube[offset % 6],
        )
    if 232 <= colour <= 255:
        gray = 8 + (colour - 232) * 10
        return gray, gray, gray
    return 0, 0, 0


@lru_cache(maxsize=65_536)
def _ansi_colour_distance(first: int, second: int) -> int:
    """Return squared RGB distance between two ANSI palette entries."""

    red_a, green_a, blue_a = _ansi256_rgb(first)
    red_b, green_b, blue_b = _ansi256_rgb(second)
    return (
        (red_a - red_b) ** 2
        + (green_a - green_b) ** 2
        + (blue_a - blue_b) ** 2
    )


_MONO_LIT: Final[frozenset[int]] = frozenset(
    {
        _WALL_LIGHT,
        _MINIMAP_VISIBLE_FLOOR,
        _MINIMAP_VISIBLE_WALL,
        _MINIMAP_PLAYER,
        _MINIMAP_PET,
        _MINIMAP_MONSTER,
        _PLAYER_CAP,
        _PLAYER_CAP_LIGHT,
        _PLAYER_SKIN,
        _PLAYER_HAIR,
        _PLAYER_JACKET,
        _PLAYER_JACKET_LIGHT,
        _PLAYER_PACK,
        _PLAYER_LEGS,
        _PLAYER_BOOTS,
        _MONSTER_OUTLINE,
        _MONSTER_LETTER,
        16,
        67,
        94,
        109,
        130,
        136,
        166,
        173,
        180,
        223,
        231,
    }
)


def _pack_micro_monochrome(
    canvas: Sequence[Sequence[int]],
    forced_lit: AbstractSet[tuple[int, int]] = frozenset(),
) -> list[str]:
    """Render micro-pixel shapes without ANSI colour support."""

    _validate_micro_canvas(canvas)
    lines: list[str] = []
    for y in range(0, len(canvas), MICRO_PIXEL_HEIGHT):
        row: list[str] = []
        for x in range(0, len(canvas[y]), MICRO_PIXEL_WIDTH):
            mask = 0
            colours: list[int] = []
            for offset_y, bits in enumerate(_BRAILLE_BITS):
                for offset_x, bit in enumerate(bits):
                    colour = canvas[y + offset_y][x + offset_x]
                    colours.append(colour)
                    pixel = (x + offset_x, y + offset_y)
                    if pixel in forced_lit or (
                        colour in _MONO_LIT and colour not in {_FOG, _VOID}
                    ):
                        mask |= bit
            if mask:
                row.append(chr(_BRAILLE_OFFSET + mask))
            elif any(colour in {_FOG, _FOG_WISP} for colour in colours):
                row.append("░")
            elif any(colour in {_EXPLORED, _EXPLORED_MARK} for colour in colours):
                row.append("·")
            elif any(colour in {_WALL, _WALL_DARK} for colour in colours):
                row.append("▓")
            else:
                row.append(" ")
        lines.append("".join(row))
    return lines


def _pack_full_monochrome(
    canvas: Sequence[Sequence[int]],
    forced_lit: AbstractSet[tuple[int, int]] = frozenset(),
) -> list[str]:
    lines: list[str] = []
    for y in range(0, len(canvas), 2):
        row: list[str] = []
        for x, (upper, lower) in enumerate(zip(canvas[y], canvas[y + 1])):
            upper_lit = (x, y) in forced_lit or (
                upper in _MONO_LIT and upper not in {_FOG, _VOID}
            )
            lower_lit = (x, y + 1) in forced_lit or (
                lower in _MONO_LIT and lower not in {_FOG, _VOID}
            )
            if upper_lit and lower_lit:
                row.append("█")
            elif upper_lit:
                row.append("▀")
            elif lower_lit:
                row.append("▄")
            elif upper in {_FOG, _FOG_WISP} or lower in {_FOG, _FOG_WISP}:
                row.append("░")
            elif upper in {_EXPLORED, _EXPLORED_MARK} or lower in {
                _EXPLORED,
                _EXPLORED_MARK,
            }:
                row.append("·")
            elif upper in {_WALL, _WALL_DARK} or lower in {_WALL, _WALL_DARK}:
                row.append("▓")
            else:
                row.append(" ")
        lines.append("".join(row))
    return lines


def _validate_frame(frame: Sequence[str]) -> None:
    if len(frame) != TILE_PIXEL_HEIGHT:
        raise ValueError(
            f"Sprite frames must contain exactly {TILE_PIXEL_HEIGHT} rows."
        )
    if any(len(row) != TILE_PIXEL_WIDTH for row in frame):
        raise ValueError(
            f"Sprite rows must contain exactly {TILE_PIXEL_WIDTH} characters."
        )


def _noise(x: int, y: int, salt: int) -> int:
    value = (
        (x * 0x45D9F3B)
        ^ (y * 0x119DE1F3)
        ^ (salt * 0x3449D)
        ^ 0x9E3779B9
    ) & 0xFFFFFFFF
    value ^= value >> 16
    value = (value * 0x45D9F3B) & 0xFFFFFFFF
    value ^= value >> 16
    return value


def _stable_text_number(value: str) -> int:
    number = 0
    for character in value:
        number = (number * 131 + ord(character)) & 0xFFFFFFFF
    return number


def _dark_companion_colour(colour: int) -> int:
    # Nearby low-intensity colours in the 6x6x6 ANSI cube are not always a
    # simple subtraction, so use a compact hand-tuned shadow palette.
    return {
        67: 24,
        94: 52,
        109: 66,
        130: 88,
        136: 94,
        166: 124,
        173: 131,
        180: 138,
    }.get(colour, 52)


def _hex_to_ansi256(value: str) -> int:
    """Return the closest xterm-256 palette entry for a validated hex colour."""

    red = int(value[1:3], 16)
    green = int(value[3:5], 16)
    blue = int(value[5:7], 16)
    candidates: list[tuple[int, int, int, int]] = []
    cube = (0, 95, 135, 175, 215, 255)
    for red_index, cube_red in enumerate(cube):
        for green_index, cube_green in enumerate(cube):
            for blue_index, cube_blue in enumerate(cube):
                index = 16 + 36 * red_index + 6 * green_index + blue_index
                candidates.append((index, cube_red, cube_green, cube_blue))
    for offset in range(24):
        level = 8 + offset * 10
        candidates.append((232 + offset, level, level, level))
    return min(
        candidates,
        key=lambda item: (
            (red - item[1]) ** 2
            + (green - item[2]) ** 2
            + (blue - item[3]) ** 2
        ),
    )[0]


__all__ = [
    "ANSI_RESET",
    "PetSpriteData",
    "PixelArtRenderer",
    "PixelViewport",
    "MICRO_PIXEL_HEIGHT",
    "MICRO_PIXEL_WIDTH",
    "MINIMAP_COLUMNS",
    "SCENE_COLUMNS",
    "TILE_PIXEL_HEIGHT",
    "TILE_PIXEL_WIDTH",
    "VIEWPORT_COLUMNS",
    "VIEWPORT_ROWS",
    "VIEWPORT_TILE_HEIGHT",
    "VIEWPORT_TILE_WIDTH",
    "camera_origin",
    "pet_sprite_for",
    "render_minimap",
    "render_pet_preview",
    "render_pixel_viewport",
]
