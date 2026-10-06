"""Tests for admin password storage and verification."""

import pytest

from pikaraoke.lib.admin_auth import AdminAuth
from pikaraoke.lib.preference_manager import PreferenceManager


@pytest.fixture
def config_path(tmp_path):
    return str(tmp_path / "config.ini")


@pytest.fixture
def auth(config_path):
    return AdminAuth(PreferenceManager(config_path))


class TestSecretKey:
    def test_it_survives_a_restart(self, auth, config_path):
        assert auth.secret_key == AdminAuth(PreferenceManager(config_path)).secret_key

    def test_it_is_long_enough_to_sign_with(self, auth):
        assert len(auth.secret_key) >= 32


class TestPassword:
    def test_none_is_set_initially(self, auth):
        assert not auth.is_password_set()

    def test_verify_is_false_when_no_password_is_set(self, auth):
        assert not auth.verify("anything")

    def test_the_correct_password_verifies(self, auth):
        auth.set_password("hunter2")

        assert auth.is_password_set()
        assert auth.verify("hunter2")

    def test_an_incorrect_password_does_not(self, auth):
        auth.set_password("hunter2")

        assert not auth.verify("hunter3")
        assert not auth.verify("")

    def test_it_survives_a_restart(self, auth, config_path):
        auth.set_password("hunter2")

        assert AdminAuth(PreferenceManager(config_path)).verify("hunter2")

    def test_the_password_is_not_stored_verbatim(self, auth, config_path):
        auth.set_password("hunter2")

        assert "hunter2" not in open(config_path, encoding="utf-8").read()

    def test_the_same_password_hashes_differently_each_time(self, auth, config_path):
        auth.set_password("hunter2")
        first = PreferenceManager(config_path).get("password_hash", "", section="SECRETS")
        auth.set_password("hunter2")
        second = PreferenceManager(config_path).get("password_hash", "", section="SECRETS")

        assert first != second

    def test_an_empty_password_clears_it(self, auth):
        auth.set_password("hunter2")
        auth.set_password("")

        assert not auth.is_password_set()
        assert not auth.verify("hunter2")

    def test_none_clears_it_too(self, auth):
        auth.set_password("hunter2")
        auth.set_password(None)

        assert not auth.is_password_set()

    def test_admin_and_user_passwords_are_independent(self, auth):
        auth.set_password("admin")
        auth.set_user_password("user")

        assert auth.verify("admin")
        assert not auth.verify_user("admin")
        assert auth.verify_user("user")
        assert not auth.verify("user")

    def test_changing_the_user_password_changes_its_session_token(self, auth):
        auth.set_user_password("user")
        before = auth.user_session_token
        auth.set_user_password("new-user")

        assert auth.user_session_token != before

    def test_the_user_password_survives_a_restart(self, auth, config_path):
        auth.set_user_password("user")

        restarted = AdminAuth(PreferenceManager(config_path))

        assert restarted.is_user_password_set()
        assert restarted.verify_user("user")
        assert restarted.user_session_token == auth.user_session_token


class TestSessionToken:
    def test_it_changes_when_the_password_changes(self, auth):
        auth.set_password("hunter2")
        before = auth.session_token
        auth.set_password("hunter3")

        assert auth.session_token != before

    def test_it_changes_when_the_password_is_cleared(self, auth):
        auth.set_password("hunter2")
        before = auth.session_token
        auth.set_password("")

        assert auth.session_token != before

    def test_it_survives_a_restart(self, auth, config_path):
        auth.set_password("hunter2")

        assert AdminAuth(PreferenceManager(config_path)).session_token == auth.session_token
