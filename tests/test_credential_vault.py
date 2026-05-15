import sqlite3
import pytest
from pathlib import Path
from nemo_coding_platform.credential_vault import (
    vault_encrypt,
    vault_decrypt,
    vault_create,
    vault_list,
    vault_lookup,
    vault_update,
    vault_delete,
)


def test_encrypt_decrypt_roundtrip():
    blob = vault_encrypt("secret123")
    assert isinstance(blob, bytes)
    assert len(blob) > 0
    assert vault_decrypt(blob) == "secret123"


def test_encrypt_produces_different_bytes_than_plaintext():
    blob = vault_encrypt("hello")
    assert blob != b"hello"


def test_create_and_list(tmp_path):
    db = tmp_path / "vault.db"
    result = vault_create(db, "github", "user1", "pass1", "github.com")
    assert result["alias"] == "github"
    assert "id" in result
    assert "created_at" in result
    rows = vault_list(db)
    assert len(rows) == 1
    assert rows[0]["alias"] == "github"
    assert rows[0]["url_pattern"] == "github.com"
    assert "username" not in rows[0]
    assert "password" not in rows[0]
    assert rows[0]["has_notes"] == 0


def test_create_with_notes(tmp_path):
    db = tmp_path / "vault.db"
    vault_create(db, "gmail", "me@gmail.com", "hunter2", notes="personal account")
    rows = vault_list(db)
    assert rows[0]["has_notes"] == 1


def test_lookup_returns_plaintext(tmp_path):
    db = tmp_path / "vault.db"
    vault_create(db, "github", "myuser", "mypass")
    creds = vault_lookup(db, "github")
    assert creds["username"] == "myuser"
    assert creds["password"] == "mypass"


def test_lookup_missing_raises(tmp_path):
    db = tmp_path / "vault.db"
    with pytest.raises(KeyError):
        vault_lookup(db, "nonexistent")


def test_update_password(tmp_path):
    db = tmp_path / "vault.db"
    r = vault_create(db, "github", "user1", "pass1")
    vault_update(db, r["id"], password="newpass")
    creds = vault_lookup(db, "github")
    assert creds["password"] == "newpass"
    assert creds["username"] == "user1"


def test_update_missing_raises(tmp_path):
    db = tmp_path / "vault.db"
    with pytest.raises(KeyError):
        vault_update(db, "nonexistent-id", alias="x")


def test_delete(tmp_path):
    db = tmp_path / "vault.db"
    r = vault_create(db, "github", "u", "p")
    vault_delete(db, r["id"])
    assert vault_list(db) == []


def test_delete_missing_raises(tmp_path):
    db = tmp_path / "vault.db"
    with pytest.raises(KeyError):
        vault_delete(db, "nonexistent-id")


def test_alias_uniqueness_enforced(tmp_path):
    db = tmp_path / "vault.db"
    vault_create(db, "github", "u1", "p1")
    with pytest.raises(sqlite3.IntegrityError):
        vault_create(db, "github", "u2", "p2")


def test_list_ordered_by_alias(tmp_path):
    db = tmp_path / "vault.db"
    vault_create(db, "zoom", "u", "p")
    vault_create(db, "aws", "u", "p")
    vault_create(db, "github", "u", "p")
    aliases = [r["alias"] for r in vault_list(db)]
    assert aliases == ["aws", "github", "zoom"]


def test_update_no_fields_missing_id_raises(tmp_path):
    db = tmp_path / "vault.db"
    with pytest.raises(KeyError):
        vault_update(db, "nonexistent-id")
