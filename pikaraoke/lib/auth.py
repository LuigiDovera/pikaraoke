"""Authorization: a route is host-only unless it is marked public.

The gate is a `before_request`, so it answers ahead of `@bp.arguments`
validation -- an unauthenticated malformed POST gets a 403, not a 422.
"""

from collections.abc import Callable

import flask_babel
from flask import Flask, flash, jsonify, redirect, request, session, url_for
from flask.typing import ResponseReturnValue
from flask_smorest import Api

from pikaraoke.lib.current_app import get_admin_auth, is_admin, is_user

_ = flask_babel.gettext

# Registered by Flask and flask-smorest, so they cannot carry a marker. Never a
# hatch for endpoints we own.
_LIBRARY_ENDPOINTS = frozenset({"static", "api-docs.openapi_json", "api-docs.openapi_swagger_ui"})

_SECURITY_SCHEME = "pikaSession"


def public(view: Callable) -> Callable:
    """This endpoint is open to everyone in the room, to the gate and to the spec."""
    view.pika_public = True
    # flask-smorest reads `_apidoc` when `@route` registers the view, so this has
    # to sit below it -- above, the gate opens while the spec still asks for a login.
    apidoc = getattr(view, "_apidoc", {})
    apidoc.setdefault("manual_doc", {})["security"] = []
    view._apidoc = apidoc
    return view


def user(view: Callable) -> Callable:
    """Allow callers signed in with either the user or admin password."""
    view.pika_user = True
    return view


def grant_admin_session() -> None:
    """Make this caller an admin until the password changes or the cookie expires."""
    session.pop("user", None)
    session["admin"] = get_admin_auth().session_token
    session.permanent = True


def grant_user_session() -> None:
    """Make this caller an ordinary user until the user password changes."""
    session.pop("admin", None)
    session["user"] = get_admin_auth().user_session_token
    session.permanent = True


def log_in(password: str) -> str | None:
    """Authenticate with either password, preferring admin if they are identical."""
    auth = get_admin_auth()
    if auth.verify(password):
        grant_admin_session()
        return "admin"
    if auth.verify_user(password):
        grant_user_session()
        return "user"
    return None


def document_auth(app: Flask, api: Api) -> None:
    """Name the session cookie in the spec and require it on protected operations.

    Without this, a client reading `/apidocs` sees the whole API and no way in.
    """
    api.spec.components.security_scheme(
        _SECURITY_SCHEME,
        {
            "type": "apiKey",
            "in": "cookie",
            "name": app.config["SESSION_COOKIE_NAME"],
            "description": (
                "Session cookie issued by POST /api/auth. Its privileges depend on whether "
                "the user or admin password was supplied."
            ),
        },
    )
    # Read when the spec is serialised, so this need not run before the blueprints.
    api.spec.options["security"] = [{_SECURITY_SCHEME: []}]


def install_auth_gate(app: Flask) -> None:
    """Require the role declared on each endpoint, defaulting to admin."""

    @app.before_request
    def require_admin() -> ResponseReturnValue | None:
        # An unmatched rule is a 404, and Flask's to answer.
        if request.endpoint is None:
            return None
        if request.endpoint in _LIBRARY_ENDPOINTS:
            return None
        view = app.view_functions.get(request.endpoint)
        if getattr(view, "pika_public", False):
            return None
        if getattr(view, "pika_user", False):
            if is_user():
                return None
            return _refuse()
        # Last, because it re-reads config.ini: ~0.4ms on a desktop and several
        # times that on a Pi, which the public routes serving HLS segments skip.
        if is_admin():
            return None
        return _refuse()


def _refuse() -> ResponseReturnValue:
    """One refusal for the whole app, in the medium the caller is reading.

    The path is the whole answer: every JSON route sits under `/api` and no page
    does, which `test_route_mediums.py` holds the tree to. Nothing is left to
    guess from the request headers.
    """
    if request.path.startswith("/api/"):
        return jsonify({"error": "Unauthorized"}), 403
    # MSG: Message shown when someone who is not logged in tries to load a page.
    flash(_("You don't have permission to do that"), "is-danger")
    return redirect(url_for("admin.login_page"))
