import io
import json
import tarfile

import pytest
from sqlalchemy import create_engine, text

from services.help_backup import (
    BackupArchiveError,
    RestoreTargetError,
    assert_restore_target,
    build_encrypted_archive,
    create_postgres_backup,
    encrypt_backup_payload,
    inspect_encrypted_archive,
    restore_archive_files,
)


PASSPHRASE = "synthetic-runtime-passphrase"


def test_archive_is_encrypted_and_restores_verified_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    marker = b"PRIVATE-PLAINTEXT-MARKER"
    (source / "private.enc").write_bytes(marker)

    artifact = build_encrypted_archive(
        database_dump=b"SYNTHETIC-POSTGRES-DUMP",
        upload_root=source,
        passphrase=PASSPHRASE,
        key_versions=["v1"],
        tool_identifiers={"pg_dump": "synthetic", "schema": "help-v2"},
    )

    assert marker not in artifact
    assert b"SYNTHETIC-POSTGRES-DUMP" not in artifact
    inspected = inspect_encrypted_archive(artifact, passphrase=PASSPHRASE)
    assert inspected.manifest["key_versions"] == ["v1"]
    assert inspected.database_dump == b"SYNTHETIC-POSTGRES-DUMP"
    destination = tmp_path / "restored"
    destination.mkdir()
    restore_archive_files(artifact, passphrase=PASSPHRASE, destination_root=destination)
    assert (destination / "private.enc").read_bytes() == marker


@pytest.mark.parametrize("mutation", ["wrong-passphrase", "corruption"])
def test_archive_authentication_fails_closed(mutation, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.enc").write_bytes(b"ciphertext")
    artifact = build_encrypted_archive(
        database_dump=b"dump",
        upload_root=source,
        passphrase=PASSPHRASE,
        key_versions=[],
        tool_identifiers={},
    )
    if mutation == "wrong-passphrase":
        candidate, passphrase = artifact, "wrong-passphrase"
    else:
        candidate, passphrase = artifact[:-1] + bytes([artifact[-1] ^ 1]), PASSPHRASE
    with pytest.raises(BackupArchiveError):
        inspect_encrypted_archive(candidate, passphrase=passphrase)


def encrypted_tar(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, payload in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return encrypt_backup_payload(buffer.getvalue(), PASSPHRASE)


def test_restore_rejects_checksum_mismatch_before_writing(tmp_path):
    manifest = {
        "format_version": 1,
        "files": [{"archive_path": "uploads/file.enc", "size": 3, "sha256": "0" * 64}],
        "database": {"archive_path": "database.dump", "size": 4, "sha256": "1" * 64},
        "key_versions": [],
        "tool_identifiers": {},
    }
    artifact = encrypted_tar({
        "manifest.json": json.dumps(manifest).encode(),
        "database.dump": b"dump",
        "uploads/file.enc": b"bad",
    })
    destination = tmp_path / "destination"
    destination.mkdir()
    with pytest.raises(BackupArchiveError, match="checksum"):
        restore_archive_files(artifact, passphrase=PASSPHRASE, destination_root=destination)
    assert not list(destination.iterdir())


def test_restore_rejects_traversal_and_nonempty_root(tmp_path):
    manifest = {
        "format_version": 1,
        "files": [{"archive_path": "uploads/../escape.enc", "size": 1, "sha256": "0" * 64}],
        "database": {"archive_path": "database.dump", "size": 4, "sha256": "0" * 64},
        "key_versions": [],
        "tool_identifiers": {},
    }
    artifact = encrypted_tar({
        "manifest.json": json.dumps(manifest).encode(),
        "database.dump": b"dump",
        "uploads/../escape.enc": b"x",
    })
    destination = tmp_path / "destination"
    destination.mkdir()
    with pytest.raises(BackupArchiveError, match="ruta"):
        restore_archive_files(artifact, passphrase=PASSPHRASE, destination_root=destination)

    (destination / "existing").write_text("keep")
    with pytest.raises(RestoreTargetError, match="vacio"):
        assert_restore_target(create_engine(f"sqlite:///{tmp_path / 'fresh_test.db'}"), destination)


def test_restore_target_requires_empty_test_database_and_root(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    safe = create_engine(f"sqlite:///{tmp_path / 'fresh_test.db'}")
    unsafe = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        assert_restore_target(safe, root)
        with pytest.raises(RestoreTargetError):
            assert_restore_target(unsafe, root)
        with safe.begin() as connection:
            connection.execute(text("CREATE TABLE occupied (id INTEGER)"))
        with pytest.raises(RestoreTargetError, match="vacia"):
            assert_restore_target(safe, root)
    finally:
        safe.dispose()
        unsafe.dispose()


def test_backup_and_restore_reject_remote_postgres_before_connecting(tmp_path):
    remote_url = "postgresql+psycopg2://user:password@db.example.test/archive_test"
    root = tmp_path / "root"
    root.mkdir()
    remote = create_engine(remote_url)
    try:
        with pytest.raises(RestoreTargetError, match="local"):
            assert_restore_target(remote, root)
        with pytest.raises(BackupArchiveError, match="local"):
            create_postgres_backup(
                database_url=remote_url,
                upload_root=root,
                output_path=tmp_path / "never-created.enc",
                passphrase=PASSPHRASE,
                key_versions=[],
                schema_identifier="test",
            )
    finally:
        remote.dispose()
