"""Тестовый CA + серверный сертификат для пилота и автотестов TLS.

Для работы в компании используйте сертификат от корпоративного CA. Ключи создаются с правами 600.
"""
from __future__ import annotations

import ipaddress
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def make_dev_ca_and_cert(directory: Path, host: "str | list[str]" = "localhost") -> dict[str, Path]:
    """Создать тестовый CA и сертификат сервера, действительные 30 дней.

    host — имя или список имён/IP-адресов, по которым клиенты обращаются к серверу (первое — CN).
    localhost и 127.0.0.1 добавляются всегда: тест на одном ПК работает с тем же сертификатом.
    Существующие файлы не перезаписываются (закрытый ключ создаётся с O_EXCL).
    """
    hosts = [host] if isinstance(host, str) else list(host)
    hosts = [h.strip() for h in hosts if h and h.strip()] or ["localhost"]
    paths = {"ca_cert": directory / "ca.pem", "server_cert": directory / "server.pem", "server_key": directory / "server.key"}
    # Проверяем ДО записи: иначе новый ca.pem/server.pem перезаписали бы старые, а ключ остался бы прежним —
    # сертификат и ключ перестали бы совпадать, и сервер стенда не запустился бы.
    existing = [str(p) for p in paths.values() if p.exists()]
    if existing:
        raise FileExistsError("Файлы сертификата уже существуют, ничего не изменено: " + ", ".join(existing)
                              + ". Чтобы выпустить новые, удалите их или укажите другой каталог.")
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "VECTOR TEST CA — не для работы")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                       key_encipherment=False, data_encipherment=False, key_agreement=False, encipher_only=False,
                                       decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    sans: list[x509.GeneralName] = []
    for h in hosts + ["localhost", "127.0.0.1"]:
        try:
            name: x509.GeneralName = x509.IPAddress(ipaddress.ip_address(h))
        except ValueError:
            name = x509.DNSName(h)
        if name not in sans:
            sans.append(name)
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])])).issuer_name(ca_name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    paths["ca_cert"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths["server_cert"].write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    _write_private(paths["server_key"], key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
    return paths
