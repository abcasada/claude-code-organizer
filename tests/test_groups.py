import json
import os
import uuid

from claude_code_organizer import paths
from claude_code_organizer.groups import Groups
from claude_code_organizer.settings import Settings


def ids(n):
    return [str(uuid.uuid4()) for _ in range(n)]


def test_assign_rename_remove_and_persist(tmp_path):
    a, b, c = ids(3)
    groups = Groups(tmp_path / "groups.json")
    groups.assign([a, b], " Client  X ")
    groups.assign([c], "beta")
    assert groups.group_of(a) == "Client X"
    assert groups.names() == ["beta", "Client X"]
    assert groups.names([c]) == ["beta"]

    groups.rename("Client X", "beta")  # merges
    assert Groups(groups.path).names() == ["beta"]

    groups.assign([a], None)
    groups.remove("beta")
    assert Groups(groups.path).names() == []
    assert groups.group_of(b) is None


def test_picks_up_changes_made_elsewhere(tmp_path):
    (a,) = ids(1)
    mine, theirs = Groups(tmp_path / "groups.json"), Groups(tmp_path / "groups.json")
    theirs.assign([a], "Elsewhere")
    os.utime(theirs.path, ns=(1, 1))  # make sure the change is visible even with a coarse clock
    mine.reload()
    assert mine.group_of(a) == "Elsewhere"


def test_unreadable_file_is_kept_as_backup(tmp_path):
    (a,) = ids(1)
    path = tmp_path / "groups.json"
    path.write_text("{not json", encoding="utf-8")
    groups = Groups(path)
    assert groups.names() == []
    groups.assign([a], "Fresh")
    assert (tmp_path / "groups.json.bak").read_text(encoding="utf-8") == "{not json"
    assert json.loads(path.read_text(encoding="utf-8"))["sessions"] == {a: "Fresh"}


def test_ignores_bad_entries(tmp_path):
    (a,) = ids(1)
    path = tmp_path / "groups.json"
    path.write_text(json.dumps({"version": 1, "sessions": {a: "Ok", "not-an-id": "X", ids(1)[0]: 5}}),
                    encoding="utf-8")
    assert Groups(path).names() == ["Ok"]


def test_settings_migrate_group_by_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "app_config_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"group_by_folder": True}), encoding="utf-8")
    assert Settings.load().group_by == "folder"
    (tmp_path / "settings.json").write_text(json.dumps({"group_by": "bogus"}), encoding="utf-8")
    assert Settings.load().group_by == "none"
