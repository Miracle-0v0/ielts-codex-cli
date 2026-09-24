"""Release comparisons and the package actually built for installation."""
import os
import re
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ielts_codex import __version__
from ielts_codex.updater import (
    InstallTarget, PROJECT_GITHUB_URL, ProjectRelease, ProjectUpdateError,
    ProjectUpdater, parse_current_version, parse_release_metadata, parse_version,
)


def stable_release(version):
    return ProjectRelease(
        version=version, version_tuple=parse_version(version),
        tag_name=f"v{version}", html_url=f"{PROJECT_GITHUB_URL}/releases/tag/v{version}",
        published_at="2026-09-24T00:00:00Z",
    )


class ReleaseVersionTests(unittest.TestCase):
    def check_update(self, current, latest):
        updater = ProjectUpdater(current_version=current)
        with patch.object(updater, "discover_latest", return_value=stable_release(latest)), \
             patch.object(updater, "detect_install", return_value=InstallTarget("pip", ROOT, "test")):
            return updater.update(dry_run=True).status

    def test_candidate_can_upgrade_to_same_version_final(self):
        self.assertEqual(self.check_update("1.0.0rc1", "1.0.0"), "available")
        self.assertEqual(self.check_update("1.0.0rc2", "1.0.0"), "available")

    def test_candidate_never_downgrades_to_older_stable(self):
        self.assertEqual(self.check_update("1.0.0rc1", "0.6.10"), "ahead")

    def test_stable_version_behavior_is_preserved(self):
        self.assertEqual(self.check_update("1.0.0", "1.0.0"), "up_to_date")
        self.assertEqual(self.check_update("1.0.0", "1.0.1"), "available")
        self.assertEqual(self.check_update("1.0.1", "1.0.0"), "ahead")

    def test_download_targets_remain_stable_only(self):
        for prerelease in (True, False):
            with self.subTest(prerelease=prerelease), self.assertRaises(ProjectUpdateError):
                parse_release_metadata({
                    "draft": False, "prerelease": prerelease, "tag_name": "v1.0.0rc1",
                    "published_at": "2026-09-24T00:00:00Z",
                    "html_url": f"{PROJECT_GITHUB_URL}/releases/tag/v1.0.0rc1",
                    "assets": [],
                })
        with self.assertRaises(ProjectUpdateError):
            parse_version("1.0.0rc1")

    def test_malformed_current_versions_are_rejected(self):
        for version in ("1.0.0rc", "1.0.0rc-1", "1.0.0rc01", "1.0.0.dev1", "1.0.0+local"):
            with self.subTest(version=version), self.assertRaises(ProjectUpdateError):
                parse_current_version(version)

    def test_package_and_runtime_versions_match(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"\n]+)"', pyproject, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), __version__)
        parse_current_version(__version__)

    @unittest.skipUnless(os.environ.get("IELTS_CODEX_TEST_WHEEL_DIR"), "run after building a wheel")
    def test_built_wheel_contains_all_runtime_code_and_content(self):
        directory = Path(os.environ["IELTS_CODEX_TEST_WHEEL_DIR"])
        wheel = directory / f"ielts_codex-{__version__}-py3-none-any.whl"
        self.assertTrue(wheel.is_file(), str(wheel))
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            package_root = ROOT / "src" / "ielts_codex"
            expected = {
                "ielts_codex/" + path.relative_to(package_root).as_posix()
                for pattern in ("**/*.py", "data/*.json")
                for path in package_root.glob(pattern)
            }
            self.assertFalse(expected - names, f"Missing package files: {sorted(expected - names)}")
        base, _ = parse_current_version(__version__)
        # Validate the local build structurally. Release discovery still rejects RC downloads.
        release = ProjectRelease(__version__, base, f"v{__version__}", "", "local")
        ProjectUpdater()._validate_wheel(wheel, release)


if __name__ == "__main__":
    unittest.main()
