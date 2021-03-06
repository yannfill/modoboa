"""Tests for core application."""

from unittest import skipIf

import httmock

from django.core import management
from django.core.urlresolvers import reverse
from django.test import override_settings, TestCase
from django.utils.functional import cached_property

from modoboa.lib import exceptions
from modoboa.lib.tests import ModoTestCase
from modoboa.lib.tests import NO_LDAP

from .. import factories
from .. import mocks
from .. import models


class AuthenticationTestCase(ModoTestCase):

    """Validate authentication scenarios."""

    @classmethod
    def setUpTestData(cls):
        """Create test data."""
        super(AuthenticationTestCase, cls).setUpTestData()
        cls.account = factories.UserFactory(
            username="user@test.com", groups=('SimpleUsers',)
        )

    def test_authentication(self):
        """Validate simple case."""
        self.client.logout()
        data = {"username": "user@test.com", "password": "toto"}
        response = self.client.post(reverse("core:login"), data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith(reverse("core:user_index")))

        response = self.client.post(reverse("core:logout"), {})
        self.assertEqual(response.status_code, 302)

        data = {"username": "admin", "password": "password"}
        response = self.client.post(reverse("core:login"), data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith(reverse("core:dashboard")))


class ChangeDefaultAdminTestCase(TestCase):
    """Try to change the default username."""

    def test_management_command(self):
        """Use dedicated option."""
        management.call_command(
            "load_initial_data", "--admin-username", "modoadmin")
        self.assertTrue(
            self.client.login(username="modoadmin", password="password"))


@skipIf(NO_LDAP, 'No ldap module installed')
class LDAPTestCaseMixin(object):
    """Set of methods used to test LDAP features."""

    @cached_property
    def ldapauthbackend(self):
        """Return LDAPAuthBackend instance."""
        from modoboa.lib.ldap_utils import LDAPAuthBackend
        return LDAPAuthBackend()

    def activate_ldap_authentication(self):
        """Modify settings."""
        self.set_global_parameters({
            "authentication_type": "ldap",
            "ldap_server_port": 3389
        })

    def restore_user_password(self, username, new_password):
        """Restore user password to its initial state."""
        for password in ["Toto1234", "test"]:
            try:
                self.ldapauthbackend.update_user_password(
                    username, password, new_password)
            except exceptions.InternalError:
                pass
            else:
                return
        raise RuntimeError("Can't restore user password.")

    def authenticate(self, user, password, restore_before=True):
        """Restore password and authenticate user."""
        self.client.logout()
        if restore_before:
            self.restore_user_password(user, password)
        self.assertTrue(
            self.client.login(username=user, password=password))

    def searchbind_mode(self):
        """Apply settings required by the searchbind mode."""
        self.set_global_parameters({
            "ldap_auth_method": "searchbind",
            "ldap_bind_dn": "cn=admin,dc=example,dc=com",
            "ldap_bind_password": "test",
            "ldap_search_base": "ou=users,dc=example,dc=com"
        })

    def directbind_mode(self):
        """Apply settings required by the directbind mode."""
        self.set_global_parameters({
            "ldap_auth_method": "directbind",
            "ldap_user_dn_template": "cn=%(user)s,ou=users,dc=example,dc=com"
        })


@override_settings(AUTHENTICATION_BACKENDS=(
    'modoboa.lib.authbackends.LDAPBackend',
    'modoboa.lib.authbackends.SimpleBackend',
))
class LDAPAuthenticationTestCase(LDAPTestCaseMixin, ModoTestCase):
    """Validate LDAP authentication scenarios."""

    def setUp(self):
        """Create test data."""
        super(LDAPAuthenticationTestCase, self).setUp()
        self.activate_ldap_authentication()

    def check_created_user(self, username, group="SimpleUsers", with_mb=True):
        """Check that created user is valid."""
        user = models.User.objects.get(username=username)
        self.assertEqual(user.role, group)
        if with_mb:
            self.assertEqual(user.email, username)
            self.assertEqual(user.mailbox.domain.name, "example.com")
            self.assertEqual(user.mailbox.full_address, username)

    @override_settings(AUTH_LDAP_USER_DN_TEMPLATE=None)
    def test_searchbind_authentication(self):
        """Test the bind&search method."""
        self.searchbind_mode()
        username = "testuser@example.com"
        self.authenticate(username, "test")
        self.check_created_user(username)

        self.set_global_parameters({
            "ldap_admin_groups": "admins",
            "ldap_groups_search_base": "ou=groups,dc=example,dc=com"
        })
        username = "mailadmin@example.com"
        self.authenticate(username, "test", False)
        self.check_created_user(username, "DomainAdmins")

    def test_directbind_authentication(self):
        """Test the directbind method."""
        self.client.logout()
        self.directbind_mode()

        # 1: must fail because usernames of simple users must be email
        # addresses
        username = "testuser"
        with self.assertRaises(TypeError):
            self.client.login(username=username, password="test")

        # 1: must work because usernames of domain admins are not
        # always email addresses
        self.set_global_parameters({
            "ldap_admin_groups": "admins",
            "ldap_groups_search_base": "ou=groups,dc=example,dc=com"
        })
        username = "mailadmin"
        self.authenticate(username, "test", False)
        self.check_created_user(username, "DomainAdmins", False)


class ProfileTestCase(LDAPTestCaseMixin, ModoTestCase):

    @classmethod
    def setUpTestData(cls):
        """Create test data."""
        super(ProfileTestCase, cls).setUpTestData()
        cls.account = factories.UserFactory(
            username="user@test.com", groups=('SimpleUsers',)
        )

    def test_update_password(self):
        """Password update

        Two cases:
        * The default admin changes his password (no associated Mailbox)
        * A normal user changes his password
        """
        self.ajax_post(reverse("core:user_profile"),
                       {"language": "en", "oldpassword": "password",
                        "newpassword": "12345Toi", "confirmation": "12345Toi"})
        self.client.logout()

        self.assertEqual(
            self.client.login(username="admin", password="12345Toi"), True
        )
        self.assertEqual(
            self.client.login(username="user@test.com", password="toto"), True
        )

        self.ajax_post(
            reverse("core:user_profile"),
            {"oldpassword": "toto",
             "newpassword": "tutu", "confirmation": "tutu"},
            status=400
        )

        self.ajax_post(
            reverse("core:user_profile"),
            {"language": "en", "oldpassword": "toto",
             "newpassword": "Toto1234", "confirmation": "Toto1234"}
        )
        self.client.logout()
        self.assertTrue(
            self.client.login(username="user@test.com", password="Toto1234")
        )

    @override_settings(AUTHENTICATION_BACKENDS=(
        'modoboa.lib.authbackends.LDAPBackend',
        'modoboa.lib.authbackends.SimpleBackend',
    ))
    def test_update_password_ldap(self):
        """Update password for an LDAP user."""
        self.activate_ldap_authentication()
        self.searchbind_mode()

        username = "testuser@example.com"
        self.authenticate(username, "test")
        self.ajax_post(
            reverse("core:user_profile"),
            {"language": "en", "oldpassword": "test",
             "newpassword": "Toto1234", "confirmation": "Toto1234"}
        )
        self.authenticate(username, "Toto1234", False)


class APIAccessFormTestCase(ModoTestCase):

    """Check form access."""

    @classmethod
    def setUpTestData(cls):
        """Create test data."""
        super(APIAccessFormTestCase, cls).setUpTestData()
        cls.account = factories.UserFactory(
            username="user@test.com", groups=('SimpleUsers',)
        )

    def test_form_access(self):
        """Check access restrictions."""
        url = reverse("core:user_api_access")
        self.ajax_get(url)
        self.client.logout()
        self.client.login(username="user@test.com", password="toto")
        response = self.client.get(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 278)

    def test_form(self):
        """Check that token is created/removed."""
        url = reverse("core:user_api_access")
        self.ajax_post(url, {"enable_api_access": True})
        user = models.User.objects.get(username="admin")
        self.assertTrue(hasattr(user, "auth_token"))
        self.ajax_post(url, {"enable_api_access": False})
        user = models.User.objects.get(username="admin")
        self.assertFalse(hasattr(user, "auth_token"))


class APICommunicationTestCase(ModoTestCase):
    """Check communication with the API."""

    def test_management_command(self):
        """Check command."""
        with httmock.HTTMock(
                mocks.modo_api_instance_search,
                mocks.modo_api_instance_create,
                mocks.modo_api_instance_update,
                mocks.modo_api_versions):
            management.call_command("communicate_with_public_api")
        self.assertEqual(models.LocalConfig.objects.first().api_pk, 100)

        url = reverse("core:information")
        response = self.ajax_request("get", url)
        self.assertIn("9.0.0", response["content"])
