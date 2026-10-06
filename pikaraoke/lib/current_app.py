"""Flask application context utilities for PiKaraoke."""

import logging
from typing import Any

from flask import current_app, request, session
from flask_socketio import emit

from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.admin_auth import AdminAuth


def is_admin() -> bool:
    """Whether this request is authenticated as the admin.

    Admin access always requires the admin password, even when no password has
    been configured; an unset admin password never elevates ordinary users.
    """
    auth = get_admin_auth()
    return auth.is_password_set() and session.get("admin") == auth.session_token


def is_user() -> bool:
    """Whether this request may use ordinary karaoke features."""
    auth = get_admin_auth()
    return (
        not auth.is_user_password_set()
        or is_admin()
        or session.get("user") == auth.user_session_token
    )


def get_karaoke_instance() -> Karaoke:
    """Get the current app's Karaoke instance
    This function returns the Karaoke instance stored in the current app's configuration.
    Returns:
        Karaoke: The Karaoke instance stored in the current app's configuration.
    """
    return current_app.config["KARAOKE_INSTANCE"]


def get_admin_auth() -> AdminAuth:
    """Get the current app's admin authentication store."""
    return current_app.config["ADMIN_AUTH"]


def get_site_name() -> str:
    """Get the site name from the current app's configuration
    This function returns the site name stored in the current app's configuration.
    Returns:
        str: The site name stored in the current app's configuration.
    """
    return current_app.config["SITE_NAME"]


def broadcast_event(event: str, data: Any = None) -> None:
    """Broadcast a SocketIO event to all connected clients.

    Args:
        event: Name of the event to broadcast.
        data: Optional data payload to send with the event.
    """
    logging.debug("Broadcasting event: " + event)
    emit(event, data, namespace="/", broadcast=True)
