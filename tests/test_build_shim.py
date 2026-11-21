"""pio/build-shim.sh refuses a piolib that is not at utils b57c48e, unless told to go ahead.

The shim reads the start of piolib's private RP1 handle (pio/wlsync_pio.c,
struct wlsync_rp1_handle_prefix), checked against utils b57c48e only. The script runs
against a fake piolib, a tiny git repository, with a stub `cc` on PATH that records that
it was called and writes nothing, so no real compiler or piolib is needed.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "pio" / "build-shim.sh"
CHECKED = "b57c48e"

pytestmark = pytest.mark.skipif(
    shutil.which("sh") is None or shutil.which("git") is None,
    reason="needs sh and git")


def _git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def stub_path(tmp_path):
    """A bin directory whose `cc` records its call and writes nothing."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cc = bin_dir / "cc"
    cc.write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/cc-calls"\n')
    cc.chmod(cc.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def _run(tmp_path, stub_path, piolib, env_extra=None):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    env = dict(os.environ, PATH=f"{stub_path}{os.pathsep}{os.environ['PATH']}")
    env.pop("WLSYNC_ALLOW_UNCHECKED_PIOLIB", None)
    env.update(env_extra or {})
    done = subprocess.run(["sh", str(SCRIPT), str(piolib), str(out)], env=env,
                          capture_output=True, text=True)
    return done, out, tmp_path / "cc-calls"


def _fake_piolib(tmp_path, commit_message="other"):
    utils = tmp_path / "utils"
    (utils / "piolib").mkdir(parents=True)
    (utils / "piolib" / "piolib.c").write_text("/* fake */\n")
    _git(utils, "init", "-q")
    _git(utils, "add", ".")
    _git(utils, "commit", "-q", "-m", commit_message)
    return utils / "piolib"


def test_refuses_an_unchecked_commit_and_builds_nothing(tmp_path, stub_path):
    piolib = _fake_piolib(tmp_path)
    head = _git(piolib, "rev-parse", "HEAD")
    done, out, calls = _run(tmp_path, stub_path, piolib)
    assert done.returncode != 0
    assert not calls.exists(), "the compiler ran before the refusal"
    assert not (out / "libwlsync_pio.so").exists()
    # names the commit found, the one expected, why it matters, and the override
    assert head in done.stderr
    assert CHECKED in done.stderr
    assert "wlsync_rp1_handle_prefix" in done.stderr
    assert "WLSYNC_ALLOW_UNCHECKED_PIOLIB=1" in done.stderr


def test_refuses_a_directory_that_is_not_a_git_checkout(tmp_path, stub_path):
    piolib = tmp_path / "plain" / "piolib"
    piolib.mkdir(parents=True)
    done, out, calls = _run(tmp_path, stub_path, piolib)
    assert done.returncode != 0
    assert not calls.exists()
    assert "unknown" in done.stderr
    assert "WLSYNC_ALLOW_UNCHECKED_PIOLIB=1" in done.stderr


def test_the_override_builds_and_prints_the_warning(tmp_path, stub_path):
    piolib = _fake_piolib(tmp_path)
    done, out, calls = _run(tmp_path, stub_path, piolib,
                            {"WLSYNC_ALLOW_UNCHECKED_PIOLIB": "1"})
    assert done.returncode == 0, done.stderr
    assert calls.exists(), "the compiler did not run under the override"
    assert f"not utils {CHECKED}" in done.stderr
    assert "warning" in done.stderr


def test_the_override_applies_to_a_non_git_directory_too(tmp_path, stub_path):
    piolib = tmp_path / "plain" / "piolib"
    piolib.mkdir(parents=True)
    done, _out, calls = _run(tmp_path, stub_path, piolib,
                             {"WLSYNC_ALLOW_UNCHECKED_PIOLIB": "1"})
    assert done.returncode == 0, done.stderr
    assert calls.exists()
    assert "warning" in done.stderr


def test_the_override_must_be_exactly_1(tmp_path, stub_path):
    piolib = _fake_piolib(tmp_path)
    done, _out, calls = _run(tmp_path, stub_path, piolib,
                             {"WLSYNC_ALLOW_UNCHECKED_PIOLIB": "0"})
    assert done.returncode != 0
    assert not calls.exists()


def test_the_checked_commit_builds_silently(tmp_path, stub_path):
    """The right commit passes with nothing on stderr.

    A commit hash cannot be chosen, so a wrapper `git` on PATH answers `rev-parse HEAD`
    with a hash that starts with b57c48e and passes every other call through.
    """
    utils = tmp_path / "utils"
    (utils / "piolib").mkdir(parents=True)
    (utils / "piolib" / "piolib.c").write_text("/* fake */\n")
    _git(utils, "init", "-q")
    _git(utils, "add", ".")
    real_git = shutil.which("git")
    wrapper = stub_path / "git"
    wrapper.write_text(
        '#!/bin/sh\n'
        'for a in "$@"; do\n'
        '  if [ "$a" = "HEAD" ]; then echo b57c48e0000000000000000000000000000000aa; exit 0; fi\n'
        'done\n'
        f'exec "{real_git}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    done, out, calls = _run(tmp_path, stub_path, utils / "piolib")
    assert done.returncode == 0, done.stderr
    assert calls.exists()
    assert done.stderr == ""
