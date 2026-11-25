"""Rules for the one-line installation (``installer/install.ps1``).

The script itself runs only on Windows and is tested there by the build
(``installer/test_setup.ps1``). These checks catch, on every platform, the
mistakes that would break it for users before a build is even started.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "installer" / "install.ps1"
ADDRESS = "https://github.com/pmanlukas/doc-anomyzer/releases/latest/download"
#: The line users paste into PowerShell.
ONE_LINER = f"irm {ADDRESS}/install.ps1 | iex"


def _script() -> str:
    return SCRIPT.read_bytes().decode("ascii").replace("\r\n", "\n")


def test_script_is_plain_ascii_without_byte_order_mark():
    # Windows PowerShell 5.1 reads a download without a declared character
    # set as ISO-8859-1: a byte order mark or an umlaut would arrive garbled
    # (and a garbled first line stops the whole script).
    raw = SCRIPT.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    raw.decode("ascii")  # raises on any other character


def test_script_never_closes_the_users_window():
    # The line runs inside the user's own PowerShell window. "exit" would
    # close it together with the message - allowed only in automatic mode.
    code = [line.strip() for line in _script().splitlines() if not line.lstrip().startswith("#")]
    assert [line for line in code if re.search(r"\bexit\b", line)] == [
        "if ($failed -and $Automatic) { exit 1 }"]


def test_line_continuations_are_not_followed_by_blanks():
    # A blank after the backtick turns the continuation into an error.
    assert not re.search(r"`[ \t]+$", _script(), flags=re.MULTILINE)


def test_every_umlaut_placeholder_is_known():
    script = _script()
    known = set(re.findall(r"'(\{[a-z]{2}\})' = 0x", script))
    assert known == {"{ae}", "{oe}", "{ue}", "{ss}"}
    assert set(re.findall(r"\{[a-zA-Z]{2}\}", script)) <= known


def test_script_matches_the_setup_it_installs():
    script = _script()
    setup = (ROOT / "installer" / "setup.iss").read_text(encoding="utf-8")
    name = re.search(r'#define AppName "(.+?)"', setup).group(1)
    assert f"$Name = '{name}'" in script
    # File name of the setup and the folder it installs to.
    assert "OutputBaseFilename={#AppName}-Setup" in setup.replace(name, "{#AppName}")
    assert '$SetupName = "$Name-Setup.exe"' in script
    assert r"DefaultDirName={localappdata}\Programs\{#AppName}" in setup
    assert r'Join-Path $env:LOCALAPPDATA "Programs\$Name"' in script
    # Started exactly like the desktop symbol.
    assert "'-m', 'doc_anonymizer.launcher'" in script


def test_documented_line_is_the_address_the_script_uses():
    assert f"$Source = '{ADDRESS}'" in _script()
    assert ONE_LINER in _script()
    assert ONE_LINER in (ROOT / "README.md").read_text(encoding="utf-8")
    assert ONE_LINER in (ROOT / "ANLEITUNG.txt").read_text(encoding="utf-8-sig")


def test_release_offers_the_files_the_script_downloads():
    workflow = (ROOT / ".github" / "workflows" / "windows-setup.yml").read_text(encoding="utf-8")
    release = workflow[workflow.index("\n  release:"):]
    assert '"${setup}" "${setup}.sha256" installer/install.ps1 ANLEITUNG.txt' in release
    assert "Dokumenten-Anonymisierer-Setup.exe" in release
    # Publishing needs a decision: a version tag or the checkbox, never a plain push.
    assert "startsWith(github.ref, 'refs/tags/v')" in release
    assert "inputs.veroeffentlichen" in release
