"""Deterministic, terminal-agnostic game rules for the experimental game mode.

The engine deliberately contains no input, rendering, networking, or persistence
code.  A UI drives it by calling :meth:`GameEngine.tick` and
:meth:`GameEngine.move`, then renders a :class:`GameSnapshot` and its events.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Callable, Mapping

from .models import Word


@dataclass(frozen=True, slots=True, order=True)
class Position:
    """A zero-based map coordinate."""

    x: int
    y: int

    def moved(self, dx: int, dy: int) -> "Position":
        return Position(self.x + dx, self.y + dy)

    def manhattan_distance(self, other: "Position") -> int:
        return abs(self.x - other.x) + abs(self.y - other.y)


class Direction(Enum):
    """The four movement directions accepted by the game."""

    UP = (0, -1)
    RIGHT = (1, 0)
    DOWN = (0, 1)
    LEFT = (-1, 0)

    @property
    def delta(self) -> tuple[int, int]:
        return self.value

    @classmethod
    def parse(cls, value: "Direction | str") -> "Direction":
        if isinstance(value, cls):
            return value
        aliases = {
            "up": cls.UP,
            "u": cls.UP,
            "w": cls.UP,
            "north": cls.UP,
            "right": cls.RIGHT,
            "r": cls.RIGHT,
            "d": cls.RIGHT,
            "east": cls.RIGHT,
            "down": cls.DOWN,
            "s": cls.DOWN,
            "south": cls.DOWN,
            "left": cls.LEFT,
            "l": cls.LEFT,
            "a": cls.LEFT,
            "west": cls.LEFT,
        }
        try:
            return aliases[str(value).strip().lower()]
        except KeyError as exc:
            raise ValueError(f"Unknown direction: {value!r}") from exc


class Tile(Enum):
    FLOOR = "floor"
    WALL = "wall"


class GameStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    DEAD = "dead"
    EXITED = "exited"

    @property
    def terminal(self) -> bool:
        return self is not GameStatus.RUNNING


class EventType(Enum):
    GAME_STARTED = "game_started"
    MOVED = "moved"
    BLOCKED = "blocked"
    LETTER_DEFEATED = "letter_defeated"
    WRONG_LETTER = "wrong_letter"
    DAMAGE = "damage"
    HUNGRY = "hungry"
    STARVING = "starving"
    FED = "fed"
    DIZZY_STARTED = "dizzy_started"
    HINT_CHANGED = "hint_changed"
    WEATHER_CHANGED = "weather_changed"
    GAME_COMPLETED = "game_completed"
    PLAYER_DIED = "player_died"
    GAME_EXITED = "game_exited"


class HintTier(IntEnum):
    """Increasing hint disclosure; higher tiers include lower-tier information."""

    MEANING = 0
    EXAMPLE = 1
    NEXT_LETTER = 2
    DIRECTION = 3
    LOCATION = 4


@dataclass(frozen=True, slots=True)
class GameEvent:
    """One state transition emitted by the engine.

    ``at`` is seconds since this word round started, not wall-clock time.
    """

    sequence: int
    type: EventType
    at: float
    details: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LetterMonster:
    """One monster in the current letter-selection stage.

    Renderers should not visually distinguish ``is_target``; it exists so the
    rules and pet-navigation layers can identify the correct collision.
    """

    sequence_index: int
    letter: str
    position: Position
    is_target: bool
    defeated: bool = False


@dataclass(frozen=True, slots=True)
class TargetInfo:
    sequence_index: int
    letter: str
    position: Position
    dx: int
    dy: int
    direction: str
    distance: int


@dataclass(frozen=True, slots=True)
class WeatherState:
    """Weather values intended for animation by a UI.

    ``tick`` is the global weather frame. ``storm_tick`` is zero outside a
    storm and one-based within a storm pulse.
    """

    tick: int
    rain_intensity: float
    is_storm: bool
    storm_tick: int


@dataclass(frozen=True, slots=True)
class HintState:
    tier: HintTier
    meaning_zh: str
    part_of_speech: str
    captured_pattern: str
    seconds_without_progress: float
    phonetic: str | None = None
    example_cloze: str | None = None
    next_letter: str | None = None
    direction: str | None = None
    distance: int | None = None
    target_position: Position | None = None


@dataclass(frozen=True, slots=True)
class GameSnapshot:
    status: GameStatus
    player_position: Position
    pet_position: Position
    health: float
    hunger: float
    is_hungry: bool
    is_starving: bool
    is_dizzy: bool
    elapsed_seconds: float
    remaining_seconds: float
    progress_index: int
    target_length: int
    captured_pattern: str
    target: TargetInfo | None
    weather: WeatherState
    hint: HintState
    visible_positions: frozenset[Position]


@dataclass(frozen=True, slots=True)
class GameConfig:
    """Tunable rules shared by a complete game session."""

    width: int = 41
    height: int = 17
    obstacle_density: float = 0.055
    distractor_count: int = 4
    max_health: float = 100.0
    max_hunger: float = 100.0
    hungry_threshold: float = 30.0
    hunger_per_second: float = 0.65
    correct_letter_food: float = 7.0
    wrong_letter_damage: float = 8.0
    wrong_letter_hunger: float = 6.0
    starvation_damage: float = 4.0
    starvation_damage_interval: float = 4.0
    time_limit_seconds: float = 60.0
    dizzy_damage: float = 5.0
    dizzy_damage_interval: float = 5.0
    player_vision_radius: int = 2
    pet_vision_radius: int = 2
    dizzy_vision_penalty: int = 2
    invincible: bool = False
    reveal_map: bool = False
    example_hint_after: float = 5.0
    next_letter_hint_after: float = 10.0
    direction_hint_after: float = 15.0
    location_hint_after: float = 24.0
    weather_tick_seconds: float = 0.4
    storm_cycle_ticks: int = 30
    storm_duration_ticks: int = 5
    rain_min_intensity: float = 0.18
    rain_max_intensity: float = 0.72
    storm_min_intensity: float = 0.82

    def __post_init__(self) -> None:
        if self.width < 9 or self.height < 7:
            raise ValueError("The map must be at least 9 by 7.")
        if not 0.0 <= self.obstacle_density < 0.35:
            raise ValueError("obstacle_density must be in [0, 0.35).")
        positive = {
            "max_health": self.max_health,
            "max_hunger": self.max_hunger,
            "starvation_damage_interval": self.starvation_damage_interval,
            "time_limit_seconds": self.time_limit_seconds,
            "dizzy_damage_interval": self.dizzy_damage_interval,
            "weather_tick_seconds": self.weather_tick_seconds,
            "storm_cycle_ticks": self.storm_cycle_ticks,
            "player_vision_radius": self.player_vision_radius,
            "pet_vision_radius": self.pet_vision_radius,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive.")
        non_negative = {
            "hunger_per_second": self.hunger_per_second,
            "correct_letter_food": self.correct_letter_food,
            "wrong_letter_damage": self.wrong_letter_damage,
            "wrong_letter_hunger": self.wrong_letter_hunger,
            "starvation_damage": self.starvation_damage,
            "dizzy_damage": self.dizzy_damage,
            "example_hint_after": self.example_hint_after,
            "next_letter_hint_after": self.next_letter_hint_after,
            "direction_hint_after": self.direction_hint_after,
            "location_hint_after": self.location_hint_after,
            "dizzy_vision_penalty": self.dizzy_vision_penalty,
        }
        for name, value in non_negative.items():
            if value < 0:
                raise ValueError(f"{name} cannot be negative.")
        hint_times = (
            self.example_hint_after,
            self.next_letter_hint_after,
            self.direction_hint_after,
            self.location_hint_after,
        )
        if hint_times != tuple(sorted(hint_times)):
            raise ValueError("Hint thresholds must be in increasing order.")
        if not 0.0 <= self.hungry_threshold <= self.max_hunger:
            raise ValueError("hungry_threshold must fit within the hunger range.")
        if not 1 <= self.distractor_count <= 25:
            raise ValueError("distractor_count must be in [1, 25].")
        if not isinstance(self.invincible, bool):
            raise ValueError("invincible must be a boolean.")
        if not isinstance(self.reveal_map, bool):
            raise ValueError("reveal_map must be a boolean.")
        if self.storm_duration_ticks < 0:
            raise ValueError("storm_duration_ticks cannot be negative.")
        if self.storm_duration_ticks > self.storm_cycle_ticks:
            raise ValueError("A storm cannot outlast its cycle.")
        if not (0.0 <= self.rain_min_intensity <= self.rain_max_intensity <= 1.0):
            raise ValueError("Rain intensities must be ordered values in [0, 1].")
        if not self.rain_min_intensity <= self.storm_min_intensity <= 1.0:
            raise ValueError("storm_min_intensity must be in [rain_min, 1].")


class GameEngine:
    """A deterministic survival-spelling round for one :class:`Word`.

    Pass a fake ``clock`` in tests.  Supplying the same word, seed, config, and
    clock values produces the same map, monsters, weather, and state changes.
    Health and hunger can be carried from a previous word round through
    ``initial_health`` and ``initial_hunger``.
    """

    def __init__(
        self,
        word: Word,
        *,
        seed: int | str | bytes = 0,
        config: GameConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
        initial_health: float | None = None,
        initial_hunger: float | None = None,
    ) -> None:
        self.word = word
        self.seed = seed
        self.config = config or GameConfig()
        self._clock = clock
        self.target_word = "".join(
            character.lower() for character in word.word if character.isalpha()
        )
        if not self.target_word:
            raise ValueError("The word must contain at least one alphabetic character.")

        self._started_at = float(clock())
        if not math.isfinite(self._started_at):
            raise ValueError("The clock must return a finite monotonic time.")
        self._last_tick_at = self._started_at
        self._last_progress_at = self._started_at
        self._finished_at: float | None = None
        self._event_sequence = 0
        self._events: list[GameEvent] = []
        self._progress_index = 0
        self._starvation_elapsed = 0.0
        self._dizzy_damage_ticks = 0
        self._last_hint_tier = HintTier.MEANING

        configured_health = (
            self.config.max_health if initial_health is None else float(initial_health)
        )
        configured_hunger = (
            self.config.max_hunger if initial_hunger is None else float(initial_hunger)
        )
        if not math.isfinite(configured_health) or not math.isfinite(configured_hunger):
            raise ValueError("Initial health and hunger must be finite.")
        self.health = _clamp(configured_health, 0.0, self.config.max_health)
        if self.config.invincible:
            self.health = self.config.max_health
        self.hunger = _clamp(configured_hunger, 0.0, self.config.max_hunger)
        self.status = GameStatus.RUNNING if self.health > 0.0 else GameStatus.DEAD
        self._is_hungry = self.hunger <= self.config.hungry_threshold
        self._is_starving = self.hunger <= 0.0
        self._is_dizzy = False

        self._walls, spawn_area = self._generate_map()
        self.player_position = min(
            spawn_area,
            key=lambda position: (
                position.manhattan_distance(
                    Position(self.config.width // 2, self.config.height // 2)
                ),
                position.y,
                position.x,
            ),
        )
        # Spawn the companion beside the player so its full sprite is visible
        # from the opening frame. It still follows the player's previous tile.
        self.pet_position = next(
            (
                self.player_position.moved(dx, dy)
                for dx, dy in ((-1, 0), (1, 0), (0, 1), (0, -1))
                if self.player_position.moved(dx, dy) in spawn_area
            ),
            self.player_position,
        )
        self._spawn_area = spawn_area
        self._monsters = self._spawn_stage(0)
        self._weather = self._weather_at_tick(0)

        self._emit(
            EventType.GAME_STARTED,
            target_length=len(self.target_word),
            seed=str(seed),
            width=self.config.width,
            height=self.config.height,
        )
        if self._is_hungry:
            self._emit(EventType.HUNGRY, hunger=self.hunger)
        if self._is_starving:
            self._emit(EventType.STARVING, hunger=self.hunger)
        if self.status is GameStatus.DEAD:
            self._emit(EventType.PLAYER_DIED, cause="initial_health")

    @property
    def walls(self) -> frozenset[Position]:
        return self._walls

    @property
    def monsters(self) -> tuple[LetterMonster, ...]:
        return tuple(self._monsters)

    @property
    def active_monsters(self) -> tuple[LetterMonster, ...]:
        return tuple(monster for monster in self._monsters if not monster.defeated)

    @property
    def progress_index(self) -> int:
        return self._progress_index

    @property
    def captured_pattern(self) -> str:
        captured = self.target_word[: self._progress_index]
        return captured + "_" * (len(self.target_word) - self._progress_index)

    @property
    def captured_letters(self) -> str:
        return self.target_word[: self._progress_index]

    @property
    def elapsed_seconds(self) -> float:
        endpoint = (
            self._finished_at if self._finished_at is not None else self._last_tick_at
        )
        return max(0.0, endpoint - self._started_at)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.config.time_limit_seconds - self.elapsed_seconds)

    @property
    def is_hungry(self) -> bool:
        return self._is_hungry

    @property
    def is_starving(self) -> bool:
        return self._is_starving

    @property
    def is_dizzy(self) -> bool:
        return self._is_dizzy

    @property
    def weather(self) -> WeatherState:
        return self._weather

    @property
    def current_target(self) -> LetterMonster | None:
        if self._progress_index >= len(self.target_word):
            return None
        return next(
            (
                monster
                for monster in self._monsters
                if monster.is_target and not monster.defeated
            ),
            None,
        )

    @property
    def target_info(self) -> TargetInfo | None:
        target = self.current_target
        if target is None:
            return None
        dx = target.position.x - self.player_position.x
        dy = target.position.y - self.player_position.y
        return TargetInfo(
            sequence_index=target.sequence_index,
            letter=target.letter,
            position=target.position,
            dx=dx,
            dy=dy,
            direction=_compass_direction(dx, dy),
            distance=abs(dx) + abs(dy),
        )

    @property
    def target_direction(self) -> str | None:
        target = self.target_info
        return target.direction if target is not None else None

    @property
    def target_distance(self) -> int | None:
        target = self.target_info
        return target.distance if target is not None else None

    @property
    def visible_positions(self) -> frozenset[Position]:
        if self.config.reveal_map:
            return frozenset(
                Position(x, y)
                for y in range(self.config.height)
                for x in range(self.config.width)
            )
        player_radius = self.config.player_vision_radius
        pet_radius = self.config.pet_vision_radius
        if self._is_dizzy:
            player_radius = max(1, player_radius - self.config.dizzy_vision_penalty)
            pet_radius = max(1, pet_radius - self.config.dizzy_vision_penalty)
        return frozenset(
            self._field_of_view(self.player_position, player_radius)
            | self._field_of_view(self.pet_position, pet_radius)
        )

    def tile_at(self, position: Position) -> Tile:
        if not self._in_bounds(position):
            raise ValueError(f"Position is outside the map: {position}")
        return Tile.WALL if position in self._walls else Tile.FLOOR

    def monster_at(self, position: Position) -> LetterMonster | None:
        for monster in self._monsters:
            if not monster.defeated and monster.position == position:
                return monster
        return None

    def is_walkable(self, position: Position) -> bool:
        return self._in_bounds(position) and position not in self._walls

    def tick(self, now: float | None = None) -> tuple[GameEvent, ...]:
        """Advance timers to ``now`` and return only newly emitted events."""

        event_start = len(self._events)
        if self.status.terminal:
            return ()
        current = self._coerce_now(now)
        delta = current - self._last_tick_at
        if delta <= 0.0:
            return ()
        self._last_tick_at = current

        self._advance_hunger(delta)
        if self.status is GameStatus.RUNNING:
            self._advance_dizziness(self.elapsed_seconds)
        if self.status is GameStatus.RUNNING:
            self._advance_weather()
            self._advance_hint_tier()
        return tuple(self._events[event_start:])

    def move(
        self,
        direction: Direction | str,
        *,
        now: float | None = None,
    ) -> tuple[GameEvent, ...]:
        """Move one tile, resolving walls and monster collisions."""

        event_start = len(self._events)
        self.tick(now)
        if self.status.terminal:
            return tuple(self._events[event_start:])

        parsed = Direction.parse(direction)
        dx, dy = parsed.delta
        destination = self.player_position.moved(dx, dy)
        if not self.is_walkable(destination):
            self._emit(
                EventType.BLOCKED,
                position=destination,
                reason="wall_or_boundary",
            )
            return tuple(self._events[event_start:])

        monster = self.monster_at(destination)
        if monster is not None and not monster.is_target:
            expected = self.current_target
            self.hunger = max(0.0, self.hunger - self.config.wrong_letter_hunger)
            self._refresh_hunger_state()
            self._emit(
                EventType.WRONG_LETTER,
                encountered=monster.letter,
                stage=self._progress_index,
                expected=expected.letter if expected else None,
                expected_index=self._progress_index,
                position=destination,
            )
            self._damage(
                self.config.wrong_letter_damage,
                cause="wrong_letter",
            )
            return tuple(self._events[event_start:])

        old_player = self.player_position
        old_pet = self.pet_position
        self.player_position = destination
        self.pet_position = old_player
        self._emit(
            EventType.MOVED,
            direction=parsed.name.lower(),
            origin=old_player,
            destination=destination,
            pet_origin=old_pet,
            pet_destination=self.pet_position,
        )

        if monster is not None:
            self._defeat(monster)
        return tuple(self._events[event_start:])

    def attack(
        self,
        direction: Direction | str,
        *,
        now: float | None = None,
    ) -> tuple[GameEvent, ...]:
        """Alias for collision-based movement, convenient for game UIs."""

        return self.move(direction, now=now)

    def hint_state(self) -> HintState:
        """Return the currently unlocked teaching hints without advancing time."""

        idle = max(0.0, self._last_tick_at - self._last_progress_at)
        tier = self._hint_tier_for(idle)
        target = self.target_info
        example_unlocked = tier >= HintTier.EXAMPLE
        letter_unlocked = tier >= HintTier.NEXT_LETTER
        direction_unlocked = tier >= HintTier.DIRECTION
        location_unlocked = tier >= HintTier.LOCATION
        return HintState(
            tier=tier,
            meaning_zh=self.word.meaning_zh,
            part_of_speech=self.word.part_of_speech,
            captured_pattern=self.captured_pattern,
            seconds_without_progress=idle,
            phonetic=self.word.phonetic if example_unlocked else None,
            example_cloze=(
                _cloze(self.word.example, self.word.word) if example_unlocked else None
            ),
            next_letter=(
                target.letter if letter_unlocked and target is not None else None
            ),
            direction=(
                target.direction if direction_unlocked and target is not None else None
            ),
            distance=(
                target.distance if direction_unlocked and target is not None else None
            ),
            target_position=(
                target.position if location_unlocked and target is not None else None
            ),
        )

    def snapshot(self, now: float | None = None) -> GameSnapshot:
        """Advance timers, then return an immutable state view for a UI."""

        self.tick(now)
        return GameSnapshot(
            status=self.status,
            player_position=self.player_position,
            pet_position=self.pet_position,
            health=self.health,
            hunger=self.hunger,
            is_hungry=self._is_hungry,
            is_starving=self._is_starving,
            is_dizzy=self._is_dizzy,
            elapsed_seconds=self.elapsed_seconds,
            remaining_seconds=self.remaining_seconds,
            progress_index=self._progress_index,
            target_length=len(self.target_word),
            captured_pattern=self.captured_pattern,
            target=self.target_info,
            weather=self._weather,
            hint=self.hint_state(),
            visible_positions=self.visible_positions,
        )

    def drain_events(self) -> tuple[GameEvent, ...]:
        events = tuple(self._events)
        self._events.clear()
        return events

    def exit(self, now: float | None = None) -> tuple[GameEvent, ...]:
        """End this round without marking it completed or dead."""

        event_start = len(self._events)
        self.tick(now)
        if self.status is not GameStatus.RUNNING:
            return tuple(self._events[event_start:])
        self.status = GameStatus.EXITED
        self._finished_at = self._last_tick_at
        self._emit(
            EventType.GAME_EXITED,
            captured=self._progress_index,
            target_length=len(self.target_word),
        )
        return tuple(self._events[event_start:])

    def _generate_map(self) -> tuple[frozenset[Position], frozenset[Position]]:
        width = self.config.width
        height = self.config.height
        border = {
            Position(x, y)
            for y in range(height)
            for x in range(width)
            if x in {0, width - 1} or y in {0, height - 1}
        }
        interior = {
            Position(x, y) for y in range(1, height - 1) for x in range(1, width - 1)
        }
        minimum_free = self.config.distractor_count + 3
        if len(interior) < minimum_free:
            raise ValueError("The map is too small for this word.")

        rng = random.Random(_stable_seed(self.seed, "map"))
        candidates = sorted(interior, key=lambda position: (position.y, position.x))
        rng.shuffle(candidates)
        target_obstacles = min(
            int(len(interior) * self.config.obstacle_density),
            len(interior) - minimum_free,
        )
        obstacles: set[Position] = set()
        for candidate in candidates:
            if len(obstacles) >= target_obstacles:
                break
            proposed = obstacles | {candidate}
            floor = interior - proposed
            if len(_connected_component(next(iter(floor)), floor)) == len(floor):
                obstacles.add(candidate)
        floor = frozenset(interior - obstacles)
        return frozenset(border | obstacles), floor

    def _spawn_stage(self, stage: int) -> list[LetterMonster]:
        """Spawn one correct monster and fresh decoys for a spelling stage."""

        expected = self.target_word[stage]
        candidates = sorted(
            (
                position
                for position in self._spawn_area
                if position not in {self.player_position, self.pet_position}
            ),
            key=lambda position: (position.y, position.x),
        )
        rng = random.Random(
            _stable_seed(self.seed, f"stage:{self.target_word}:{stage}")
        )
        rng.shuffle(candidates)
        monster_count = self.config.distractor_count + 1
        if len(candidates) < monster_count:
            raise ValueError("The map does not have enough monster positions.")

        # Keep one monster in the opening pool of light so the objective is
        # immediately legible without opening the rest of the foggy map.
        showcase = next(
            (
                position
                for position in candidates
                if position in self.visible_positions
                and position.manhattan_distance(self.player_position) > 1
                and abs(position.x - self.player_position.x) <= 3
                and abs(position.y - self.player_position.y) <= 2
            ),
            None,
        )
        far = [
            position
            for position in candidates
            if position != showcase
            and position.manhattan_distance(self.player_position) > 2
        ]
        far_set = set(far)
        near = [
            position
            for position in candidates
            if position != showcase and position not in far_set
        ]
        positions = (([showcase] if showcase is not None else []) + far + near)[
            :monster_count
        ]

        alphabet = [
            letter for letter in "abcdefghijklmnopqrstuvwxyz" if letter != expected
        ]
        rng.shuffle(alphabet)
        distractor_letters = alphabet[: self.config.distractor_count]

        # Pick a target position that remains reachable when all decoys are
        # treated as solid collisions.  This prevents unlucky corridor locks.
        target_position = positions[0]
        for candidate in positions:
            blocked = set(positions) - {candidate}
            allowed = set(self._spawn_area) - blocked
            if candidate in _connected_component(self.player_position, allowed):
                target_position = candidate
                break
        distractor_positions = [
            position for position in positions if position != target_position
        ]
        assignments = [
            (expected, target_position, True),
            *[
                (letter, position, False)
                for letter, position in zip(distractor_letters, distractor_positions)
            ],
        ]
        rng.shuffle(assignments)
        return [
            LetterMonster(
                sequence_index=stage if is_target else -1,
                letter=letter,
                position=position,
                is_target=is_target,
            )
            for letter, position, is_target in assignments
        ]

    def _advance_hunger(self, delta: float) -> None:
        previous_hunger = self.hunger
        consumption = delta * self.config.hunger_per_second
        self.hunger = max(0.0, previous_hunger - consumption)

        time_starving = 0.0
        if previous_hunger <= 0.0:
            time_starving = delta
        elif self.hunger <= 0.0 and self.config.hunger_per_second > 0.0:
            time_to_empty = previous_hunger / self.config.hunger_per_second
            time_starving = max(0.0, delta - time_to_empty)
        self._starvation_elapsed += time_starving
        self._refresh_hunger_state()

        damage_ticks = int(
            self._starvation_elapsed // self.config.starvation_damage_interval
        )
        if damage_ticks:
            self._starvation_elapsed -= (
                damage_ticks * self.config.starvation_damage_interval
            )
            self._damage(
                damage_ticks * self.config.starvation_damage,
                cause="starvation",
                ticks=damage_ticks,
            )

    def _advance_dizziness(self, current_elapsed: float) -> None:
        limit = self.config.time_limit_seconds
        if current_elapsed >= limit and not self._is_dizzy:
            self._is_dizzy = True
            self._emit(
                EventType.DIZZY_STARTED,
                elapsed=current_elapsed,
                overtime=max(0.0, current_elapsed - limit),
            )
        if current_elapsed < limit:
            return
        total_ticks = int(
            max(0.0, current_elapsed - limit) // self.config.dizzy_damage_interval
        )
        new_ticks = total_ticks - self._dizzy_damage_ticks
        if new_ticks > 0:
            self._dizzy_damage_ticks = total_ticks
            self._damage(
                new_ticks * self.config.dizzy_damage,
                cause="dizziness",
                ticks=new_ticks,
            )

    def _advance_weather(self) -> None:
        weather_tick = int(self.elapsed_seconds // self.config.weather_tick_seconds)
        if weather_tick == self._weather.tick:
            return
        old_weather = self._weather
        self._weather = self._weather_at_tick(weather_tick)
        self._emit(
            EventType.WEATHER_CHANGED,
            tick=self._weather.tick,
            skipped_ticks=max(0, weather_tick - old_weather.tick - 1),
            rain_intensity=self._weather.rain_intensity,
            is_storm=self._weather.is_storm,
            storm_tick=self._weather.storm_tick,
            storm_started=self._weather.is_storm and not old_weather.is_storm,
            storm_ended=old_weather.is_storm and not self._weather.is_storm,
        )

    def _weather_at_tick(self, weather_tick: int) -> WeatherState:
        cycle = self.config.storm_cycle_ticks
        duration = self.config.storm_duration_ticks
        # Keep the opening calm while allowing a seed-specific first storm.
        storm_offset = _stable_seed(self.seed, "storm-offset") % max(
            1, cycle - duration + 1
        )
        phase = (weather_tick + storm_offset) % cycle
        is_storm = duration > 0 and phase >= cycle - duration
        storm_tick = phase - (cycle - duration) + 1 if is_storm else 0
        sample_seed = _stable_seed(self.seed, f"rain:{weather_tick}")
        sample = (sample_seed & ((1 << 53) - 1)) / float(1 << 53)
        low = self.config.rain_min_intensity
        high = self.config.rain_max_intensity
        intensity = low + (high - low) * sample
        if is_storm:
            intensity = max(
                self.config.storm_min_intensity,
                intensity + 0.22,
            )
        return WeatherState(
            tick=weather_tick,
            rain_intensity=round(min(1.0, intensity), 3),
            is_storm=is_storm,
            storm_tick=storm_tick,
        )

    def _advance_hint_tier(self) -> None:
        idle = max(0.0, self._last_tick_at - self._last_progress_at)
        tier = self._hint_tier_for(idle)
        if tier == self._last_hint_tier:
            return
        previous = self._last_hint_tier
        self._last_hint_tier = tier
        self._emit(
            EventType.HINT_CHANGED,
            previous_tier=previous.name.lower(),
            tier=tier.name.lower(),
            seconds_without_progress=idle,
        )

    def _hint_tier_for(self, idle: float) -> HintTier:
        if idle >= self.config.location_hint_after:
            return HintTier.LOCATION
        if idle >= self.config.direction_hint_after:
            return HintTier.DIRECTION
        if idle >= self.config.next_letter_hint_after:
            return HintTier.NEXT_LETTER
        if idle >= self.config.example_hint_after:
            return HintTier.EXAMPLE
        return HintTier.MEANING

    def _defeat(self, monster: LetterMonster) -> None:
        index = self._progress_index
        monster_list_index = self._monsters.index(monster)
        self._monsters[monster_list_index] = LetterMonster(
            sequence_index=index,
            letter=monster.letter,
            position=monster.position,
            is_target=True,
            defeated=True,
        )
        self._progress_index += 1
        self._last_progress_at = self._last_tick_at
        self._last_hint_tier = HintTier.MEANING
        old_hunger = self.hunger
        self.hunger = min(
            self.config.max_hunger,
            self.hunger + self.config.correct_letter_food,
        )
        if self.hunger > 0.0:
            self._starvation_elapsed = 0.0
        self._refresh_hunger_state()
        self._emit(
            EventType.LETTER_DEFEATED,
            letter=monster.letter,
            index=index,
            captured_pattern=self.captured_pattern,
            hunger=self.hunger,
        )
        if self.hunger > old_hunger:
            self._emit(
                EventType.FED,
                amount=self.hunger - old_hunger,
                hunger=self.hunger,
            )

        if self._progress_index == len(self.target_word):
            self.status = GameStatus.COMPLETED
            self._finished_at = self._last_tick_at
            self._emit(
                EventType.GAME_COMPLETED,
                word=self.word.word,
                elapsed=self.elapsed_seconds,
                health=self.health,
                hunger=self.hunger,
            )
            self._monsters = []
        else:
            self._monsters = self._spawn_stage(self._progress_index)

    def _refresh_hunger_state(self) -> None:
        was_hungry = self._is_hungry
        was_starving = self._is_starving
        self._is_hungry = self.hunger <= self.config.hungry_threshold
        self._is_starving = self.hunger <= 0.0
        if self._is_hungry and not was_hungry:
            self._emit(EventType.HUNGRY, hunger=self.hunger)
        if self._is_starving and not was_starving:
            self._emit(EventType.STARVING, hunger=self.hunger)

    def _damage(
        self,
        amount: float,
        *,
        cause: str,
        ticks: int = 1,
    ) -> None:
        if amount <= 0.0 or self.status is not GameStatus.RUNNING:
            return
        if self.config.invincible:
            return
        previous = self.health
        self.health = max(0.0, self.health - amount)
        actual = previous - self.health
        self._emit(
            EventType.DAMAGE,
            amount=actual,
            cause=cause,
            ticks=ticks,
            health=self.health,
        )
        if self.health <= 0.0:
            self.status = GameStatus.DEAD
            self._finished_at = self._last_tick_at
            self._emit(
                EventType.PLAYER_DIED,
                cause=cause,
                captured=self._progress_index,
                target_length=len(self.target_word),
            )

    def _field_of_view(self, center: Position, radius: int) -> set[Position]:
        """Return a circular light pool with walls blocking tiles behind them."""

        visible: set[Position] = set()
        radius_squared = radius * radius
        for y in range(
            max(0, center.y - radius),
            min(self.config.height, center.y + radius + 1),
        ):
            for x in range(
                max(0, center.x - radius),
                min(self.config.width, center.x + radius + 1),
            ):
                position = Position(x, y)
                dx = x - center.x
                dy = y - center.y
                if (
                    dx * dx + dy * dy <= radius_squared
                    and self._has_line_of_sight(center, position)
                ):
                    visible.add(position)
        return visible

    def _has_line_of_sight(self, origin: Position, target: Position) -> bool:
        """Use an integer ray and prevent light leaking through closed corners."""

        return self._ray_is_clear(origin, target) and self._ray_is_clear(
            target,
            origin,
        )

    def _ray_is_clear(self, origin: Position, target: Position) -> bool:
        points = _bresenham_line(origin, target)
        previous = points[0]
        for point in points[1:]:
            step_x = point.x - previous.x
            step_y = point.y - previous.y
            if step_x and step_y:
                horizontal = previous.moved(step_x, 0)
                vertical = previous.moved(0, step_y)
                if horizontal in self._walls and vertical in self._walls:
                    return False
            if point == target:
                return True
            if point in self._walls:
                return False
            previous = point
        return True

    def _in_bounds(self, position: Position) -> bool:
        return (
            0 <= position.x < self.config.width and 0 <= position.y < self.config.height
        )

    def _coerce_now(self, now: float | None) -> float:
        current = float(self._clock() if now is None else now)
        if not math.isfinite(current):
            raise ValueError("The clock must return a finite monotonic time.")
        if current < self._last_tick_at:
            raise ValueError("Time cannot move backwards.")
        return current

    def _emit(self, event_type: EventType, **details: object) -> None:
        self._event_sequence += 1
        self._events.append(
            GameEvent(
                sequence=self._event_sequence,
                type=event_type,
                at=self.elapsed_seconds,
                details=details,
            )
        )


def _bresenham_line(origin: Position, target: Position) -> tuple[Position, ...]:
    """Return both endpoints of a deterministic integer grid ray."""

    x = origin.x
    y = origin.y
    delta_x = abs(target.x - x)
    delta_y = abs(target.y - y)
    step_x = 1 if x < target.x else -1
    step_y = 1 if y < target.y else -1
    error = delta_x - delta_y
    points = [origin]
    while x != target.x or y != target.y:
        doubled = error * 2
        if doubled > -delta_y:
            error -= delta_y
            x += step_x
        if doubled < delta_x:
            error += delta_x
            y += step_y
        points.append(Position(x, y))
    return tuple(points)


def _stable_seed(seed: int | str | bytes, namespace: str) -> int:
    if isinstance(seed, bytes):
        seed_bytes = seed
    else:
        seed_bytes = f"{type(seed).__name__}:{seed}".encode("utf-8")
    digest = hashlib.sha256(seed_bytes + b"\0" + namespace.encode("utf-8")).digest()
    return int.from_bytes(digest[:16], "big")


def _connected_component(start: Position, allowed: set[Position]) -> set[Position]:
    visited = {start}
    queue: deque[Position] = deque([start])
    while queue:
        position = queue.popleft()
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            neighbor = position.moved(dx, dy)
            if neighbor in allowed and neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return visited


def _compass_direction(dx: int, dy: int) -> str:
    vertical = "N" if dy < 0 else "S" if dy > 0 else ""
    horizontal = "W" if dx < 0 else "E" if dx > 0 else ""
    return vertical + horizontal or "HERE"


def _cloze(example: str, word: str) -> str:
    if not example:
        return ""
    return re.sub(
        rf"\b{re.escape(word)}(?:s|es)?\b",
        lambda match: "_" * len(match.group(0)),
        example,
        flags=re.IGNORECASE,
    )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


__all__ = [
    "Direction",
    "EventType",
    "GameConfig",
    "GameEngine",
    "GameEvent",
    "GameSnapshot",
    "GameStatus",
    "HintState",
    "HintTier",
    "LetterMonster",
    "Position",
    "TargetInfo",
    "Tile",
    "WeatherState",
]
