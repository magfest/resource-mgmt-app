"""The preview must render what the email renders."""
from app import db
from app.models import EmailTemplate, EmailTemplateEventOverride, EventCycle
from app.services.email_templates import (
    EMAIL_TEMPLATE_VARIABLES, get_sample_context, preview_template,
)


def test_sample_context_covers_every_documented_variable(app):
    """A documented variable missing from the sample context shows as a blank
    in the preview only, so the preview stops being a preview."""
    with app.app_context():
        ctx = get_sample_context()
        documented = set()
        for variables in EMAIL_TEMPLATE_VARIABLES.values():
            documented.update(name.split(".")[0] for name in variables)

        assert documented <= set(ctx), f"missing from sample: {documented - set(ctx)}"


def test_documented_dotted_attributes_resolve(app):
    """A top-level key is not enough. work_item.reason must exist on the mock,
    or Jinja renders it blank exactly like a missing variable."""
    with app.app_context():
        ctx = get_sample_context()
        missing = []
        for variables in EMAIL_TEMPLATE_VARIABLES.values():
            for dotted in variables:
                parts = dotted.split(".")
                obj = ctx.get(parts[0])
                for attr in parts[1:]:
                    obj = getattr(obj, attr, None)
                    if obj is None:
                        missing.append(dotted)
                        break

        assert not missing, f"documented but unresolvable: {sorted(set(missing))}"


def test_preview_keeps_unsaved_form_text(app, seed_workflow_data):
    """The route previews the form's current text, not the saved row."""
    with app.app_context():
        base = EmailTemplate(
            template_key="dispatched", name="D", subject="Saved subject",
            body_text="Saved body", is_active=True, version=1)
        db.session.add(base)
        db.session.commit()

        unsaved = EmailTemplate(
            template_key="dispatched", name="D", subject="Unsaved subject",
            body_text="Unsaved body", is_active=True)

        rendered = preview_template(unsaved)

        assert rendered.subject == "Unsaved subject"


def test_preview_for_an_event_shows_the_override_wording(app, seed_workflow_data):
    """An overridden field wins: that is what the event receives."""
    with app.app_context():
        base = EmailTemplate(
            template_key="dispatched", name="D", subject="Base subject",
            body_text="Base body", is_active=True, version=1)
        db.session.add(base)
        db.session.flush()
        cycle = db.session.query(EventCycle).first()
        db.session.add(EmailTemplateEventOverride(
            email_template_id=base.id, event_cycle_id=cycle.id,
            subject="Event subject"))
        db.session.commit()

        rendered = preview_template(base, event_cycle_id=cycle.id)

        assert rendered.subject == "Event subject"
        # body_text is not overridden, so it still inherits.
        assert rendered.body_text == "Base body"


def test_preview_for_an_event_keeps_unsaved_text_for_inherited_fields(
    app, seed_workflow_data
):
    """Both halves at once.

    The event overrides the subject only, so the unsaved body must still show
    through. Rendering the stored row instead would silently discard the edit
    the admin is looking at.
    """
    with app.app_context():
        base = EmailTemplate(
            template_key="dispatched", name="D", subject="Base subject",
            body_text="Saved body", is_active=True, version=1)
        db.session.add(base)
        db.session.flush()
        cycle = db.session.query(EventCycle).first()
        db.session.add(EmailTemplateEventOverride(
            email_template_id=base.id, event_cycle_id=cycle.id,
            subject="Event subject"))
        db.session.commit()

        unsaved = EmailTemplate(
            template_key="dispatched", name="D", subject="Unsaved subject",
            body_text="Unsaved body", is_active=True)

        rendered = preview_template(unsaved, event_cycle_id=cycle.id)

        assert rendered.subject == "Event subject"
        assert rendered.body_text == "Unsaved body"
