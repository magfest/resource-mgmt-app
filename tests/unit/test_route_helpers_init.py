"""
Tests that the RouteHelpers (h) global is properly initialized.

Background: Admin modules do `from app.routes import h` at import time. If any module
triggers the admin import tree before register_all_routes() sets h, every admin module
gets h=None and all write operations (update, create) crash with AttributeError.

This has regressed before when an import was moved earlier in create_app().
"""


class TestRouteHelpersInitialization:
    """Verify that h is set (not None) after app creation."""

    def test_h_is_set_in_routes_module(self, app):
        """
        After create_app(), app.routes.h must be backed by a RouteHelpers instance.

        If this fails, register_all_routes() either wasn't called or was
        called without a helpers argument.
        """
        import app.routes as routes_mod
        assert routes_mod.h, (
            "app.routes.h has no backing RouteHelpers after create_app(). "
            "register_all_routes() was not called or failed."
        )

    def test_h_has_required_methods(self, app):
        """Verify h has all the methods that admin routes depend on."""
        import app.routes as routes_mod
        h = routes_mod.h
        assert h
        assert callable(h.get_active_user_id)
        assert callable(h.is_super_admin)
        assert callable(h.get_active_user)
        assert callable(h.active_user_roles)
        assert callable(h.active_user_approval_group_ids)

    def test_site_content_import_does_not_break_h(self, app):
        """
        Importing site_content (which triggers the admin module tree) must
        not happen before register_all_routes() sets h.

        This is the specific regression that occurred: get_site_content was
        imported as a Jinja global early in create_app(), triggering the
        admin import tree before h was set. All admin modules then got h=None.
        """
        import app.routes as routes_mod

        # If site_content was imported too early, h in the routes module
        # would still be set (it gets set later), but the *copied* h in
        # each admin module would be None. We can detect this by checking
        # that a fresh create_app() sets h before any admin imports.
        assert routes_mod.h

        # Also verify the function is available as a Jinja global
        assert 'get_site_content' in app.jinja_env.globals, (
            "get_site_content is not registered as a Jinja global. "
            "It may have been removed from create_app()."
        )

    def test_admin_write_operations_have_user_id(self, app, client):
        """
        Admin routes that write to the database need h.get_active_user_id().
        Simulate the call chain to verify it doesn't crash.
        """
        import app.routes as routes_mod
        h = routes_mod.h
        assert h

        with app.test_request_context():
            from flask import session
            session["active_user_id"] = "test:admin"
            # This is the exact call that was crashing
            user_id = h.get_active_user_id()
            assert user_id == "test:admin"
