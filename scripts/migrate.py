"""Apply pending database migrations (also done automatically at API start unless AUTO_MIGRATE=0).

python -m scripts.migrate            # apply what is pending
python -m scripts.migrate --status   # show what is applied / pending, change nothing
"""

import argparse

from app import db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    db.wait_ready()
    try:
        if a.status:
            pending = db.pending_migrations()
            print("pending:", ", ".join(pending) if pending else "none (schema is current)")
            return
        applied = db.migrate()
        db.check_embedding_compat()
        print("applied:", ", ".join(applied) if applied else "nothing to do (schema is current)")
    finally:
        db.close_pool()


if __name__ == "__main__":
    main()
