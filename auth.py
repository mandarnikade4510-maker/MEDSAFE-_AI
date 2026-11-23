"""Simple login / sign-up for MedSafe AI (SQLite + salted PBKDF2 password hashes)."""
import hashlib
import hmac
import os
import re
import sqlite3

import streamlit as st

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.db")
ITERATIONS = 200_000


def _conn():
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "username TEXT PRIMARY KEY, full_name TEXT, salt BLOB, pw_hash BLOB)"
    )
    return con


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)


def create_user(username, full_name, password):
    """Return (ok, message)."""
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.]{3,30}", username):
        return False, "Username: 3-30 characters, letters, numbers, _ or . only."
    if len(password) < 6:
        return False, "Password must be at least 6 characters."
    salt = os.urandom(16)
    try:
        with _conn() as con:
            con.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?)",
                (username, full_name.strip() or username, salt, _hash(password, salt)),
            )
    except sqlite3.IntegrityError:
        return False, "That username is already taken."
    return True, "Account created. You can log in now."


def verify_user(username, password):
    """Return the user's display name if the login is correct, else None."""
    with _conn() as con:
        row = con.execute(
            "SELECT full_name, salt, pw_hash FROM users WHERE username = ?",
            (username.strip().lower(),),
        ).fetchone()
    if row and hmac.compare_digest(_hash(password, row[1]), row[2]):
        return row[0]
    return None


def require_login():
    """Call once near the top of the app. Stops the page until the user is logged in."""
    if st.session_state.get("user"):
        with st.sidebar:
            st.write(f"👤 Logged in as **{st.session_state['user']}**")
            if st.button("Log out"):
                st.session_state.pop("user", None)
                st.rerun()
        return

    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.subheader("🔐 Please log in to continue")
        login_tab, signup_tab = st.tabs(["Log in", "Sign up"])

        with login_tab:
            u = st.text_input("Username", key="login_user")
            p = st.text_input("Password", type="password", key="login_pass")
            if st.button("Log in", type="primary"):
                name = verify_user(u, p)
                if name:
                    st.session_state["user"] = name
                    st.rerun()
                else:
                    st.error("Wrong username or password.")

        with signup_tab:
            n = st.text_input("Full name", key="su_name")
            u2 = st.text_input("Choose a username", key="su_user")
            p2 = st.text_input("Choose a password", type="password", key="su_pass")
            p3 = st.text_input("Confirm password", type="password", key="su_pass2")
            if st.button("Create account"):
                if p2 != p3:
                    st.error("Passwords do not match.")
                else:
                    ok, msg = create_user(u2, n, p2)
                    (st.success if ok else st.error)(msg)
    st.stop()