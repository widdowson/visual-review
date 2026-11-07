// Service worker for the Visual Review extension.
//
// Two jobs: the #vr_reload hash reload, and the LGTM button's requests to
// Visual Review. The content script cannot make those itself: it runs in
// github.com's origin, so a request to vr.apw.photos from it is cross-site and
// carries neither the Cloudflare Access cookie nor, through CORS, the custom
// header the server requires. This worker runs in the extension's origin with
// a host permission for VR, which gets it both. The server decides who may
// apply the label (a person on its LGTM list, signed in through Access); this
// only carries the question there.

// vr:lgtm:begin
var LGTM_VR_BASE_URL = 'https://vr.apw.photos';

// GitHub's own rules for the two names, tighter than "no slash": these go into
// a URL path on VR, and a "..", a "?" or an encoded slash must not reach it.
var LGTM_NAME_RE = /^[A-Za-z0-9_.-]{1,100}$/;

function lgtmTarget(msg) {
  if (!msg || typeof msg !== 'object') return null;
  var owner = msg.owner, repo = msg.repo, number = msg.prNumber;
  if (typeof owner !== 'string' || !LGTM_NAME_RE.test(owner) || /^\.+$/.test(owner)) return null;
  if (typeof repo !== 'string' || !LGTM_NAME_RE.test(repo) || /^\.+$/.test(repo)) return null;
  if (typeof number !== 'number' || !Number.isInteger(number) || number < 1) return null;
  return LGTM_VR_BASE_URL + '/api/' + owner + '/' + repo + '/pr/' + number + '/lgtm';
}

function lgtmPageUrl(msg) {
  return LGTM_VR_BASE_URL + '/' + msg.owner + '/' + msg.repo + '/pr/' + msg.prNumber;
}

// Cloudflare Access answers a request with no valid session by redirecting to
// its login page. Followed, that is an HTML page from another host; not
// followed, it is an opaque redirect, which is the one unambiguous sign that
// the person needs to sign in to VR, rather than that they may not use LGTM.
function lgtmResult(resp) {
  if (resp.type === 'opaqueredirect' || (resp.status >= 300 && resp.status < 400)) {
    return Promise.resolve({ signIn: true });
  }
  return resp.json().then(
    function(body) { return { status: resp.status, ok: resp.ok, body: body || {} }; },
    function() { return { status: resp.status, ok: false, body: { error: 'HTTP ' + resp.status } }; }
  );
}

function lgtmHandle(msg) {
  var url = lgtmTarget(msg);
  if (!url) return Promise.resolve({ error: 'bad pull request' });
  var page = lgtmPageUrl(msg);
  var opts = { credentials: 'include', redirect: 'manual', cache: 'no-store' };

  if (msg.type === 'lgtm-status') {
    opts.method = 'GET';
    opts.headers = { 'Accept': 'application/json' };
  } else if (msg.type === 'lgtm-apply') {
    opts.method = 'POST';
    opts.headers = { 'Content-Type': 'application/json', 'X-VR-Intent': 'lgtm' };
    opts.body = '{}';
  } else {
    return Promise.resolve({ error: 'unknown request' });
  }

  return fetch(url, opts)
    .then(lgtmResult)
    .then(function(result) { result.page = page; return result; })
    .catch(function(e) { return { error: String((e && e.message) || e), page: page }; });
}

// Only this extension's own content script, on a github.com page, may ask.
// A web page cannot message this worker at all without externally_connectable,
// which the manifest does not declare; this is the belt to that.
function lgtmTrustedSender(sender, extensionId) {
  if (!sender || sender.id !== extensionId) return false;
  var url = String(sender.url || (sender.tab && sender.tab.url) || '');
  return url.indexOf('https://github.com/') === 0;
}
// vr:lgtm:end

chrome.runtime.onMessage.addListener(function(msg, sender, sendResponse) {
  if (msg && msg.type === 'reload') {
    chrome.runtime.reload();
    return false;
  }
  if (msg && (msg.type === 'lgtm-status' || msg.type === 'lgtm-apply')) {
    if (!lgtmTrustedSender(sender, chrome.runtime.id)) {
      sendResponse({ error: 'untrusted sender' });
      return false;
    }
    lgtmHandle(msg).then(sendResponse);
    return true;  // keeps sendResponse alive until the fetch settles
  }
  return false;
});
