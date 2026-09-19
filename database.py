import sqlite3


DB_NAME = "scores.db"


# ============================================================
# CONNECTION
# ============================================================

def get_connection():
    return sqlite3.connect(
        DB_NAME
    )


# ============================================================
# INIT DATABASE
# ============================================================

def init_database():

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scores (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.commit()

    conn.close()


# ============================================================
# GET SCORE
# ============================================================

def get_score(user_id):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT score
        FROM scores
        WHERE user_id = ?
        """,
        (
            str(user_id),
        )
    )

    result = cursor.fetchone()

    conn.close()

    if result is None:
        return 0

    return result[0]


# ============================================================
# ADD SCORE
# ============================================================

def add_score(
    user_id,
    username,
    points
):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO scores (
            user_id,
            username,
            score
        )
        VALUES (?, ?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET
            username = excluded.username,
            score = scores.score + excluded.score
        """,
        (
            str(user_id),
            username,
            points
        )
    )

    conn.commit()

    conn.close()


# ============================================================
# RESET SCORES
# ============================================================

def reset_scores():

    conn = get_connection()

    cursor = conn.cursor()
    cursor.execute("DELETE FROM scores")

    conn.commit()
    conn.close()


# ============================================================
# LEADERBOARD
# ============================================================

def get_leaderboard(
    limit=10
):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            username,
            score
        FROM scores
        ORDER BY score DESC
        LIMIT ?
        """,
        (
            limit,
        )
    )

    results = cursor.fetchall()

    conn.close()

    return results