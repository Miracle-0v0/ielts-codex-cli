"""Open English WordNet release discovery and vocabulary synchronization.

OEWN content is downloaded only when the user explicitly runs the sync
command. Synced definitions are stored in a local overlay under CC BY 4.0;
the bundled, MIT-licensed vocabulary file is never rewritten.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import __version__
from .models import Word


OEWN_RELEASE_API = (
    "https://api.github.com/repos/globalwordnet/english-wordnet/releases/latest"
)
OEWN_HOMEPAGE = "https://en-word.net/"
OEWN_LICENSE = "CC BY 4.0"
OEWN_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
OVERLAY_SCHEMA_VERSION = 1
OVERLAY_FILENAME = "oewn_overlay.json"
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 160 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 256

DOWNLOAD_RE = re.compile(
    r"https://en-word\.net/static/english-wordnet-(?P<version>\d{4})-json\.zip"
)
ASSET_NAME_RE = re.compile(
    r"^english-wordnet-(?P<version>\d{4})-json\.zip$"
)
TAG_RE = re.compile(r"^(?P<version>\d{4})-edition$")
SHA256_RE = re.compile(r"^sha256:(?P<digest>[0-9a-f]{64})$", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-z]+")
EXPECTED_POS = {
    "n.": {"n"},
    "v.": {"v"},
    "adj.": {"a", "s"},
    "adv.": {"r"},
}
SYNSET_PREFIXES = {
    "n": ("noun.",),
    "v": ("verb.",),
    "a": ("adj.",),
    "s": ("adj.",),
    "r": ("adv.",),
}
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "being",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "to",
    "used",
    "which",
    "with",
}


class OEWNSyncError(RuntimeError):
    """Raised when authoritative vocabulary synchronization cannot continue."""


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    version: str
    tag_name: str
    published_at: str
    release_url: str
    download_url: str
    asset_size: int | None = None
    asset_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class SynsetCandidate:
    synset_id: str
    part_of_speech: str
    definition: str
    members: tuple[str, ...]
    rank: int


@dataclass(frozen=True, slots=True)
class SelectedSense:
    candidate: SynsetCandidate
    match_score: float


@dataclass(slots=True)
class SyncResult:
    release: ReleaseInfo
    entries: dict[str, dict[str, Any]]
    skipped: dict[str, str]
    changed: int
    archive_sha256: str
    overlay_path: Path
    dry_run: bool = False
    up_to_date: bool = False


def parse_release_metadata(payload: Mapping[str, Any]) -> ReleaseInfo:
    """Validate GitHub release metadata and select the standard JSON archive."""

    release_url = str(payload.get("html_url", "")).strip()
    tag_name = str(payload.get("tag_name", "")).strip()
    published_at = str(payload.get("published_at", "")).strip()
    tag_match = TAG_RE.fullmatch(tag_name)
    if tag_match is None or not published_at:
        raise OEWNSyncError("OEWN release metadata is incomplete.")
    expected_release_url = (
        "https://github.com/globalwordnet/english-wordnet/releases/tag/"
        f"{tag_name}"
    )
    if release_url != expected_release_url:
        raise OEWNSyncError("Unexpected OEWN release URL.")

    version = tag_match.group("version")
    assets_value = payload.get("assets", [])
    if not isinstance(assets_value, list):
        raise OEWNSyncError("OEWN release assets metadata is invalid.")
    core_assets: list[tuple[Mapping[str, Any], re.Match[str]]] = []
    for raw_asset in assets_value:
        if not isinstance(raw_asset, Mapping):
            continue
        name_match = ASSET_NAME_RE.fullmatch(str(raw_asset.get("name", "")))
        if name_match is not None:
            core_assets.append((raw_asset, name_match))

    if len(core_assets) > 1:
        raise OEWNSyncError("The OEWN release advertises duplicate core JSON assets.")
    if core_assets:
        asset, name_match = core_assets[0]
        if name_match.group("version") != version:
            raise OEWNSyncError("The OEWN asset version does not match its release tag.")
        asset_name = name_match.group(0)
        expected_download_url = (
            "https://github.com/globalwordnet/english-wordnet/releases/download/"
            f"{tag_name}/{asset_name}"
        )
        download_url = str(asset.get("browser_download_url", "")).strip()
        if download_url != expected_download_url:
            raise OEWNSyncError("Unexpected OEWN asset download URL.")
        asset_size = asset.get("size")
        if (
            isinstance(asset_size, bool)
            or not isinstance(asset_size, int)
            or asset_size <= 0
            or asset_size > MAX_ARCHIVE_BYTES
        ):
            raise OEWNSyncError("OEWN asset size metadata is invalid.")
        digest_match = SHA256_RE.fullmatch(str(asset.get("digest", "")).strip())
        if digest_match is None:
            raise OEWNSyncError("OEWN asset is missing a valid SHA-256 digest.")
        return ReleaseInfo(
            version=version,
            tag_name=tag_name,
            published_at=published_at,
            release_url=release_url,
            download_url=download_url,
            asset_size=asset_size,
            asset_sha256=digest_match.group("digest").lower(),
        )

    # Older OEWN releases predate GitHub's asset digest metadata. Keep the
    # official release-body URL as a compatibility fallback, but current
    # releases should always take the verified asset path above.
    body_match = DOWNLOAD_RE.search(str(payload.get("body", "")))
    if body_match is None or body_match.group("version") != version:
        raise OEWNSyncError(
            "The latest OEWN release does not advertise a standard JSON archive."
        )
    return ReleaseInfo(
        version=version,
        tag_name=tag_name,
        published_at=published_at,
        release_url=release_url,
        download_url=body_match.group(0),
    )


def load_overlay(path: Path | str) -> dict[str, Any]:
    """Load and minimally validate a saved OEWN overlay."""

    overlay_path = Path(path)
    try:
        payload = json.loads(overlay_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OEWNSyncError(f"Cannot read OEWN overlay {overlay_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise OEWNSyncError("OEWN overlay root must be a JSON object.")
    schema_version = payload.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != OVERLAY_SCHEMA_VERSION
    ):
        raise OEWNSyncError("Unsupported OEWN overlay schema version.")
    provider = payload.get("provider")
    entries = payload.get("entries")
    if not isinstance(provider, dict) or provider.get("id") != "oewn":
        raise OEWNSyncError("OEWN overlay provider metadata is invalid.")
    provider_version = provider.get("version")
    if (
        not isinstance(provider_version, str)
        or TAG_RE.fullmatch(f"{provider_version}-edition") is None
    ):
        raise OEWNSyncError("OEWN overlay provider version is missing.")
    if not isinstance(entries, dict):
        raise OEWNSyncError("OEWN overlay entries must be a JSON object.")
    if not all(
        isinstance(key, str) and isinstance(value, dict)
        for key, value in entries.items()
    ):
        raise OEWNSyncError("OEWN overlay entries contain invalid records.")
    if not isinstance(payload.get("skipped", {}), dict):
        raise OEWNSyncError("OEWN overlay skipped entries must be a JSON object.")
    return payload


def apply_overlay(words: Iterable[Word], payload: Mapping[str, Any]) -> tuple[Word, ...]:
    """Apply only English definitions while preserving all curated fields."""

    provider = payload["provider"]
    version = str(provider["version"])
    source_url = str(
        provider.get("release_url")
        or provider.get("homepage")
        or OEWN_HOMEPAGE
    )
    overlay_entries = payload["entries"]
    merged: list[Word] = []
    for word in words:
        update = overlay_entries.get(word.word)
        if not isinstance(update, dict):
            merged.append(word)
            continue
        definition_value = update.get("definition_en")
        if not isinstance(definition_value, str):
            merged.append(word)
            continue
        definition = definition_value.strip()
        if not definition:
            merged.append(word)
            continue
        replacements: dict[str, Any] = {"definition_en": definition}
        optional_metadata = {
            "definition_source": f"Open English WordNet {version}",
            "definition_license": OEWN_LICENSE,
            "definition_source_url": source_url,
        }
        replacements.update(
            (name, value)
            for name, value in optional_metadata.items()
            if hasattr(word, name)
        )
        merged.append(replace(word, **replacements))
    return tuple(merged)


class OEWNClient:
    """Small standard-library client for official OEWN release assets."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen
        self.user_agent = f"ielts-codex/{__version__} (+https://github.com/Miracle-0v0/ielts-codex-cli)"

    def discover_latest(self) -> ReleaseInfo:
        request = urllib.request.Request(
            OEWN_RELEASE_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": self.user_agent,
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = self._read_limited(response, MAX_METADATA_BYTES)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise OEWNSyncError(f"Cannot check the latest OEWN release: {exc}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OEWNSyncError("GitHub returned invalid OEWN release metadata.") from exc
        if not isinstance(payload, dict):
            raise OEWNSyncError("GitHub returned unexpected OEWN release metadata.")
        return parse_release_metadata(payload)

    def download(
        self,
        release: ReleaseInfo,
        directory: Path,
    ) -> tuple[Path, str]:
        directory.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(
            release.download_url,
            headers={"Accept": "application/zip", "User-Agent": self.user_agent},
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".oewn-download-", suffix=".zip", dir=directory
        )
        temporary_path = Path(temporary_name)
        digest = hashlib.sha256()
        received = 0
        try:
            with os.fdopen(descriptor, "wb") as output:
                try:
                    with self._opener(request, timeout=self.timeout) as response:
                        headers = getattr(response, "headers", {})
                        length_value = headers.get("Content-Length")
                        content_length: int | None = None
                        if length_value:
                            try:
                                content_length = int(length_value)
                            except (TypeError, ValueError) as exc:
                                raise OEWNSyncError(
                                    "OEWN returned an invalid Content-Length."
                                ) from exc
                            if (
                                content_length <= 0
                                or content_length > MAX_ARCHIVE_BYTES
                            ):
                                raise OEWNSyncError(
                                    "OEWN archive is larger than expected."
                                )
                            if (
                                release.asset_size is not None
                                and content_length != release.asset_size
                            ):
                                raise OEWNSyncError(
                                    "OEWN download size does not match release metadata."
                                )
                        while True:
                            chunk = response.read(64 * 1024)
                            if not chunk:
                                break
                            received += len(chunk)
                            if received > MAX_ARCHIVE_BYTES:
                                raise OEWNSyncError(
                                    "OEWN archive exceeded the safe download limit."
                                )
                            output.write(chunk)
                            digest.update(chunk)
                except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                    raise OEWNSyncError(f"Cannot download OEWN data: {exc}") from exc
                output.flush()
                os.fsync(output.fileno())
            if received == 0:
                raise OEWNSyncError("OEWN returned an empty archive.")
            if release.asset_size is not None and received != release.asset_size:
                raise OEWNSyncError(
                    "OEWN download size does not match release metadata."
                )
            actual_sha256 = digest.hexdigest()
            if (
                release.asset_sha256 is not None
                and actual_sha256 != release.asset_sha256
            ):
                raise OEWNSyncError(
                    "OEWN download SHA-256 does not match release metadata."
                )
            _validate_archive(temporary_path)
            return temporary_path, actual_sha256
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _read_limited(response: Any, limit: int) -> bytes:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise OEWNSyncError("Remote metadata exceeded the safe size limit.")
        return data


class OEWNSynchronizer:
    def __init__(self, client: OEWNClient | None = None) -> None:
        self.client = client or OEWNClient()

    def synchronize(
        self,
        words: Iterable[Word],
        data_dir: Path | str,
        *,
        force: bool = False,
        dry_run: bool = False,
        release: ReleaseInfo | None = None,
        archive_path: Path | str | None = None,
    ) -> SyncResult:
        items = tuple(words)
        destination_dir = Path(data_dir)
        overlay_path = destination_dir / OVERLAY_FILENAME
        release_info = release or self.client.discover_latest()

        if overlay_path.exists() and not force and archive_path is None:
            try:
                existing = load_overlay(overlay_path)
            except OEWNSyncError:
                # A damaged cache is never used, but it also should not prevent
                # an explicitly requested online refresh from repairing it.
                existing = None
        else:
            existing = None
        if existing is not None:
            provider = existing["provider"]
            same_version = str(provider.get("version")) == release_info.version
            stored_digest = str(provider.get("archive_sha256", "")).lower()
            same_asset = (
                release_info.asset_sha256 is None
                or stored_digest == release_info.asset_sha256
            )
            if same_version and same_asset:
                return SyncResult(
                    release=release_info,
                    entries=dict(existing["entries"]),
                    skipped=dict(existing.get("skipped", {})),
                    changed=0,
                    archive_sha256=str(
                        existing["provider"].get("archive_sha256", "")
                    ),
                    overlay_path=overlay_path,
                    dry_run=dry_run,
                    up_to_date=True,
                )

        downloaded_path: Path | None = None
        if archive_path is None:
            downloaded_path, archive_sha256 = self.client.download(
                release_info, destination_dir
            )
            source_path = downloaded_path
        else:
            source_path = Path(archive_path)
            archive_sha256 = _verify_archive_identity(source_path, release_info)

        try:
            entries, skipped = build_overlay_entries(source_path, items)
        finally:
            if downloaded_path is not None:
                downloaded_path.unlink(missing_ok=True)

        changed = sum(
            1
            for word in items
            if word.word in entries
            and entries[word.word]["definition_en"] != word.definition_en
        )
        result = SyncResult(
            release=release_info,
            entries=entries,
            skipped=skipped,
            changed=changed,
            archive_sha256=archive_sha256,
            overlay_path=overlay_path,
            dry_run=dry_run,
        )
        if not dry_run:
            payload = _overlay_payload(result)
            _atomic_write_json(overlay_path, payload)
        return result


def build_overlay_entries(
    archive_path: Path | str,
    words: Iterable[Word],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Parse only the bundled lemmas and choose a matching OEWN sense."""

    items = tuple(words)
    _validate_archive(Path(archive_path))
    candidates_by_word: dict[str, list[SynsetCandidate]] = {
        item.word: [] for item in items
    }
    pending: dict[str, list[tuple[str, str, int]]] = {}
    skipped: dict[str, str] = {}

    with zipfile.ZipFile(archive_path) as archive:
        by_bucket: dict[str, list[Word]] = {}
        for item in items:
            first = item.word[:1].lower()
            bucket = first if first.isascii() and first.isalpha() else "0"
            by_bucket.setdefault(bucket, []).append(item)

        needed_pos: set[str] = set()
        for bucket, bucket_words in by_bucket.items():
            member = f"entries-{bucket}.json"
            if member not in archive.namelist():
                for item in bucket_words:
                    skipped[item.word] = "entry index is missing"
                continue
            index = _load_member_json(archive, member)
            if not isinstance(index, dict):
                raise OEWNSyncError(f"{member} must contain a JSON object.")
            for item in bucket_words:
                raw_entry = index.get(item.word)
                if not isinstance(raw_entry, dict):
                    folded_matches = [
                        value
                        for lemma, value in index.items()
                        if isinstance(lemma, str)
                        and lemma.casefold() == item.word.casefold()
                        and isinstance(value, dict)
                    ]
                    if len(folded_matches) == 1:
                        raw_entry = folded_matches[0]
                if not isinstance(raw_entry, dict):
                    skipped[item.word] = "lemma not found"
                    continue
                expected = EXPECTED_POS.get(
                    item.part_of_speech,
                    set(SYNSET_PREFIXES),
                )
                found_sense = False
                for pos_bucket, pos_entry in raw_entry.items():
                    canonical_pos = _canonical_pos_bucket(pos_bucket)
                    if (
                        canonical_pos not in expected
                        or not isinstance(pos_entry, dict)
                    ):
                        continue
                    senses = pos_entry.get("sense", [])
                    if not isinstance(senses, list):
                        continue
                    needed_pos.add(canonical_pos)
                    for rank, sense in enumerate(senses):
                        if not isinstance(sense, dict):
                            continue
                        synset_id = str(sense.get("synset", "")).strip()
                        if not synset_id:
                            continue
                        pending.setdefault(synset_id, []).append(
                            (item.word, canonical_pos, rank)
                        )
                        found_sense = True
                if not found_sense:
                    skipped[item.word] = "no matching part of speech"

        prefixes = {
            prefix
            for pos in needed_pos
            for prefix in SYNSET_PREFIXES.get(pos, ())
        }
        unresolved = set(pending)
        for member in archive.namelist():
            if not member.endswith(".json") or not member.startswith(tuple(prefixes)):
                continue
            synsets = _load_member_json(archive, member)
            if not isinstance(synsets, dict):
                raise OEWNSyncError(f"{member} must contain a JSON object.")
            for synset_id in tuple(unresolved.intersection(synsets)):
                raw_synset = synsets[synset_id]
                if not isinstance(raw_synset, dict):
                    continue
                definitions = raw_synset.get("definition", [])
                if isinstance(definitions, str):
                    definitions = [definitions]
                elif not isinstance(definitions, list):
                    continue
                definition = next(
                    (
                        value.strip()
                        for value in definitions
                        if isinstance(value, str) and value.strip()
                    ),
                    "",
                )
                if not definition:
                    continue
                members_value = raw_synset.get("members", [])
                members = (
                    tuple(
                        value.strip()
                        for value in members_value
                        if isinstance(value, str) and value.strip()
                    )
                    if isinstance(members_value, list)
                    else ()
                )
                raw_part_of_speech = str(
                    raw_synset.get("partOfSpeech", "")
                ).strip()
                for word_name, entry_pos, rank in pending[synset_id]:
                    part_of_speech = (
                        raw_part_of_speech
                        if raw_part_of_speech in SYNSET_PREFIXES
                        else entry_pos
                    )
                    candidates_by_word[word_name].append(
                        SynsetCandidate(
                            synset_id=synset_id,
                            part_of_speech=part_of_speech,
                            definition=definition,
                            members=members,
                            rank=rank,
                        )
                    )
                unresolved.discard(synset_id)

    output: dict[str, dict[str, Any]] = {}
    by_name = {item.word: item for item in items}
    for word_name, candidates in candidates_by_word.items():
        if not candidates:
            skipped.setdefault(word_name, "no definition found")
            continue
        selection = select_sense(by_name[word_name], candidates)
        if selection is None:
            skipped[word_name] = "ambiguous sense"
            continue
        candidate = selection.candidate
        synonyms = _unique(
            member
            for member in candidate.members
            if member.strip().casefold() != word_name.casefold()
        )
        output[word_name] = {
            "definition_en": candidate.definition,
            "synonyms": synonyms,
            "synset_id": candidate.synset_id,
            "part_of_speech": candidate.part_of_speech,
            "match_score": round(selection.match_score, 4),
        }
        skipped.pop(word_name, None)
    return dict(sorted(output.items())), dict(sorted(skipped.items()))


def select_sense(
    word: Word,
    candidates: Iterable[SynsetCandidate],
) -> SelectedSense | None:
    """Select the OEWN sense closest to the project's curated definition."""

    options = tuple(candidates)
    if not options:
        return None
    if len(options) == 1:
        return SelectedSense(options[0], 1.0)

    current_tokens = _content_tokens(
        " ".join((word.definition_en, *word.synonyms))
    )
    scored: list[tuple[float, SynsetCandidate]] = []
    for candidate in options:
        candidate_tokens = _content_tokens(
            " ".join((candidate.definition, *candidate.members))
        )
        intersection = len(current_tokens.intersection(candidate_tokens))
        denominator = math.sqrt(
            max(1, len(current_tokens)) * max(1, len(candidate_tokens))
        )
        token_score = intersection / denominator
        sequence_score = SequenceMatcher(
            None,
            _normalized_text(word.definition_en),
            _normalized_text(candidate.definition),
        ).ratio()
        synonym_score = _synonym_overlap(word.synonyms, candidate.members)
        rank_bonus = max(0.0, 0.04 - candidate.rank * 0.01)
        score = (
            token_score * 0.55
            + sequence_score * 0.30
            + synonym_score * 0.15
            + rank_bonus
        )
        scored.append((score, candidate))

    scored.sort(key=lambda item: (-item[0], item[1].rank, item[1].synset_id))
    best_score, best = scored[0]
    second_score = scored[1][0]
    if best_score < 0.12 and best_score - second_score < 0.035:
        return None
    return SelectedSense(best, min(1.0, best_score))


def _overlay_payload(result: SyncResult) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": OVERLAY_SCHEMA_VERSION,
        "created_at": now,
        "synced_at": now,
        "provider": {
            "id": "oewn",
            "name": "Open English WordNet",
            "version": result.release.version,
            "tag_name": result.release.tag_name,
            "published_at": result.release.published_at,
            "release_url": result.release.release_url,
            "download_url": result.release.download_url,
            "homepage": OEWN_HOMEPAGE,
            "license": OEWN_LICENSE,
            "license_url": OEWN_LICENSE_URL,
            "archive_sha256": result.archive_sha256,
            "archive_size": result.release.asset_size,
            "attribution": (
                "Open English WordNet, derived from Princeton WordNet, "
                "licensed under CC BY 4.0."
            ),
        },
        "entries": result.entries,
        "skipped": result.skipped,
    }


def _validate_archive(path: Path) -> None:
    if not path.is_file():
        raise OEWNSyncError(f"OEWN archive does not exist: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos:
                raise OEWNSyncError("OEWN archive is empty.")
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise OEWNSyncError("OEWN archive contains too many members.")
            total_size = sum(info.file_size for info in infos)
            if total_size > MAX_UNCOMPRESSED_BYTES:
                raise OEWNSyncError("OEWN archive exceeds the safe expanded size.")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise OEWNSyncError("OEWN archive contains duplicate member names.")
            if "entries-a.json" not in names or not any(
                name.startswith("noun.") for name in names
            ):
                raise OEWNSyncError("OEWN archive structure is not recognized.")
            for info in infos:
                if info.flag_bits & 0x1:
                    raise OEWNSyncError("Encrypted OEWN archives are not supported.")
                path_value = Path(info.filename)
                if (
                    path_value.is_absolute()
                    or len(path_value.parts) != 1
                    or "\\" in info.filename
                    or ".." in path_value.parts
                    or not info.filename.endswith(".json")
                ):
                    raise OEWNSyncError("Unsafe path found in OEWN archive.")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise OEWNSyncError(
                    f"OEWN archive failed its CRC check at {bad_member}."
                )
    except OEWNSyncError:
        raise
    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as exc:
        raise OEWNSyncError(
            "Downloaded OEWN data is not a valid ZIP archive."
        ) from exc


def _load_member_json(archive: zipfile.ZipFile, member: str) -> Any:
    try:
        raw = archive.read(member)
        return json.loads(raw.decode("utf-8"))
    except KeyError as exc:
        raise OEWNSyncError(f"OEWN archive is missing {member}.") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OEWNSyncError(f"OEWN member {member} is not valid JSON.") from exc


def _canonical_pos_bucket(value: object) -> str:
    """Return the OEWN POS code from buckets such as ``n-1`` or ``a-2``."""

    canonical = str(value).split("-", 1)[0]
    return canonical if canonical in SYNSET_PREFIXES else ""


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".oewn-overlay-", suffix=".json", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(64 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise OEWNSyncError(f"Cannot read OEWN archive {path}: {exc}") from exc
    return digest.hexdigest()


def _verify_archive_identity(path: Path, release: ReleaseInfo) -> str:
    """Verify an injected archive against the selected release metadata."""

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise OEWNSyncError(f"Cannot read OEWN archive {path}: {exc}") from exc
    if size <= 0 or size > MAX_ARCHIVE_BYTES:
        raise OEWNSyncError("OEWN archive size is outside the safe range.")
    if release.asset_size is not None and size != release.asset_size:
        raise OEWNSyncError(
            "OEWN archive size does not match release metadata."
        )
    digest = _sha256_file(path)
    if release.asset_sha256 is not None and digest != release.asset_sha256:
        raise OEWNSyncError(
            "OEWN archive SHA-256 does not match release metadata."
        )
    return digest


def _normalized_text(value: str) -> str:
    return " ".join(TOKEN_RE.findall(value.lower()))


def _content_tokens(value: str) -> set[str]:
    return {
        _light_stem(token)
        for token in TOKEN_RE.findall(value.lower())
        if token not in STOPWORDS
    }


def _light_stem(token: str) -> str:
    if len(token) > 6 and token.endswith("ation"):
        return token[:-5]
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 4 and token.endswith("al"):
        return token[:-2]
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def _synonym_overlap(current: Iterable[str], candidate: Iterable[str]) -> float:
    current_set = {value.strip().casefold() for value in current if value.strip()}
    candidate_set = {
        value.strip().casefold() for value in candidate if value.strip()
    }
    if not current_set or not candidate_set:
        return 0.0
    return len(current_set.intersection(candidate_set)) / len(current_set)


def _unique(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            output.append(cleaned)
    return output
