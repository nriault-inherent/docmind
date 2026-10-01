import json

import bcrypt
import pytest
import yaml

import auth


@pytest.fixture
def config(tmp_path):
    value = {
        "credentials": {"usernames": {"nico": {"name": "Nicolas", "password": bcrypt.hashpw(b"correct", bcrypt.gensalt(rounds=4)).decode()}}},
        "cookie": {"name": "docmind", "key": "existing-config-key", "expiry_days": 1},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(value))
    return path


def test_valid_bcrypt_login(config):
    loaded = auth.load_auth_config(config)
    assert auth.verify_credentials(loaded, "nico", "correct") == ("nico", "Nicolas")


def test_wrong_or_unknown_credentials(config):
    loaded = auth.load_auth_config(config)
    assert auth.verify_credentials(loaded, "nico", "wrong") is None
    assert auth.verify_credentials(loaded, "unknown", "wrong") is None
    assert auth.verify_credentials(loaded, "nico", "x" * 100) is None


@pytest.mark.parametrize("content", ["[broken", "null", json.dumps({"cookie": {}}), "credentials:\n  usernames:\n    nico:\n      password: REPLACE_WITH_BCRYPT_HASH\ncookie:\n  name: docmind\n  expiry_days: 1"])
def test_invalid_config_is_presentable(tmp_path, content):
    path = tmp_path / "config.yaml"
    path.write_text(content)
    with pytest.raises(auth.AuthConfigError) as error:
        auth.load_auth_config(path)
    assert "REPLACE_WITH" not in str(error.value)
    assert "[broken" not in str(error.value)


def test_session_expiry_and_revocation(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    store = auth.SessionStore()
    token, session = store.create("nico", "Nicolas", 1)
    assert len(token) >= 40
    assert session.csrf_token != token
    assert store.get(token) is session
    now[0] += 86401
    assert store.get(token) is None
    token, _ = store.create("nico", "Nicolas", 1)
    store.revoke(token)
    assert store.get(token) is None
