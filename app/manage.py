import argparse
import secrets
import sys
from pathlib import Path

from app.branding import APP_NAME
from app.config import Settings
from app.db import Database
from app.repository import Repository


def main():
    parser = argparse.ArgumentParser(description=f"Initialize {APP_NAME} or manage local credentials")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    add = sub.add_parser("create-user")
    add.add_argument("--name", required=True)
    add.add_argument("--role", choices=["user", "reviewer", "admin"], default="user")
    sub.add_parser("revoke-key", help="Read the key to revoke from standard input")
    sub.add_parser("seed-demo")
    args = parser.parse_args()
    settings = Settings()
    db = Database(settings.database_url)
    repo = Repository(db)
    try:
        if args.command == "init":
            db.migrate()
            for name, role, token in [("demo-user", "user", settings.user_api_key), ("demo-reviewer", "reviewer", settings.reviewer_api_key), ("demo-admin", "admin", settings.admin_api_key)]:
                if token:
                    repo.seed_user(name, role, token)
            print("Database initialized. Configured seed credentials are ready.")
        elif args.command == "create-user":
            token = secrets.token_urlsafe(36)
            user = repo.seed_user(args.name, args.role, token)
            print(f"User: {user.name}\nRole: {user.role}\nAPI key (save securely): {token}")
        elif args.command == "revoke-key":
            repo.revoke_key(sys.stdin.read().strip())
            print("Key revoked (if it existed).")
        elif args.command == "seed-demo":
            p = repo.authenticate(settings.user_api_key)
            if not p:
                raise SystemExit("USER_API_KEY must identify an initialized user")
            text = (Path(__file__).resolve().parent.parent / "sample-data" / "store-policy.md").read_text()
            if not any(d["title"] == "Sample store policy" for d in repo.list_documents(p.id)):
                doc = repo.add_document(p.id, "Sample store policy", text)
                print(f"Queued sample document {doc['id']}. Run the worker to index it.")
            else:
                print("Sample document already exists.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
