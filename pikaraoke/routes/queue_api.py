"""API for the song queue and the downloads feeding it."""

from urllib.parse import unquote

import flask_babel
from flask import Response, jsonify, request
from flask_smorest import Blueprint
from marshmallow import Schema, fields, validate

from pikaraoke.lib.auth import user
from pikaraoke.lib.current_app import broadcast_event, get_karaoke_instance

_ = flask_babel.gettext

queue_api_bp = Blueprint("queue_api", __name__)


class ReorderForm(Schema):
    old_index = fields.Integer(
        required=True, metadata={"description": "Current index of the item to move"}
    )
    new_index = fields.Integer(
        required=True, metadata={"description": "Target index to move the item to"}
    )


class EnqueueForm(Schema):
    song_to_add = fields.String(required=True, metadata={"description": "Path to the song file"})
    song_added_by = fields.String(
        load_default="", metadata={"description": "Name of the user adding the song"}
    )


class QueueEditQuery(Schema):
    action = fields.String(required=True, metadata={"description": "Queue edit action to perform"})
    song = fields.String(
        metadata={"description": "Path to the song file (required unless action is 'clear')"}
    )


class OwnQueueEditForm(Schema):
    action = fields.String(
        required=True,
        validate=validate.OneOf(["skip", "swap"]),
        metadata={"description": "Whether to skip or swap the user's queued song"},
    )
    song = fields.String(required=True, metadata={"description": "Path to the queued song"})
    replacement = fields.String(metadata={"description": "Available song to swap into the queue"})


class OwnQueueReplacementsQuery(Schema):
    song = fields.String(required=True, metadata={"description": "Path to the user's queued song"})


@queue_api_bp.route("/api/get_queue")
@user
def get_queue():
    """Get the current song queue."""
    k = get_karaoke_instance()
    return jsonify(k.queue_manager.queue)


@queue_api_bp.route("/api/queue/addrandom/<int:amount>", methods=["POST"])
def add_random(amount):
    """Add random songs to the queue.

    The queue redraws itself off `queue_update`, so only the empty-handed case
    needs saying.
    """
    k = get_karaoke_instance()
    added = k.queue_manager.queue_add_random(amount)
    broadcast_event("queue_update")
    # MSG: Message shown after running out songs to add during random track addition
    message = "" if added else _("Ran out of songs!")
    return jsonify({"success": added, "message": message})


@queue_api_bp.route("/api/queue/reorder", methods=["POST"])
@queue_api_bp.arguments(ReorderForm, location="form")
def reorder(form):
    """Handle drag-and-drop reordering of the queue."""
    k = get_karaoke_instance()
    try:
        success = k.queue_manager.reorder(form["old_index"], form["new_index"])
        return jsonify({"success": success})
    except (ValueError, IndexError):
        pass

    return jsonify({"success": False})


@queue_api_bp.route("/api/queue/edit", methods=["POST"])
@queue_api_bp.arguments(QueueEditQuery, location="query")
def queue_edit(query):
    """Edit queue items (admin only)."""
    k = get_karaoke_instance()
    action = query["action"]

    if action == "clear":
        k.queue_manager.queue_clear()
        broadcast_event("skip", "clear queue")
        return jsonify({"success": True})

    song = unquote(query.get("song", ""))
    if action == "top":
        success = k.queue_manager.move_to_top(song)
    elif action == "bottom":
        success = k.queue_manager.move_to_bottom(song)
    else:
        success = k.queue_manager.queue_edit(song, action)

    # QueueManager emits queue_update and now_playing_update itself, and Karaoke
    # bridges those to the socket -- so this path adds no broadcast_event.
    return jsonify({"success": success})


@queue_api_bp.route("/api/queue/own", methods=["POST"])
@user
@queue_api_bp.arguments(OwnQueueEditForm, location="form")
def edit_own_queue(form):
    """Skip or swap a queued song belonging to the caller's device identity."""
    owner = unquote(request.cookies.get("user", "")).strip()
    if not owner:
        return (
            jsonify({"success": False, "message": _("Could not identify your queued songs.")}),
            403,
        )

    karaoke = get_karaoke_instance()
    queue_manager = karaoke.queue_manager
    if not queue_manager.user_owns_song(form["song"], owner):
        return jsonify({"success": False, "message": _("That song is not yours to change.")}), 403

    if form["action"] == "skip":
        success = queue_manager.skip_user_song(form["song"], owner)
        message = _("Song removed from your queue.") if success else _("Could not skip your song.")
    else:
        replacement = form.get("replacement", "")
        success = queue_manager.swap_user_song(form["song"], owner, replacement)
        if success:
            title = karaoke.song_manager.display_name_from_path(replacement)
            message = _("Song swapped for: %s") % title
        else:
            message = _("That replacement is no longer available.")

    return jsonify({"success": success, "message": message})


@queue_api_bp.route("/api/queue/own/replacements")
@user
@queue_api_bp.arguments(OwnQueueReplacementsQuery, location="query")
def get_own_queue_replacements(query):
    """List downloaded songs the caller can swap into their own queue."""
    owner = unquote(request.cookies.get("user", "")).strip()
    if not owner:
        return jsonify({"error": _("Could not identify your queued songs.")}), 403

    karaoke = get_karaoke_instance()
    queue_manager = karaoke.queue_manager
    if not queue_manager.user_owns_song(query["song"], owner):
        return jsonify({"error": _("That song is not yours to change.")}), 403

    songs = queue_manager.get_user_song_replacements(query["song"], owner)
    return jsonify(
        [
            {"file": song, "title": karaoke.song_manager.display_name_from_path(song)}
            for song in songs
        ]
    )


def _do_enqueue(song: str, user: str) -> Response:
    k = get_karaoke_instance()
    rc = k.queue_manager.enqueue(song, user)
    broadcast_event("queue_update")
    song_title = k.song_manager.display_name_from_path(song)
    return jsonify({"song": song_title, "success": rc})


@queue_api_bp.route("/api/enqueue", methods=["POST"])
@user
@queue_api_bp.arguments(EnqueueForm, location="form")
def enqueue_form(form):
    """Add a song to the queue."""
    return _do_enqueue(form["song_to_add"], form["song_added_by"])


@queue_api_bp.route("/api/queue/downloads")
@user
def get_current_downloads():
    """Get the status of current and pending downloads."""
    k = get_karaoke_instance()
    return jsonify(k.download_manager.get_downloads_status())


@queue_api_bp.route("/api/queue/downloads/errors/<error_id>", methods=["DELETE"])
@user
def delete_download_error(error_id):
    """Remove a download error from the list."""
    k = get_karaoke_instance()
    if k.download_manager.remove_error(error_id):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Error not found"}), 404


@queue_api_bp.route("/api/queue/downloads/errors/<error_id>/retry", methods=["POST"])
@user
def retry_download_error(error_id):
    """Re-queue a failed download."""
    k = get_karaoke_instance()
    if k.download_manager.retry_error(error_id):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Error not found"}), 404
