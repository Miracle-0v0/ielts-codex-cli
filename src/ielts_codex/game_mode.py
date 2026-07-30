"""Terminal orchestration for the experimental survival-spelling game."""

from __future__ import annotations

import json
import os
import random
import select
import shutil
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .game_engine import (
    Direction,
    EventType,
    GameConfig,
    GameEngine,
    GameEvent,
    GameSnapshot,
    GameStatus,
    Position,
    Tile,
)
from .models import Rating, Word
from .pet_api import (
    DEFAULT_PET_PALETTE,
    DEFAULT_PET_SPRITE,
    PROVIDER_PROFILES,
    PetAPIClient,
    PetAPIConfig,
    PetAPIError,
    PetCreationResult,
    PetProfile,
    load_api_config,
    prepare_image,
)
from .pixel_art import (
    VIEWPORT_COLUMNS,
    render_pet_preview,
    render_pixel_viewport,
)
from .storage import ProgressStore
from .ui import TerminalUI
from .word_bank import WordBank


GAME_DEFAULT_COUNT = 3
GAME_MAX_COUNT = 10
GAME_SCHEMA_VERSION = 2
FRAME_SECONDS = 0.1
TURN_SECONDS = 0.25
MIN_ANIMATED_COLUMNS = 80
MIN_ANIMATED_ROWS = 24

DEFAULT_PET = PetProfile(
    name="Pip",
    species="small dog",
    glyph="d",
    personality="A brave little dog who sniffs out paths through the fog.",
    vision_bonus=2,
    catchphrase="I will light the way.",
    portrait=("/ \\__", "(    @\\___", " /         O", "/   (_____/", "/_____/   U"),
)


class GameStoreError(RuntimeError):
    """Raised when game metadata exists but cannot be loaded safely."""


@dataclass(frozen=True, slots=True)
class SavedPet:
    profile: PetProfile
    image_sha256: str | None = None
    provider: str = "offline"
    endpoint_host: str = "none"
    model: str = "built-in"
    created_at: str | None = None

    @classmethod
    def from_record(cls, value: object) -> "SavedPet":
        if not isinstance(value, dict):
            raise GameStoreError("The saved pet record must be a JSON object.")
        profile_value = value.get("profile")
        if not isinstance(profile_value, dict):
            raise GameStoreError("The saved pet profile is missing.")
        try:
            profile = PetProfile.from_mapping(profile_value)
        except PetAPIError as exc:
            raise GameStoreError(f"The saved pet profile is invalid: {exc}") from exc

        metadata: dict[str, str | None] = {}
        for key in (
            "image_sha256",
            "provider",
            "endpoint_host",
            "model",
            "created_at",
        ):
            item = value.get(key)
            if item is not None and (
                not isinstance(item, str)
                or len(item) > 256
                or not _safe_single_line(item)
            ):
                raise GameStoreError(f"The saved pet field {key!r} is invalid.")
            metadata[key] = item
        digest = metadata["image_sha256"]
        if digest is not None and (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise GameStoreError("The saved pet image digest is invalid.")
        return cls(
            profile=profile,
            image_sha256=digest,
            provider=metadata["provider"] or "unknown",
            endpoint_host=metadata["endpoint_host"] or "unknown",
            model=metadata["model"] or "unknown",
            created_at=metadata["created_at"],
        )

    @classmethod
    def from_result(cls, result: PetCreationResult) -> "SavedPet":
        return cls(
            profile=result.profile,
            image_sha256=result.image_sha256,
            provider=result.provider,
            endpoint_host=result.endpoint_host,
            model=result.model,
            created_at=result.created_at,
        )

    def to_record(self) -> dict[str, object]:
        return {
            "profile": self.profile.to_dict(),
            "image_sha256": self.image_sha256,
            "provider": self.provider,
            "endpoint_host": self.endpoint_host,
            "model": self.model,
            "created_at": self.created_at,
        }


class GameProfileStore:
    """Atomic storage for generated pet metadata, separate from study progress."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.path = data_dir / "game.json"

    def load(self) -> SavedPet:
        if not self.path.exists():
            return SavedPet(profile=DEFAULT_PET)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise GameStoreError("The game save must be a JSON object.")
            version = payload.get("version")
            pet_record = payload.get("pet")
            if isinstance(version, bool) or not isinstance(version, int):
                raise GameStoreError(
                    f"Unsupported game save version {version!r}."
                )
            if version == 1:
                pet_record = _migrate_v1_pet_record(pet_record)
            elif version != GAME_SCHEMA_VERSION:
                raise GameStoreError(
                    f"Unsupported game save version {version!r}."
                )
            return SavedPet.from_record(pet_record)
        except GameStoreError:
            raise
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise GameStoreError(f"Could not read {self.path}: {exc}") from exc

    def save(self, pet: SavedPet) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": GAME_SCHEMA_VERSION,
            "pet": pet.to_record(),
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".game-", suffix=".json", dir=self.data_dir
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()


@dataclass(slots=True)
class RoundMetrics:
    wrong_hits: int = 0
    moves: int = 0
    hint_level: int = 0
    used_direct_letter: bool = False
    navigation_stages: set[int] = field(default_factory=set)
    became_dizzy: bool = False
    revealed_letter: str | None = None
    direct_hint_pending: bool = False


@dataclass(slots=True)
class GameSessionResult:
    attempted: int = 0
    completed: int = 0
    fainted: int = 0
    stopped: bool = False


class _StepClock:
    """Synthetic monotonic clock used by the accessible turn-based mode."""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float = TURN_SECONDS) -> float:
        self.value += seconds
        return self.value


class _PauseableClock:
    """Monotonic clock that excludes time spent in pause/help/quit overlays."""

    def __init__(self, source: Callable[[], float] = time.monotonic) -> None:
        self._source = source
        self._origin = source()
        self._paused_at: float | None = None
        self._paused_total = 0.0

    def __call__(self) -> float:
        current = self._paused_at if self._paused_at is not None else self._source()
        return current - self._origin - self._paused_total

    @property
    def paused(self) -> bool:
        return self._paused_at is not None

    def pause(self) -> None:
        if self._paused_at is None:
            self._paused_at = self._source()

    def resume(self) -> None:
        if self._paused_at is not None:
            self._paused_total += self._source() - self._paused_at
            self._paused_at = None


class GameMode:
    """Connect the pure game engine to the existing word bank and terminal UI."""

    def __init__(
        self,
        bank: WordBank,
        store: ProgressStore,
        ui: TerminalUI,
        *,
        rng: random.Random | None = None,
        config_loader: Callable[[], PetAPIConfig] = load_api_config,
        client_factory: Callable[[PetAPIConfig], PetAPIClient] = PetAPIClient,
    ) -> None:
        self.bank = bank
        self.store = store
        self.ui = ui
        self.rng = rng or random.Random()
        self.profile_store = GameProfileStore(store.data_dir)
        self._config_loader = config_loader
        self._client_factory = client_factory

    def run(self, count: int = GAME_DEFAULT_COUNT, topic: str | None = None) -> GameSessionResult:
        """Run a multi-word expedition and save one SRS result per word."""

        if not 1 <= count <= GAME_MAX_COUNT:
            self.ui.error(f"游戏每局数量须在 1 到 {GAME_MAX_COUNT} 之间。")
            return GameSessionResult()
        words = self._select_words(count, topic)
        if not words:
            self.ui.warning("当前范围没有可用单词。请换一个主题。")
            return GameSessionResult()
        if len(words) < count:
            self.ui.warning(f"当前范围只有 {len(words)} 个可用单词。")

        pet = self._load_pet()
        self.ui.write()
        self.ui.panel(
            "game · storm expedition",
            [
                f"远征      {len(words)} 个词 · {topic or 'all topics'}",
                f"伙伴      {pet.profile.glyph} {pet.profile.name} · {pet.profile.species}",
                "任务      撞击字母怪物，严格按单词拼写顺序击败它们",
                "生存      雨、迷雾、饥饿、眩晕与生命值会持续变化",
                "帮助      h 学习提示 · g 宠物指路 · q 退出并结算",
            ],
        )

        result = GameSessionResult()
        health = 50.0
        hunger = 100.0
        animated = self._can_animate()
        mode_label = "实时动画" if animated else "无障碍回合制"
        self.ui.hint(f"  运行模式：{mode_label}")

        for index, word in enumerate(words, start=1):
            metrics = RoundMetrics()
            seed = self.rng.getrandbits(64)
            game_config = GameConfig(
                max_health=50.0,
                hungry_threshold=55.0,
                hunger_per_second=1.35,
                pet_vision_radius=4 + pet.profile.vision_bonus,
                time_limit_seconds=max(
                    36.0, min(52.0, 20.0 + len(word.word) * 2.0)
                ),
                dizzy_damage_interval=8.0,
                weather_tick_seconds=0.15,
            )
            if animated:
                game_clock: _PauseableClock | _StepClock = _PauseableClock()
            else:
                game_clock = _StepClock()
            engine = GameEngine(
                word,
                seed=seed,
                config=game_config,
                clock=game_clock,
                initial_health=health,
                initial_hunger=hunger,
            )
            result.attempted += 1
            if animated:
                status = self._play_animated(
                    engine,
                    game_clock,
                    pet.profile,
                    index,
                    len(words),
                    metrics,
                )
            else:
                status = self._play_turn_based(
                    engine,
                    game_clock,
                    pet.profile,
                    index,
                    len(words),
                    metrics,
                )

            snapshot = engine.snapshot()
            health = snapshot.health
            hunger = snapshot.hunger
            if status is GameStatus.EXITED:
                result.stopped = True
                break

            if status is GameStatus.COMPLETED:
                result.completed += 1
                rating = self._rating_for(metrics)
                self.store.record_review(word.word, rating)
                self._round_debrief(word, rating, metrics, snapshot, completed=True)
            elif status is GameStatus.DEAD:
                result.fainted += 1
                self.store.record_review(word.word, Rating.AGAIN)
                self._round_debrief(
                    word,
                    Rating.AGAIN,
                    metrics,
                    snapshot,
                    completed=False,
                )
                break

        self._summary(result, len(words), pet.profile)
        return result

    def create_pet(self, image_path: str) -> bool:
        """Create and persist a pet only after explicit external-upload consent."""

        try:
            config = self._config_loader()
            image = prepare_image(image_path)
        except PetAPIError as exc:
            self.ui.error(f"宠物 API 配置或图片无效：{exc}")
            self.ui.hint(
                "先设置 IELTS_CODEX_GAME_PROVIDER、IELTS_CODEX_GAME_MODEL "
                "和 IELTS_CODEX_GAME_API_KEY；自定义端点还需 API_URL。"
            )
            return False

        profile = PROVIDER_PROFILES[config.provider]
        self.ui.panel(
            "external image upload",
            [
                f"供应商    {profile.display_name}",
                f"模型      {config.model}",
                f"目标主机  {config.endpoint_host}",
                f"图片      {image.mime_type} · {_format_bytes(image.size_bytes)}",
                f"SHA-256   {image.sha256[:16]}…",
                "",
                "确认后，图片内容会离开本机并受所选供应商的数据政策约束。",
                "API key、原图、Base64 和本地路径不会写入 IELTS Codex 存档。",
            ],
        )
        try:
            answer = self.ui.prompt("  确认上传并创建宠物？ [y/N] › ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            self.ui.write()
            answer = ""
        if answer not in {"y", "yes", "是", "好"}:
            self.ui.hint("已取消；没有发送网络请求。")
            return False

        self.ui.hint("正在调用你的视觉模型创建终端伙伴…")
        try:
            result = self._client_factory(config).create_pet_from_prepared(image)
            saved = SavedPet.from_result(result)
            self.profile_store.save(saved)
        except (PetAPIError, GameStoreError, OSError) as exc:
            self.ui.error(f"宠物创建失败：{exc}")
            self.ui.hint("离线默认宠物仍可正常进入 /game。")
            return False

        lines = [
            *render_pet_preview(saved.profile, colour=self.ui.color),
            "",
            f"{saved.profile.glyph} {saved.profile.name} · {saved.profile.species}",
            saved.profile.personality,
            f"“{saved.profile.catchphrase}”",
            f"视野加成  +{saved.profile.vision_bonus}",
        ]
        self.ui.panel("companion created", lines)
        self.ui.success("宠物档案已保存；API key 和图片内容未保存。")
        return True

    def show_pet_status(self) -> None:
        pet = self._load_pet()
        digest = (
            f"{pet.image_sha256[:16]}…"
            if pet.image_sha256 is not None
            else "not stored (built-in pet)"
        )
        self.ui.panel(
            "game · companion",
            [
                *render_pet_preview(pet.profile, colour=self.ui.color),
                "",
                f"{pet.profile.glyph} {pet.profile.name} · {pet.profile.species}",
                pet.profile.personality,
                f"“{pet.profile.catchphrase}”",
                f"视野加成  +{pet.profile.vision_bonus}",
                f"来源      {pet.provider} · {pet.model}",
                f"目标主机  {pet.endpoint_host}",
                f"图片摘要  {digest}",
                f"创建时间  {pet.created_at or 'built in'}",
            ],
        )

    def show_providers(self) -> None:
        lines = []
        for key, profile in PROVIDER_PROFILES.items():
            endpoint = profile.endpoint or "set IELTS_CODEX_GAME_API_URL"
            lines.append(f"{key:<8} {profile.display_name}")
            lines.append(f"         {endpoint}")
        lines.extend(
            (
                "",
                "Required: PROVIDER, MODEL, API_KEY",
                "Optional: API_URL override, TIMEOUT",
                "Environment prefix: IELTS_CODEX_GAME_",
                "No provider supplies a default model ID.",
            )
        )
        self.ui.panel("game · API providers", lines)

    def show_help(self) -> None:
        self.ui.panel(
            "game · commands",
            [
                "/game [数量] [主题]          开始暴雨拼写远征（默认 3）",
                "/game pet create <图片>     用自己的视觉 API 创建宠物",
                "/game pet status            查看宠物与非敏感来源信息",
                "/game providers             查看支持的 API profile",
                "/game help                  查看本帮助",
                "",
                "游戏中：WASD/方向键移动 · h 学习提示 · g 宠物指路 · q 退出",
                "没有 API 也可完整游玩；默认宠物会自动跟随并开视野。",
            ],
        )

    def _select_words(self, count: int, topic: str | None) -> list[Word]:
        selected: list[Word] = []
        seen: set[str] = set()

        def add(items: Iterable[Word]) -> None:
            for item in items:
                if item.word not in seen and len(selected) < count:
                    selected.append(item)
                    seen.add(item.word)

        from datetime import date

        add(self.bank.due(self.store.cards, date.today(), count, topic))
        learned = self.bank.learned(self.store.cards, topic)
        self.rng.shuffle(learned)
        add(learned)
        add(self.bank.unseen(self.store.cards, count, topic, self.rng))
        remaining = [
            word
            for word in self.bank.words
            if word.word not in seen and (topic is None or word.topic == topic)
        ]
        self.rng.shuffle(remaining)
        add(remaining)
        return selected

    def _load_pet(self) -> SavedPet:
        try:
            return self.profile_store.load()
        except GameStoreError as exc:
            self.ui.warning(f"宠物存档不可读，将使用离线伙伴：{exc}")
            return SavedPet(profile=DEFAULT_PET)

    def _can_animate(self) -> bool:
        if os.environ.get("IELTS_CODEX_GAME_TURN_BASED", "").lower() in {
            "1",
            "true",
            "yes",
        }:
            return False
        if os.environ.get("TERM", "").lower() == "dumb":
            return False
        if not (
            getattr(self.ui.stream, "isatty", lambda: False)()
            and getattr(self.ui.input_stream, "isatty", lambda: False)()
        ):
            return False
        size = shutil.get_terminal_size((80, 24))
        return (
            size.columns >= MIN_ANIMATED_COLUMNS
            and size.lines >= MIN_ANIMATED_ROWS
            and os.name in {"posix", "nt"}
        )

    def _play_animated(
        self,
        engine: GameEngine,
        clock: _PauseableClock | _StepClock,
        pet: PetProfile,
        index: int,
        total: int,
        metrics: RoundMetrics,
    ) -> GameStatus:
        assert isinstance(clock, _PauseableClock)
        explored: set[Position] = set()
        messages = [f"{pet.glyph} {pet.name} 跟进了雨幕。"]
        quit_pending = False
        show_help = False
        key_reader = _ImmediateInput(self.ui.input_stream)
        last_frame: str | None = None

        try:
            key_reader.enter()
            self.ui.write("\033[?1049h\033[2J\033[H\033[?25l", end="")
            while engine.status is GameStatus.RUNNING:
                now = clock()
                events = engine.tick(now)
                self._consume_events(events, metrics, messages)
                snapshot = engine.snapshot(now)
                explored.update(snapshot.visible_positions)
                frame = self._render_frame(
                    engine,
                    snapshot,
                    pet,
                    index,
                    total,
                    explored,
                    messages,
                    metrics,
                    paused=clock.paused,
                    quit_pending=quit_pending,
                    show_help=show_help,
                    animation_tick=int(now / FRAME_SECONDS),
                )
                if frame != last_frame:
                    cleared_frame = "\033[K\n".join(frame.splitlines()) + "\033[K"
                    self.ui.write("\033[H" + cleared_frame, end="")
                    last_frame = frame

                key = key_reader.read(FRAME_SECONDS)
                if key is None:
                    continue
                if quit_pending:
                    if key == "q":
                        engine.exit(now=clock())
                        break
                    quit_pending = False
                    clock.resume()
                    messages.append("继续远征。")
                    continue
                if show_help:
                    show_help = False
                    clock.resume()
                    messages.append("帮助已关闭。")
                    continue
                if key == "p":
                    if clock.paused:
                        clock.resume()
                        messages.append("继续。")
                    else:
                        clock.pause()
                        messages.append("已暂停；按 p 继续。")
                    continue
                if clock.paused:
                    continue
                if key == "q":
                    quit_pending = True
                    clock.pause()
                    messages.append("再按 q 确认退出；任意其他键继续。")
                    continue
                if key == "?":
                    show_help = True
                    clock.pause()
                    continue
                if key == "h":
                    messages.append(self._use_learning_hint(engine, metrics))
                    continue
                if key == "g":
                    messages.append(self._use_pet_navigation(engine, pet, metrics))
                    continue
                direction = _direction_for_key(key)
                if direction is not None:
                    events = engine.move(direction, now=clock())
                    metrics.moves += 1
                    self._consume_events(events, metrics, messages)
        finally:
            with suppress(OSError, ValueError):
                key_reader.exit()
            with suppress(OSError, ValueError):
                self.ui.write("\033[?25h\033[?1049l", end="")
        return engine.status

    def _play_turn_based(
        self,
        engine: GameEngine,
        clock: _PauseableClock | _StepClock,
        pet: PetProfile,
        index: int,
        total: int,
        metrics: RoundMetrics,
    ) -> GameStatus:
        assert isinstance(clock, _StepClock)
        explored: set[Position] = set()
        messages = [f"{pet.glyph} {pet.name} 会替你照亮附近的雾。"]
        while engine.status is GameStatus.RUNNING:
            snapshot = engine.snapshot(clock())
            explored.update(snapshot.visible_positions)
            self.ui.write()
            self.ui.write(
                self._render_frame(
                    engine,
                    snapshot,
                    pet,
                    index,
                    total,
                    explored,
                    messages,
                    metrics,
                    compact=True,
                )
            )
            try:
                action = self.ui.prompt(
                    "  动作 [w/a/s/d/h/g/q] › "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                self.ui.write()
                engine.exit(now=clock())
                break
            if action in {"q", "quit", "exit"}:
                engine.exit(now=clock())
                break
            if action in {"h", "hint"}:
                events = engine.tick(clock.advance())
                self._consume_events(events, metrics, messages)
                messages.append(self._use_learning_hint(engine, metrics))
                continue
            if action in {"g", "guide"}:
                events = engine.tick(clock.advance())
                self._consume_events(events, metrics, messages)
                messages.append(self._use_pet_navigation(engine, pet, metrics))
                continue
            direction = _direction_for_key(action)
            if direction is None:
                messages.append("可用动作：w/a/s/d 移动，h 提示，g 指路，q 退出。")
                continue
            events = engine.move(direction, now=clock.advance())
            metrics.moves += 1
            self._consume_events(events, metrics, messages)
        return engine.status

    def _render_frame(
        self,
        engine: GameEngine,
        snapshot: GameSnapshot,
        pet: PetProfile,
        index: int,
        total: int,
        explored: set[Position],
        messages: list[str],
        metrics: RoundMetrics,
        *,
        paused: bool = False,
        quit_pending: bool = False,
        show_help: bool = False,
        compact: bool = False,
        animation_tick: int | None = None,
    ) -> str:
        state = "眩晕" if snapshot.is_dizzy else "饥饿" if snapshot.is_hungry else "正常"
        if paused:
            state = "暂停"
        weather = "暴雨" if snapshot.weather.is_storm else "雨"
        health_bar = _meter(snapshot.health, engine.config.max_health, 10)
        hunger_bar = _meter(snapshot.hunger, engine.config.max_hunger, 10)
        pattern = " ".join(snapshot.captured_pattern)
        frame_width = (
            max(
                engine.config.width,
                min(shutil.get_terminal_size((80, 24)).columns, 88),
            )
            if compact
            else VIEWPORT_COLUMNS
        )
        title = _truncate_cells(
            f"IELTS CODEX // STORM RUN {index}/{total}",
            frame_width,
        )
        header = [
            self.ui.style(
                title,
                self.ui.palette.bold,
                self.ui.palette.teal,
            ),
            _truncate_cells(
                f"HP {health_bar} {snapshot.health:>3.0f}  "
                f"HUNGER {hunger_bar} {snapshot.hunger:>3.0f}  "
                f"{state} · {weather} · {snapshot.elapsed_seconds:04.0f}s",
                frame_width,
            ),
            _truncate_cells(
                f"线索  {engine.word.meaning_zh} · "
                f"{engine.word.part_of_speech} · {engine.word.topic}",
                frame_width,
            ),
            _truncate_cells(f"拼写  {pattern}", frame_width),
        ]
        map_lines = (
            self._render_map(engine, snapshot, pet, explored)
            if compact
            else render_pixel_viewport(
                engine,
                snapshot,
                pet,
                explored=explored,
                animation_tick=(
                    snapshot.weather.tick
                    if animation_tick is None
                    else animation_tick
                ),
                player_frame=metrics.moves,
                colour=self.ui.color,
            )
        )
        footer = [
            messages[-2] if len(messages) >= 2 else "",
            messages[-1] if messages else "",
            "WASD/方向键 · h 提示 · g 指路 · p 暂停 · ? 帮助 · q 退出",
        ]
        if compact:
            footer[-1] = "w/a/s/d 移动 · h 学习提示 · g 宠物指路 · q 退出"
        if quit_pending:
            footer[-2:] = ["退出当前远征？再按 q 确认。", "任意其他键取消并继续。"]
        if show_help:
            footer[-2:] = [
                "撞击当前正确字母会推进拼写；错误怪物会扣生命和饱腹。",
                "h 逐级给学习线索；g 只报方向。按任意键关闭帮助。",
            ]
        if metrics.revealed_letter is not None:
            footer[-2] = f"直接提示：下一只字母怪物是 {metrics.revealed_letter.upper()}。"
        footer = [_truncate_cells(line, frame_width) for line in footer]
        return "\n".join((*header, *map_lines, *footer))

    def _render_map(
        self,
        engine: GameEngine,
        snapshot: GameSnapshot,
        pet: PetProfile,
        explored: set[Position],
    ) -> list[str]:
        visible = snapshot.visible_positions
        active = {monster.position: monster for monster in engine.active_monsters}
        lines: list[str] = []
        for y in range(engine.config.height):
            row: list[str] = []
            for x in range(engine.config.width):
                position = Position(x, y)
                if position not in visible:
                    char = "·" if position in explored else "░"
                    style = (self.ui.palette.gray,)
                elif position == snapshot.player_position:
                    char = "@"
                    style = (self.ui.palette.bold, self.ui.palette.teal)
                elif position == snapshot.pet_position:
                    char = pet.glyph
                    style = (self.ui.palette.bold, self.ui.palette.violet)
                elif position in active:
                    char = active[position].letter.upper()
                    style = (self.ui.palette.bold, self.ui.palette.red)
                elif engine.tile_at(position) is Tile.WALL:
                    char = "#"
                    style = (self.ui.palette.gray,)
                elif _rain_at(position, snapshot):
                    char = "|/\\"[
                        (position.x + position.y + snapshot.weather.tick) % 3
                    ]
                    style = (self.ui.palette.blue,)
                else:
                    char = "."
                    style = (self.ui.palette.dim,)
                row.append(self.ui.style(char, *style))
            lines.append("".join(row))
        return lines

    def _consume_events(
        self,
        events: Iterable[GameEvent],
        metrics: RoundMetrics,
        messages: list[str],
    ) -> None:
        for event in events:
            if event.type is EventType.WRONG_LETTER:
                metrics.wrong_hits += 1
                encountered = str(event.details.get("encountered", "?")).upper()
                messages.append(f"{encountered} 不是下一只；饱腹和生命下降。")
            elif event.type is EventType.LETTER_DEFEATED:
                letter = str(event.details.get("letter", "?")).upper()
                messages.append(f"击败 {letter}！拼写向前推进。")
                metrics.revealed_letter = None
                metrics.direct_hint_pending = False
            elif event.type is EventType.DIZZY_STARTED:
                metrics.became_dizzy = True
                messages.append("你开始眩晕，视野缩小；尽快找到下一只。")
            elif event.type is EventType.HUNGRY:
                messages.append("肚子在叫；正确击败字母可以恢复饱腹。")
            elif event.type is EventType.STARVING:
                messages.append("饱腹已空，生命会持续下降。")
            elif event.type is EventType.DAMAGE:
                cause = str(event.details.get("cause", "danger"))
                amount = float(event.details.get("amount", 0.0))
                messages.append(f"{cause} 造成 {amount:.0f} 点伤害。")
            elif event.type is EventType.WEATHER_CHANGED:
                if event.details.get("storm_started"):
                    messages.append("暴雨突然增强，雨幕压低了视线。")
                elif event.details.get("storm_ended"):
                    messages.append("最强的雨势过去了。")
            elif event.type is EventType.HINT_CHANGED:
                tier = str(event.details.get("tier", ""))
                if tier == "example":
                    messages.append("卡住了？按 h 看学习线索，或按 g 让宠物指路。")
        if len(messages) > 8:
            del messages[:-8]

    def _use_learning_hint(self, engine: GameEngine, metrics: RoundMetrics) -> str:
        if metrics.hint_level == 0:
            metrics.hint_level = 1
            return f"学习提示 1/4 · 音标：{engine.word.phonetic}"
        if metrics.hint_level == 1:
            metrics.hint_level = 2
            return f"学习提示 2/4 · 例句：{_cloze(engine.word)}"
        if metrics.hint_level == 2:
            metrics.hint_level = 3
            synonyms = ", ".join(engine.word.synonyms[:2]) or "暂无"
            return f"学习提示 3/4 · 近义词：{synonyms}"
        if not metrics.direct_hint_pending:
            metrics.direct_hint_pending = True
            return "下一层会直接显示字母并记为 Again；再按 h 确认。"
        target = engine.target_info
        if target is None:
            return "当前没有待击败的字母。"
        metrics.hint_level = 4
        metrics.used_direct_letter = True
        metrics.revealed_letter = target.letter
        metrics.direct_hint_pending = False
        return f"学习提示 4/4 · 下一只字母是 {target.letter.upper()}。"

    def _use_pet_navigation(
        self,
        engine: GameEngine,
        pet: PetProfile,
        metrics: RoundMetrics,
    ) -> str:
        target = engine.target_info
        if target is None:
            return f"{pet.glyph} {pet.name} 安静地跟在你身后。"
        if target.sequence_index in metrics.navigation_stages:
            return f"{pet.glyph} {pet.name} 本阶段已经指过一次方向；继续探索吧。"
        metrics.navigation_stages.add(target.sequence_index)
        direction = {
            "N": "北",
            "NE": "东北",
            "E": "东",
            "SE": "东南",
            "S": "南",
            "SW": "西南",
            "W": "西",
            "NW": "西北",
            "HERE": "脚下",
        }.get(target.direction, target.direction)
        return f"{pet.glyph} {pet.name} 朝{direction}方示意，没有泄露字母。"

    @staticmethod
    def _rating_for(metrics: RoundMetrics) -> Rating:
        if metrics.used_direct_letter:
            return Rating.AGAIN
        if (
            metrics.hint_level > 0
            or metrics.navigation_stages
            or metrics.wrong_hits >= 2
            or metrics.became_dizzy
        ):
            return Rating.HARD
        return Rating.GOOD

    def _round_debrief(
        self,
        word: Word,
        rating: Rating,
        metrics: RoundMetrics,
        snapshot: GameSnapshot,
        *,
        completed: bool,
    ) -> None:
        title = "word secured" if completed else "fainted · word returned"
        reason = (
            "直接字母提示"
            if metrics.used_direct_letter
            else "使用学习提示"
            if metrics.hint_level
            else "使用宠物指路"
            if metrics.navigation_stages
            else "多次撞错或超时"
            if metrics.wrong_hits >= 2 or metrics.became_dizzy
            else "无答案提示完成"
        )
        self.ui.write()
        self.ui.panel(
            title,
            [
                f"{word.word}  {word.phonetic}  {word.part_of_speech}",
                f"中文      {word.meaning_zh}",
                f"English   {word.definition_en}",
                f"例句      {word.example}",
                f"评分      {rating.label} · {reason}",
                f"战况      {snapshot.elapsed_seconds:.0f}s · 撞错 {metrics.wrong_hits} "
                f"· 生命 {snapshot.health:.0f} · 饱腹 {snapshot.hunger:.0f}",
            ],
        )

    def _summary(
        self,
        result: GameSessionResult,
        planned: int,
        pet: PetProfile,
    ) -> None:
        status = "提前结算" if result.stopped else "远征结束"
        self.ui.panel(
            f"game · {status}",
            [
                f"计划      {planned}",
                f"完成      {result.completed}",
                f"昏倒      {result.fainted}",
                f"伙伴      {pet.glyph} {pet.name}",
                "进度      每个已结算单词都已立即写入间隔复习记录",
            ],
        )


class _ImmediateInput:
    """Small cross-platform immediate-key reader with guaranteed restoration."""

    def __init__(self, stream: object) -> None:
        self.stream = stream
        self.fd: int | None = None
        self._saved: object | None = None

    def enter(self) -> None:
        if os.name == "posix":
            import termios
            import tty

            self.fd = self.stream.fileno()  # type: ignore[attr-defined]
            self._saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)

    def exit(self) -> None:
        if os.name == "posix" and self.fd is not None and self._saved is not None:
            import termios

            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)

    def read(self, timeout: float) -> str | None:
        if os.name == "nt":
            import msvcrt

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if msvcrt.kbhit():
                    char = msvcrt.getwch()
                    if char in {"\x00", "\xe0"}:
                        code = msvcrt.getwch()
                        return {
                            "H": "up",
                            "M": "right",
                            "P": "down",
                            "K": "left",
                        }.get(code)
                    return char.lower()
                time.sleep(0.01)
            return None

        assert self.fd is not None
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return None
        first = os.read(self.fd, 1)
        if first == b"\x1b":
            sequence = bytearray(first)
            while len(sequence) < 3:
                more, _, _ = select.select([self.fd], [], [], 0.005)
                if not more:
                    break
                sequence.extend(os.read(self.fd, 1))
            return {
                b"\x1b[A": "up",
                b"\x1b[B": "down",
                b"\x1b[C": "right",
                b"\x1b[D": "left",
            }.get(bytes(sequence))
        try:
            return first.decode("utf-8").lower()
        except UnicodeDecodeError:
            return None


def _direction_for_key(value: str) -> Direction | None:
    return {
        "w": Direction.UP,
        "up": Direction.UP,
        "d": Direction.RIGHT,
        "right": Direction.RIGHT,
        "s": Direction.DOWN,
        "down": Direction.DOWN,
        "a": Direction.LEFT,
        "left": Direction.LEFT,
    }.get(value)


def _rain_at(position: Position, snapshot: GameSnapshot) -> bool:
    sample = (
        position.x * 97
        + position.y * 193
        + snapshot.weather.tick * 389
    ) % 1000
    return sample < int(snapshot.weather.rain_intensity * 220)


def _meter(value: float, maximum: float, width: int) -> str:
    filled = round(width * max(0.0, min(1.0, value / maximum))) if maximum else 0
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _cloze(word: Word) -> str:
    import re

    return re.sub(
        rf"\b{re.escape(word.word)}(?:s|es)?\b",
        lambda match: "_" * len(match.group(0)),
        word.example,
        flags=re.IGNORECASE,
    )


def _format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1024 * 1024):.1f} MiB"


def _migrate_v1_pet_record(value: object) -> object:
    """Add the indexed sprite fields introduced by game save schema v2."""

    if not isinstance(value, dict):
        return value
    migrated = dict(value)
    profile = value.get("profile")
    if isinstance(profile, dict):
        migrated_profile = dict(profile)
        migrated_profile.setdefault("palette", list(DEFAULT_PET_PALETTE))
        migrated_profile.setdefault("sprite", list(DEFAULT_PET_SPRITE))
        migrated["profile"] = migrated_profile
    return migrated


def _safe_single_line(value: str) -> bool:
    return bool(value) and all(
        char.isprintable() and char not in {"\r", "\n"} for char in value
    )


def _truncate_cells(value: str, width: int) -> str:
    """Truncate plain terminal text without splitting a wide CJK character."""

    import unicodedata

    if width <= 0:
        return ""
    used = 0
    output: list[str] = []
    for char in value:
        char_width = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if used + char_width > width:
            if width >= 1 and output:
                while output and used + 1 > width:
                    removed = output.pop()
                    used -= (
                        2
                        if unicodedata.east_asian_width(removed) in {"W", "F"}
                        else 1
                    )
                output.append("…")
            break
        output.append(char)
        used += char_width
    return "".join(output)


__all__ = [
    "DEFAULT_PET",
    "GAME_DEFAULT_COUNT",
    "GAME_MAX_COUNT",
    "GameMode",
    "GameProfileStore",
    "GameSessionResult",
    "GameStoreError",
    "SavedPet",
]
