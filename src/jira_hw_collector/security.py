"""Права доступа, маскирование и шифрование результатов."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

from .config import ArchiveConfig


SECRET_FIELD = re.compile(r"(?i)(password|passwd|secret|token|community|private.?key|client.?secret)")
PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
    re.DOTALL,
)
ASSIGNMENT = re.compile(
    r"(?im)^(\s*(?:[A-Z0-9_.-]*(?:PASSWORD|PASSWD|SECRET|TOKEN|COMMUNITY|PRIVATE[_-]?KEY)[A-Z0-9_.-]*)\s*[=:]\s*)(.*)$"
)


def redact(value):
    if isinstance(value, dict):
        return {key: ("<REDACTED>" if SECRET_FIELD.search(str(key)) else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = PRIVATE_KEY.sub("-----BEGIN PRIVATE KEY-----\n<REDACTED>\n-----END PRIVATE KEY-----", value)
        return ASSIGNMENT.sub(r"\1<REDACTED>", value)
    return value


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700); path.chmod(0o700)


def harden(path: Path) -> None:
    for root, directories, files in os.walk(path, followlinks=False):
        Path(root).chmod(0o700)
        for name in directories:
            (Path(root) / name).chmod(0o700)
        for name in files:
            (Path(root) / name).chmod(0o600)


def _tar_filter(info: tarfile.TarInfo):
    if info.isdir(): info.mode = 0o700; return info
    if info.isfile(): info.mode = 0o600; return info
    return None


def archive_run(run_dir: Path, config: ArchiveConfig) -> Path:
    harden(run_dir)
    if not config.enabled:
        return run_dir
    if shutil.which("age") is None:
        raise RuntimeError("Для шифрования требуется утилита age")
    if not config.recipient.startswith("age1") or not config.identity_file:
        raise ValueError("В hardware_archive нужны recipient и identity_file")
    if config.identity_file.stat().st_mode & 0o077:
        raise PermissionError("Закрытый ключ age должен иметь права 0600")
    archive_dir = run_dir.parent.parent / f"{run_dir.parent.name}-archives"
    private_directory(archive_dir)
    final = archive_dir / f"{run_dir.name}.tar.gz.age"
    temporary = archive_dir / f".{run_dir.name}.{os.getpid()}.tmp"
    process = subprocess.Popen(["age", "--encrypt", "--recipient", config.recipient, "--output", str(temporary)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    try:
        with tarfile.open(fileobj=process.stdin, mode="w|gz", format=tarfile.PAX_FORMAT) as bundle:
            bundle.add(run_dir, arcname=run_dir.name, filter=_tar_filter)
    finally:
        if not process.stdin.closed:
            process.stdin.close()
    error = process.stderr.read().decode(errors="replace"); code = process.wait()
    if code:
        temporary.unlink(missing_ok=True); raise RuntimeError(f"Ошибка age: {error.strip()}")
    temporary.chmod(0o600)
    verifier = subprocess.Popen(
        ["age", "--decrypt", "--identity", str(config.identity_file), str(temporary)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert verifier.stdout is not None
    members = 0
    with tarfile.open(fileobj=verifier.stdout, mode="r|gz") as bundle:
        for member in bundle:
            members += 1
            if not Path(member.name).parts or Path(member.name).parts[0] != run_dir.name:
                raise RuntimeError(f"Неожиданный объект архива: {member.name}")
    verifier.stdout.close()
    verify_error = verifier.stderr.read().decode(errors="replace")
    if verifier.wait() != 0 or members == 0:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Проверка зашифрованного архива не выполнена: {verify_error.strip()}")
    os.replace(temporary, final); final.chmod(0o600)
    hasher = hashlib.sha256()
    with final.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    digest = hasher.hexdigest()
    checksum = final.with_suffix(final.suffix + ".sha256")
    checksum.write_text(f"{digest}  {final.name}\n", encoding="utf-8"); checksum.chmod(0o600)
    if config.delete_plaintext_after_success:
        shutil.rmtree(run_dir)
    return final
