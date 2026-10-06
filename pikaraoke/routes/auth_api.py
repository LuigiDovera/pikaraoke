"""Login for programs: the door a client reading /apidocs comes through.

The browser's `POST /auth` answers in flashes and redirects, which a script
cannot read; this one answers in status codes.
"""

from flask import jsonify
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.auth import log_in, public
from pikaraoke.lib.current_app import get_admin_auth, is_admin, is_user

auth_api_bp = Blueprint("auth_api", __name__)


class LoginSchema(Schema):
    password = fields.String(load_default=None, metadata={"description": "User or admin password"})
    # Retain the original request field for existing API clients.
    admin_password = fields.String(load_default=None, metadata={"description": "Deprecated alias"})


@auth_api_bp.route("/api/auth")
@public
def auth_status():
    """Report the caller's role and whether ordinary users need a password."""
    return jsonify(
        {
            "authenticated": is_user(),
            "admin": is_admin(),
            "password_required": get_admin_auth().is_user_password_set(),
            "admin_password_set": get_admin_auth().is_password_set(),
        }
    )


@auth_api_bp.route("/api/auth", methods=["POST"])
@public
@auth_api_bp.arguments(LoginSchema)
def login(credentials):
    """Exchange the user or admin password for a session cookie.

    The cookie comes back in `Set-Cookie`, which browsers hide from scripts, so
    this console cannot show it. Call a host-only endpoint to confirm it worked.
    """
    password = credentials["password"]
    if password is None:
        password = credentials["admin_password"]
    if password is None:
        password = ""
    if not get_admin_auth().is_password_set() and not get_admin_auth().is_user_password_set():
        return jsonify({"authenticated": True, "admin": False})
    role = log_in(password)
    if role is None:
        return jsonify({"error": "Incorrect password"}), 401
    return jsonify({"authenticated": True, "admin": role == "admin"})
