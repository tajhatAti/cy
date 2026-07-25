/* Targeted regression test for the route-backed RunSpace Details page.
   No backend is needed: jsdom runs the real index.html + static/pro.js and a
   tiny fetch stub supplies one authenticated user's job. */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const ROOT = path.join(__dirname, "..", "..");
const job = {
  id: 7,
  name: "My Bot",
  language: "python",
  code: "print('ok')",
  status: "running",
  uptime_s: 12,
  restarts: 0,
  runner_job_id: "runner-7",
};

function response(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  };
}

function waitFor(fn, timeout = 2500) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      try {
        const result = fn();
        if (result) return resolve(result);
      } catch (error) {
        return reject(error);
      }
      if (Date.now() - started > timeout) return reject(new Error("timed out"));
      setTimeout(tick, 20);
    };
    tick();
  });
}

(async () => {
  let html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
  const script = fs.readFileSync(path.join(ROOT, "static", "pro.js"), "utf8");
  const css = fs.readFileSync(path.join(ROOT, "static", "runspace-dark.css"), "utf8");
  html = html
    .replace(/<script[^>]*src=["'][^"']+["'][^>]*>\s*<\/script>/g, "")
    .replace(/<link[^>]*rel=["']stylesheet["'][^>]*>/g, "")
    .replace("</head>", `<style>${css}</style></head>`);

  const errors = [];
  const console = new VirtualConsole();
  console.on("jsdomError", error => {
    const message = String(error && error.message || error);
    if (!message.includes("Not implemented")) errors.push(message);
  });

  const dom = new JSDOM(html, {
    url: "https://example.test/runspace/alice/my-bot",
    runScripts: "dangerously",
    pretendToBeVisual: true,
    virtualConsole: console,
    beforeParse(window) {
      window.localStorage.setItem("ahad_token", "test-token");
      window.scrollTo = () => {};
      window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
      window.HTMLElement.prototype.scrollIntoView = function () {};
      window.navigator.clipboard = { writeText: async () => {} };
      window.EventSource = class {
        constructor() { this.readyState = 1; }
        close() { this.readyState = 2; }
      };
      window.fetch = async url => {
        const pathname = new URL(String(url), window.location.href).pathname;
        if (pathname === "/profile") return response({ username: "alice", email: "alice@example.test" });
        if (pathname === "/snippets") return response({ snippets: [] });
        if (pathname === "/api/jobs") return response({ jobs: [job], runner: "ok", max_per_user: 3 });
        if (pathname === "/api/jobs/7") return response(job);
        if (pathname === "/api/jobs/7/logs") return response({ status: "running", logs: "ready" });
        if (pathname === "/2fa/status") return response({ enabled: false, backup_codes_count: 0 });
        if (pathname === "/sessions") return response({ sessions: [] });
        return response({}, 200);
      };
    },
  });

  const { window } = dom;
  window.eval(script); // synchronously register the real app's boot listeners

  await waitFor(() => window.document.body.classList.contains("rs-active"));
  await waitFor(() => window.document.querySelectorAll("#jobsList .job-item").length === 1);
  await waitFor(() => window.document.getElementById("btnJobDetails")._w === 1);
  await waitFor(() => window.location.pathname === "/runspace/alice/my-bot");

  // A click/open changes to a distinct Details URL.
  assert.strictEqual(window.openJobDetails(), true);
  assert.strictEqual(window.location.pathname, "/runspace/alice/my-bot/details");
  assert(window.document.body.classList.contains("rs-detail-open"));
  assert.strictEqual(window.document.getElementById("jobDetailPanel").getAttribute("aria-hidden"), "false");

  // Regression: the old rule put pointer-events:none on .rs-ws and froze all
  // of its Details descendants. Both the parent and page must remain live.
  const workspace = window.document.getElementById("wbWorkspace");
  const details = window.document.getElementById("jobDetailPanel");
  assert.notStrictEqual(window.getComputedStyle(workspace).pointerEvents, "none");
  assert.strictEqual(window.getComputedStyle(details).pointerEvents, "auto");

  // Background job polling must not erase /details.
  window.eval("_updateJobUrl(window._lastJobs[0])");
  assert.strictEqual(window.location.pathname, "/runspace/alice/my-bot/details");

  // The real Back control is wired and consumes the in-app Details entry.
  window.document.getElementById("jobDetailBack").click();
  await waitFor(() => window.location.pathname === "/runspace/alice/my-bot");
  assert(!window.document.body.classList.contains("rs-detail-open"));

  // A hard-loaded Details URL has no in-app history marker; closing replaces
  // it with the editor URL rather than navigating away from the site.
  window.history.replaceState({}, "", "/runspace/alice/my-bot/details");
  window.openJobDetails(7, { navigate: false });
  window.closeJobDetails();
  assert.strictEqual(window.location.pathname, "/runspace/alice/my-bot");

  // Refresh/deep-link restoration reopens Details from the URL alone.
  window.history.replaceState({}, "", "/runspace/alice/my-bot/details");
  window.routeFromUrl();
  await waitFor(() => window.document.body.classList.contains("rs-detail-open"));
  assert.strictEqual(window.location.pathname, "/runspace/alice/my-bot/details");

  assert.deepStrictEqual(errors, []);
  window.close();
  process.stdout.write("✓ RunSpace Details has a separate URL and remains interactive\n");
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
