"""
Single-user authentication for GSTR-1 Generator.

In LOCAL/DEV mode (FLASK_ENV not set to "production"), authentication is
completely bypassed — the tool is treated as a personal utility on your own
machine.

In PRODUCTION mode, reads APP_USERNAME and APP_PASSWORD_HASH from environment
variables. Password is stored ONLY as a bcrypt hash, never plaintext.

To generate a password hash:
    python -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_PASSWORD', bcrypt.gensalt()).decode())"
"""
import os
import secrets
from functools import wraps
from flask import session, redirect, url_for, request, render_template, flash

import bcrypt


def is_production() -> bool:
    return os.environ.get("FLASK_ENV") == "production"


def get_credentials():
    username = os.environ.get("APP_USERNAME", "admin")
    password_hash = os.environ.get("APP_PASSWORD_HASH", "")
    return username, password_hash


def verify_password(plain_password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"),
                              password_hash.encode("utf-8"))
    except Exception:
        return False


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_production():
            return view(*args, **kwargs)
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def register_auth_routes(app):
    """Register /login and /logout endpoints on the Flask app."""

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not is_production():
            return redirect(url_for("index"))

        if session.get("logged_in"):
            return redirect(url_for("index"))

        if request.method == "POST":
            username, password_hash = get_credentials()
            entered_user = (request.form.get("username") or "").strip()
            entered_pass = request.form.get("password") or ""

            user_ok = secrets.compare_digest(entered_user, username)
            pass_ok = verify_password(entered_pass, password_hash)

            if user_ok and pass_ok:
                session.clear()
                session["logged_in"] = True
                session["user"] = username
                session.permanent = True
                next_url = request.args.get("next") or url_for("index")
                return redirect(next_url)

            flash("Invalid username or password.", "error")

        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login") if is_production() else url_for("index"))

    @app.before_request
    def require_login_on_protected():
        # In local/dev mode, skip auth entirely
        if not is_production():
            return None
        # In production, allow public endpoints
        public = {"login", "static", "healthz"}
        if request.endpoint in public:
            return None
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                from flask import jsonify
                return jsonify({"ok": False, "error": "Authentication required"}), 401
            return redirect(url_for("login", next=request.path))
        return None
