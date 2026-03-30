"""
Unit tests for Jinja2 template filters (markdown_links, strip_links, format_qty).
"""


class TestMarkdownLinks:
    """Tests for the markdown_links filter."""

    def test_converts_markdown_link(self, app):
        """Standard markdown link should become an <a> tag."""
        with app.app_context():
            md_links = app.jinja_env.filters['markdown_links']
            result = md_links('[Hotel Policy](https://docs.google.com/policy)')
            assert '<a href="https://docs.google.com/policy" target="_blank" rel="noopener">Hotel Policy</a>' in result

    def test_converts_bare_url(self, app):
        """Bare URL should become a clickable link."""
        with app.app_context():
            md_links = app.jinja_env.filters['markdown_links']
            result = md_links('Visit https://example.com for details')
            assert '<a href="https://example.com"' in result
            assert '>https://example.com</a>' in result

    def test_empty_input(self, app):
        """Empty/None input should return empty string."""
        with app.app_context():
            md_links = app.jinja_env.filters['markdown_links']
            assert md_links('') == ''
            assert md_links(None) == ''

    def test_plain_text_unchanged(self, app):
        """Plain text without links should pass through."""
        with app.app_context():
            md_links = app.jinja_env.filters['markdown_links']
            result = md_links('Just some plain text')
            assert result == 'Just some plain text'

    def test_xss_in_link_text_escaped(self, app):
        """Script tags in link text must be escaped, not rendered."""
        with app.app_context():
            md_links = app.jinja_env.filters['markdown_links']
            result = md_links('[<script>alert(1)</script>](https://example.com)')
            assert '<script>' not in result
            assert '&lt;script&gt;' in result

    def test_xss_img_onerror_escaped(self, app):
        """Image onerror XSS in link text must be escaped."""
        with app.app_context():
            md_links = app.jinja_env.filters['markdown_links']
            result = md_links('[<img src=x onerror=alert(1)>](https://example.com)')
            assert '<img' not in result
            assert '&lt;img' in result

    def test_xss_in_bare_text_escaped(self, app):
        """Raw HTML outside links must be escaped."""
        with app.app_context():
            md_links = app.jinja_env.filters['markdown_links']
            result = md_links('Hello <b>bold</b> world')
            assert '<b>' not in result
            assert '&lt;b&gt;' in result

    def test_multiple_links(self, app):
        """Multiple markdown links in one string should all convert."""
        with app.app_context():
            md_links = app.jinja_env.filters['markdown_links']
            result = md_links('[One](https://one.com) and [Two](https://two.com)')
            assert 'href="https://one.com"' in result
            assert 'href="https://two.com"' in result
            assert '>One</a>' in result
            assert '>Two</a>' in result

    def test_ampersand_in_text_escaped(self, app):
        """Ampersands in text should be escaped."""
        with app.app_context():
            md_links = app.jinja_env.filters['markdown_links']
            result = md_links('[Tom & Jerry](https://example.com)')
            assert 'Tom &amp; Jerry' in result
            assert 'href="https://example.com"' in result
