// Visual Review — repo page.
//
// Lists a repository's open pull requests and says which of them have image
// changes, i.e. which ones this tool can actually show you something for.
//
// Unlike the viewer, whose logic is inlined in index.html and extracted by its
// tests, this page's logic is a file of its own: nothing here has to ship in
// the same response as the markup, and a module a test can require beats a
// region a test has to cut out of an HTML file and check for purity.
(function (root, factory) {
    const api = factory();
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;          // node, for the tests
    } else {
        root.VRRepo = api;             // browser
    }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    // ── verdict:begin ──────────────────────────────────────────────────────
    // What one row's image count means, as the page states it.
    //
    // Four outcomes, and keeping them four is the point. `images: null` is a
    // row that has not been probed yet, an `image_error` is a row whose probe
    // failed, and neither may be shown as "no images": a PR wrongly called
    // empty is a PR nobody opens, which is exactly the mistake this page
    // exists to stop people making by hand.
    //
    // `useful` is what the page hangs the visual distinction and the VR link
    // off, so it is true only where the viewer has something to show.
    function verdictFor(row) {
        if (row && row.image_error) {
            return {
                state: 'unknown',
                label: 'check failed',
                detail: String(row.image_error),
                useful: false,
            };
        }
        const n = row ? row.images : null;
        if (n === null || n === undefined) {
            return { state: 'checking', label: 'checking…', detail: '', useful: false };
        }
        if (n === 0) {
            // A walk that ran out of pages without finding an image has not
            // established that there is none — it stopped early. Saying "no
            // images" here would be a claim the evidence does not support.
            if (row.images_truncated) {
                return {
                    state: 'unknown',
                    label: 'too many files to check',
                    detail: 'This PR changes more files than the file list will serve.',
                    useful: false,
                };
            }
            return { state: 'empty', label: 'no images', detail: '', useful: false };
        }
        // A truncated walk found images, so the count is a floor, not a total.
        const plural = n === 1 ? 'image' : 'images';
        return {
            state: 'images',
            label: row.images_truncated ? n + '+ ' + plural : n + ' ' + plural,
            detail: row.images_truncated
                ? 'At least ' + n + ' — the file list was cut short.'
                : '',
            useful: true,
        };
    }

    // How the page describes the list as a whole. Counted from the verdicts
    // rather than from the rows, so one definition decides both.
    function summarize(rows) {
        const counts = { total: rows.length, useful: 0, empty: 0, unknown: 0, checking: 0 };
        for (const row of rows) {
            const state = verdictFor(row).state;
            if (state === 'images') counts.useful++;
            else if (state === 'empty') counts.empty++;
            else if (state === 'unknown') counts.unknown++;
            else counts.checking++;
        }
        return counts;
    }
    // ── verdict:end ────────────────────────────────────────────────────────

    // ── paths:begin ────────────────────────────────────────────────────────
    // The repository this page is for, read from its own URL. Returns null for
    // any path that is not exactly two segments, so the caller reports a bad
    // URL rather than requesting /api//./pulls.
    function repoFromPath(pathname) {
        const parts = String(pathname || '').split('/').filter(Boolean);
        if (parts.length !== 2) return null;
        return { owner: parts[0], repo: parts[1] };
    }

    function viewerHref(owner, repo, number) {
        return '/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo) +
            '/pr/' + encodeURIComponent(number);
    }

    function pullsApiHref(owner, repo, probe) {
        return '/api/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo) +
            '/pulls' + (probe ? '' : '?probe=0');
    }
    // ── paths:end ──────────────────────────────────────────────────────────

    // ── age:begin ──────────────────────────────────────────────────────────
    // "3h ago", from an ISO timestamp. `nowMs` is passed in rather than read
    // from the clock so this stays a function of its arguments.
    function relativeTime(iso, nowMs) {
        const then = Date.parse(iso);
        if (isNaN(then)) return '';
        const secs = Math.round((nowMs - then) / 1000);
        if (secs < 0) return 'just now';
        if (secs < 60) return secs + 's ago';
        const mins = Math.round(secs / 60);
        if (mins < 60) return mins + 'm ago';
        const hours = Math.round(mins / 60);
        if (hours < 24) return hours + 'h ago';
        const days = Math.round(hours / 24);
        if (days < 30) return days + 'd ago';
        return Math.round(days / 30) + 'mo ago';
    }
    // ── age:end ────────────────────────────────────────────────────────────

    // -- Rendering (browser only) ------------------------------------------

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        // textContent throughout: every string below is GitHub's, i.e. a PR
        // title somebody chose, and none of it is trusted as markup.
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    function renderRow(owner, repo, row, nowMs) {
        const verdict = verdictFor(row);
        // A row is a link into the viewer only when the viewer has something
        // to show. Where it does not, the row is a plain container and the
        // only link on it is the one to GitHub.
        const node = el(verdict.useful ? 'a' : 'div', 'pr-row pr-' + verdict.state);
        if (verdict.useful) {
            node.href = viewerHref(owner, repo, row.number);
        }

        const badge = el('span', 'pr-badge badge-' + verdict.state, verdict.label);
        if (verdict.detail) badge.title = verdict.detail;
        node.appendChild(badge);

        const body = el('div', 'pr-body');
        const line = el('div', 'pr-line');
        line.appendChild(el('span', 'pr-number', '#' + row.number));
        line.appendChild(el('span', 'pr-title', row.title));
        if (row.draft) line.appendChild(el('span', 'pr-tag', 'draft'));
        for (const label of row.labels || []) {
            line.appendChild(el('span', 'pr-tag pr-label', label));
        }
        body.appendChild(line);

        const meta = el('div', 'pr-meta');
        const bits = [row.author, row.head_ref + ' → ' + row.base_ref];
        const age = relativeTime(row.updated_at, nowMs);
        if (age) bits.push('updated ' + age);
        meta.appendChild(el('span', null, bits.join(' · ')));
        body.appendChild(meta);
        node.appendChild(body);

        const links = el('div', 'pr-links');
        const gh = el('a', 'pr-gh-link', 'GitHub ↗');
        gh.href = row.html_url;
        gh.target = '_blank';
        gh.rel = 'noopener';
        // The row itself is the link where there is one, so a nested link has
        // to stop the click reaching it.
        gh.addEventListener('click', function (e) { e.stopPropagation(); });
        links.appendChild(gh);
        node.appendChild(links);

        return node;
    }

    function renderList(container, owner, repo, rows, opts) {
        const nowMs = opts.nowMs;
        container.textContent = '';
        let shown = 0;
        for (const row of rows) {
            if (opts.hideEmpty && verdictFor(row).state === 'empty') continue;
            container.appendChild(renderRow(owner, repo, row, nowMs));
            shown++;
        }
        if (shown === 0) {
            container.appendChild(el('div', 'pr-none', rows.length
                ? 'Every open pull request here changes images — nothing to hide.'
                : 'No open pull requests.'));
        }
    }

    function summaryText(counts, probed) {
        if (!counts.total) return 'No open pull requests.';
        if (!probed || counts.checking) {
            return counts.total + ' open pull request' + (counts.total === 1 ? '' : 's') +
                ' · checking which have image changes…';
        }
        const parts = [counts.useful + ' of ' + counts.total + ' have image changes'];
        if (counts.unknown) parts.push(counts.unknown + ' could not be checked');
        return parts.join(' · ');
    }

    return {
        verdictFor: verdictFor,
        summarize: summarize,
        summaryText: summaryText,
        repoFromPath: repoFromPath,
        viewerHref: viewerHref,
        pullsApiHref: pullsApiHref,
        relativeTime: relativeTime,
        renderList: renderList,
    };
});
