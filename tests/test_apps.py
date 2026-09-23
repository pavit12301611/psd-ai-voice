"""Apps skill — desktop discovery + fuzzy matching (no actual launches)."""

from pathlib import Path

import pytest

from assistant.skills.apps_skill import AppsSkill, DesktopEntry, _parse_desktop


@pytest.fixture()
def fake_apps(monkeypatch, tmp_path):
    """Real .desktop files the skill will parse."""
    contents = {
        "firefox.desktop": (
            "[Desktop Entry]\nType=Application\nName=Firefox\n"
            "GenericName=Web Browser\nExec=firefox %u\n"
            "Keywords=internet;www;browser;\n"
        ),
        "org.gnome.Calculator.desktop": (
            "[Desktop Entry]\nType=Application\nName=Calculator\n"
            "Exec=gnome-calculator\nKeywords=math;calculator;\n"
        ),
        "org.gnome.Nautilus.desktop": (
            "[Desktop Entry]\nType=Application\nName=Files\n"
            "GenericName=File Manager\nExec=nautilus %U\n"
            "Keywords=folder;manager;file manager;\n"
        ),
        "hidden.desktop": (
            "[Desktop Entry]\nType=Application\nName=Secret\n"
            "Exec=secret\nNoDisplay=true\n"
        ),
    }
    for name, body in contents.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    monkeypatch.setattr("assistant.skills.apps_skill._DESKTOP_DIRS", [tmp_path])
    return contents


def test_parse_desktop(tmp_path):
    path = tmp_path / "demo.desktop"
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=Demo App\n"
        "GenericName=Demo\nExec=demo %f\nKeywords=demo;test;\n",
        encoding="utf-8",
    )
    entry = _parse_desktop(path)
    assert entry is not None
    assert entry.name == "Demo App"
    assert entry.exec_line == "demo %f"


def test_exact_match(cfg, fake_apps):
    skill = AppsSkill(cfg)
    entry, score, _ = skill.best_match("firefox")
    assert entry is not None and entry.app_id == "firefox"
    assert score >= 0.9


def test_alias_files_nautilus(cfg, fake_apps):
    skill = AppsSkill(cfg)
    entry, _, _ = skill.best_match("files")
    assert entry is not None and entry.app_id == "org.gnome.Nautilus"


def test_fuzzy_typo(cfg, fake_apps):
    skill = AppsSkill(cfg)
    entry, _, _ = skill.best_match("calcuator")  # typo
    assert entry is not None and entry.app_id == "org.gnome.Calculator"


def test_hidden_not_matched(cfg, fake_apps):
    skill = AppsSkill(cfg)
    entry, score, _ = skill.best_match("secret")
    # hidden entries only match on exact app id
    assert entry is None or entry.app_id != "hidden" or score < 0.9


def test_no_match_returns_none(cfg, fake_apps):
    skill = AppsSkill(cfg)
    entry, score, suggestions = skill.best_match("nonexistent zebra browser x")
    assert entry is None
