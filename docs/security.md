# Security Documentation

## Content Security Policy (CSP)

This application uses Content Security Policy headers to prevent XSS attacks by controlling what resources can be loaded and executed.

### Current Policy

```
default-src 'self';
script-src 'self' 'nonce-{random}';
style-src 'self' 'unsafe-inline';
img-src 'self' data:;
font-src 'self';
form-action 'self';
frame-ancestors 'none';
base-uri 'self';
object-src 'none'
```

### Key Points

| Directive | Value | What It Means |
|-----------|-------|---------------|
| `script-src` | `'self' 'nonce-...'` | Scripts require a nonce to execute |
| `style-src` | `'self' 'unsafe-inline'` | Inline styles allowed |

### Why Nonces for Scripts?

Without CSP, any injected `<script>` tag executes immediately - this is XSS. With nonces:

- **Good**: `<script nonce="abc123">doSomething()</script>` - executes
- **Blocked**: `<script>evil()</script>` - no nonce, blocked by browser

The nonce is a random value generated per-request, so attackers can't predict it.

### Why Allow Inline Styles?

We allow `'unsafe-inline'` for styles because:

1. **Low risk**: CSS can't execute code or steal data (mostly)
2. **High refactoring cost**: Templates use `style="..."` attributes extensively

---

## Developer Guide: Adding Inline Scripts

### Rule 1: Always Add Nonce to Script Tags

```html
<!-- CORRECT -->
<script nonce="{{ csp_nonce }}">
  // Your JavaScript here
</script>

<!-- WRONG - will be blocked by CSP -->
<script>
  // This won't run!
</script>
```

The `csp_nonce` variable is automatically available in all templates.

### Rule 2: No Inline Event Handlers

Inline handlers like `onclick="..."` won't work because they're blocked by our CSP policy (no `'unsafe-inline'` in script-src). The browser simply ignores them.

```html
<!-- WON'T WORK - browser ignores onclick due to CSP -->
<button onclick="doSomething()">Click</button>

<!-- CORRECT - use data attributes handled by base.html JS -->
<button data-confirm="Are you sure?">Click</button>
```

**How to test**: Open browser DevTools console. If you see "Refused to execute inline event handler" errors, you've used an inline handler.

### Rule 3: Use Data Attributes Instead

The base template includes handlers for common patterns:

#### Confirm Dialogs

```html
<!-- Shows confirm() dialog before form submit -->
<button type="submit" data-confirm="Delete this item?">Delete</button>
```

#### Modal Open/Close

```html
<!-- Open modal -->
<button data-modal-open="my-modal-id">Open Modal</button>

<!-- Close modal -->
<button data-modal-close="my-modal-id">Cancel</button>
```

#### Show/Hide Elements

```html
<!-- Show an element and hide self -->
<button data-show="form-container" data-hide-self>Show Form</button>

<!-- Hide an element and show another by selector -->
<button data-hide="form-container" data-show-selector="#section > button">Cancel</button>
```

#### Auto-Submit on Change

```html
<select name="filter" data-autosubmit>
  <option>Option 1</option>
  <option>Option 2</option>
</select>
```

### Rule 4: For Complex Logic, Use Nonced Script Blocks

If you need custom behavior, add a script block with nonce:

```html
{% block scripts %}
<script nonce="{{ csp_nonce }}">
(function() {
  // Use event delegation
  document.addEventListener('click', function(e) {
    var btn = e.target.closest('[data-my-action]');
    if (btn) {
      // Handle the click
    }
  });
})();
</script>
{% endblock %}
```

---

## Common Patterns

### Form with Confirm + Value Copy

For review forms that need to copy values from shared fields to hidden form fields:

```html
<!-- Shared input fields -->
<textarea id="note_field"></textarea>
<input type="text" id="amount_field" />

<!-- Form with hidden fields -->
<form method="post" action="/submit">
  <input type="hidden" name="note" class="note-field" />
  <input type="hidden" name="amount" class="amount-field" />
  <button type="submit" data-review-action="Approve?" data-require-note>
    Approve
  </button>
</form>

<!-- Script to handle it -->
<script nonce="{{ csp_nonce }}">
document.addEventListener('click', function(e) {
  var btn = e.target.closest('[data-review-action]');
  if (!btn) return;

  var message = btn.getAttribute('data-review-action');
  var requireNote = btn.hasAttribute('data-require-note');
  var note = document.getElementById('note_field').value.trim();

  if (requireNote && !note) {
    alert('Note required');
    e.preventDefault();
    return;
  }

  if (!confirm(message)) {
    e.preventDefault();
    return;
  }

  // Copy to hidden fields
  var form = btn.closest('form');
  form.querySelector('.note-field').value = note;
});
</script>
```

---

## Testing CSP

1. Open browser DevTools (F12)
2. Go to Console tab
3. If CSP blocks something, you'll see errors like:
   ```
   Refused to execute inline script because it violates the following
   Content Security Policy directive: "script-src 'self' 'nonce-...'"
   ```

4. Fix by adding `nonce="{{ csp_nonce }}"` to your script tag

---

## Security Audit Logging

Security-relevant events are logged to the `security_audit_logs` table:

- Login success/failure
- Logout
- Access denied (403)
- Impersonation start/end
- Admin actions

View logs at `/admin/security-logs` (super-admin only).

Logs are retained for 180 days by default. Clean up with:

```bash
flask cleanup-audit-logs --days 180
```

---

## Quick Reference

| Want to... | Use... |
|------------|--------|
| Add inline script | `<script nonce="{{ csp_nonce }}">` |
| Confirm before submit | `data-confirm="message"` |
| Open modal | `data-modal-open="modal-id"` |
| Close modal | `data-modal-close="modal-id"` |
| Auto-submit select | `data-autosubmit` |
| Show element | `data-show="element-id"` |
| Hide element | `data-hide="element-id"` |
| Hide button after click | `data-hide-self` |
