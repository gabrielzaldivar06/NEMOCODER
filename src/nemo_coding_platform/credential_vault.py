import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------

def vault_encrypt(plaintext: str) -> bytes:
    """Encrypt plaintext using Windows DPAPI; falls back to XOR obfuscation."""
    try:
        import win32crypt  # type: ignore[import]
        return win32crypt.CryptProtectData(plaintext.encode(), None, None, None, None, 0)
    except ImportError:
        import socket
        key = socket.gethostname().encode() or b"spacecode"
        data = plaintext.encode()
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def vault_decrypt(blob: bytes) -> str:
    """Decrypt a vault blob; mirrors vault_encrypt fallback logic."""
    try:
        import win32crypt  # type: ignore[import]
        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1].decode()
    except ImportError:
        import socket
        key = socket.gethostname().encode() or b"spacecode"
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(blob)).decode()


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials (
    id           TEXT PRIMARY KEY,
    alias        TEXT NOT NULL UNIQUE,
    username_enc BLOB NOT NULL,
    password_enc BLOB NOT NULL,
    url_pattern  TEXT,
    notes_enc    BLOB,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
)
"""


def _conn(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def vault_list(db_path: Path) -> list[dict]:
    """Return all credentials without decrypting sensitive fields."""
    with closing(_conn(db_path)) as conn:
        rows = conn.execute(
            "SELECT id, alias, url_pattern, "
            "(notes_enc IS NOT NULL) AS has_notes, created_at, updated_at "
            "FROM credentials ORDER BY alias"
        ).fetchall()
    return [dict(r) for r in rows]


def vault_create(
    db_path: Path,
    alias: str,
    username: str,
    password: str,
    url_pattern: str = "",
    notes: str = "",
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    cred_id = uuid.uuid4().hex
    with closing(_conn(db_path)) as conn:
        conn.execute(
            "INSERT INTO credentials "
            "(id, alias, username_enc, password_enc, url_pattern, notes_enc, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cred_id,
                alias,
                vault_encrypt(username),
                vault_encrypt(password),
                url_pattern or None,
                vault_encrypt(notes) if notes else None,
                now,
                now,
            ),
        )
        conn.commit()
    return {"id": cred_id, "alias": alias, "created_at": now}


def vault_update(db_path: Path, cred_id: str, **fields: object) -> dict:
    """Update one or more fields of a credential. Pass only fields to change."""
    now = datetime.now(timezone.utc).isoformat()
    updates: list[str] = []
    params: list[object] = []
    if "alias" in fields:
        updates.append("alias = ?")
        params.append(fields["alias"])
    if "username" in fields:
        updates.append("username_enc = ?")
        params.append(vault_encrypt(str(fields["username"])))
    if "password" in fields:
        updates.append("password_enc = ?")
        params.append(vault_encrypt(str(fields["password"])))
    if "url_pattern" in fields:
        updates.append("url_pattern = ?")
        params.append(fields["url_pattern"] or None)
    if "notes" in fields:
        val = str(fields["notes"]) if fields["notes"] else None
        updates.append("notes_enc = ?")
        params.append(vault_encrypt(val) if val else None)
    if not updates:
        with closing(_conn(db_path)) as conn:
            row = conn.execute("SELECT id FROM credentials WHERE id = ?", (cred_id,)).fetchone()
        if row is None:
            raise KeyError(cred_id)
        return {"id": cred_id, "updated_at": now}
    updates.append("updated_at = ?")
    params.append(now)
    params.append(cred_id)
    with closing(_conn(db_path)) as conn:
        cur = conn.execute(
            f"UPDATE credentials SET {', '.join(updates)} WHERE id = ?", params
        )
        conn.commit()
        if cur.rowcount == 0:
            raise KeyError(cred_id)
    return {"id": cred_id, "updated_at": now}


def vault_delete(db_path: Path, cred_id: str) -> None:
    with closing(_conn(db_path)) as conn:
        cur = conn.execute("DELETE FROM credentials WHERE id = ?", (cred_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise KeyError(cred_id)


def vault_lookup(db_path: Path, alias: str) -> dict:
    """Return decrypted {username, password} for an alias. Internal use only."""
    with closing(_conn(db_path)) as conn:
        row = conn.execute(
            "SELECT username_enc, password_enc FROM credentials WHERE alias = ?",
            (alias,),
        ).fetchone()
    if row is None:
        raise KeyError(alias)
    return {
        "username": vault_decrypt(bytes(row["username_enc"])),
        "password": vault_decrypt(bytes(row["password_enc"])),
    }
