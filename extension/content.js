// Visual Review — Chrome extension content script for GitHub
//
// Adds "Visual Review" links to open PRs that contain image files. Which
// extensions count is the server's answer, fetched from /api/extensions and
// cached; see the vr:extensions region below. Works on PR list and PR detail
// pages.
//
// Goggles icon: Font Awesome Free (CC BY 4.0) — fa-vr-cardboard

(function() {
  'use strict';

  if (window.__vrInjected) return;
  window.__vrInjected = true;

  /* ─── Extension reload via URL hash ─── */

  function checkReloadHash() {
    if (location.hash === '#vr_reload') {
      history.replaceState(null, '', location.pathname + location.search);
      try { chrome.runtime.sendMessage({ type: 'reload' }); } catch (e) {}
    }
  }
  checkReloadHash();
  window.addEventListener('hashchange', checkReloadHash);

  /* ─── Configuration ─── */

  const VR_BASE_URL = 'https://vr.apw.photos';
  const CACHE_KEY_PREFIX = 'vr_pr_has_images_';
  const CACHE_DURATION_MS = 7 * 24 * 60 * 60 * 1000;
  // IMAGE_EXTENSIONS is defined in image_config.js (loaded before this script)
  const MAX_FILES = 100;
  const OWNER_FILTER = 'widdowson';

  /* ─── Supported image extensions ─── */
  //
  // The server owns the list. IMAGE_EXTENSIONS is the copy baked into this
  // bundle at build time from image_extensions.json, and it is what the
  // extension matches against until the server's answer arrives — and
  // whatever happens, if that answer never does. So a format added on the
  // server reaches an already-installed extension without a rebuild, and a
  // server that is down, blocked or answering nonsense leaves the extension
  // behaving exactly as it did before this was added.
  //
  // The region below is marked because extension/test_extensions_fetch.js
  // extracts and runs this source rather than keeping its own copy of it.
  //
  // vr:extensions:begin
  var EXT_CACHE_KEY = 'vr_image_extensions';
  var EXT_CACHE_DURATION_MS = 24 * 60 * 60 * 1000;
  var EXT_FETCH_TIMEOUT_MS = 5000;

  var _extensions = IMAGE_EXTENSIONS.slice();
  var _extensionsPromise = null;

  // Returns the list to adopt, or null to keep the one we have. An empty array
  // is rejected rather than adopted: it is what a half-configured server would
  // answer, and adopting it would stop the extension matching anything at all
  // — which on a PR page is indistinguishable from "this PR has no images",
  // so nobody would notice the links had quietly stopped appearing.
  function normalizeExtensions(value) {
    if (!Array.isArray(value) || value.length === 0) return null;
    var out = [];
    for (var i = 0; i < value.length; i++) {
      var ext = value[i];
      if (typeof ext !== 'string') return null;
      if (ext.length < 2 || ext.charAt(0) !== '.') return null;
      out.push(ext.toLowerCase());
    }
    return out;
  }

  function readCachedExtensions() {
    try {
      var raw = localStorage.getItem(EXT_CACHE_KEY);
      if (!raw) return null;
      var entry = JSON.parse(raw);
      if (!entry || typeof entry.ts !== 'number') return null;
      if (Date.now() - entry.ts > EXT_CACHE_DURATION_MS) return null;
      return normalizeExtensions(entry.extensions);
    } catch (e) {
      return null;
    }
  }

  function writeCachedExtensions(list) {
    try {
      localStorage.setItem(EXT_CACHE_KEY,
        JSON.stringify({ extensions: list, ts: Date.now() }));
    } catch (e) {}
  }

  // Memoized: one fetch per page however many PR rows ask, since a list page
  // calls prHasImageFiles once per row. A failure memoizes the bundled list
  // too, so an unreachable server costs one request rather than one per row.
  //
  // The request has to be bounded, because prHasImageFiles awaits it before it
  // looks at anything and a PR list page awaits that once per row. A VR server
  // that refuses or errors falls back below; one that accepts the connection
  // and never answers — a blackholing proxy, a captive portal, a wedged host
  // — would otherwise block the first row forever and inject nothing at all,
  // on the list page, the detail page and the hovercard alike, for the life of
  // that page. fetchTimeoutSignal below is what bounds it, and the abort lands
  // in the same catch as any other failure.
  //
  // credentials:'omit' is not about cookies: fetch defaults to 'same-origin'
  // and this is cross-origin, so none would be sent either way. It is there to
  // keep the response readable. The server answers
  // Access-Control-Allow-Origin: *, and a wildcard cannot satisfy a
  // credentialed request — so a later 'include' here would make every
  // response unreadable and pin the extension to the bundled list forever,
  // silently. That is what the assertion on this option is protecting.
  // AbortSignal.timeout is Chrome 103+. The manifest sets no
  // minimum_chrome_version and MV3 loads from Chrome 88, and this is evaluated
  // while building fetch's options — before _extensionsPromise is assigned and
  // outside the catch below. So calling it unguarded means that on a browser
  // without it, a TypeError escapes ensureExtensions, then prHasImageFiles
  // (which awaits it ahead of its own try), then the row loop and run(), and
  // the extension injects nothing at all on any surface. That is precisely the
  // failure the timeout was added to prevent, so it must not be the way the
  // timeout is added.
  //
  // undefined is a valid `signal`, so a browser without it gets the unbounded
  // request it would have had before any of this — which is the fallback
  // behaviour this whole region promises.
  function fetchTimeoutSignal() {
    return (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function')
      ? AbortSignal.timeout(EXT_FETCH_TIMEOUT_MS)
      : undefined;
  }

  function ensureExtensions() {
    if (_extensionsPromise) return _extensionsPromise;

    var cached = readCachedExtensions();
    if (cached) {
      _extensions = cached;
      _extensionsPromise = Promise.resolve(cached);
      return _extensionsPromise;
    }

    _extensionsPromise = fetch(VR_BASE_URL + '/api/extensions', {
      credentials: 'omit',
      signal: fetchTimeoutSignal(),
    })
      .then(function(resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      })
      .then(function(data) {
        var list = normalizeExtensions(data && data.extensions);
        if (!list) throw new Error('malformed /api/extensions response');
        _extensions = list;
        writeCachedExtensions(list);
        return list;
      })
      .catch(function(e) {
        console.warn('[VR] Using the bundled image extensions:', e);
        return _extensions;
      });

    return _extensionsPromise;
  }

  function hasImageExtension(path) {
    var lower = path.toLowerCase();
    for (var i = 0; i < _extensions.length; i++) {
      if (lower.endsWith(_extensions[i])) return true;
    }
    return false;
  }
  // vr:extensions:end

  /* ─── Cache helpers ─── */

  function getCachedResult(owner, repo, prNumber) {
    const key = CACHE_KEY_PREFIX + owner + '_' + repo + '_' + prNumber;
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return null;
      const entry = JSON.parse(raw);
      if (Date.now() - entry.ts > CACHE_DURATION_MS) {
        localStorage.removeItem(key);
        return null;
      }
      return entry.hasImages ? true : null;
    } catch (e) { return null; }
  }

  function setCachedResult(owner, repo, prNumber, hasImages) {
    if (!hasImages) return;
    const key = CACHE_KEY_PREFIX + owner + '_' + repo + '_' + prNumber;
    try {
      localStorage.setItem(key, JSON.stringify({ hasImages: true, ts: Date.now() }));
    } catch (e) {}
  }

  /* ─── File check ─── */

  async function prHasImageFiles(owner, repo, prNumber) {
    const cached = getCachedResult(owner, repo, prNumber);
    if (cached === true) return true;

    // Every caller reaches hasImageExtension through here, so this one await
    // covers the list page, the detail page and the hovercard.
    await ensureExtensions();

    try {
      const resp = await fetch('/' + owner + '/' + repo + '/pull/' + prNumber + '/files', {
        headers: {
          'Accept': 'application/json',
          'X-Requested-With': 'XMLHttpRequest'
        }
      });
      if (!resp.ok) return false;
      const data = await resp.json();
      const summaries = data.payload.pullRequestsChangesRoute.diffSummaries;
      const hasImages = summaries.slice(0, MAX_FILES).some(function(s) {
        return hasImageExtension(s.path);
      });
      setCachedResult(owner, repo, prNumber, hasImages);
      return hasImages;
    } catch (e) {
      console.warn('[VR] Error checking files for PR #' + prNumber + ':', e);
      return false;
    }
  }

  /* ─── Goggles icon ─── */
  // Font Awesome Free fa-vr-cardboard (CC BY 4.0, Fonticons Inc.)
  // viewBox 0 0 640 512 — wider aspect ratio, rendered to fit

  var GOGGLES_PATH = 'M608 64H32C14.33 64 0 78.33 0 96v320c0 17.67 14.33 32 32 32h160.22c25.19 0 48.03-14.77 58.36-37.74l27.74-61.64C286.21 331.08 302.35 320 320 320s33.79 11.08 41.68 28.62l27.74 61.64C399.75 433.23 422.6 448 447.78 448H608c17.67 0 32-14.33 32-32V96c0-17.67-14.33-32-32-32zM160 304c-35.35 0-64-28.65-64-64s28.65-64 64-64 64 28.65 64 64-28.65 64-64 64zm320 0c-35.35 0-64-28.65-64-64s28.65-64 64-64 64 28.65 64 64-28.65 64-64 64z';

  function gogglesSvg(width) {
    // 640:512 aspect ratio → height = width * 0.8
    var h = Math.round(width * 0.8);
    return '<svg width="' + width + '" height="' + h + '" viewBox="0 0 640 512" fill="currentColor" style="flex-shrink:0"><path d="' + GOGGLES_PATH + '"/></svg>';
  }

  /* ─── VR link factory ─── */

  function createVRLink(owner, repo, prNumber, style) {
    const url = VR_BASE_URL + '/' + owner + '/' + repo + '/pr/' + prNumber;
    const a = document.createElement('a');
    a.href = url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.title = 'Visual Review for PR #' + prNumber;
    a.setAttribute('data-vr-injected', 'true');

    if (style === 'button') {
      // Detail page: rectangular button matching GitHub's native style
      a.style.cssText = [
        'display:inline-flex', 'align-items:center', 'gap:6px',
        'padding:5px 12px', 'border-radius:6px',
        'background:linear-gradient(135deg,#6f42c1,#2563eb)', 'border:none',
        'color:#fff', 'font-size:14px', 'font-weight:500',
        'text-decoration:none', 'white-space:nowrap', 'line-height:20px',
        'cursor:pointer', 'transition:opacity 0.15s',
        'box-shadow:0 1px 3px rgba(99,66,193,0.3)'
      ].join(';');
      a.innerHTML = gogglesSvg(18) + 'Visual Review';
      a.onmouseenter = function() { a.style.opacity = '0.85'; };
      a.onmouseleave = function() { a.style.opacity = '1'; };
      return a;
    }

    // PR list: purple gradient circle with goggles icon
    a.style.cssText = [
      'display:inline-flex', 'align-items:center', 'justify-content:center',
      'width:24px', 'height:24px', 'border-radius:50%',
      'background:linear-gradient(135deg,#6f42c1,#2563eb)', 'border:none',
      'color:#fff', 'text-decoration:none',
      'vertical-align:middle', 'margin-left:6px', 'cursor:pointer',
      'transition:opacity 0.15s', 'box-shadow:0 1px 3px rgba(99,66,193,0.3)'
    ].join(';');
    a.innerHTML = gogglesSvg(15);
    a.onmouseenter = function() { a.style.opacity = '0.8'; };
    a.onmouseleave = function() { a.style.opacity = '1'; };
    return a;
  }

  /* ─── Page context detection ─── */

  function getPageContext() {
    const path = window.location.pathname;
    const listMatch = path.match(/^\/([^\/]+)\/([^\/]+)\/pulls\/?$/);
    if (listMatch) {
      return { type: 'list', owner: listMatch[1], repo: listMatch[2] };
    }
    const detailMatch = path.match(/^\/([^\/]+)\/([^\/]+)\/pull\/(\d+)\/?/);
    if (detailMatch) {
      return {
        type: 'detail',
        owner: detailMatch[1],
        repo: detailMatch[2],
        prNumber: parseInt(detailMatch[3])
      };
    }
    return null;
  }

  /* ─── PR list page handler ─── */

  async function handlePRListPage(ctx) {
    if (ctx.owner !== OWNER_FILTER) return;

    const rows = document.querySelectorAll('.js-issue-row');
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      if (row.querySelector('[data-vr-injected]')) continue;
      if (!row.querySelector('[aria-label="Open Pull Request"]')) continue;

      const prNumber = parseInt(row.id.replace('issue_', ''));
      if (!prNumber) continue;

      const titleLink = row.querySelector('a[data-hovercard-type="pull_request"]');
      if (!titleLink) continue;

      const hasImages = await prHasImageFiles(ctx.owner, ctx.repo, prNumber);
      if (!hasImages) continue;

      titleLink.after(createVRLink(ctx.owner, ctx.repo, prNumber, 'chip'));
    }
  }

  /* ─── PR detail page handler ─── */

  async function handlePRDetailPage(ctx) {
    if (ctx.owner !== OWNER_FILTER) return;
    if (document.querySelector('[data-vr-injected]')) return;

    const descArea = document.querySelector('[class*="PageHeader-Description"]');
    if (!descArea) return;

    const spans = descArea.querySelectorAll('span');
    let isOpen = false;
    for (let i = 0; i < spans.length; i++) {
      const t = spans[i].textContent.trim();
      if (t === 'Open' || t === 'Draft') { isOpen = true; break; }
    }
    if (!isOpen) return;

    const hasImages = await prHasImageFiles(ctx.owner, ctx.repo, ctx.prNumber);
    if (!hasImages) return;

    const actionsArea = document.querySelector('[class*="PageHeader-Actions"]');
    if (!actionsArea) return;

    actionsArea.insertBefore(
      createVRLink(ctx.owner, ctx.repo, ctx.prNumber, 'button'),
      actionsArea.firstChild
    );
  }

  /* ─── PR hovercard handler (site-wide) ─── */
  //
  // GitHub shows hovercards when you hover over PR links anywhere on the site.
  // The trigger link has data-hovercard-type="pull_request" and
  // data-hovercard-url="/{owner}/{repo}/pull/{n}/hovercard".
  //
  // The hovercard content loads async into a shared .Popover container.
  // We track the last-hovered PR link, then when hovercard content appears,
  // inject a VR goggles chip next to the state badge ("Open"/"Draft").

  var _lastHoveredPR = null;

  document.addEventListener('mouseover', function(e) {
    var link = e.target.closest('[data-hovercard-type="pull_request"]');
    if (!link) return;
    var url = link.getAttribute('data-hovercard-url') || '';
    var match = url.match(/\/([^\/]+)\/([^\/]+)\/pull\/(\d+)\/hovercard/);
    if (match && match[1] === OWNER_FILTER) {
      _lastHoveredPR = { owner: match[1], repo: match[2], prNumber: parseInt(match[3]) };
    } else {
      _lastHoveredPR = null;
    }
  }, true);

  async function handleHovercard(popover) {
    if (!_lastHoveredPR) return;
    if (popover.querySelector('[data-vr-injected]')) return;

    // The state badge is <span class="State State--open State--small">
    // inside a flex container <div class="mt-2 d-flex flex-wrap gap-2">
    var stateEl = null;
    var candidates = popover.querySelectorAll('.State');
    for (var i = 0; i < candidates.length; i++) {
      var t = candidates[i].textContent.trim();
      if (t === 'Open' || t === 'Draft') { stateEl = candidates[i]; break; }
    }
    if (!stateEl) return;

    var pr = _lastHoveredPR;
    var hasImages = await prHasImageFiles(pr.owner, pr.repo, pr.prNumber);
    if (!hasImages) return;

    // Small chip sized to match the State--small badge
    var chip = createVRLink(pr.owner, pr.repo, pr.prNumber, 'chip');
    chip.style.width = '22px';
    chip.style.height = '22px';
    stateEl.after(chip);
  }

  /* ─── Main runner ─── */

  let _running = false;
  let _lastPath = '';

  async function run() {
    if (_running) return;
    _running = true;
    try {
      const ctx = getPageContext();
      if (!ctx) return;
      if (ctx.type === 'list') await handlePRListPage(ctx);
      else if (ctx.type === 'detail') await handlePRDetailPage(ctx);
      _lastPath = window.location.pathname;
    } finally {
      _running = false;
    }
  }

  run();

  document.addEventListener('turbo:load', function() { setTimeout(run, 300); });
  document.addEventListener('turbo:render', function() { setTimeout(run, 300); });

  // Single MutationObserver handles both page navigation and hovercard injection
  const observer = new MutationObserver(function(mutations) {
    // Check for hovercards — GitHub adds children INTO an existing
    // .Popover-message rather than adding the .Popover-message itself
    for (var m = 0; m < mutations.length; m++) {
      var added = mutations[m].addedNodes;
      for (var n = 0; n < added.length; n++) {
        var node = added[n];
        if (node.nodeType !== 1) continue;
        var popover = null;
        if (node.classList && node.classList.contains('Popover-message')) {
          popover = node;
        } else if (node.closest) {
          popover = node.closest('.Popover-message');
        }
        if (!popover && node.querySelector) {
          popover = node.querySelector('.Popover-message');
        }
        if (popover && popover.offsetHeight > 0 && !popover.querySelector('[data-vr-injected]')) {
          handleHovercard(popover);
        }
      }
    }

    // Debounced page-level handler for list/detail pages
    clearTimeout(window.__vrDebounce);
    window.__vrDebounce = setTimeout(function() {
      const ctx = getPageContext();
      if (!ctx) return;
      if (window.location.pathname !== _lastPath || !document.querySelector('[data-vr-injected]')) {
        run();
      }
    }, 500);
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  console.log('[VR] Visual Review extension loaded');
})();
