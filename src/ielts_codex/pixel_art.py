"""ANSI pixel-art renderer for the survival-spelling game.

The game engine continues to reason in map tiles.  This module expands every
logical tile into a 7 by 6 pixel canvas, then packs two vertical pixels into one
terminal cell with the Unicode upper-half block (``▀``).  The default viewport
is therefore 77 columns by 15 terminal rows.

The renderer is deliberately stateless and has no third-party dependencies.
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
sprite editor provides custom pixel art.  ANSI-free output uses block shading
so the map and silhouettes remain legible in redirected or monochrome output.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import AbstractSet, Final

from .game_engine import GameEngine, GameSnapshot, Position, Tile
from .pet_api import DEFAULT_PET_PALETTE, DEFAULT_PET_SPRITE, PetProfile


TILE_PIXEL_WIDTH: Final = 7
TILE_PIXEL_HEIGHT: Final = 6
VIEWPORT_TILE_WIDTH: Final = 11
VIEWPORT_TILE_HEIGHT: Final = 5
VIEWPORT_COLUMNS: Final = TILE_PIXEL_WIDTH * VIEWPORT_TILE_WIDTH
VIEWPORT_ROWS: Final = TILE_PIXEL_HEIGHT * VIEWPORT_TILE_HEIGHT // 2
ANSI_RESET: Final = "\x1b[0m"

_TRANSPARENT: Final = "."

# ANSI 256-colour indexes.  Restricting the renderer to the standard palette
# keeps output small and works in essentially every colour-capable terminal.
_FOG: Final = 16
_FOG_WISP: Final = 17
_EXPLORED: Final = 234
_EXPLORED_MARK: Final = 236
_VOID: Final = 0
_FLOOR: Final = 22
_FLOOR_LIGHT: Final = 28
_FLOOR_DARK: Final = 23
_WALL: Final = 238
_WALL_LIGHT: Final = 241
_WALL_DARK: Final = 235
_RAIN: Final = 39
_STORM_RAIN: Final = 45
_PLAYER_HAIR: Final = 52
_PLAYER_SKIN: Final = 223
_PLAYER_COAT: Final = 37
_PLAYER_COAT_LIGHT: Final = 44
_PLAYER_LEGS: Final = 24
_PLAYER_BOOTS: Final = 94
_MONSTER_BODY: Final = 88
_MONSTER_BODY_LIGHT: Final = 124
_MONSTER_OUTLINE: Final = 160
_MONSTER_LETTER: Final = 226
_MONSTER_SHADOW: Final = 52

SpriteFrame = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PixelViewport:
    """Viewport dimensions expressed in logical map tiles."""

    width_tiles: int = VIEWPORT_TILE_WIDTH
    height_tiles: int = VIEWPORT_TILE_HEIGHT

    def __post_init__(self) -> None:
        if self.width_tiles <= 0 or self.height_tiles <= 0:
            raise ValueError("Pixel viewport dimensions must be positive.")
        if self.height_tiles * TILE_PIXEL_HEIGHT % 2:
            raise ValueError("Pixel viewport height must contain an even pixel count.")

    @property
    def columns(self) -> int:
        """Return the terminal-cell width of this viewport."""

        return self.width_tiles * TILE_PIXEL_WIDTH

    @property
    def rows(self) -> int:
        """Return the packed terminal-row height of this viewport."""

        return self.height_tiles * TILE_PIXEL_HEIGHT // 2


@dataclass(frozen=True, slots=True)
class PetSpriteData:
    """A validated animated pet sprite.

    Frames contain exactly six strings of seven characters.  ``.`` is
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
    """Render complete animated sprites and terrain into a terminal viewport.

    ``colour`` selects ANSI 256-colour output.  Set it to ``False`` when output
    is redirected or the terminal does not support colour.  Rendering is a pure
    operation: the engine, snapshot, explored set, and pet are never mutated.
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
        player_frame: int | None = None,
    ) -> list[str]:
        """Return the current pixel-art viewport as terminal-ready lines.

        ``animation_tick`` defaults to the weather tick, synchronising rain,
        monster bobbing, the player's walking cycle, and the pet's wagging tail.
        A caller that tracks actual movement can pass ``player_frame`` to choose
        the player's walking frame independently.
        """

        current = engine.snapshot() if snapshot is None else snapshot
        tick = current.weather.tick if animation_tick is None else int(animation_tick)
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
            tick,
        )
        # Lay rain over terrain first so faces, pet eyes, and the monsters'
        # bitmap letters stay readable even in the densest storm.
        _draw_rain(canvas, current, origin, self.viewport, tick)
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
            return _pack_ansi(canvas)
        return _pack_monochrome(canvas, pet_pixels)


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
    frame = tuple(DEFAULT_PET_SPRITE if pet is None else pet.sprite)
    mirrored = tuple(row[::-1] for row in frame)
    return PetSpriteData(
        frames=(frame, mirrored),
        palette={
            str(index): _hex_to_ansi256(colour)
            for index, colour in enumerate(indexed_palette, start=1)
        },
    )


def render_pet_preview(
    pet: PetProfile | PetSpriteData | None,
    *,
    colour: bool = True,
    frame: int = 0,
) -> list[str]:
    """Render a complete seven-by-six companion as three terminal rows."""

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
        _pack_ansi(canvas)
        if colour
        else _pack_monochrome(canvas, pet_pixels)
    )


def render_pixel_viewport(
    engine: GameEngine,
    snapshot: GameSnapshot | None = None,
    pet: PetProfile | PetSpriteData | None = None,
    *,
    explored: AbstractSet[Position] | None = None,
    animation_tick: int | None = None,
    player_frame: int | None = None,
    colour: bool = True,
    viewport: PixelViewport = PixelViewport(),
) -> list[str]:
    """Convenience function returning a complete 77 by 15 default viewport."""

    return PixelArtRenderer(colour=colour, viewport=viewport).render(
        engine,
        snapshot,
        pet,
        explored=explored,
        animation_tick=animation_tick,
        player_frame=player_frame,
    )


_PLAYER_FRAMES: Final[tuple[SpriteFrame, ...]] = (
    (
        "..HH...",
        "..SS...",
        ".CCCC..",
        "cCCCCc.",
        "..P.P..",
        ".K...K.",
    ),
    (
        "..HH...",
        "..SS...",
        ".cCCC..",
        "CCCCCc.",
        ".P..P..",
        "K....K.",
    ),
)

_PLAYER_PALETTE: Final[Mapping[str, int]] = {
    "H": _PLAYER_HAIR,
    "S": _PLAYER_SKIN,
    "C": _PLAYER_COAT,
    "c": _PLAYER_COAT_LIGHT,
    "P": _PLAYER_LEGS,
    "K": _PLAYER_BOOTS,
}

_DOG_FRAMES: Final[tuple[SpriteFrame, ...]] = (
    (
        ".......",
        ".BB....",
        ".BEB..B",
        ".BBBBBB",
        "..B.B..",
        ".b...b.",
    ),
    (
        "......B",
        ".BB..B.",
        ".BEB.B.",
        ".BBBBB.",
        ".B..B..",
        "..b..b.",
    ),
)

_CAT_FRAMES: Final[tuple[SpriteFrame, ...]] = (
    (
        ".B...B.",
        ".BBBBB.",
        ".BEBEB.",
        "..BNB..",
        "..BBB.B",
        ".b...b.",
    ),
    (
        ".B...B.",
        ".BBBBB.",
        ".BEBEB.",
        "..BNB.B",
        "..BBBB.",
        "..b.b..",
    ),
)

_BIRD_FRAMES: Final[tuple[SpriteFrame, ...]] = (
    (
        ".......",
        "...B...",
        "..BEBT.",
        ".BBBB..",
        "..BB...",
        "..b....",
    ),
    (
        ".......",
        "...B...",
        ".BBEBT.",
        "..BBB..",
        "...B...",
        "...b...",
    ),
)

_RABBIT_FRAMES: Final[tuple[SpriteFrame, ...]] = (
    (
        "..B.B..",
        "..B.B..",
        ".BBEB..",
        ".BBBBB.",
        "..B.B..",
        ".b...b.",
    ),
    (
        "..B.B..",
        "..B.B..",
        ".BBEB..",
        "..BBBB.",
        ".B.B...",
        "..b.b..",
    ),
)

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
                    pixels = _explored_tile(world)
                else:
                    pixels = _fog_tile(world, animation_tick)
            elif engine.tile_at(world) is Tile.WALL:
                pixels = _wall_tile(world)
            else:
                pixels = _floor_tile(world)
            _blit_pixels(canvas, pixels, destination_x, destination_y)
    return canvas


def _floor_tile(position: Position) -> list[list[int]]:
    tile = _solid_tile(_FLOOR)
    first = _noise(position.x, position.y, 7) % (TILE_PIXEL_WIDTH * TILE_PIXEL_HEIGHT)
    second = _noise(position.x, position.y, 23) % (
        TILE_PIXEL_WIDTH * TILE_PIXEL_HEIGHT
    )
    for index, colour in ((first, _FLOOR_LIGHT), (second, _FLOOR_DARK)):
        tile[index // TILE_PIXEL_WIDTH][index % TILE_PIXEL_WIDTH] = colour
    return tile


def _wall_tile(position: Position) -> list[list[int]]:
    tile = _solid_tile(_WALL)
    for x in range(TILE_PIXEL_WIDTH):
        tile[0][x] = _WALL_LIGHT
        tile[TILE_PIXEL_HEIGHT - 1][x] = _WALL_DARK
    offset = (_noise(position.x, position.y, 41) % 3) + 1
    for y in (2, 4):
        for x in range(TILE_PIXEL_WIDTH):
            tile[y][x] = _WALL_DARK
    for y in range(TILE_PIXEL_HEIGHT):
        seam = (offset + (3 if y >= 3 else 0)) % TILE_PIXEL_WIDTH
        tile[y][seam] = _WALL_LIGHT
    return tile


def _fog_tile(position: Position, tick: int) -> list[list[int]]:
    tile = _solid_tile(_FOG)
    wisp = (_noise(position.x, position.y, 59) + tick) % (
        TILE_PIXEL_WIDTH * TILE_PIXEL_HEIGHT
    )
    tile[wisp // TILE_PIXEL_WIDTH][wisp % TILE_PIXEL_WIDTH] = _FOG_WISP
    return tile


def _explored_tile(position: Position) -> list[list[int]]:
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
    actor_tick = animation_tick // 2
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
    rows: list[str] = []
    for y, bits in enumerate(bitmap):
        fill = "n" if (y + phase) % 2 else "m"
        row = list(f".M{fill * 3}M.")
        for x, bit in enumerate(bits, start=2):
            if bit == "1":
                row[x] = "L"
        rows.append("".join(row))
    rows.append(".s.M.M." if phase else ".M.M.M.")
    return tuple(rows)


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


def _draw_rain(
    canvas: list[list[int]],
    snapshot: GameSnapshot,
    origin: Position,
    viewport: PixelViewport,
    tick: int,
) -> None:
    intensity = max(0.0, min(1.0, snapshot.weather.rain_intensity))
    if intensity <= 0.0:
        return
    visible = snapshot.visible_positions
    drops_per_tile = 2 if intensity >= 0.72 else 1
    colour = _STORM_RAIN if snapshot.weather.is_storm else _RAIN
    threshold = int(20 + intensity * 72)
    for tile_y in range(viewport.height_tiles):
        for tile_x in range(viewport.width_tiles):
            world = Position(origin.x + tile_x, origin.y + tile_y)
            if world not in visible:
                continue
            if _noise(world.x, world.y, 101) % 100 >= threshold:
                continue
            for drop in range(drops_per_tile):
                seed = _noise(world.x, world.y, 173 + drop * 97)
                drift = tick // 3 if snapshot.weather.is_storm else tick // 6
                local_x = (seed + drift + drop * 3) % TILE_PIXEL_WIDTH
                speed = 2 if snapshot.weather.is_storm else 1
                local_y = (seed // 11 + tick * speed + drop) % TILE_PIXEL_HEIGHT
                pixel_x = tile_x * TILE_PIXEL_WIDTH + local_x
                pixel_y = tile_y * TILE_PIXEL_HEIGHT + local_y
                canvas[pixel_y][pixel_x] = colour
                if snapshot.weather.is_storm and local_y > 0 and local_x > 0:
                    canvas[pixel_y - 1][pixel_x - 1] = colour


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


def _pack_ansi(canvas: Sequence[Sequence[int]]) -> list[str]:
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
                    f"\x1b[38;5;{upper_colour}m"
                    f"\x1b[48;5;{lower_colour}m"
                )
                previous = colours
            pieces.append("▀")
        pieces.append(ANSI_RESET)
        lines.append("".join(pieces))
    return lines


_MONO_LIT: Final[frozenset[int]] = frozenset(
    {
        _WALL_LIGHT,
        _RAIN,
        _STORM_RAIN,
        _PLAYER_HAIR,
        _PLAYER_SKIN,
        _PLAYER_COAT,
        _PLAYER_COAT_LIGHT,
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


def _pack_monochrome(
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
    "TILE_PIXEL_HEIGHT",
    "TILE_PIXEL_WIDTH",
    "VIEWPORT_COLUMNS",
    "VIEWPORT_ROWS",
    "VIEWPORT_TILE_HEIGHT",
    "VIEWPORT_TILE_WIDTH",
    "camera_origin",
    "pet_sprite_for",
    "render_pet_preview",
    "render_pixel_viewport",
]
