"""Admin routes for system control and authentication."""

import getpass
import logging
import shutil
import subprocess
import sys
import threading
import time

import flask_babel
from flask import Response, flash, redirect, render_template, session, url_for
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.auth import grant_admin_session, log_in, public
from pikaraoke.lib.current_app import (
    get_admin_auth,
    get_karaoke_instance,
    get_site_name,
    is_admin,
)
from pikaraoke.lib.youtube_dl import get_youtubedl_version, upgrade_youtubedl

_ = flask_babel.gettext

admin_bp = Blueprint("admin", __name__)


class AdminPasswordForm(Schema):
    admin_password = fields.String(
        load_default="", metadata={"description": "New admin password; empty clears it"}
    )


class UserPasswordForm(Schema):
    user_password = fields.String(
        load_default="", metadata={"description": "New user password; empty clears it"}
    )


class AuthForm(Schema):
    password = fields.String(load_default=None, metadata={"description": "User or admin password"})
    admin_password = fields.String(load_default=None, metadata={"description": "Deprecated alias"})


# A desktop session's polkit agent would otherwise put a password dialog on the TV and
# hold the halt until someone answers it.
_SYSTEMCTL = ["systemctl", "--no-ask-password"]

# Each host step, named for the log.
HostStep = tuple[str, list[str]]
_SHUTDOWN: HostStep = ("Shutting down", [*_SYSTEMCTL, "poweroff"])
_REBOOT: HostStep = ("Rebooting", [*_SYSTEMCTL, "reboot"])
_EXPAND: HostStep = ("Expanding the filesystem", ["raspi-config", "--expand-rootfs"])


def _run_as_root(step: HostStep) -> bool:
    """Run a root-only command directly, then through passwordless sudo.

    PiKaraoke normally runs as a user, so the direct attempt only succeeds as root or
    where polkit allows it, and the sudo one only where a sudoers rule does.
    """
    action, cmd = step
    # On stock Pi OS the direct attempt is always refused and sudo succeeds, so a
    # refusal is only a warning once every attempt has failed.
    refusals = []
    for attempt in (cmd, ["sudo", "-n", *cmd]):
        try:
            result = subprocess.run(attempt, capture_output=True, text=True)
        except OSError as e:
            refusals.append(f"{' '.join(attempt)}: {e}")
            continue
        if result.returncode == 0:
            refused_first = f", after {'; '.join(refusals)}" if refusals else ""
            logging.info(f"{action}: ran {' '.join(attempt)}{refused_first}")
            return True
        refusals.append(f"{' '.join(attempt)}: {result.stderr.strip()}")
    rule = f"{getpass.getuser()} ALL=(root) NOPASSWD: {shutil.which(cmd[0]) or cmd[0]}"
    logging.warning(
        f"{action} refused - {'; '.join(refusals)}. To allow it, add this line with "
        f"'sudo visudo -f /etc/sudoers.d/pikaraoke': {' '.join([rule, *cmd[1:]])}"
    )
    return False


def delayed_halt(k: Karaoke, steps: list[HostStep], refused: str = "") -> None:
    """Run the host commands once the page has rendered, then stop PiKaraoke.

    No steps means quit. A refused step leaves the queue and playback as they were,
    and tells the room, since the page has already claimed success.
    """
    time.sleep(1.5)
    # all() stops at the first refusal, so a failed expand never reboots.
    if not all(_run_as_root(step) for step in steps):
        # The announcement still holds the room's one notification slot.
        k.reset_now_playing_notification()
        k.send_notification(refused, "danger")
        return
    k.queue_manager.queue_clear()
    k.stop()
    if not steps:
        sys.exit()


@admin_bp.route("/update_ytdl", methods=["POST"])
def update_ytdl():
    """Update yt-dlp to the latest version."""
    k = get_karaoke_instance()

    def update_youtube_dl():
        time.sleep(3)
        k.youtubedl_version = upgrade_youtubedl()

    flash(
        # MSG: Message shown after starting the yt-dlp update.
        _("Updating yt-dlp! Should take a minute or two... "),
        "is-warning",
    )
    th = threading.Thread(target=update_youtube_dl)
    th.start()
    return redirect(url_for("info.info"))


def _announce_halt(message: str, steps: list[HostStep], refused: str = "") -> Response:
    """Tell every screen in the room, then halt once the page has rendered."""
    k = get_karaoke_instance()
    flash(message, "is-danger")
    k.send_notification(message, "danger")
    threading.Thread(target=delayed_halt, args=[k, steps, refused]).start()
    return redirect(url_for("home.home"))


@admin_bp.route("/quit", methods=["POST"])
def quit():
    """Exit the PiKaraoke application."""
    # MSG: Message shown after quitting pikaraoke.
    return _announce_halt(_("Exiting pikaraoke now!"), [])


@admin_bp.route("/shutdown", methods=["POST"])
def shutdown():
    """Shut down the host system."""
    return _announce_halt(
        # MSG: Message shown after shutting down the system.
        _("Shutting down system now!"),
        [_SHUTDOWN],
        # MSG: Message shown when the system refuses to let pikaraoke shut it down.
        _("Shutdown failed: pikaraoke does not have permission to power off this system."),
    )


@admin_bp.route("/reboot", methods=["POST"])
def reboot():
    """Reboot the host system."""
    return _announce_halt(
        # MSG: Message shown after rebooting the system.
        _("Rebooting system now!"),
        [_REBOOT],
        # MSG: Message shown when the system refuses to let pikaraoke reboot it.
        _("Reboot failed: pikaraoke does not have permission to restart this system."),
    )


@admin_bp.route("/expand_fs", methods=["POST"])
def expand_fs():
    """Expand filesystem on Raspberry Pi."""
    k = get_karaoke_instance()
    if k.is_raspberry_pi:
        # MSG: Message shown after expanding the filesystem.
        flash(_("Expanding filesystem and rebooting system now!"), "is-danger")
        # MSG: Message shown when the system refuses to let pikaraoke expand the filesystem.
        refused = _("Expand failed: pikaraoke does not have permission to resize the filesystem.")
        threading.Thread(target=delayed_halt, args=[k, [_EXPAND, _REBOOT], refused]).start()
    else:
        # MSG: Message shown after trying to expand the filesystem on a non-raspberry pi device.
        flash(_("Cannot expand fs on non-raspberry pi devices!"), "is-danger")
    return redirect(url_for("home.home"))


@admin_bp.route("/auth", methods=["POST"])
@public
@admin_bp.arguments(AuthForm, location="form")
def auth(form):
    """Authenticate as a user or admin from the browser form."""
    password = form["password"]
    if password is None:
        password = form["admin_password"] or ""
    role = log_in(password)
    if role == "admin":
        # MSG: Message shown after logging in as admin successfully
        flash(_("Admin mode granted!"), "is-success")
        return redirect(url_for("home.home"))
    if role == "user":
        # MSG: Message shown after logging in as a user successfully.
        flash(_("Logged in successfully!"), "is-success")
        return redirect(url_for("home.home"))
    # MSG: Message shown after a failed login.
    flash(_("Incorrect password!"), "is-danger")
    return redirect(url_for("admin.login_page"))


@admin_bp.route("/login")
@public
def login_page():
    """Show the sign-in page to unauthenticated visitors."""
    if is_admin():
        return redirect(url_for("home.home"))
    return render_template(
        "login.html",
        site_title=get_site_name(),
        # MSG: Title of the page used to sign in.
        title=_("Login"),
    )


@admin_bp.route("/admin_password", methods=["POST"])
@admin_bp.arguments(AdminPasswordForm, location="form")
def set_admin_password(form):
    """Set, change or clear the admin password without restarting."""
    # No current-password field: an admin session can already shut the box down.
    password = form["admin_password"]
    admin_auth = get_admin_auth()
    admin_auth.set_password(password)
    if password:
        # set_password logged every device out; keep the one that just set it.
        grant_admin_session()
        # MSG: Message shown after setting a new admin password.
        flash(_("Admin password set. Other devices will need to log in again."), "is-success")
    else:
        # MSG: Message shown after clearing the admin password.
        flash(
            _(
                "Admin password cleared. Admin access now requires setting a new password "
                "from the command line."
            ),
            "is-warning",
        )
    return redirect(url_for("info.info"))


@admin_bp.route("/user_password", methods=["POST"])
@admin_bp.arguments(UserPasswordForm, location="form")
def set_user_password(form):
    """Set, change or clear the ordinary-user password."""
    password = form["user_password"]
    auth = get_admin_auth()
    auth.set_user_password(password)
    if password:
        grant_admin_session()
        # MSG: Message shown after setting the user password.
        flash(_("User password set. Other users will need to log in again."), "is-success")
    else:
        # MSG: Message shown after clearing the user password.
        flash(
            _("User password cleared. Ordinary karaoke features are open to everyone."),
            "is-warning",
        )
    return redirect(url_for("info.info"))


@admin_bp.route("/logout", methods=["POST"])
# Public though the button is admin-only: it clears the caller's own
# session and grants nothing, so gating it would refuse a no-op.
@public
def logout():
    """Log out of this browser session."""
    session.pop("admin", None)
    session.pop("user", None)
    # MSG: Message shown after logging out successfully.
    flash(_("Logged out!"), "is-success")
    return redirect(url_for("admin.login_page"))
