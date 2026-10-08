// Browser-level dashboard test — the layer nothing else covers.
//
// Every other test in this repo exercises the API or greps index.html for
// strings. Neither proves the page actually *works*: that the JS parses, that
// the ids referenced by the handlers exist, that clicking "Run behavior audit"
// really drives POST /api/runs and paints the results. A dashboard whose
// markup and script drift apart passes all 500+ Python tests and shows a blank
// page to the user. This closes that gap.
//
// It is deliberately a standalone Node script rather than a pytest module:
// playwright is not a Python dependency of this project, and adding one just
// for a smoke test would change the install footprint for every user. Run it
// via tests/browser/test_dashboard_browser.py, which skips when no browser is
// available.
'use strict';

const path = require('path');
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');

// -- locating playwright ------------------------------------------------------
// Reuse a playwright-core that already exists on the machine instead of
// declaring our own: an npm install into the repo would add a node_modules/
// tree that git would then have to ignore. Checked in order of preference.
const PW_CANDIDATES = [
    path.join(__dirname, '..', '..', 'promo', 'node_modules', 'playwright-core'),
    path.join(__dirname, '..', '..', 'node_modules', 'playwright-core'),
    path.join(
        process.env.USERPROFILE || os.homedir(),
        '.workbuddy', 'binaries', 'node', 'workspace', 'node_modules', 'playwright-core',
    ),
];

function loadChromium() {
    for (const p of PW_CANDIDATES) {
        try {
            return require(p).chromium;
        } catch (e) {
            if (e.code !== 'MODULE_NOT_FOUND') throw e;
        }
    }
    return null;
}

// The bundled chromium download is version-pinned (playwright-core X wants
// chromium-<rev>), and that cache is frequently stale or absent. The system
// Chrome is always present on a dev desktop, so try it first and fall back to
// the bundled build.
async function launch(chromium) {
    const attempts = [
        { label: 'system chrome', opts: { channel: 'chrome' } },
        { label: 'bundled chromium', opts: {} },
    ];
    const errors = [];
    for (const a of attempts) {
        try {
            const browser = await chromium.launch(Object.assign({ headless: true }, a.opts));
            return { browser, label: a.label };
        } catch (e) {
            errors.push(`${a.label}: ${String(e.message).split('\n')[0]}`);
        }
    }
    throw new Error('no usable browser:\n  ' + errors.join('\n  '));
}

// -- server -------------------------------------------------------------------

function freePort() {
    // Bind to port 0, read the assigned port, close. A small race remains but
    // is acceptable: the window is milliseconds and we bind immediately after.
    const net = require('net');
    return new Promise((resolve, reject) => {
        const srv = net.createServer();
        srv.on('error', reject);
        srv.listen(0, '127.0.0.1', () => {
            const { port } = srv.address();
            srv.close(() => resolve(port));
        });
    });
}

function startServer(python, port, dbPath, extraEnv, appModule = 'app.main:app', appDir = '.') {
    const child = spawn(
        python,
        ['-m', 'uvicorn', appModule, '--app-dir', appDir, '--host', '127.0.0.1', '--port', String(port), '--log-level', 'warning'],
        {
            cwd: path.join(__dirname, '..', '..'),
            env: Object.assign({}, process.env, {
                SPECAGENT_SKIP_DOTENV: '1',
                SPECAGENT_DB: dbPath,   // never touch the repo's specagent.db
                OPENAI_API_KEY: '',            // deterministic demo compiler
                TARGET_AGENT_URL: '',          // demo agent
                SPECAGENT_ALLOW_SOURCE: '',
                PYTHONUNBUFFERED: '1',
            }, extraEnv || {}),
            stdio: ['ignore', 'pipe', 'pipe'],
        },
    );
    let stderr = '';
    child.stderr.on('data', (d) => { stderr += d.toString(); });
    child.stdout.on('data', () => {});
    return { child, stderr: () => stderr };
}

async function waitForHealth(port, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        try {
            const r = await fetch(`http://127.0.0.1:${port}/api/health`);
            if (r.ok) return await r.json();
        } catch (e) { /* not up yet */ }
        await new Promise((r) => setTimeout(r, 250));
    }
    throw new Error(`server did not become healthy on port ${port}`);
}

// -- assertions ---------------------------------------------------------------

const results = [];
function check(name, condition, detail) {
    results.push({ name, ok: !!condition, detail: detail || '' });
}

async function main() {
    const chromium = loadChromium();
    if (!chromium) {
        console.error('SKIP: no playwright-core found in:');
        PW_CANDIDATES.forEach((p) => console.error('  ' + p));
        process.exit(2);
    }

    const port = await freePort();
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'specagent-ui-'));
    const dbPath = path.join(tmpDir, 'ui-test.db');
    const python = process.env.SPECAGENT_PYTHON || 'python';
    const server = startServer(python, port, dbPath);

    let browser = null;
    const consoleErrors = [];
    const pageErrors = [];
    try {
        const health = await waitForHealth(port, 30000);
        check('server healthy', health.ok === true, JSON.stringify(health));

        const launched = await launch(chromium);
        browser = launched.browser;
        check('browser launched', true, launched.label);

        const context = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
        const page = await context.newPage();
        page.on('console', (m) => {
            if (m.type() === 'error') consoleErrors.push(m.text());
        });
        page.on('pageerror', (e) => pageErrors.push(String(e)));

        const base = `http://127.0.0.1:${port}`;
        await page.goto(base + '/', { waitUntil: 'domcontentloaded' });

        // -- load: no JS parse errors, static assets resolve ---------------
        // A syntax error in index.html leaves the DOM present but every handler
        // unbound, which is exactly the failure mode string-grep tests miss.
        check('no uncaught page errors on load', pageErrors.length === 0, pageErrors.join(' | '));
        check('no console errors on load', consoleErrors.length === 0, consoleErrors.join(' | '));

        const title = await page.title();
        check('page has a title', !!title && title.length > 0, title);

        // -- /api/health is wired into the version pill (no stale literal) --
        await page.waitForFunction(
            (v) => {
                const el = document.getElementById('versionPill');
                return el && el.textContent === 'v' + v;
            },
            health.version,
            { timeout: 10000 },
        );
        const pill = await page.textContent('#versionPill');
        check('version pill shows live /api/health version', pill === 'v' + health.version, pill);

        // -- project bar (方案 B): an unconfigured server points at the demo flow
        await page.waitForFunction(
            () => {
                const t = document.getElementById('projectBarText');
                return t && t.textContent.indexOf('未检测到项目配置') !== -1;
            },
            null,
            { timeout: 10000 },
        ).catch(() => {});
        const barText = (await page.textContent('#projectBarText')).trim();
        check('project bar shows demo hint when unconfigured',
            barText.indexOf('未检测到项目配置') !== -1, barText);
        check('project run button hidden when unconfigured',
            await page.isHidden('#projectRunBtn'), 'hidden');

        // -- projects load on first paint -----------------------------------
        await page.waitForFunction(
            () => {
                const b = document.getElementById('projectsBody');
                return b && b.textContent.indexOf('loading') === -1;
            },
            null,
            { timeout: 10000 },
        );

        // -- the core interaction: run an audit ----------------------------
        // This is the assertion that matters most. It exercises the whole
        // chain in a real browser: click handler -> api() -> POST /api/runs ->
        // renderRun() -> DOM. If a handler id was renamed, or renderRun throws
        // on a field the API stopped sending, this is where it shows.
        await page.fill('#specText', '退款超过500元需要人工审批。修改地址之前必须获得用户确认。');
        await page.click('#runBtn');
        // Assert the disabled state *during* the run rather than after: the
        // guard against double-submitting an audit is part of the contract, and
        // by the time the status flips the button is already re-enabled.
        const disabledDuring = await page.isDisabled('#runBtn');
        check('run button disabled while running', disabledDuring, `disabled=${disabledDuring}`);

        await page.waitForFunction(
            () => {
                const s = document.getElementById('status');
                return s && /audit complete|error:/.test(s.textContent);
            },
            null,
            { timeout: 60000 },
        );
        const status = (await page.textContent('#status')).trim();
        check('audit completed without error', /^audit complete/.test(status), status);
        check('run button re-enabled after run',
            await page.isDisabled('#runBtn') === false, 'aria state');

        // results panel becomes visible and gets real numbers
        const resultsHidden = await page.getAttribute('#results', 'class');
        check('results panel unhidden', !/\bhidden\b/.test(resultsHidden || ''), resultsHidden);

        const score = (await page.textContent('#score')).trim();
        // One decimal place: score = passed / total * 100 rounded to 1dp, so
        // "84.6%" is the normal shape, not an edge case.
        check('score is a percentage', /^\d+(\.\d+)?%$/.test(score), score);
        const passed = (await page.textContent('#passed')).trim();
        const failed = (await page.textContent('#failed')).trim();
        check('passed/failed are numeric', /^\d+$/.test(passed) && /^\d+$/.test(failed),
            `passed=${passed} failed=${failed}`);
        check('passed+failed > 0', Number(passed) + Number(failed) > 0,
            `passed=${passed} failed=${failed}`);

        const ruleCount = await page.locator('#rules > *').count();
        check('compiled rules rendered', ruleCount > 0, `${ruleCount} nodes`);
        const testCount = await page.locator('#tests > *').count();
        check('test results rendered', testCount > 0, `${testCount} nodes`);

        // the run must show up in the history table after loadProjects()
        await page.waitForFunction(
            () => {
                const b = document.getElementById('runsBody');
                return b && !/No runs yet/.test(b.textContent);
            },
            null,
            { timeout: 15000 },
        );
        const runsText = await page.textContent('#runsBody');
        check('run appears in history table', /dashboard/.test(runsText),
            runsText.slice(0, 120).replace(/\s+/g, ' '));

        // -- metrics panel renders all six tiles ----------------------------
        await page.waitForFunction(
            () => {
                const m = document.getElementById('metricsRow');
                return m && m.children.length >= 6;
            },
            null,
            { timeout: 10000 },
        );
        check('metrics panel shows six tiles',
            await page.locator('#metricsRow > *').count() >= 6,
            String(await page.locator('#metricsRow > *').count()));

        // -- deep link (?project=&run=) still works -------------------------
        const runId = (status.match(/run (\S+)/) || [])[1];
        if (runId) {
            const p2 = await context.newPage();
            const errs2 = [];
            p2.on('pageerror', (e) => errs2.push(String(e)));
            await p2.goto(`${base}/?project=default&run=${encodeURIComponent(runId)}`,
                { waitUntil: 'domcontentloaded' });
            await p2.waitForFunction(
                () => {
                    const t = document.getElementById('tests');
                    return t && t.children.length > 0;
                },
                null,
                { timeout: 20000 },
            ).catch(() => {});
            const deepCount = await p2.locator('#tests > *').count();
            check('deep link renders the referenced run', deepCount > 0, `${deepCount} nodes`);
            check('deep link raises no page errors', errs2.length === 0, errs2.join(' | '));
            await p2.close();
        }

        // -- no late errors from the whole session --------------------------
        check('no uncaught page errors overall', pageErrors.length === 0, pageErrors.join(' | '));
        check('no console errors overall', consoleErrors.length === 0, consoleErrors.join(' | '));

        await context.close();

        // -- token flow on a server that requires one ------------------------
        // Entirely front-end logic: /api/health reports auth_required, the page
        // reveals #tokenBox, a 401 from any api() call re-reveals it, and the
        // token is stashed in sessionStorage and replayed as a Bearer header.
        // None of that is reachable from a string-grep test, and a regression
        // here locks the user out of their own dashboard with no visible cause.
        const authPort = await freePort();
        const authDb = path.join(tmpDir, 'ui-test-auth.db');
        const TOKEN = 'browser-test-token';
        const authServer = startServer(python, authPort, authDb, {
            SPECAGENT_API_TOKEN: TOKEN,
            SPECAGENT_AGENT_API_INSECURE: '0',
        });
        try {
            const h = await waitForHealth(authPort, 30000);
            check('auth server reports auth_required', h.auth_required === true, JSON.stringify(h));

            const authCtx = await browser.newContext();
            const ap = await authCtx.newPage();
            const authErrs = [];
            ap.on('pageerror', (e) => authErrs.push(String(e)));
            await ap.goto(`http://127.0.0.1:${authPort}/`, { waitUntil: 'domcontentloaded' });

            // /api/health reports auth_required -> box revealed without any 401
            await ap.waitForFunction(
                () => {
                    const b = document.getElementById('tokenBox');
                    return b && getComputedStyle(b).display !== 'none';
                },
                null,
                { timeout: 10000 },
            ).catch(() => {});
            const boxShown = await ap.evaluate(() =>
                getComputedStyle(document.getElementById('tokenBox')).display !== 'none');
            check('token box revealed when /api/health says auth_required', boxShown,
                `display=${await ap.evaluate(() => getComputedStyle(document.getElementById('tokenBox')).display)}`);

            // an unauthenticated call must NOT paint results
            await ap.click('#runBtn');
            await ap.waitForFunction(
                () => /error:|audit complete/.test(document.getElementById('status').textContent),
                null,
                { timeout: 30000 },
            ).catch(() => {});
            const authStatus = (await ap.textContent('#status')).trim();
            check('unauthenticated run is refused, not silently painted',
                /^error:/.test(authStatus), authStatus);
            const resultsVisible = await ap.evaluate(() =>
                !document.getElementById('results').classList.contains('hidden'));
            check('no results painted without a token', !resultsVisible,
                `results visible=${resultsVisible}`);
            check('401 surfaces the token prompt hint', await ap.isVisible('#tokenHint'),
                `visible=${await ap.isVisible('#tokenHint')}`);

            // a wrong token keeps it locked: submitToken() stashes whatever was typed and
            // reloads, so the 401 from that reload must re-show the hint rather
            // than leaving the user with a silently broken page
            await ap.fill('#tokenInput', 'wrong-token');
            await ap.click('#tokenSet');
            await ap.waitForFunction(
                () => getComputedStyle(document.getElementById('tokenHint')).display !== 'none',
                null,
                { timeout: 10000 },
            ).catch(() => {});
            check('wrong token does not unlock the dashboard',
                await ap.isVisible('#tokenHint'), 'hint still visible after a bad token');

            // correct token -> a fresh run succeeds. The click is required: setting the
            // token only calls loadRuns(), it does not re-issue the audit, so
            // #status would still hold the stale "error:" from the attempt above.
            await ap.fill('#tokenInput', TOKEN);
            await ap.click('#tokenSet');
            await ap.waitForTimeout(500);
            await ap.click('#runBtn');
            await ap.waitForFunction(
                () => /audit complete/.test(document.getElementById('status').textContent),
                null,
                { timeout: 60000 },
            ).catch(() => {});
            const okStatus = (await ap.textContent('#status')).trim();
            check('correct token unlocks the dashboard', /^audit complete/.test(okStatus), okStatus);
            check('token stored in sessionStorage, not localStorage',
                await ap.evaluate(() => !!sessionStorage.getItem('specagent_token')),
                'sessionStorage checked');
            check('auth flow raises no page errors', authErrs.length === 0, authErrs.join(' | '));

            await ap.waitForFunction(() => document.getElementById('runsBody').textContent.includes('Report'));
            const authDownloadWait = ap.waitForEvent('download');
            const authRequestWait = ap.waitForRequest(r => r.url().endsWith('/report.html'));
            await ap.getByRole('button', {name: 'Report', exact: true}).first().click();
            const authDownload = await authDownloadWait;
            const authRequest = await authRequestWait;
            check('report download sends the dashboard token',
                authDownload.suggestedFilename().endsWith('.html') && authRequest.headers().authorization === 'Bearer ' + TOKEN);
            await authCtx.close();
        } finally {
            authServer.child.kill();
            await new Promise((resolve) => {
                if (authServer.child.exitCode !== null) return resolve();
                const t = setTimeout(resolve, 5000);
                authServer.child.once('exit', () => { clearTimeout(t); resolve(); });
            });
        }

        // -- configured project server (方案 B): bar, project run, gate line ---
        // The full CLI story in the browser: run the *fixed* FinCare config,
        // set its run as baseline through the API, then restart the server
        // with the *defective* config (same database) — the gate line must
        // show FAILED with the documented 4 regressions.
        const projDb = path.join(tmpDir, 'ui-test-project.db');
        const fincareDir = path.join(__dirname, '..', '..', 'examples', 'fincare-agent');
        async function killServer(server) {
            server.child.kill();
            await new Promise((resolve) => {
                if (server.child.exitCode !== null) return resolve();
                const t = setTimeout(resolve, 5000);
                server.child.once('exit', () => { clearTimeout(t); resolve(); });
            });
        }
        const projErrs = [];
        // -- phase 1: fixed agent, first run, baseline ------------------------
        const portA = await freePort();
        const fixedServer = startServer(python, portA, projDb, {
            SPECAGENT_PROJECT_CONFIG: path.join(fincareDir, 'specagent.baseline.yaml'),
        });
        let runIdA = null;
        let runIdB = null;
        try {
            await waitForHealth(portA, 30000);
            const pc = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
            const pp = await pc.newPage();
            pp.on('pageerror', (e) => projErrs.push(String(e)));
            await pp.goto(`http://127.0.0.1:${portA}/`, { waitUntil: 'domcontentloaded' });

            await pp.waitForFunction(
                () => {
                    const t = document.getElementById('projectBarText');
                    return t && t.textContent.indexOf('Testing: fincare-agent') !== -1;
                },
                null,
                { timeout: 20000 },
            ).catch(() => {});
            const cfgText = (await pp.textContent('#projectBarText')).trim();
            check('project bar shows configured target',
                cfgText.indexOf('fincare-agent') !== -1
                && cfgText.indexOf(':run_agent') !== -1
                && cfgText.indexOf('5 rules') !== -1 && cfgText.indexOf('43 cases') !== -1,
                cfgText);

            await pp.click('#projectRunBtn');
            await pp.waitForFunction(
                () => /project suite complete|error:/.test(
                    document.getElementById('projectStatus').textContent),
                null,
                { timeout: 120000 },
            ).catch(() => {});
            const pst = (await pp.textContent('#projectStatus')).trim();
            check('project suite run completes', /^project suite complete/.test(pst), pst);
            check('project run renders results',
                (await pp.locator('#tests > *').count()) > 0, 'tests painted');
            const gate1 = (await pp.textContent('#projectGate')).trim();
            check('gate line renders (not evaluated without baseline)',
                /Gate: not evaluated/.test(gate1), gate1);

            runIdA = (pst.match(/run (\S+)/) || [])[1] || null;
            if (runIdA) {
                const br = await fetch(`http://127.0.0.1:${portA}/api/runs/${encodeURIComponent(runIdA)}/baseline`,
                    { method: 'POST' });
                check('baseline set through the API', br.ok, String(br.status));
            } else {
                check('baseline set through the API', false, 'no run id parsed from status');
            }
            check('project tools render for a configured project',
                await pp.isVisible('#projectTools') && !(await pp.isDisabled('#validateBtn')));
            check('run options and LLM availability are explained',
                await pp.locator('#runOptions').count() === 1 && await pp.isDisabled('#projectExpand'));
            await pp.click('#validateBtn');
            await pp.waitForFunction(() => !document.getElementById('validateResult').hidden);
            check('validate lists the CLI configuration and rules',
                /config OK/.test(await pp.textContent('#validateResult')) && /LARGE_TRANSFER_APPROVAL/.test(await pp.textContent('#validateResult')));
            check('equivalent CLI hints appear',
                (await pp.textContent('#projectTools')).includes('等价命令行: specagent run'));
            await pp.click('#triageBtn');
            await pp.waitForFunction(() => !document.getElementById('triageResult').hidden);
            check('triage view appears', /All 43 cases passed/.test(await pp.textContent('#triageResult')));
            check('history offers report and both export downloads',
                (await pp.textContent('#runsBody')).includes('Export JUnit') && (await pp.textContent('#runsBody')).includes('Export JSON') && (await pp.textContent('#runsBody')).includes('Report'));
            const reportWait = pp.waitForEvent('download');
            await pp.click('#reportBtn');
            const download = await reportWait;
            check('report button downloads an HTML attachment', download.suggestedFilename().endsWith('.html'));
            await pp.locator('summary', {hasText: /^Draft$/}).click();
            await pp.fill('#draftText', '退款超过500元需要人工审批');
            await pp.click('#draftBtn');
            await pp.waitForFunction(() => !document.getElementById('draftYaml').hidden);
            check('draft shows a readonly deterministic YAML preview',
                await pp.getAttribute('#draftYaml', 'readonly') !== null && /deterministic/.test(await pp.textContent('#draftCompiler')));
            await pp.locator('summary', {hasText: /^Verify$/}).click();
            await pp.click('#verifyBtn');
            await pp.waitForFunction(() => !document.getElementById('verifyVerdict').hidden);
            check('verify renders the deterministic quote and verdict',
                (await pp.textContent('#verifyVerdict')) === 'NO_CHANGE' && /Verification vs pre-fix run/.test(await pp.textContent('#verifyResult')));
            await pc.close();
        } finally {
            await killServer(fixedServer);
        }

        // -- phase 2: defective agent vs that baseline ------------------------
        if (runIdA) {
            const portB = await freePort();
            const brokenServer = startServer(python, portB, projDb, {
                SPECAGENT_PROJECT_CONFIG: path.join(fincareDir, 'specagent.yaml'),
            });
            try {
                await waitForHealth(portB, 30000);
                const pc2 = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
                const p2 = await pc2.newPage();
                p2.on('pageerror', (e) => projErrs.push(String(e)));
                await p2.goto(`http://127.0.0.1:${portB}/`, { waitUntil: 'domcontentloaded' });
                await p2.waitForFunction(
                    () => {
                        const t = document.getElementById('projectBarText');
                        return t && t.textContent.indexOf('Testing: fincare-agent') !== -1;
                    },
                    null,
                    { timeout: 20000 },
                ).catch(() => {});
                check('project bar shows the defective target',
                    (await p2.textContent('#projectBarText')).indexOf('python:agent:run_agent') !== -1,
                    (await p2.textContent('#projectBarText')).trim());

                await p2.click('#projectRunBtn');
                await p2.waitForFunction(
                    () => /Gate: (FAILED|PASSED)/.test(document.getElementById('projectGate').textContent),
                    null,
                    { timeout: 120000 },
                ).catch(() => {});
                const gate2 = (await p2.textContent('#projectGate')).trim();
                check('gate line shows FAILED with the new regressions',
                    /Gate: FAILED/.test(gate2) && /4 new regression/.test(gate2), gate2);
                await p2.waitForFunction(
                    () => [...document.getElementById('projectSel').options]
                        .some((o) => o.value === 'fincare-agent'),
                    null,
                    { timeout: 15000 },
                ).catch(() => {});
                check('project dropdown follows the configured project',
                    (await p2.evaluate(() => document.getElementById('projectSel').value)) === 'fincare-agent',
                    await p2.evaluate(() => document.getElementById('projectSel').value));
                await p2.waitForFunction(() => document.querySelectorAll('#diffBody .diffentry').length >= 4);
                const highlighted = await p2.locator('#diffBody .newcall').allTextContents();
                check('new-call markers never highlight the existing approval call',
                    highlighted.length > 0 && highlighted.every(t => !t.includes('request_human_approval')),
                    highlighted.map(t => t.replaceAll('◀', '<')).join(' | '));
                check('new-call markers include the unauthorized transfer',
                    highlighted.some(t => /transfer\(/.test(t)), highlighted.map(t => t.replaceAll('◀', '<')).join(' | '));
                check('history can compare arbitrary runs',
                    await p2.locator('#runsBody select[aria-label*="another run"]').count() >= 2);
                check('project tools select the newly displayed run before triage',await p2.evaluate(()=>document.getElementById('toolsRun').value===window.currentRunId));
                await p2.click('#triageBtn');
                await p2.waitForFunction(() => !document.getElementById('triageResult').hidden);
                check('triage groups failed rules with hints and evidence events',
                    /2 rules failing/.test(await p2.textContent('#triageResult')) && /hint:/.test(await p2.textContent('#triageResult')) && /evidence events:/.test(await p2.textContent('#triageResult')));
                runIdB = ((await p2.textContent('#projectStatus')).match(/run (\S+)/) || [])[1] || null;
                await pc2.close();
            } finally {
                await killServer(brokenServer);
            }
        } else {
            check('gate line shows FAILED with the new regressions', false, 'skipped: no run id');
            check('project dropdown follows the configured project', false, 'skipped: no run id');
        }
        // -- phase 3: fixed configuration again, web verify after repair -----
        if (runIdB) {
            const portC = await freePort();
            const agentProjectDir=path.join(tmpDir,'agent-project');
            fs.mkdirSync(path.join(agentProjectDir,'specs'),{recursive:true});
            for(const relative of ['agent_fixed.py','specagent.baseline.yaml','specs/behavior.yaml']){
                fs.copyFileSync(path.join(fincareDir,relative),path.join(agentProjectDir,relative));
            }
            const repairedServer = startServer(python, portC, projDb, {
                SPECAGENT_PROJECT_CONFIG: path.join(agentProjectDir, 'specagent.baseline.yaml'),
                SPECAGENT_AGENT_API_INSECURE: '1',
            });
            try {
                await waitForHealth(portC, 30000);
                const repairedContext = await browser.newContext();
                const repairedPage = await repairedContext.newPage();
                repairedPage.on('pageerror', e => projErrs.push(String(e)));
                await repairedPage.goto(`http://127.0.0.1:${portC}/`, {waitUntil: 'domcontentloaded'});
                await repairedPage.waitForFunction(() => !document.getElementById('toolsControls').disabled);
                await repairedPage.locator('summary', {hasText: /^Verify$/}).click();
                await repairedPage.selectOption('#verifyPreRun', runIdB);
                await repairedPage.click('#verifyBtn');
                await repairedPage.waitForFunction(() => !document.getElementById('verifyVerdict').hidden);
                check('repair can be verified entirely through the web',
                    (await repairedPage.textContent('#verifyVerdict')) === 'ALL_FIXED');
                check('repaired verification shows four fixed cases in the CLI quote',
                    /fixed=4 still_failing=0 new_regression=0/.test(await repairedPage.textContent('#verifyResult')));
                const repairedRuns = await repairedPage.evaluate(() => apiJson('/api/runs?project_id=fincare-agent'));
                check('web verify preserves the original baseline',
                    repairedRuns.find(r => r.is_baseline)?.id === runIdA);
                await repairedPage.click('#agentCard > summary');
                await repairedPage.waitForFunction(() => agent.sid !== null);
                await repairedPage.fill('#agentInput', 'Run the suite and explain failures');
                await repairedPage.click('#agentSend');
                await repairedPage.waitForSelector('#agentLog .approval');
                check('agent timeline displays real inspect and run requests',
                    /inspect_project/.test(await repairedPage.textContent('#agentTimeline')) &&
                    /run_suite/.test(await repairedPage.textContent('#agentTimeline')));
                check('agent timeline displays parked approval without executing',
                    /deferred/.test(await repairedPage.textContent('#agentTimeline')));
                const logId = await repairedPage.evaluate(async () => {
                    const d=await apiJson('/api/agent/logs');
                    return d.logs.find(l=>l.title==='Run the suite and explain failures').id;
                });
                await repairedPage.reload({waitUntil:'domcontentloaded'});
                await repairedPage.waitForFunction(() => !document.getElementById('agentSection').classList.contains('hidden'));
                await repairedPage.click('#agentCard > summary');
                await repairedPage.waitForFunction(id => [...document.getElementById('agentHistorySelect').options].some(o=>o.value===id), logId);
                await repairedPage.selectOption('#agentHistorySelect', logId);
                await repairedPage.click('#agentHistoryOpen');
                await repairedPage.waitForFunction(() => agent.readonly&&!agent.busy);
                check('agent history survives a page reload with tool details',
                    /inspect_project/.test(await repairedPage.textContent('#agentTimeline')));
                check('historical approval preview is readonly',
                    await repairedPage.isDisabled('#agentSend') &&
                    await repairedPage.locator('#agentLog .approval').count()===0);
                await repairedPage.click('#agentHistoryResume');
                await repairedPage.waitForSelector('#agentLog .approval');
                check('active history restores the original pending approval',
                    /run_suite/.test(await repairedPage.textContent('#agentLog .approval')));
                await repairedPage.locator('#agentLog .approval .primary').click();
                await repairedPage.waitForFunction(() => !agent.busy&&!agent.pending);
                check('agent approval streams run results and triage into the timeline',
                    /triage_run/.test(await repairedPage.textContent('#agentTimeline')) &&
                    /run_suite/.test(await repairedPage.textContent('#agentTimeline')));
                check('agent reply keeps offline deterministic counts',
                    /43 passed/.test(await repairedPage.textContent('#agentLog .verbatim')));
                await repairedPage.evaluate(id=>agentOpenLog(id), logId);
                check('completed history preserves deterministic results',
                    /43 passed/.test(await repairedPage.textContent('#agentLog .verbatim')));
                const logDownload = repairedPage.waitForEvent('download');
                await repairedPage.click('#agentLogDownload');
                check('agent history offers a sanitized JSONL download',
                    (await logDownload).suggestedFilename()===logId+'.jsonl');
                if(process.env.SPECAGENT_UI_SCREENSHOT){
                    await repairedPage.setViewportSize({width:1400,height:1050});
                    await repairedPage.locator('#agentSection').screenshot({path:process.env.SPECAGENT_UI_SCREENSHOT});
                }
                await repairedContext.close();
            } finally {
                await killServer(repairedServer);
            }
        }
        check('project flow raises no page errors', projErrs.length === 0,
            projErrs.join(' | '));
        const streamPort=await freePort();
        const streamServer=startServer(python,streamPort,path.join(tmpDir,'stream.db'),{
            SPECAGENT_PROJECT_CONFIG:path.join(tmpDir,'agent-project','specagent.baseline.yaml'),
            SPECAGENT_AGENT_API_INSECURE:'1',
        },'agent_stream_server:app','tests/browser');
        try{
            await waitForHealth(streamPort,30000);
            const streamContext=await browser.newContext();const streamPage=await streamContext.newPage();
            const streamErrors=[];streamPage.on('pageerror',e=>streamErrors.push(String(e)));
            await streamPage.goto(`http://127.0.0.1:${streamPort}/`,{waitUntil:'domcontentloaded'});
            await streamPage.waitForFunction(()=>!document.getElementById('agentSection').classList.contains('hidden'));
            await streamPage.click('#agentCard > summary');
            await streamPage.waitForFunction(()=>agent.sid!==null);
            await streamPage.fill('#agentInput','Explain the behavior rules');await streamPage.click('#agentSend');
            await streamPage.waitForSelector('#agentLog .stream-bubble');
            check('provider text appears before the model completes',
                await streamPage.evaluate(()=>agent.busy) && /Inspecting/.test(await streamPage.textContent('#agentLog .stream-bubble')));
            check('new session is disabled while a stream is executing',await streamPage.isDisabled('#agentNew'));
            await streamPage.waitForFunction(()=>!agent.busy);
            check('stream completion replaces the live preview with the full reply',
                /scripted browser test/.test(await streamPage.textContent('#agentLog')) && await streamPage.locator('.stream-bubble').count()===0);
            check('streamed untrusted HTML remains text',
                await streamPage.locator('#agentLog img').count()===0 && /<img src=x/.test(await streamPage.textContent('#agentLog')));
            check('model stream ends with a timeline record',/completed/.test(await streamPage.textContent('#agentTimeline')));
            check('model stream browser flow raises no page errors',streamErrors.length===0,streamErrors.join(' | '));
            await streamContext.close();
        }finally{await killServer(streamServer)}
        const suggestionRoot=path.join(tmpDir,'suggestion-project'),suggestionModule='browser_target_'+Date.now();
        fs.mkdirSync(path.join(suggestionRoot,'specs'),{recursive:true});
        fs.copyFileSync(path.join(fincareDir,'agent.py'),path.join(suggestionRoot,suggestionModule+'.py'));
        fs.copyFileSync(path.join(fincareDir,'agent_fixed.py'),path.join(suggestionRoot,'agent_fixed.py'));
        fs.copyFileSync(path.join(fincareDir,'specs','behavior.yaml'),path.join(suggestionRoot,'specs','behavior.yaml'));
        fs.writeFileSync(path.join(suggestionRoot,'specagent.yaml'),fs.readFileSync(path.join(fincareDir,'specagent.yaml'),'utf8').replace('agent: agent:run_agent','agent: '+suggestionModule+':run_agent')+'\nagent:\n  allow_source: true\n');
        const suggestionPort=await freePort(),suggestionToken='suggestion-browser-test-token';
        const suggestionServer=startServer(python,suggestionPort,path.join(tmpDir,'suggestions.db'),{
            SPECAGENT_PROJECT_CONFIG:path.join(suggestionRoot,'specagent.yaml'),SPECAGENT_API_TOKEN:suggestionToken,
        },'agent_fix_server:app','tests/browser');
        try{
            try { await waitForHealth(suggestionPort,30000); }
            catch (error) { throw new Error(`${error.message}: ${suggestionServer.stderr().trim().split('\n').slice(-3).join(' / ')}`); }
            const context=await browser.newContext({permissions:['clipboard-read','clipboard-write']});
            const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(String(e)));
            await page.goto(`http://127.0.0.1:${suggestionPort}/`,{waitUntil:'domcontentloaded'});
            await page.fill('#tokenInput',suggestionToken);await page.click('#tokenSet');
            await page.waitForFunction(()=>!document.getElementById('toolsControls').disabled);
            await page.click('#suggestionsCard > summary');
            await page.waitForSelector('#suggestionsList button');
            check('fix suggestions card lists the approved proposal',await page.locator('#suggestionsCard').count()===1);
            await page.click('#suggestionsList button');await page.waitForSelector('#suggestionDetail pre.diff');
            let id=await page.getAttribute('#suggestionDetail','data-suggestion-id');
            check('fix detail displays added and removed diff lines',await page.locator('#suggestionDetail .add').count()>0&&await page.locator('#suggestionDetail .del').count()>0);
            check('fix detail has copy download command and verify controls',await page.locator('#suggestionDetail button').count()===4);
            const appliedButtons=await page.locator('#suggestionDetail button').allTextContents();
            check('fix detail has no apply button',!appliedButtons.some(t=>/^(Apply|应用)(\s|$)/i.test(t)));
            const raw=fs.readFileSync(path.join(suggestionRoot,'.specagent','suggestions',id,'fix.diff'));
            await page.getByRole('button',{name:'Copy diff',exact:true}).click();
            check('copy diff preserves the proposal lines',
                (await page.evaluate(()=>navigator.clipboard.readText())).replace(/\r\n/g,'\n')===raw.toString('utf8').replace(/\r\n/g,'\n'));
            await page.getByRole('button',{name:'Copy git apply command',exact:true}).click();
            check('copied apply command uses the relative project path',await page.evaluate(()=>navigator.clipboard.readText())===`git apply .specagent/suggestions/${id}/fix.diff`);
            const downloaded=page.waitForEvent('download');await page.getByRole('button',{name:'Download fix.diff',exact:true}).click();
            const download=await downloaded;
            check('fix download matches the original bytes',fs.readFileSync(await download.path()).equals(raw));
            check('fix diagnosis renders injected HTML as text',await page.locator('#suggestionDetail img').count()===0&&/<img src=x>/.test(await page.textContent('#suggestionDetail')));
            await page.click('#agentCard > summary');await page.waitForFunction(()=>agent.sid!==null);
            await page.fill('#agentInput','Propose a fix');await page.click('#agentSend');
            await page.waitForSelector('#agentLog .approval');await page.locator('#agentLog .approval .primary').click();
            await page.waitForFunction(()=>!agent.busy&&!agent.pending);
            await page.locator('#agentLog .toolcall').filter({has:page.locator('a[href="#suggestionsCard"]')}).locator('summary').click();
            await page.locator('#agentLog a[href="#suggestionsCard"]').click();
            id=await page.evaluate(()=>agent.records.filter(r=>r.type==='dashboard_result'&&r.data.action?.suggestion_id).at(-1).data.action.suggestion_id);
            await page.waitForFunction(value=>document.getElementById('suggestionDetail').dataset.suggestionId===value,id);
            check('approved agent tool card links to the saved suggestion',await page.getAttribute('#suggestionDetail','data-suggestion-id')===id);
            const relative=`.specagent/suggestions/${id}/fix.diff`;
            const init=spawnSync('git',['init','-q'],{cwd:suggestionRoot});if(init.status!==0) throw new Error('git init failed');
            for(const args of [['--check'],[]]){
                const apply=spawnSync('git',['-c','core.autocrlf=false','apply',...args,relative],{cwd:suggestionRoot});
                if(apply.status!==0) throw new Error('git apply failed: '+apply.stderr.toString());
            }
            await page.locator('#suggestionDetail').getByRole('button',{name:'Verify',exact:true}).click();
            await page.waitForSelector('#suggestionDetail .suggestion-verify-result');
            check('locally applied fix verifies as ALL_FIXED in the web',/ALL_FIXED/.test(await page.textContent('#suggestionDetail')));
            check('fix verification appears in persistent history',fs.readdirSync(path.join(suggestionRoot,'.specagent','suggestions',id)).some(n=>/^verify-.*\.json$/.test(n))&&/fixed=4/.test(await page.textContent('#suggestionDetail')));
            check('fix suggestion browser flow raises no page errors',errors.length===0,errors.join(' | '));
            if(process.env.SPECAGENT_FIX_SCREENSHOT){await page.setViewportSize({width:1400,height:1300});await page.locator('#projectTools').screenshot({path:process.env.SPECAGENT_FIX_SCREENSHOT})}
            await context.close();
        }finally{await killServer(suggestionServer)}
        const progressRoot=path.join(tmpDir,'progress-project'),progressModule='progress_target_'+Date.now();
        fs.mkdirSync(path.join(progressRoot,'specs'),{recursive:true});
        const slowSource=fs.readFileSync(path.join(fincareDir,'agent.py'),'utf8')
            .replace('def run_agent(message, history=None, actor=None):','def run_agent(message, history=None, actor=None):\n    import time\n    time.sleep(0.2)');
        fs.writeFileSync(path.join(progressRoot,progressModule+'.py'),slowSource);
        fs.copyFileSync(path.join(fincareDir,'specs','behavior.yaml'),path.join(progressRoot,'specs','behavior.yaml'));
        fs.writeFileSync(path.join(progressRoot,'specagent.yaml'),fs.readFileSync(path.join(fincareDir,'specagent.yaml'),'utf8')
            .replace('agent: agent:run_agent','agent: '+progressModule+':run_agent').replace('concurrency: 4','concurrency: 1'));
        const progressPort=await freePort(),progressToken='progress-browser-test-token';
        const progressServer=startServer(python,progressPort,path.join(tmpDir,'progress.db'),{
            SPECAGENT_PROJECT_CONFIG:path.join(progressRoot,'specagent.yaml'),SPECAGENT_API_TOKEN:progressToken,
        });
        try{
            await waitForHealth(progressPort,30000);
            const context=await browser.newContext(),page=await context.newPage(),errors=[];
            page.on('pageerror',e=>errors.push(String(e)));
            await page.goto(`http://127.0.0.1:${progressPort}/`,{waitUntil:'domcontentloaded'});
            await page.fill('#tokenInput',progressToken);await page.click('#tokenSet');
            await page.waitForFunction(()=>!document.getElementById('projectRunBtn').hidden);
            await page.click('#projectRunBtn');
            await page.waitForFunction(()=>{const b=document.getElementById('progressBar');return b.value>0&&b.value<b.max});
            const first=await page.evaluate(()=>document.getElementById('progressBar').value);
            const rid=await page.getAttribute('#runProgress','data-run-id');
            check('live progress appears before the project run completes',first>0&&!/complete/.test(await page.textContent('#projectStatus')));
            check('live progress exposes accessible case counts and phase',await page.getAttribute('#progressCounts','role')==='status'&&/暂定/.test(await page.textContent('#progressNote'))&&/通过/.test(await page.textContent('#progressCounts')));
            await page.waitForFunction(n=>document.getElementById('progressBar').value>n,first);
            check('live completed counts advance during execution',(await page.evaluate(()=>document.getElementById('progressBar').value))>first);
            const resumed=await context.newPage();resumed.on('pageerror',e=>errors.push(String(e)));
            await resumed.goto(`http://127.0.0.1:${progressPort}/`,{waitUntil:'domcontentloaded'});
            // sessionStorage is per tab: enter the same token explicitly.
            await resumed.fill('#tokenInput',progressToken);await resumed.click('#tokenSet');
            await resumed.waitForFunction(id=>document.getElementById('runProgress').dataset.runId===id,rid);
            check('opening the dashboard restores the same live run',await resumed.getAttribute('#runProgress','data-run-id')===rid);
            await resumed.waitForFunction(()=>!document.getElementById('projectCancelBtn').hidden);
            await resumed.click('#projectCancelBtn');
            await page.waitForFunction(()=>/run canceled/.test(document.getElementById('projectStatus').textContent));
            await resumed.waitForFunction(()=>/最终判定/.test(document.getElementById('progressNote').textContent));
            check('restored live run can be canceled with remaining cases recorded',/取消 [1-9]/.test(await resumed.textContent('#progressCounts')));
            const p=await resumed.evaluate(id=>apiJson('/api/runs/'+id+'/progress'),rid);
            check('final progress agrees with persisted cancellation counters',p.final&&p.completed===p.total&&/通过/.test(await resumed.textContent('#progressCounts'))&&(await resumed.evaluate(()=>document.getElementById('progressBar').value))===p.completed);
            check('live progress browser flow has no page errors',errors.length===0,errors.join(' | '));
            if(process.env.SPECAGENT_PROGRESS_SCREENSHOT){await resumed.setViewportSize({width:1400,height:900});await resumed.locator('#runProgress').screenshot({path:process.env.SPECAGENT_PROGRESS_SCREENSHOT})}
            await context.close();
        }finally{await killServer(progressServer)}
        // Two real behavior versions, with all files confined to a private copy.
        const specRoot=path.join(tmpDir,'spec-project'),specModule='spec_target_'+Date.now();
        fs.mkdirSync(path.join(specRoot,'specs'),{recursive:true});
        fs.copyFileSync(path.join(fincareDir,'agent_fixed.py'),path.join(specRoot,specModule+'.py'));
        const specPath=path.join(specRoot,'specs','behavior.yaml'),specOriginal=fs.readFileSync(path.join(fincareDir,'specs','behavior.yaml'),'utf8');
        fs.writeFileSync(specPath,specOriginal);
        fs.writeFileSync(path.join(specRoot,'specagent.yaml'),fs.readFileSync(path.join(fincareDir,'specagent.yaml'),'utf8').replace('agent: agent:run_agent','agent: '+specModule+':run_agent'));
        const specPort=await freePort(),specToken='spec-browser-test-token',specBase=`http://127.0.0.1:${specPort}`;
        const specServer=startServer(python,specPort,path.join(tmpDir,'specs.db'),{
            SPECAGENT_PROJECT_CONFIG:path.join(specRoot,'specagent.yaml'),SPECAGENT_API_TOKEN:specToken,
        });
        try{
            await waitForHealth(specPort,30000);
            async function specRun(label=''){const r=await fetch(specBase+'/api/project/runs',{method:'POST',headers:{Authorization:'Bearer '+specToken,'Content-Type':'application/json'},body:JSON.stringify({label})});if(!r.ok)throw new Error(await r.text());return (await r.json()).run}
            const first=await specRun('<img src=x onerror="window.trendInjected=true">');
            const revised='# <img src=x onerror="window.specInjected=true">\n'+specOriginal.replaceAll('severity: critical','severity: high');
            fs.writeFileSync(specPath,revised);const second=await specRun();
            const context=await browser.newContext({permissions:['clipboard-read','clipboard-write']}),page=await context.newPage(),errors=[];
            page.on('pageerror',e=>errors.push(String(e)));
            await page.goto(specBase+'/',{waitUntil:'domcontentloaded'});
            await page.fill('#tokenInput',specToken);await page.click('#tokenSet');
            await page.waitForFunction(()=>!document.getElementById('specCurrent').disabled);
            await page.waitForFunction(()=>document.getElementById('specVersion').options.length===2);
            check('spec history shows two saved behavior versions',await page.locator('#specVersion option').count()===2);
            await page.click('#specCurrent');await page.waitForSelector('#specSource');
            check('current YAML is readable before choosing a historical version',(await page.textContent('#specSource'))===revised);
            check('spec source renders markup as text without executing it',await page.locator('#specDetail img').count()===0&&!(await page.evaluate(()=>window.specInjected)));
            await page.selectOption('#specVersion',first.spec_id);await page.click('#specOpen');
            await page.waitForFunction(id=>document.getElementById('specDetail').dataset.specId===id,first.spec_id);
            check('historical spec retains the original source',(await page.textContent('#specSource')).replace(/\r\n/g,'\n')===specOriginal.replace(/\r\n/g,'\n'));
            await page.locator('#specDetail').getByRole('button',{name:'复制源内容',exact:true}).click();
            await page.waitForFunction(()=>document.getElementById('specStatus').textContent==='完成');
            check('spec source can be copied',(await page.evaluate(()=>navigator.clipboard.readText())).replace(/\r\n/g,'\n')===specOriginal.replace(/\r\n/g,'\n'));
            const downloading=page.waitForEvent('download');await page.locator('#specDetail').getByRole('button',{name:'下载源内容',exact:true}).click();
            const download=await downloading;const downloaded=await download.path();
            check('authenticated spec download retains source bytes',fs.readFileSync(downloaded).equals(Buffer.from(specOriginal,'utf8')));
            await page.locator('#specDetail button[data-run-id="'+first.id+'"]').click();
            await page.waitForFunction(id=>window.currentRunId===id,first.id);
            check('spec links to its associated run',await page.locator('#runSpecLink').count()===1);
            await page.click('#runSpecLink');await page.waitForFunction(id=>document.getElementById('specDetail').dataset.specId===id,first.spec_id);
            check('run links back to the exact saved spec',await page.getAttribute('#specDetail','data-spec-id')===first.spec_id);
            await page.selectOption('#specBaseline',first.spec_id);await page.selectOption('#specVersion',second.spec_id);await page.click('#specCompare');
            await page.waitForFunction(()=>!document.getElementById('specDiff').hidden);
            check('spec comparison displays added and removed source lines',await page.locator('#specDiff .add').count()>0&&await page.locator('#specDiff .del').count()>0&&/severity: high/.test(await page.textContent('#specDiff')));
            check('spec browser flow raises no page errors',errors.length===0,errors.join(' | '));
            if(process.env.SPECAGENT_SPEC_SCREENSHOT){await page.setViewportSize({width:1400,height:1100});await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.locator('#specBrowser').screenshot({path:process.env.SPECAGENT_SPEC_SCREENSHOT})}
            await page.evaluate(async id=>{await api('/api/runs/'+id+'/baseline',{method:'POST'});await loadMetricTrends()},first.id);
            await page.waitForFunction(id=>document.getElementById('metricTrends').dataset.baselineId===id,first.id);
            check('all six trend charts use the current baseline',await page.locator('#trendCharts svg').count()===6&&/当前基线/.test(await page.textContent('#trendMeta')));
            check('trend charts plot both real runs with UTC labels',await page.locator('#trendCharts circle').count()===12&&/UTC/.test(await page.textContent('#trendMeta')));
            check('trend history renders injected labels as text',await page.locator('#metricTrends img').count()===0&&!await page.evaluate(()=>window.trendInjected)&&/<img/.test(await page.textContent('#trendRows')));
            const values=await page.evaluate(async pid=>{const h=await apiJson('/api/metrics/history?project_id='+pid),m=await apiJson('/api/metrics?project_id='+pid);return ['behavior_pass_rate','critical_violation_rate','new_regression_count','flaky_rate','tool_accuracy','latency_ms'].every(k=>JSON.stringify(h.points.at(-1)[k])===JSON.stringify(m[k]))},first.project_id);
            check('latest trend values match all six current metric values',values);
            await page.selectOption('#trendDays','all');await page.waitForFunction(()=>document.getElementById('metricTrends').dataset.days==='all');
            check('trend time range can change to all saved history',await page.locator('#trendRows tr').count()===2);
            const circle=page.locator('#trendCharts [data-metric="behavior_pass_rate"] circle').last();
            await circle.focus();await page.waitForFunction(()=>document.getElementById('trendPoint').textContent.includes('行为通过率'));
            await circle.press('Enter');await page.waitForFunction(id=>window.currentRunId===id,second.id);
            check('trend points expose precise values and support keyboard run navigation',/100%/.test(await page.textContent('#trendPoint'))&&await page.locator('#runSpecLink').count()===1);
            if(process.env.SPECAGENT_TREND_SCREENSHOT){
                await page.setViewportSize({width:1400,height:1200});
                await page.evaluate(()=>{document.getElementById('metricTrends').scrollIntoView({behavior:'instant',block:'start'})});
                await page.screenshot({path:process.env.SPECAGENT_TREND_SCREENSHOT,animations:'disabled'});
            }
            await page.evaluate(async()=>{await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:'trend-empty',name:'Empty trend project'})});await loadProjects();document.getElementById('projectSel').value='trend-empty';refreshProject()});
            await page.waitForFunction(()=>document.getElementById('metricTrends').dataset.projectId==='trend-empty');
            check('empty project has an explicit trend empty state',await page.locator('#trendCharts svg').count()===0&&/没有已结束/.test(await page.textContent('#trendCharts')));
            check('trend browser flow has no page errors',errors.length===0,errors.join(' | '));
            await page.goto(specBase+'/?project='+encodeURIComponent(first.project_id),{waitUntil:'domcontentloaded'});
            await page.waitForFunction(pid=>document.getElementById('projectSel').value===pid&&document.querySelectorAll('#projectsBody button').length===2,first.project_id);
            check('project deep link chooses the initial history view',(await page.inputValue('#projectSel'))===first.project_id);
            const projectConfigBytes=fs.readFileSync(path.join(specRoot,'specagent.yaml')),projectSpecBytes=fs.readFileSync(specPath);
            const configuredBefore=await page.evaluate(()=>apiJson('/api/project'));
            await page.locator('#projectCreate summary').click();
            await page.fill('#newProjectId','review-workspace');await page.fill('#newProjectName','评审工作台');
            await page.fill('#newProjectDescription','记录比赛展示与行为审查结果。\n项目资料不会改变被测 Agent。');
            await page.click('#projectCreateSubmit');
            await page.waitForFunction(()=>/已创建并选中/.test(document.getElementById('projectCreateStatus').textContent));
            check('project can be created and selected through the form',(await page.inputValue('#projectSel'))==='review-workspace');
            check('project table and selector show its name and description',/评审工作台/.test(await page.textContent('#projectsBody'))&&/比赛展示/.test(await page.textContent('#projectsBody'))&&/评审工作台/.test(await page.locator('#projectSel option:checked').textContent()));
            await page.waitForFunction(()=>document.getElementById('metricTrends').dataset.projectId==='review-workspace'&&/0 个行为版本/.test(document.getElementById('specStatus').textContent)&&/No runs/.test(document.getElementById('runsBody').textContent));
            check('new project has empty runs specs and trends',/No runs/.test(await page.textContent('#runsBody'))&&await page.locator('#trendCharts svg').count()===0);
            const configuredAfter=await page.evaluate(()=>apiJson('/api/project'));
            check('metadata creation leaves the tested target and config files unchanged',JSON.stringify(configuredBefore)===JSON.stringify(configuredAfter)&&fs.readFileSync(path.join(specRoot,'specagent.yaml')).equals(projectConfigBytes)&&fs.readFileSync(specPath).equals(projectSpecBytes)&&/fincare-agent/.test(await page.textContent('#projectSelectionInfo')));
            await page.evaluate(()=>loadProjects());
            check('refresh retains the newly selected project',(await page.inputValue('#projectSel'))==='review-workspace');
            if(process.env.SPECAGENT_PROJECT_SCREENSHOT){
                await page.setViewportSize({width:1400,height:1050});
                await page.evaluate(()=>document.getElementById('projectsSection').scrollIntoView({behavior:'instant',block:'start'}));
                await page.screenshot({path:process.env.SPECAGENT_PROJECT_SCREENSHOT,animations:'disabled'});
            }
            await page.fill('#newProjectId','review-workspace');await page.fill('#newProjectName','Overwrite attempt');
            await page.click('#projectCreateSubmit');await page.waitForFunction(()=>/已存在/.test(document.getElementById('projectCreateStatus').textContent));
            check('duplicate project shows a conflict without replacing the original',await page.getByRole('button',{name:'评审工作台',exact:true}).count()===1&&(await page.inputValue('#newProjectName'))==='Overwrite attempt');
            await page.fill('#newProjectId','blank-name');await page.fill('#newProjectName','   ');await page.click('#projectCreateSubmit');
            await page.waitForFunction(()=>/不能为空/.test(document.getElementById('projectCreateStatus').textContent));
            check('blank project name is rejected in the form',/不能为空/.test(await page.textContent('#projectCreateStatus')));
            const quotedId="quote';window.projectInjected=true;//",quotedName='<img src=x onerror="window.projectInjected=true">';
            await page.fill('#newProjectId',quotedId);await page.fill('#newProjectName',quotedName);await page.fill('#newProjectDescription','<script>window.projectInjected=true</script>');
            await page.click('#projectCreateSubmit');await page.waitForFunction(id=>document.getElementById('projectSel').value===id,quotedId);
            check('project markup is displayed as text',await page.locator('#projectsBody img, #projectsBody script').count()===0&&!(await page.evaluate(()=>window.projectInjected))&&/<script>/.test(await page.textContent('#projectsBody')));
            await page.getByRole('button',{name:'评审工作台',exact:true}).click();
            const quoteButton=page.getByRole('button',{name:quotedName,exact:true});await quoteButton.focus();await quoteButton.press('Enter');
            check('quoted project IDs support keyboard selection without executing script',(await page.inputValue('#projectSel'))===quotedId&&!(await page.evaluate(()=>window.projectInjected)));
            // Hold real responses until the new empty project has painted.
            await page.waitForLoadState('networkidle');
            let releaseOld,oldReady,heldCount=0;
            const pendingPaths=new Set(['/api/runs/search','/api/metrics']);
            const oldGate=new Promise(resolve=>{releaseOld=resolve}),oldHeld=new Promise(resolve=>{oldReady=resolve});
            await page.route('**/api/**',async route=>{
                const url=new URL(route.request().url());
                if(pendingPaths.has(url.pathname)&&url.searchParams.get('project_id')===first.project_id){
                    pendingPaths.delete(url.pathname);
                    const response=await route.fetch();if(++heldCount===2)oldReady();await oldGate;await route.fulfill({response});
                }else await route.continue();
            });
            const oldViews=page.evaluate(pid=>{document.getElementById('projectSel').value=pid;return Promise.all([loadRuns(),loadMetrics()])},first.project_id).then(()=>null,error=>error);
            try{
                await Promise.race([oldHeld,new Promise((_,reject)=>setTimeout(()=>reject(new Error('old project responses were not intercepted')),15000))]);
                await page.getByRole('button',{name:'评审工作台',exact:true}).click();
                await page.waitForFunction(()=>/No runs/.test(document.getElementById('runsBody').textContent)&&/review-workspace/.test(document.getElementById('metricsMeta').textContent));
            }finally{
                releaseOld();const error=await oldViews;await page.unrouteAll({behavior:'wait'});if(error)throw error;
            }
            check('late previous-project responses cannot replace the new project view',/No runs/.test(await page.textContent('#runsBody'))&&/review-workspace/.test(await page.textContent('#metricsMeta')));
            check('project management browser flow has no page errors',errors.length===0,errors.join(' | '));
            await page.selectOption('#projectSel',first.project_id);
            await page.waitForFunction(()=>/共 2 条/.test(document.getElementById('runsPageInfo').textContent));
            check('run history displays paginated totals',await page.locator('#runsBody tr[data-run-id]').count()===2&&await page.isDisabled('#runsNext'));
            await page.selectOption('#runsBaseline','yes');await page.waitForFunction(()=>/共 1 条/.test(document.getElementById('runsPageInfo').textContent));
            check('baseline filter isolates and protects the current baseline',await page.getAttribute('#runsBody tr','data-run-id')===first.id&&await page.locator('#runsBody button[data-action="delete"]').isDisabled());
            await page.selectOption('#runsBaseline','all');await page.waitForFunction(()=>document.querySelectorAll('#runsBody tr[data-run-id]').length===2);
            await page.locator('#runsBody tr[data-run-id="'+second.id+'"] button[data-action="label"]').click();await page.waitForSelector('#runNewLabel');
            const newLabel='<img src=x onerror="window.labelInjected=true"> Review';
            await page.fill('#runNewLabel',newLabel);await page.click('#runLabelSave');await page.waitForFunction(()=>/标签已保存/.test(document.getElementById('runManagementStatus').textContent));
            check('run label is saved and safely rendered as text',/Review/.test(await page.textContent('#runsBody'))&&await page.locator('#runsBody img').count()===0&&!await page.evaluate(()=>window.labelInjected));
            await page.fill('#runsQuery','Review');await page.locator('#runsSearchForm button[type="submit"]').click();
            await page.waitForFunction(()=>/共 1 条/.test(document.getElementById('runsPageInfo').textContent));
            check('run search finds the edited label',await page.getAttribute('#runsBody tr','data-run-id')===second.id);
            await page.locator('#runsBody button[data-action="delete"]').click();await page.waitForSelector('#runDeleteConfirm');
            check('delete preview describes retained evidence and affected metrics',/指标/.test(await page.textContent('#runManagementPanel'))&&/执行/.test(await page.textContent('#runManagementPanel'))&&await page.isDisabled('#runDeleteSubmit'));
            await page.fill('#runDeleteConfirm','incorrect');
            check('delete requires the exact run ID',await page.isDisabled('#runDeleteSubmit'));
            await page.fill('#runDeleteConfirm',second.id);
            if(process.env.SPECAGENT_RUN_MANAGEMENT_SCREENSHOT){await page.setViewportSize({width:1400,height:1100});await page.evaluate(()=>document.getElementById('runsSection').scrollIntoView({behavior:'instant',block:'start'}));await page.screenshot({path:process.env.SPECAGENT_RUN_MANAGEMENT_SCREENSHOT,animations:'disabled'})}
            await page.click('#runDeleteSubmit');await page.waitForFunction(()=>/已移入回收站/.test(document.getElementById('runManagementStatus').textContent));
            check('deleted run leaves the filtered normal history',await page.locator('#runsBody tr[data-run-id]').count()===0);
            await page.selectOption('#runsView','trash');await page.waitForFunction(()=>document.querySelectorAll('#runsBody button[data-action="restore"]').length===1);
            check('trash exposes a restore action',await page.getAttribute('#runsBody tr','data-run-id')===second.id);
            await page.locator('#runsBody button[data-action="restore"]').click();await page.waitForFunction(()=>/运行已恢复/.test(document.getElementById('runManagementStatus').textContent));
            await page.selectOption('#runsView','active');await page.waitForFunction(()=>document.querySelectorAll('#runsBody tr[data-run-id]').length===1);
            check('restored run returns to search results',await page.getAttribute('#runsBody tr','data-run-id')===second.id);
            await page.selectOption('#runsStatus','running');await page.waitForFunction(()=>/共 0 条/.test(document.getElementById('runsPageInfo').textContent));
            check('status filter displays a clear empty result',/No runs/.test(await page.textContent('#runsBody')));
            await page.evaluate(async pid=>{
                for(let i=0;i<26;i++)await apiJson('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:pid,agent:'demo',text:'退款必须人工确认。',label:'分页样本 '+i})});
            },first.project_id);
            await page.fill('#runsQuery','');await page.selectOption('#runsStatus','all');
            await page.waitForFunction(()=>/共 28 条/.test(document.getElementById('runsPageInfo').textContent));
            const firstPageIds=await page.locator('#runsBody tr[data-run-id]').evaluateAll(rows=>rows.map(row=>row.dataset.runId));
            check('first history page is bounded and offers more results',firstPageIds.length===25&&!await page.isDisabled('#runsNext'));
            await page.click('#runsNext');await page.waitForFunction(()=>document.getElementById('runsBody').dataset.offset==='25');
            const nextPageIds=await page.locator('#runsBody tr[data-run-id]').evaluateAll(rows=>rows.map(row=>row.dataset.runId));
            check('next history page includes older runs without duplicates',nextPageIds.length===3&&nextPageIds.includes(first.id)&&nextPageIds.every(id=>!firstPageIds.includes(id)));
            await page.click('#runsPrev');await page.waitForFunction(()=>document.getElementById('runsBody').dataset.offset==='0');
            check('previous history page restores the first page',JSON.stringify(await page.locator('#runsBody tr[data-run-id]').evaluateAll(rows=>rows.map(row=>row.dataset.runId)))===JSON.stringify(firstPageIds));
            check('run management browser flow has no page errors',errors.length===0,errors.join(' | '));
            await context.close();
        }finally{await killServer(specServer)}
    } catch (e) {
        check('test run completed', false, String(e && e.stack ? e.stack.split('\n').slice(0, 4).join(' / ') : e));
    } finally {
        if (browser) await browser.close().catch(() => {});
        server.child.kill();
        // uvicorn keeps the SQLite file open until the process actually exits.
        // Removing the temp dir before that lands fails with EBUSY on Windows,
        // so wait for the exit event (with a ceiling) and treat cleanup as
        // best-effort either way — a stray temp dir must never fail the run.
        await new Promise((resolve) => {
            if (server.child.exitCode !== null) return resolve();
            const timer = setTimeout(resolve, 5000);
            server.child.once('exit', () => { clearTimeout(timer); resolve(); });
        });
        try { fs.rmSync(tmpDir, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 }); }
        catch (e) { console.error(`note: could not remove ${tmpDir} (${e.code}); leaving it behind`); }
    }

    // -- report ---------------------------------------------------------------
    let failed = 0;
    for (const r of results) {
        if (!r.ok) failed++;
        const mark = r.ok ? 'PASS' : 'FAIL';
        console.log(`${mark}  ${r.name}${r.detail ? '  [' + r.detail + ']' : ''}`);
    }
    console.log(`\n${results.length - failed} passed, ${failed} failed  (browser: ${results.length} checks)`);
    process.exit(failed ? 1 : 0);
}

main().catch((e) => {
    console.error('harness error:', e);
    process.exit(3);
});
