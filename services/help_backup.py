import hashlib
import io
import json
import os
import subprocess
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import inspect
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


MAGIC = b"VRHELPBK1"
SALT_BYTES = 16
NONCE_BYTES = 12


class BackupArchiveError(ValueError):
    pass


class RestoreTargetError(ValueError):
    pass


def _postgres_process(
    database_url,
    executable,
    arguments,
    *,
    append_database=True,
    input_data=None,
):
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql") or not url.database:
        raise BackupArchiveError("A PostgreSQL database is required")
    process_environment = os.environ.copy()
    mappings = {
        "PGHOST": url.host,
        "PGPORT": str(url.port) if url.port else None,
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
    }
    process_environment.update({key: value for key, value in mappings.items() if value})
    command = [executable, *arguments]
    if append_database:
        command.append(url.database)
    try:
        return subprocess.run(
            command,
            env=process_environment,
            check=True,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BackupArchiveError("PostgreSQL backup tool failed") from exc


def _tool_version(executable):
    try:
        result = subprocess.run(
            [executable, "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BackupArchiveError("PostgreSQL backup tool is unavailable") from exc
    return result.stdout.strip()[:200]


def _is_local_url(database_url):
    url = make_url(database_url)
    return url.drivername.startswith("sqlite") or url.host in {
        None,
        "localhost",
        "127.0.0.1",
        "::1",
    }


@dataclass(frozen=True)
class InspectedBackup:
    manifest: dict
    database_dump: bytes
    files: dict


def _key(passphrase, salt):
    if not isinstance(passphrase, str) or len(passphrase) < 12:
        raise BackupArchiveError("Backup passphrase must contain at least 12 characters")
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(passphrase.encode())


def encrypt_backup_payload(payload, passphrase):
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    header = MAGIC + salt + nonce
    return header + AESGCM(_key(passphrase, salt)).encrypt(nonce, bytes(payload), header)


def _decrypt_backup_payload(artifact, passphrase):
    minimum = len(MAGIC) + SALT_BYTES + NONCE_BYTES + 16
    if len(artifact) < minimum or not bytes(artifact).startswith(MAGIC):
        raise BackupArchiveError("Invalid encrypted backup format")
    offset = len(MAGIC)
    salt = artifact[offset:offset + SALT_BYTES]
    nonce = artifact[offset + SALT_BYTES:offset + SALT_BYTES + NONCE_BYTES]
    header = artifact[:offset + SALT_BYTES + NONCE_BYTES]
    try:
        return AESGCM(_key(passphrase, salt)).decrypt(nonce, artifact[len(header):], header)
    except (InvalidTag, ValueError) as exc:
        raise BackupArchiveError("Backup authentication failed") from exc


def _safe_archive_path(value):
    path = PurePosixPath(str(value or ""))
    if not str(value or "") or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise BackupArchiveError("Invalid archive ruta")
    return path


def _entry(payload, archive_path, *, kind):
    return {
        "archive_path": archive_path,
        "kind": kind,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _root_files(root, archive_prefix, kind):
    root = Path(root).resolve()
    if not root.is_dir():
        raise BackupArchiveError("Backup source root does not exist")
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BackupArchiveError("Backup source cannot contain symlinks")
        if not path.is_file():
            continue
        resolved = path.resolve()
        if root not in resolved.parents:
            raise BackupArchiveError("Backup source escaped its configured root")
        relative = path.relative_to(root).as_posix()
        archive_path = f"{archive_prefix}/{relative}"
        _safe_archive_path(archive_path)
        result.append((archive_path, path.read_bytes(), kind))
    return result


def build_encrypted_archive(
    *,
    database_dump,
    upload_root,
    passphrase,
    key_versions,
    tool_identifiers,
    firestore_emulator_root=None,
):
    entries = [("database.dump", bytes(database_dump), "postgresql")]
    entries.extend(_root_files(upload_root, "uploads", "private-upload"))
    if firestore_emulator_root is not None:
        metadata = Path(firestore_emulator_root) / "firebase-export-metadata.json"
        if not metadata.is_file():
            raise BackupArchiveError("Only a Firestore emulator export is accepted")
        entries.extend(_root_files(
            firestore_emulator_root,
            "firestore-emulator",
            "firestore-emulator",
        ))
    database_entry = _entry(entries[0][1], entries[0][0], kind=entries[0][2])
    file_entries = [_entry(payload, name, kind=kind) for name, payload, kind in entries[1:]]
    manifest = {
        "format_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": database_entry,
        "files": file_entries,
        "key_versions": sorted(set(str(item) for item in key_versions)),
        "tool_identifiers": dict(sorted(tool_identifiers.items())),
    }
    manifest_payload = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, payload, _kind in [("manifest.json", manifest_payload, "manifest"), *entries]:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(payload))
    return encrypt_backup_payload(buffer.getvalue(), passphrase)


def inspect_encrypted_archive(artifact, *, passphrase):
    payload = _decrypt_backup_payload(artifact, passphrase)
    entries = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            for member in archive.getmembers():
                path = _safe_archive_path(member.name)
                if not member.isfile() or member.issym() or member.islnk():
                    raise BackupArchiveError("Backup contains a non-regular entry")
                name = path.as_posix()
                if name in entries:
                    raise BackupArchiveError("Backup contains duplicate entries")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise BackupArchiveError("Backup entry is unavailable")
                entries[name] = extracted.read()
    except (tarfile.TarError, OSError) as exc:
        raise BackupArchiveError("Invalid backup container") from exc
    try:
        manifest = json.loads(entries.pop("manifest.json"))
        if manifest.get("format_version") != 1:
            raise BackupArchiveError("Unsupported backup manifest")
        declared = [manifest["database"], *manifest.get("files", [])]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BackupArchiveError("Invalid backup manifest") from exc
    declared_names = set()
    for item in declared:
        try:
            name = _safe_archive_path(item["archive_path"]).as_posix()
            content = entries[name]
            size = int(item["size"])
            checksum = str(item["sha256"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupArchiveError("Invalid backup manifest entry") from exc
        if name in declared_names:
            raise BackupArchiveError("Duplicate backup manifest entry")
        declared_names.add(name)
        if len(content) != size or hashlib.sha256(content).hexdigest() != checksum:
            raise BackupArchiveError("Backup checksum verification failed")
    if declared_names != set(entries):
        raise BackupArchiveError("Backup has undeclared or missing entries")
    database_name = manifest["database"]["archive_path"]
    files = {
        item["archive_path"]: entries[item["archive_path"]]
        for item in manifest.get("files", [])
    }
    return InspectedBackup(manifest, entries[database_name], files)


def _database_name(engine):
    database = str(engine.url.database or "")
    return Path(database).stem if engine.url.drivername.startswith("sqlite") else database


def assert_restore_target(engine, destination_root):
    if not _is_local_url(engine.url):
        raise RestoreTargetError("Restore requires a local database target")
    if not _database_name(engine).endswith("_test"):
        raise RestoreTargetError("Restore database must end in _test")
    root = Path(destination_root)
    if not root.is_dir() or any(root.iterdir()):
        raise RestoreTargetError("Restore destination root must be vacio")
    if inspect(engine).get_table_names():
        raise RestoreTargetError("Restore database must be vacia")


def restore_archive_files(artifact, *, passphrase, destination_root):
    inspected = inspect_encrypted_archive(artifact, passphrase=passphrase)
    root = Path(destination_root).resolve()
    if not root.is_dir() or any(root.iterdir()):
        raise RestoreTargetError("Restore destination root must be vacio")
    upload_entries = {
        name: payload for name, payload in inspected.files.items()
        if name.startswith("uploads/")
    }
    destinations = []
    for archive_name, payload in upload_entries.items():
        relative = _safe_archive_path(archive_name).relative_to("uploads")
        destination = root.joinpath(*relative.parts)
        resolved = destination.resolve(strict=False)
        if root not in resolved.parents:
            raise BackupArchiveError("Invalid restore ruta")
        destinations.append((destination, payload))
    for destination, payload in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(destination)
    return inspected.manifest


def create_postgres_backup(
    *,
    database_url,
    upload_root,
    output_path,
    passphrase,
    key_versions,
    schema_identifier,
    firestore_emulator_root=None,
    pg_dump_bin="pg_dump",
):
    if not _is_local_url(database_url):
        raise BackupArchiveError("Backup requires a local database source")
    output = Path(output_path)
    if output.exists() or not output.parent.is_dir():
        raise BackupArchiveError("Backup output must be new and its parent must exist")
    dump = _postgres_process(
        database_url,
        pg_dump_bin,
        ["--format=custom", "--no-owner", "--no-privileges"],
    ).stdout
    artifact = build_encrypted_archive(
        database_dump=dump,
        upload_root=upload_root,
        passphrase=passphrase,
        key_versions=key_versions,
        tool_identifiers={
            "pg_dump": _tool_version(pg_dump_bin),
            "schema": schema_identifier,
        },
        firestore_emulator_root=firestore_emulator_root,
    )
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        temporary.write_bytes(artifact)
        os.chmod(temporary, 0o600)
        temporary.replace(output)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise BackupArchiveError("Encrypted backup could not be written") from exc
    inspected = inspect_encrypted_archive(artifact, passphrase=passphrase)
    return {
        "files": len(inspected.manifest.get("files", [])),
        "encrypted": True,
    }


def restore_postgres_backup(
    *,
    database_url,
    destination_root,
    artifact_path,
    passphrase,
    pg_restore_bin="pg_restore",
):
    artifact = Path(artifact_path).read_bytes()
    inspected = inspect_encrypted_archive(artifact, passphrase=passphrase)
    missing_versions = [
        version for version in inspected.manifest.get("key_versions", [])
        if not os.getenv(f"HELP_DATA_ENCRYPTION_KEY_{str(version).upper()}")
    ]
    if missing_versions:
        raise BackupArchiveError("Required data key version is unavailable")
    engine = create_engine(database_url)
    try:
        assert_restore_target(engine, destination_root)
    finally:
        engine.dispose()
    _postgres_process(
        database_url,
        pg_restore_bin,
        [
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            make_url(database_url).database,
        ],
        append_database=False,
        input_data=inspected.database_dump,
    )
    restore_archive_files(
        artifact,
        passphrase=passphrase,
        destination_root=destination_root,
    )
    return {
        "files": sum(
            item.get("kind") == "private-upload"
            for item in inspected.manifest.get("files", [])
        ),
        "encrypted": True,
    }
