from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("DATABASE_PATH", ROOT_DIR / "data" / "ledger.db")).expanduser()
DB_LOCK = threading.Lock()


class IdempotencyConflictError(ValueError):
    """Raised when an idempotency key is reused with different parameters."""
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.replace("Z", "+00:00")
    return datetime.fromisoformat(cleaned)


def day_key(value: datetime) -> str:
    return value.date().isoformat()


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                total_points INTEGER NOT NULL DEFAULT 0,
                transaction_count INTEGER NOT NULL DEFAULT 0,
                active_days INTEGER NOT NULL DEFAULT 0,
                largest_transaction INTEGER NOT NULL DEFAULT 0,
                first_transaction_at TEXT,
                last_transaction_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                raw_amount INTEGER NOT NULL,
                effective_points INTEGER NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS user_activity_days (
                user_id TEXT NOT NULL,
                activity_day TEXT NOT NULL,
                PRIMARY KEY (user_id, activity_day),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );
            """
        )


def seed_demo_data() -> None:
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS count FROM transactions").fetchone()["count"]
        if count:
            return

    demo_rows = [
        ("demo-alice", 120, "seed-alice-1", "Alice joined the board"),
        ("demo-alice", 60, "seed-alice-2", "Alice follow-up"),
        ("demo-bob", 75, "seed-bob-1", "Bob opened strong"),
        ("demo-bob", 35, "seed-bob-2", "Bob consistency"),
        ("demo-bob", 25, "seed-bob-3", "Bob steady activity"),
        ("demo-charlie", 95, "seed-charlie-1", "Charlie burst"),
    ]

    base = utc_now() - timedelta(days=6)
    with DB_LOCK, get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for index, (user_id, amount, key, note) in enumerate(demo_rows):
                created_at = isoformat(base + timedelta(days=index, hours=index % 3))
                _insert_transaction(
                    conn=conn,
                    user_id=user_id,
                    amount=amount,
                    idempotency_key=key,
                    note=note,
                    created_at=created_at,
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def validate_user_id(user_id: str) -> str:
    cleaned = user_id.strip()
    if not 2 <= len(cleaned) <= 32:
        raise ValueError("userId must be between 2 and 32 characters")
    allowed = all(ch.isalnum() or ch in {"_", "-"} for ch in cleaned)
    if not allowed or not cleaned[0].isalnum():
        raise ValueError("userId may contain only letters, numbers, underscore, and dash")
    return cleaned


def effective_points(raw_amount: int) -> int:
    return min(raw_amount, 100)


def _upsert_user(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    points_delta: int,
    transaction_count_delta: int,
    active_days_delta: int,
    largest_transaction: int,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO users (
            user_id,
            total_points,
            transaction_count,
            active_days,
            largest_transaction,
            first_transaction_at,
            last_transaction_at,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            total_points = users.total_points + excluded.total_points,
            transaction_count = users.transaction_count + excluded.transaction_count,
            active_days = users.active_days + excluded.active_days,
            largest_transaction = MAX(users.largest_transaction, excluded.largest_transaction),
            first_transaction_at = MIN(users.first_transaction_at, excluded.first_transaction_at),
            last_transaction_at = excluded.last_transaction_at,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            points_delta,
            transaction_count_delta,
            active_days_delta,
            largest_transaction,
            created_at,
            created_at,
            created_at,
            created_at,
        ),
    )


def _insert_transaction(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    amount: int,
    idempotency_key: str,
    note: str | None,
    created_at: str,
) -> dict:
    existing = conn.execute(
        "SELECT * FROM transactions WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if existing:
        if (
            existing["user_id"] != user_id
            or existing["raw_amount"] != amount
            or existing["note"] != note
        ):
            raise IdempotencyConflictError(
                f"Idempotency key collision: key '{idempotency_key}' is already used for different request data."
            )
        return dict(existing)

    points = effective_points(amount)
    _upsert_user(
        conn,
        user_id=user_id,
        points_delta=points,
        transaction_count_delta=1,
        active_days_delta=0,
        largest_transaction=amount,
        created_at=created_at,
    )

    day_added = conn.execute(
        """
        INSERT OR IGNORE INTO user_activity_days (user_id, activity_day)
        VALUES (?, ?)
        """,
        (user_id, day_key(parse_iso_datetime(created_at) or utc_now())),
    ).rowcount

    if day_added:
        conn.execute(
            """
            UPDATE users
            SET active_days = active_days + 1,
                updated_at = ?
            WHERE user_id = ?
            """,
            (created_at, user_id),
        )

    conn.execute(
        """
        INSERT INTO transactions (
            idempotency_key,
            user_id,
            raw_amount,
            effective_points,
            note,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (idempotency_key, user_id, amount, points, note, created_at),
    )
    saved = conn.execute(
        "SELECT * FROM transactions WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    return dict(saved)


def create_transaction(
    *,
    user_id: str,
    amount: int,
    idempotency_key: str,
    note: str | None,
) -> dict:
    created_at = isoformat(utc_now())
    with DB_LOCK, get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            inserted = _insert_transaction(
                conn,
                user_id=user_id,
                amount=amount,
                idempotency_key=idempotency_key,
                note=note,
                created_at=created_at,
            )
            conn.execute("COMMIT")
            return inserted
        except Exception:
            conn.execute("ROLLBACK")
            raise


def get_user_summary(user_id: str) -> dict | None:
    with get_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not user:
            return None

        recent_transactions = conn.execute(
            """
            SELECT idempotency_key, raw_amount, effective_points, note, created_at
            FROM transactions
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 10
            """,
            (user_id,),
        ).fetchall()

        return {
            "userId": user["user_id"],
            "totalPoints": user["total_points"],
            "transactionCount": user["transaction_count"],
            "activeDays": user["active_days"],
            "largestTransaction": user["largest_transaction"],
            "firstTransactionAt": user["first_transaction_at"],
            "lastTransactionAt": user["last_transaction_at"],
            "recentTransactions": [
                {
                    "idempotencyKey": tx["idempotency_key"],
                    "rawAmount": tx["raw_amount"],
                    "effectivePoints": tx["effective_points"],
                    "note": tx["note"],
                    "createdAt": tx["created_at"],
                }
                for tx in recent_transactions
            ],
        }


def ranking_score(user: sqlite3.Row) -> dict:
    total_points = int(user["total_points"])
    transaction_count = int(user["transaction_count"])
    active_days = int(user["active_days"])
    largest_transaction = int(user["largest_transaction"])
    last_transaction_at = parse_iso_datetime(user["last_transaction_at"])

    recency_bonus = 0.0
    if last_transaction_at:
        days_since = max(0, (utc_now() - last_transaction_at).days)
        recency_bonus = max(0.0, 18.0 - days_since * 1.5)

    consistency_bonus = min(transaction_count, 40) * 2.25
    activity_bonus = min(active_days, 30) * 3.75

    largest_transaction_points = min(largest_transaction, 100)
    dominance_share = (largest_transaction_points / total_points) if total_points else 0.0
    dominance_penalty = max(0.0, dominance_share - 0.55) * 55.0

    score = round(
        total_points + consistency_bonus + activity_bonus + recency_bonus - dominance_penalty,
        2,
    )
    return {
        "userId": user["user_id"],
        "score": score,
        "totalPoints": total_points,
        "transactionCount": transaction_count,
        "activeDays": active_days,
        "largestTransaction": largest_transaction,
        "lastTransactionAt": user["last_transaction_at"],
        "components": {
            "points": total_points,
            "consistencyBonus": round(consistency_bonus, 2),
            "activityBonus": round(activity_bonus, 2),
            "recencyBonus": round(recency_bonus, 2),
            "dominancePenalty": round(dominance_penalty, 2),
        },
    }


def get_ranking(limit: int = 10) -> list[dict]:
    with get_connection() as conn:
        users = conn.execute("SELECT * FROM users").fetchall()

    ranked = [ranking_score(user) for user in users]
    ranked.sort(
        key=lambda item: (
            -item["score"],
            -item["totalPoints"],
            -item["transactionCount"],
            item["userId"],
        )
    )
    return ranked[:limit]
