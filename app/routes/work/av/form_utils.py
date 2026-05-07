"""
WTForms form class and helpers for AV request create (and future edit) routes.
"""
from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import (
    DecimalField,
    HiddenField,
    IntegerField,
    RadioField,
    SelectField,
    StringField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional


# ---------------------------------------------------------------------------
# Action constants
# ---------------------------------------------------------------------------

ACTION_SAVE_DRAFT = "save_draft"
ACTION_SUBMIT = "submit"


# ---------------------------------------------------------------------------
# Form class
# ---------------------------------------------------------------------------

class AVRequestForm(FlaskForm):
    """Single-page 6-section form for creating (or editing) an AV request."""

    # Section 1: SPACE
    space_id = SelectField(
        "Space", coerce=int, validators=[DataRequired()],
    )

    # Section 2: WHAT YOU NEED
    description = TextAreaField(
        "What you need", validators=[DataRequired(), Length(max=10000)],
    )
    duration_model = RadioField(
        "How much programming/content?",
        choices=[
            ("HOURS_OF_CONTENT", "Hours of content"),
            ("FULL_EVENT", "Full event"),
            ("MULTIPLE_SLOTS", "Multiple slots"),
        ],
        validators=[DataRequired()],
        default="HOURS_OF_CONTENT",
    )
    duration_hours = DecimalField(
        "Hours of content",
        validators=[Optional(strip_whitespace=True), NumberRange(min=0, max=999)],
        places=2,
    )
    duration_slots = IntegerField(
        "Number of slots",
        validators=[Optional(strip_whitespace=True), NumberRange(min=0, max=999)],
    )
    duration_notes = TextAreaField(
        "Duration notes", validators=[Optional(), Length(max=5000)],
    )

    # Section 3: PRIORITY
    priority = RadioField(
        "Priority",
        choices=[
            ("MUST_HAVE", "Must have"),
            ("STRONG_PREFERENCE", "Strong preference"),
            ("NICE_TO_HAVE", "Nice to have"),
        ],
        validators=[DataRequired()],
    )

    # Section 4: GEAR DETAIL
    gear_specificity = RadioField(
        "Gear specificity",
        choices=[
            ("USAGE_ONLY", "Just describe usage"),
            ("SUGGESTIONS", "I have suggestions"),
            ("REQUIRED", "I require specific gear"),
        ],
        validators=[DataRequired()],
        default="USAGE_ONLY",
    )
    suggested_gear_text = TextAreaField(
        "Suggested gear", validators=[Optional(), Length(max=10000)],
    )

    # Section 5: DEPT-SOURCED GEAR
    dept_sourced_gear_mode = RadioField(
        "Department-sourced gear",
        choices=[
            ("NONE", "Nothing — AV team will provide everything"),
            ("SOME", "We're bringing or renting some gear ourselves"),
        ],
        validators=[DataRequired()],
        default="NONE",
    )
    dept_sourced_gear_text = TextAreaField(
        "What you're bringing", validators=[Optional(), Length(max=10000)],
    )

    # Section 6: PRIMARY CONTACT
    primary_contact_name = StringField(
        "Primary contact name", validators=[DataRequired(), Length(max=256)],
    )
    primary_contact_email = StringField(
        "Primary contact email", validators=[DataRequired(), Length(max=256)],
    )

    # Action: save_draft (Task 24) or submit (Task 25 — placeholder)
    action = HiddenField()

    # ---------------------------------------------------------------------------
    # Cross-field validation
    # ---------------------------------------------------------------------------

    def validate(self, extra_validators=None):
        """Run WTForms base validation then apply cross-field rules.

        Called by validate_on_submit().  We add cross-field errors after the
        base pass so that Optional() on the numeric fields doesn't suppress
        them via StopValidation.
        """
        ok = super().validate(extra_validators=extra_validators)

        if self.duration_model.data == "HOURS_OF_CONTENT" and not self.duration_hours.data:
            self.duration_hours.errors.append(
                "Hours required when 'Hours of content' is selected."
            )
            ok = False

        if self.duration_model.data == "MULTIPLE_SLOTS" and not self.duration_slots.data:
            self.duration_slots.errors.append(
                "Slot count required when 'Multiple slots' is selected."
            )
            ok = False

        if (
            self.gear_specificity.data in ("SUGGESTIONS", "REQUIRED")
            and not self.suggested_gear_text.data
        ):
            self.suggested_gear_text.errors.append(
                "Suggested gear required when not 'Just describe usage'."
            )
            ok = False

        if self.dept_sourced_gear_mode.data == "SOME" and not self.dept_sourced_gear_text.data:
            self.dept_sourced_gear_text.errors.append("Specify what you're bringing.")
            ok = False

        return ok
