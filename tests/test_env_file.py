import os

from finitact.env_file import load_env_file


def test_fills_unset_keys_and_keeps_client_values(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nFINITACT_T_A="from-file"\nFINITACT_T_B=from-file\nFINITACT_T_EMPTY=\n', encoding="utf-8")
    for key in ("FINITACT_T_A", "FINITACT_T_EMPTY"):
        monkeypatch.setenv(key, "")  # registers restore, so the loaded value does not leak into other tests
        monkeypatch.delenv(key)
    monkeypatch.setenv("FINITACT_T_B", "from-client")

    load_env_file(env)

    assert os.environ["FINITACT_T_A"] == "from-file"
    assert os.environ["FINITACT_T_B"] == "from-client"
    assert "FINITACT_T_EMPTY" not in os.environ


def test_missing_file_is_ignored(tmp_path):
    load_env_file(tmp_path / "absent.env")
