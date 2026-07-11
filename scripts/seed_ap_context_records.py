from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ap_context import iter_ap_context_records_from_manifest  # noqa: E402
from app.mail_store import MailDatabase, MailRepository  # noqa: E402


DEFAULT_MANIFEST_DIR = Path("storage/test_pdfs")
DEFAULT_OWNER_USER_ID = "demo-user"


def seed_ap_context_records(
    *,
    database_url: str,
    owner_user_id: str,
    manifest_dir: Path = DEFAULT_MANIFEST_DIR,
) -> int:
    database = MailDatabase(database_url)
    repo = MailRepository(database)
    try:
        repo.initialize_schema()
        inserted = 0
        for manifest_path in _manifest_paths(manifest_dir):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for record in iter_ap_context_records_from_manifest(
                manifest,
                owner_user_id=owner_user_id,
                manifest_path=manifest_path,
            ):
                repo.upsert_ap_context_record(**record)
                inserted += 1
        return inserted
    finally:
        database.close()


def resolve_database_url(explicit_database_url: str | None, *, env_path: Path = ROOT / ".env") -> str | None:
    return explicit_database_url or os.environ.get("DATABASE_URL") or _load_env_file(env_path).get("DATABASE_URL")


def _manifest_paths(manifest_dir: Path) -> list[Path]:
    return sorted(path for path in manifest_dir.iterdir() if path.is_file() and path.name.endswith(".manifest.json"))


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed simulated AP context records from generated invoice manifests.")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--owner-user-id", default=DEFAULT_OWNER_USER_ID)
    parser.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFEST_DIR)
    args = parser.parse_args()

    database_url = resolve_database_url(args.database_url)
    if not database_url:
        raise SystemExit("DATABASE_URL is required. Pass --database-url or set DATABASE_URL in the environment/.env.")

    count = seed_ap_context_records(
        database_url=database_url,
        owner_user_id=args.owner_user_id,
        manifest_dir=args.manifest_dir,
    )
    print(f"Seeded {count} AP context records for owner {args.owner_user_id}.")


if __name__ == "__main__":
    main()
