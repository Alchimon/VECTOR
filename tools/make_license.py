#!/usr/bin/env python3
"""Лицензии платформы ВЕКТОР для стенда и вендора (D-80, A-64): ключи Ed25519 и подписанные файлы лицензий.

Боевой закрытый ключ вендора в репозитории НЕ хранится — здесь генерируется стендовый. Формат файла и правила —
в src/vector/core/modules.py; программа проверяет подпись офлайн встроенным публичным ключом.

Примеры:
    python tools/make_license.py gen-keys /tmp/lickeys
    python tools/make_license.py issue /tmp/lickeys/private.pem --customer "ООО «Завод»" \\
        --kind subscription --valid-until 2027-12-31 --support-until 2027-12-31 \\
        --modules pdm,supply,production --seats 25 --out /tmp/license.json
    python tools/make_license.py verify /tmp/license.json /tmp/lickeys/public.pem
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vector.core import modules as M


def _save_private(path: Path, key: Ed25519PrivateKey) -> None:
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    path.chmod(0o600)


def _load_private(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(f"{path}: ключ не Ed25519")
    return key


def _load_public(path: Path):
    return serialization.load_pem_public_key(path.read_bytes())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen-keys", help="создать пару ключей (private.pem — только у вендора!)")
    g.add_argument("dir", type=Path)

    i = sub.add_parser("issue", help="выписать лицензию")
    i.add_argument("private", type=Path)
    i.add_argument("--customer", required=True)
    i.add_argument("--license-id", default=f"L-{date.today().year}-0001")
    i.add_argument("--kind", choices=M.LICENSE_KINDS, required=True)
    i.add_argument("--valid-until", default=None, help="ГГГГ-ММ-ДД (обязательно для подписки)")
    i.add_argument("--support-until", default=None, help="ГГГГ-ММ-ДД; не задано — «без поддержки»")
    i.add_argument("--modules", required=True, help="через запятую: " + ",".join(sorted(M.CURRENT_MODULE_IDS)))
    i.add_argument("--seats", type=int, default=None, help="именных мест; не задано — без ограничения")
    i.add_argument("--grace-days", type=int, default=M.DEFAULT_GRACE_DAYS)
    i.add_argument("--key-id", default="dev")
    i.add_argument("--out", type=Path, required=True)

    v = sub.add_parser("verify", help="проверить файл лицензии публичным ключом")
    v.add_argument("license", type=Path)
    v.add_argument("public", type=Path)

    args = ap.parse_args()
    if args.cmd == "gen-keys":
        args.dir.mkdir(parents=True, exist_ok=True)
        key = Ed25519PrivateKey.generate()
        _save_private(args.dir / "private.pem", key)
        (args.dir / "public.pem").write_bytes(key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        print(f"Ключи: {args.dir}/private.pem (хранить только у вендора), {args.dir}/public.pem (встраивается в программу)")
        return 0

    if args.cmd == "issue":
        payload = {"license_id": args.license_id, "customer": args.customer,
                   "issued_at": date.today().isoformat(), "kind": args.kind,
                   "valid_until": args.valid_until, "support_until": args.support_until,
                   "modules": [m.strip() for m in args.modules.split(",") if m.strip()],
                   "seats": args.seats, "grace_days": args.grace_days, "key_id": args.key_id}
        try:
            lic = M.parse_license_payload(payload)                   # схема проверяется ДО подписи
        except M.LicenseError as e:
            raise SystemExit(f"Отказ: {e.message_ru}") from None
        import base64
        key = _load_private(args.private)
        doc = {"payload": payload,
               "signature": base64.b64encode(key.sign(M.canonical_payload(payload))).decode("ascii")}
        args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Лицензия: {args.out} — {lic.customer}, {M.KIND_LABELS_RU[lic.kind]}, "
              f"модулей {len(lic.modules)}, мест {lic.seats or 'без ограничения'}")
        return 0

    # verify
    try:
        lic = M.load_license(args.license.read_text(encoding="utf-8"), _load_public(args.public))
    except M.LicenseError as e:
        raise SystemExit(f"НЕ ПРОШЛА: {e.code} — {e.message_ru}") from None
    st = M.evaluate(lic, date.today())
    print(f"OK: {lic.customer} · {M.KIND_LABELS_RU[lic.kind]} · {st.support_label_ru}")
    for m in M.MODULES:
        s = st.modules[m.id]
        print(f"  {m.id:12s} {s.label_ru}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
