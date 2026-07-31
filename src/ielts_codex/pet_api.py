"""Safe, zero-dependency client for creating a game pet from an image.

The module targets OpenAI-compatible Chat Completions endpoints.  It deliberately
separates configuration and network access so the CLI can show ``endpoint_host``
and ask for confirmation before calling :meth:`PetAPIClient.create_pet`.

API keys and image data are never persisted by this module.  Callers that want a
serializable audit record should use :meth:`PetCreationResult.to_record`, which
contains the input image's SHA-256 digest instead of the image itself.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from . import __version__


DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
ENV_PREFIX = "IELTS_CODEX_GAME_"
PROVIDER_ENV = f"{ENV_PREFIX}PROVIDER"

_ALLOWED_PROFILE_FIELDS = {
    "name",
    "species",
    "glyph",
    "personality",
    "vision_bonus",
    "catchphrase",
    "portrait",
    "palette",
    "sprite",
}
_PET_GLYPHS = frozenset("&*;:mpcd")
_PALETTE_COLOR_RE = re.compile(r"\A#[0-9A-Fa-f]{6}\Z")
_SPRITE_PIXELS = frozenset(".123")
_SPRITE_WIDTH = 7
_SPRITE_HEIGHT = 6
_JSON_FENCE_RE = re.compile(
    r"\A\s*```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n?```\s*\Z",
    re.IGNORECASE | re.DOTALL,
)
DEFAULT_PET_PALETTE = ("#493126", "#C9834D", "#FFE0A3")
DEFAULT_PET_SPRITE = (
    "11...11",
    "1221221",
    "1222221",
    "1232321",
    ".122211",
    ".1.1.1.",
)
_PORTRAIT_ALLOWED = frozenset(
    " !\"#$%&'()*+,-./0123456789:;<=>?@"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`"
    "abcdefghijklmnopqrstuvwxyz{|}~"
)


class PetAPIError(RuntimeError):
    """Base class for all expected pet creation failures."""


class PetConfigError(PetAPIError):
    """Raised when BYO API configuration is missing or unsafe."""


class ImageValidationError(PetAPIError):
    """Raised when the selected image is unreadable, unsupported, or too large."""


class PetRequestError(PetAPIError):
    """Raised when the configured API cannot complete a request."""


class PetResponseError(PetAPIError):
    """Raised when the API returns malformed or unsafe pet data."""


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    """Named provider whose endpoint follows OpenAI Chat Completions."""

    provider: str
    display_name: str
    endpoint: str | None


# These are complete HTTP endpoints documented by each provider.  Qwen's
# workspace-specific and non-Beijing URLs can be supplied through ``endpoint``.
# No model is selected here: callers must explicitly choose a vision-capable one.
PROVIDER_PROFILES: Mapping[str, ProviderProfile] = MappingProxyType(
    {
        "openai": ProviderProfile(
            provider="openai",
            display_name="OpenAI",
            endpoint="https://api.openai.com/v1/chat/completions",
        ),
        "kimi": ProviderProfile(
            provider="kimi",
            display_name="Kimi (Moonshot AI)",
            endpoint="https://api.moonshot.ai/v1/chat/completions",
        ),
        "qwen": ProviderProfile(
            provider="qwen",
            display_name="Qwen (Alibaba Cloud Beijing)",
            endpoint=(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/"
                "chat/completions"
            ),
        ),
        "glm": ProviderProfile(
            provider="glm",
            display_name="GLM (Zhipu BigModel)",
            endpoint="https://open.bigmodel.cn/api/paas/v4/chat/completions",
        ),
        "custom": ProviderProfile(
            provider="custom",
            display_name="Custom OpenAI-compatible provider",
            endpoint=None,
        ),
    }
)


@dataclass(frozen=True, slots=True)
class PetAPIConfig:
    """User-supplied OpenAI-compatible endpoint configuration.

    A named provider supplies its documented Chat Completions endpoint; custom
    providers must supply a complete endpoint.  No provider or model is selected
    implicitly.  ``api_key`` is excluded from representations and comparisons.
    """

    model: str
    api_key: str = field(repr=False, compare=False)
    provider: str = "custom"
    endpoint: str | None = None
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str):
            raise PetConfigError("PET API provider must be text.")
        if not isinstance(self.model, str):
            raise PetConfigError("PET API model must be text.")
        if not isinstance(self.api_key, str):
            raise PetConfigError("PET API key must be text.")
        provider = self.provider.strip().lower()
        profile = PROVIDER_PROFILES.get(provider)
        if profile is None:
            choices = ", ".join(PROVIDER_PROFILES)
            raise PetConfigError(f"PET API provider must be one of: {choices}.")
        if self.endpoint is None:
            endpoint = profile.endpoint or ""
        elif not isinstance(self.endpoint, str):
            raise PetConfigError("PET API endpoint must be text.")
        else:
            endpoint = self.endpoint.strip()
        model = self.model.strip()
        api_key = self.api_key.strip()

        if not endpoint:
            raise PetConfigError(
                "Custom PET API provider requires a complete endpoint URL."
            )
        try:
            parsed = urllib.parse.urlsplit(endpoint)
            parsed_host = parsed.hostname
            _ = parsed.port
        except ValueError as exc:
            raise PetConfigError("PET API endpoint URL is malformed.") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed_host
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise PetConfigError(
                "PET API endpoint must be a complete HTTP(S) URL without "
                "credentials or a fragment."
            )
        if parsed.scheme == "http" and parsed_host not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise PetConfigError(
                "PET API endpoint must use HTTPS; HTTP is allowed only for "
                "localhost, 127.0.0.1, or [::1]."
            )
        if any(unicodedata.category(char).startswith("C") for char in endpoint):
            raise PetConfigError("PET API endpoint contains control characters.")
        if not model or len(model) > 200 or not _is_single_line_printable(model):
            raise PetConfigError("PET API model must be 1-200 printable characters.")
        if not api_key or len(api_key) > 4096 or not _is_single_line_printable(api_key):
            raise PetConfigError("PET API key is missing or invalid.")
        if isinstance(self.timeout, bool):
            raise PetConfigError("PET API timeout must be a number.")
        try:
            timeout = float(self.timeout)
        except (TypeError, ValueError) as exc:
            raise PetConfigError("PET API timeout must be a number.") from exc
        if not 0.5 <= timeout <= 300:
            raise PetConfigError("PET API timeout must be between 0.5 and 300 seconds.")

        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "timeout", timeout)

    @property
    def endpoint_host(self) -> str:
        """Return only the host (and optional port) for preflight confirmation."""

        parsed = urllib.parse.urlsplit(self.endpoint)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{host}:{parsed.port}" if parsed.port is not None else host

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        prefix: str = ENV_PREFIX,
    ) -> "PetAPIConfig":
        """Load configuration without mutating or persisting the environment.

        ``<prefix>PROVIDER`` selects ``openai``, ``kimi``, ``qwen``, ``glm``,
        or ``custom``.  Expected credential variables are ``<prefix>MODEL`` and
        ``<prefix>API_KEY``.  ``<prefix>API_URL`` is required for ``custom`` and
        can override a named provider's endpoint (for example, a regional Qwen
        or private gateway URL).
        ``<prefix>TIMEOUT`` is optional.
        """

        source = os.environ if environ is None else environ
        provider_name = f"{prefix}PROVIDER"
        provider = source.get(provider_name, "custom").strip().lower()
        if provider not in PROVIDER_PROFILES:
            choices = ", ".join(PROVIDER_PROFILES)
            raise PetConfigError(
                f"{provider_name} must be one of: {choices}."
            )
        names = {
            "model": f"{prefix}MODEL",
            "api_key": f"{prefix}API_KEY",
        }
        missing = [name for name in names.values() if not source.get(name, "").strip()]
        endpoint_name = f"{prefix}API_URL"
        endpoint = source.get(endpoint_name, "").strip() or None
        if provider == "custom" and endpoint is None:
            missing.append(endpoint_name)
        if missing:
            raise PetConfigError(
                "Missing PET API environment variable(s): " + ", ".join(missing)
            )

        raw_timeout = source.get(f"{prefix}TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS))
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise PetConfigError(
                f"{prefix}TIMEOUT must be a number between 0.5 and 300."
            ) from exc
        return cls(
            model=source[names["model"]],
            api_key=source[names["api_key"]],
            provider=provider,
            endpoint=endpoint,
            timeout=timeout,
        )


def load_api_config(
    environ: Mapping[str, str] | None = None,
    *,
    prefix: str = ENV_PREFIX,
) -> PetAPIConfig:
    """Convenience wrapper around :meth:`PetAPIConfig.from_env`."""

    return PetAPIConfig.from_env(environ, prefix=prefix)


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """Validated image payload ready for a single API request."""

    mime_type: str
    size_bytes: int
    sha256: str
    data_url: str = field(repr=False, compare=False)

    def to_record(self) -> dict[str, str | int]:
        """Return metadata safe to persist; the data URL is intentionally absent."""

        return {
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class PetProfile:
    """Strictly bounded terminal-friendly pet description."""

    name: str
    species: str
    glyph: str
    personality: str
    vision_bonus: int
    catchphrase: str
    portrait: tuple[str, ...]
    palette: tuple[str, str, str] = DEFAULT_PET_PALETTE
    sprite: tuple[str, ...] = DEFAULT_PET_SPRITE

    def __post_init__(self) -> None:
        """Validate direct construction as strictly as decoded API profiles."""

        name = _bounded_label(self.name, "name", 1, 20)
        species = _bounded_label(self.species, "species", 1, 32)
        glyph = _validate_glyph(self.glyph)
        personality = _bounded_text(self.personality, "personality", 1, 96)
        catchphrase = _bounded_text(self.catchphrase, "catchphrase", 1, 96)
        vision_bonus = _validate_vision_bonus(self.vision_bonus)
        portrait = _validate_portrait(self.portrait, json_array=False)
        palette = _validate_palette(self.palette, json_array=False)
        sprite = _validate_sprite(self.sprite, json_array=False)

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "species", species)
        object.__setattr__(self, "glyph", glyph)
        object.__setattr__(self, "personality", personality)
        object.__setattr__(self, "catchphrase", catchphrase)
        object.__setattr__(self, "vision_bonus", vision_bonus)
        object.__setattr__(self, "portrait", portrait)
        object.__setattr__(self, "palette", palette)
        object.__setattr__(self, "sprite", sprite)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PetProfile":
        """Validate an exact JSON profile schema and normalize safe text."""

        keys = set(value)
        missing = _ALLOWED_PROFILE_FIELDS - keys
        extra = keys - _ALLOWED_PROFILE_FIELDS
        if missing or extra:
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(sorted(missing)))
            if extra:
                details.append("unexpected " + ", ".join(sorted(extra)))
            raise PetResponseError("Invalid pet profile fields: " + "; ".join(details))

        portrait = _validate_portrait(value["portrait"], json_array=True)
        palette = _validate_palette(value["palette"], json_array=True)
        sprite = _validate_sprite(value["sprite"], json_array=True)

        return cls(
            name=value["name"],
            species=value["species"],
            glyph=value["glyph"],
            personality=value["personality"],
            vision_bonus=value["vision_bonus"],
            catchphrase=value["catchphrase"],
            portrait=portrait,
            palette=palette,
            sprite=sprite,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible validated profile."""

        return {
            "name": self.name,
            "species": self.species,
            "glyph": self.glyph,
            "personality": self.personality,
            "vision_bonus": self.vision_bonus,
            "catchphrase": self.catchphrase,
            "portrait": list(self.portrait),
            "palette": list(self.palette),
            "sprite": list(self.sprite),
        }


@dataclass(frozen=True, slots=True)
class PetCreationResult:
    """Pet profile plus non-sensitive provenance suitable for local storage."""

    profile: PetProfile
    image_sha256: str
    provider: str
    endpoint_host: str
    model: str
    created_at: str

    def to_record(self) -> dict[str, Any]:
        """Serialize the result without an API key, image path, or image bytes."""

        return {
            "profile": self.profile.to_dict(),
            "image_sha256": self.image_sha256,
            "provider": self.provider,
            "endpoint_host": self.endpoint_host,
            "model": self.model,
            "created_at": self.created_at,
        }


OpenURL = Callable[..., Any]


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Never resend an image or Authorization header to a redirect target."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_RejectRedirects()).open


class PetAPIClient:
    """OpenAI-compatible multimodal pet profile client.

    The class never prints or prompts.  A UI should display ``endpoint_host`` and
    ask the user for consent before invoking :meth:`create_pet`.
    """

    def __init__(
        self,
        config: PetAPIConfig,
        *,
        opener: OpenURL | None = None,
    ) -> None:
        self.config = config
        self._opener = opener or _NO_REDIRECT_OPENER

    @property
    def endpoint_host(self) -> str:
        """Host-only value intended for a user-facing confirmation prompt."""

        return self.config.endpoint_host

    def create_pet(self, image_path: str | os.PathLike[str]) -> PetCreationResult:
        """Validate an image, request a profile, and return safe provenance."""

        image = prepare_image(image_path)
        return self.create_pet_from_prepared(image)

    def create_pet_from_prepared(
        self,
        image: PreparedImage,
    ) -> PetCreationResult:
        """Create a pet from the exact image payload approved by the user."""

        if not isinstance(image, PreparedImage):
            raise ImageValidationError("Prepared pet image is invalid.")
        request = self._build_request(image)
        raw_response = self._send(request)
        try:
            profile = parse_chat_completion(raw_response)
        except PetResponseError as exc:
            safe_message = _scrub_secret(str(exc), self.config.api_key)
            raise PetResponseError(safe_message) from None
        return PetCreationResult(
            profile=profile,
            image_sha256=image.sha256,
            provider=self.config.provider,
            endpoint_host=self.endpoint_host,
            model=self.config.model,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def _build_request(self, image: PreparedImage) -> urllib.request.Request:
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": _SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Create one friendly terminal-game companion "
                                "by abstracting colors and non-identifying visual "
                                "motifs from this image. Never identify or recreate "
                                "a person. Return JSON only."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image.data_url,
                            },
                        },
                    ],
                },
            ],
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        return urllib.request.Request(
            self.config.endpoint,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "User-Agent": f"ielts-codex-pet/{__version__}",
            },
        )

    def _send(self, request: urllib.request.Request) -> bytes:
        try:
            response = self._opener(request, timeout=self.config.timeout)
        except urllib.error.HTTPError as exc:
            detail = _read_http_error_detail(exc, self.config.api_key)
            suffix = f": {detail}" if detail else ""
            raise PetRequestError(
                f"PET API returned HTTP {exc.code}{suffix}"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            message = _scrub_secret(str(getattr(exc, "reason", exc)), self.config.api_key)
            raise PetRequestError(f"Could not reach PET API: {message}") from None
        except Exception as exc:
            message = _scrub_secret(str(exc), self.config.api_key)
            raise PetRequestError(f"PET API request failed: {message}") from None

        try:
            status = int(
                getattr(response, "status", getattr(response, "code", 200))
            )
            body = response.read(MAX_RESPONSE_BYTES + 1)
        except Exception as exc:
            message = _scrub_secret(str(exc), self.config.api_key)
            raise PetRequestError(f"Could not read PET API response: {message}") from None
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

        if not 200 <= status < 300:
            detail = _extract_api_error(body, self.config.api_key)
            suffix = f": {detail}" if detail else ""
            raise PetRequestError(f"PET API returned HTTP {status}{suffix}")
        if len(body) > MAX_RESPONSE_BYTES:
            raise PetResponseError("PET API response exceeds the 256 KiB limit.")
        return body


def prepare_image(image_path: str | os.PathLike[str]) -> PreparedImage:
    """Read and validate a PNG, JPEG, GIF, or WebP image from a local path."""

    path = Path(image_path).expanduser()
    try:
        if not path.is_file():
            raise ImageValidationError("Image path does not point to a regular file.")
        size_hint = path.stat().st_size
    except ImageValidationError:
        raise
    except OSError as exc:
        raise ImageValidationError(f"Could not inspect image: {exc}") from None

    if size_hint <= 0:
        raise ImageValidationError("Image is empty.")
    if size_hint > MAX_IMAGE_BYTES:
        raise ImageValidationError("Image exceeds the 10 MiB upload limit.")
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_IMAGE_BYTES + 1)
    except OSError as exc:
        raise ImageValidationError(f"Could not read image: {exc}") from None
    if not data:
        raise ImageValidationError("Image is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageValidationError("Image exceeds the 10 MiB upload limit.")

    mime_type = _detect_image_mime(data)
    if mime_type is None:
        raise ImageValidationError(
            "Unsupported image. Use a valid PNG, JPEG, GIF, or WebP file."
        )
    suffix_mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(path.suffix.lower())
    if suffix_mime is not None and suffix_mime != mime_type:
        raise ImageValidationError(
            "Image contents do not match the filename extension."
        )

    digest = hashlib.sha256(data).hexdigest()
    encoded = base64.b64encode(data).decode("ascii")
    return PreparedImage(
        mime_type=mime_type,
        size_bytes=len(data),
        sha256=digest,
        data_url=f"data:{mime_type};base64,{encoded}",
    )


def parse_chat_completion(raw_response: bytes | str) -> PetProfile:
    """Parse a Chat Completions response into a strictly validated profile."""

    if isinstance(raw_response, bytes):
        if len(raw_response) > MAX_RESPONSE_BYTES:
            raise PetResponseError("PET API response exceeds the 256 KiB limit.")
        try:
            response_text = raw_response.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PetResponseError("PET API response is not valid UTF-8.") from exc
    elif isinstance(raw_response, str):
        response_text = raw_response
        if len(response_text.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise PetResponseError("PET API response exceeds the 256 KiB limit.")
    else:
        raise PetResponseError("PET API response must be UTF-8 JSON.")

    envelope = _strict_json_loads(response_text, "PET API response")
    if not isinstance(envelope, dict):
        raise PetResponseError("PET API response must be a JSON object.")
    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices:
        raise PetResponseError("PET API response has no completion choice.")
    first = choices[0]
    if not isinstance(first, dict):
        raise PetResponseError("PET API completion choice is malformed.")
    message = first.get("message")
    if not isinstance(message, dict):
        raise PetResponseError("PET API completion message is malformed.")
    content = _extract_message_text(message.get("content"))

    fence_match = _JSON_FENCE_RE.fullmatch(content)
    if fence_match is not None:
        content = fence_match.group("body")
    profile_value = _strict_json_loads(content, "pet profile")
    if not isinstance(profile_value, dict):
        raise PetResponseError("Pet profile must be a JSON object.")
    return PetProfile.from_mapping(profile_value)


def _strict_json_loads(text: str, label: str) -> Any:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise PetResponseError(f"{label} contains duplicate field {key!r}.")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise PetResponseError(f"{label} contains invalid number {value}.")

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except PetResponseError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PetResponseError(f"{label} is not strict JSON: {exc}") from None


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list) and len(content) == 1:
        block = content[0]
        if (
            isinstance(block, dict)
            and block.get("type") in {"text", "output_text"}
            and isinstance(block.get("text"), str)
            and block["text"].strip()
        ):
            return block["text"]
    raise PetResponseError("PET API completion content must contain one text value.")


def _bounded_label(
    value: Any,
    field_name: str,
    minimum: int,
    maximum: int,
) -> str:
    if not isinstance(value, str):
        raise PetResponseError(f"Pet {field_name} must be text.")
    normalized = unicodedata.normalize("NFC", value).strip()
    if not minimum <= len(normalized) <= maximum:
        raise PetResponseError(
            f"Pet {field_name} must be {minimum}-{maximum} characters."
        )
    for char in normalized:
        category = unicodedata.category(char)
        if category[0] not in {"L", "N"} and char not in {" ", "-", "'", "’", "·"}:
            raise PetResponseError(
                f"Pet {field_name} contains unsupported characters."
            )
    return normalized


def _bounded_text(value: Any, field_name: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise PetResponseError(f"Pet {field_name} must be text.")
    normalized = unicodedata.normalize("NFC", value).strip()
    if not minimum <= len(normalized) <= maximum:
        raise PetResponseError(
            f"Pet {field_name} must be {minimum}-{maximum} characters."
        )
    if not _is_single_line_printable(normalized):
        raise PetResponseError(f"Pet {field_name} contains unsafe characters.")
    return normalized


def _validate_glyph(value: Any) -> str:
    if not isinstance(value, str):
        raise PetResponseError("Pet glyph must be text.")
    glyph = unicodedata.normalize("NFC", value).strip()
    if glyph not in _PET_GLYPHS:
        raise PetResponseError(
            "Pet glyph must be one of these map-safe ASCII characters: &*;:mpcd."
        )
    return glyph


def _validate_vision_bonus(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PetResponseError("Pet vision_bonus must be an integer.")
    if not 1 <= value <= 3:
        raise PetResponseError("Pet vision_bonus must be between 1 and 3.")
    return value


def _validate_portrait(value: Any, *, json_array: bool) -> tuple[str, ...]:
    expected_type = list if json_array else tuple
    if not isinstance(value, expected_type) or not 1 <= len(value) <= 7:
        raise PetResponseError("Pet portrait must contain 1-7 ASCII-art lines.")
    portrait: list[str] = []
    for index, raw_line in enumerate(value, start=1):
        if not isinstance(raw_line, str):
            raise PetResponseError(f"Pet portrait line {index} must be text.")
        line = raw_line.rstrip()
        if (
            not line
            or len(line) > 24
            or any(char not in _PORTRAIT_ALLOWED for char in line)
        ):
            raise PetResponseError(
                f"Pet portrait line {index} must be 1-24 printable ASCII "
                "characters."
            )
        portrait.append(line)
    return tuple(portrait)


def _validate_palette(
    value: Any,
    *,
    json_array: bool,
) -> tuple[str, str, str]:
    expected_type = list if json_array else tuple
    if not isinstance(value, expected_type) or len(value) != 3:
        raise PetResponseError("Pet palette must contain exactly 3 colors.")
    colors: list[str] = []
    for index, color in enumerate(value, start=1):
        if not isinstance(color, str) or _PALETTE_COLOR_RE.fullmatch(color) is None:
            raise PetResponseError(
                f"Pet palette color {index} must use strict #RRGGBB hex format."
            )
        colors.append(color.upper())
    if len(set(colors)) != 3:
        raise PetResponseError("Pet palette colors must be distinct.")
    return colors[0], colors[1], colors[2]


def _validate_sprite(value: Any, *, json_array: bool) -> tuple[str, ...]:
    expected_type = list if json_array else tuple
    if not isinstance(value, expected_type) or len(value) != _SPRITE_HEIGHT:
        raise PetResponseError(
            f"Pet sprite must contain exactly {_SPRITE_HEIGHT} pixel rows."
        )
    sprite: list[str] = []
    for index, row in enumerate(value, start=1):
        if not isinstance(row, str):
            raise PetResponseError(f"Pet sprite row {index} must be text.")
        if len(row) != _SPRITE_WIDTH or any(
            pixel not in _SPRITE_PIXELS for pixel in row
        ):
            raise PetResponseError(
                f"Pet sprite row {index} must be exactly {_SPRITE_WIDTH} "
                "characters using only '.', '1', '2', and '3'."
            )
        sprite.append(row)
    used_pixels = set("".join(sprite))
    if not {"1", "2", "3"}.issubset(used_pixels):
        raise PetResponseError("Pet sprite must use all 3 palette colors.")
    return tuple(sprite)


def _is_single_line_printable(value: str) -> bool:
    return bool(value) and all(
        char.isprintable()
        and char not in {"\r", "\n"}
        and not unicodedata.category(char).startswith("C")
        for char in value
    )


def _detect_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _read_http_error_detail(
    error: urllib.error.HTTPError,
    secret: str,
) -> str:
    try:
        body = error.read(MAX_RESPONSE_BYTES + 1)
    except Exception:
        return ""
    return _extract_api_error(body, secret)


def _extract_api_error(body: bytes, secret: str) -> str:
    if len(body) > MAX_RESPONSE_BYTES:
        return ""
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(value, dict):
        return ""
    error = value.get("error")
    if not isinstance(error, dict) or not isinstance(error.get("message"), str):
        return ""
    message = _scrub_secret(error["message"], secret).strip()
    if not _is_single_line_printable(message):
        return ""
    return message[:300]


def _scrub_secret(message: str, secret: str) -> str:
    scrubbed = message.replace(secret, "[REDACTED]") if secret else message
    return "".join(
        char if char.isprintable() and char not in {"\r", "\n"} else " "
        for char in scrubbed
    )[:500]


_SYSTEM_PROMPT = """\
You create a friendly pet for a terminal vocabulary survival game.
Abstract only non-identifying visual motifs from the uploaded image: its palette,
silhouette, texture, mood, or accessories. Never identify a person or infer
sensitive traits. If a person appears, do not recreate their face or body; make
an unrelated fictional non-human pet inspired only by safe visual motifs.
Return exactly one JSON object with exactly these fields:
- "name": 1-20 letters/numbers, spaces, apostrophes, or hyphens
- "species": 1-32 letters/numbers, spaces, apostrophes, or hyphens
- "glyph": exactly one map-safe ASCII character chosen from & * ; : m p c d
- "personality": one short, single-line description, at most 96 characters
- "vision_bonus": an integer from 1 to 3
- "catchphrase": one short, single-line phrase, at most 96 characters
- "portrait": an array of 1-7 printable ASCII-art lines, each at most 24 characters
- "palette": exactly 3 distinct colors in #RRGGBB format, ordered as main/outline,
  secondary, then highlight
- "sprite": exactly 6 strings of exactly 7 characters each; use only "." for
  transparency and "1", "2", "3" for the matching palette colors
The sprite must be a centered, readable, full-body pet and use all three colors.
Do not use Markdown, ANSI escapes, extra fields, or explanatory text.
"""
