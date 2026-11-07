"""Browser-driven tests for the PR page's LGTM button.

The button is a control over the server's decision, not the decision: who may
apply the label is enforced in app.py and exercised in //:test_auth. What can
only be seen here is whether the page asks, draws the button only when told
to, needs two clicks, and sends the header and head commit the server checks.
"""


from fixture_server import HEAD_REF

# Set to 'done' once the page has handled the GET .../lgtm answer, by the same
# microtask ordering test_spa_signed_in_chip.py explains: our continuation is
# registered first, and the extra hop puts the flag after the page's own.
DECISION_MARKER = """
window.__vrLgtm = 'pending';
(function() {
  var native = window.fetch;
  window.fetch = function(input, init) {
    var p = native.call(this, input, init);
    var method = (init && init.method) || 'GET';
    if (String(input).indexOf('/lgtm') !== -1 && method === 'GET') {
      var settle = function() { window.__vrLgtm = 'done'; };
      p.then(function(r) { return r.clone().json(); })
       .then(function() { return Promise.resolve(); })
       .then(settle, settle);
    }
    return p;
  };
})();
"""


def _open(spa_backend, spa_page, lgtm):
    backend = spa_backend(lgtm=lgtm)
    page = spa_page(DECISION_MARKER)
    page.goto(backend.pr_url(), wait_until="load")
    page.wait_for_function("window.__vrLgtm === 'done'")
    return backend, page


def test_no_button_for_a_viewer_the_server_does_not_offer_it_to(spa_backend, spa_page):
    _, page = _open(spa_backend, spa_page, None)
    assert page.locator("#lgtm-btn").is_hidden()


def test_two_clicks_apply_the_label(spa_backend, spa_page):
    backend, page = _open(spa_backend, spa_page,
                          {"can_apply": True, "applied": False, "head_sha": HEAD_REF})
    btn = page.locator("#lgtm-btn")
    assert btn.is_visible()
    assert btn.text_content() == "LGTM"

    btn.click()
    assert btn.text_content() == "Confirm LGTM"
    assert backend.state.lgtm_posts == [], "one click must not apply it"

    btn.click()
    page.wait_for_function(
        "document.getElementById('lgtm-btn').textContent === 'LGTM ✓'")
    assert btn.is_disabled()
    assert backend.state.lgtm_posts == [
        {"intent": "lgtm", "body": {"head_sha": HEAD_REF}}]


def test_an_applied_label_shows_as_applied(spa_backend, spa_page):
    _, page = _open(spa_backend, spa_page, {"can_apply": True, "applied": True})
    btn = page.locator("#lgtm-btn")
    assert btn.text_content() == "LGTM ✓"
    assert btn.is_disabled()


def test_a_refusal_is_shown_with_the_reason(spa_backend, spa_page):
    reason = "The PR has new commits since this page loaded."
    backend, page = _open(spa_backend, spa_page,
                          {"can_apply": True, "applied": False, "post_error": reason})
    btn = page.locator("#lgtm-btn")
    btn.click()
    btn.click()
    page.wait_for_function(
        "document.getElementById('lgtm-btn').textContent === 'LGTM failed'")
    assert btn.get_attribute("title") == reason
    assert not btn.is_disabled(), "a failed attempt can be retried after a reload"


def test_the_arming_lapses(spa_backend, spa_page):
    backend, page = _open(spa_backend, spa_page, {"can_apply": True, "applied": False})
    btn = page.locator("#lgtm-btn")
    btn.click()
    page.wait_for_function(
        "document.getElementById('lgtm-btn').textContent === 'LGTM'", timeout=8000)
    btn.click()
    assert btn.text_content() == "Confirm LGTM"
    assert backend.state.lgtm_posts == []
