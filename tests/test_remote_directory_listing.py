import stat
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.services import ssh_client


def _attr(name, mode, size=0):
    return SimpleNamespace(filename=name, st_mode=mode, st_size=size)


def test_symlinked_directories_are_listed_as_folders():
    sftp = MagicMock()
    sftp.listdir_attr.return_value = [
        _attr("var", stat.S_IFDIR | 0o755, 4096),
        _attr("bin", stat.S_IFLNK | 0o777, 7),
        _attr("loop", stat.S_IFLNK | 0o777, 5),
        _attr("notes.txt", stat.S_IFREG | 0o644, 12),
    ]

    def fake_stat(path):
        if path.endswith("loop"):
            raise OSError("dangling")
        return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755)

    sftp.stat.side_effect = fake_stat
    client = MagicMock()
    client.open_sftp.return_value = sftp
    with patch.object(ssh_client, "_open_ssh_client", return_value=client):
        entries = ssh_client.list_remote_directory({}, "/")

    by_name = {item["name"]: item for item in entries}
    assert by_name["bin"]["is_dir"] is True
    assert by_name["var"]["is_dir"] is True
    assert by_name["loop"]["is_dir"] is False
    assert by_name["notes.txt"]["is_dir"] is False
    assert [item["name"] for item in entries][:2] == ["bin", "var"]
