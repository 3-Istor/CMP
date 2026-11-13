import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";

const dependency = createRequire(new URL("../package.json", import.meta.url));
const ts = dependency("typescript");
const { NextRequest, NextResponse } = dependency("next/server");
const root = new URL("../", import.meta.url);
const origin = "https://cmp.test";
const prefix = "/api/auth/github";
const bindingName = "cnp-github-oauth-binding";
const binding = "fixture-browser-binding-" + "x".repeat(48);
const cnpToken = "fixture-cnp-token-must-stay-server-side";
const code = "fixture-oauth-code-must-not-leak";
const authorizeURL = "https://github.com/login/oauth/authorize?state=fresh-state&code_challenge=S256-fixture&code_challenge_method=S256";
const installationURL = "https://github.com/apps/cnp-portal/installations/new?state=installation-state";

function json(body, status = 200) {
  return Response.json(body, { status });
}

// Run the actual TypeScript handlers. Only CNP auth and backend transport are
// fixtures; cookie parsing, serialization, redirects and responses are Next's.
function transpile(file) {
  const source = readFileSync(new URL(file, root), "utf8");
  const compiled = ts.transpileModule(source, {
    reportDiagnostics: true,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  assert.equal(compiled.diagnostics.filter(item => item.category === ts.DiagnosticCategory.Error).length, 0);
  return compiled.outputText;
}

// The bridge's own helpers (no secrets, no I/O) run for real.
function loadShared(env) {
  const exports = {};
  const context = { exports, URL, process: { env } };
  new vm.Script(transpile("src/app/api/auth/github/shared.ts")).runInNewContext(context);
  return exports;
}

function load(file, { session = { accessToken: cnpToken }, fetch = async () => {
  assert.fail("Unexpected backend request");
}, env = {}, authFailure = false } = {}) {
  const outputText = transpile(file);
  const environment = { AUTH_URL: origin, API_URL: "https://backend.invalid/api", ...env };
  const exports = {};
  const logs = [];
  const requests = [];
  const context = {
    exports, URL, AbortSignal, fetch: async (...args) => {
      requests.push(args);
      return fetch(...args);
    },
    process: { env: environment },
    console: Object.fromEntries(["log", "error", "warn", "info", "debug"].map(name => [name, (...args) => logs.push(args)])),
    require: name => {
      if (name === "@/auth") return { auth: async () => {
        if (authFailure) throw new Error("private-session-failure");
        return session;
      } };
      if (name === "next-auth/react") return { getSession: async () => session, signIn: () => assert.fail("Unexpected sign-in") };
      if (name === "next/server") return { NextRequest, NextResponse };
      if (name === "node:crypto") return dependency(name);
      if (name === "../shared") return loadShared(environment);
      if (name === "./stepUp") return { parseStepUpHint: () => null, startStepUp: () => assert.fail("Unexpected step-up") };
      throw new Error(`Unexpected route dependency ${name}`);
    },
  };
  new vm.Script(outputText, { filename: file }).runInNewContext(context);
  return { ...exports, logs, requests };
}

function handler(name, options) {
  return load(`src/app/api/auth/github/${name}/route.ts`, options);
}

function request(path = `${prefix}/link`, { method = "POST", input = {}, cookie = binding,
  requestOrigin = origin, headerOrigin = origin } = {}) {
  const headers = new Headers();
  if (headerOrigin !== null) headers.set("Origin", headerOrigin);
  if (cookie !== null) headers.set("Cookie", `${bindingName}=${cookie}`);
  if (method === "POST") headers.set("Content-Type", "application/json");
  return new NextRequest(new URL(path, requestOrigin), {
    method, headers, ...(method === "POST" ? { body: JSON.stringify(input) } : {}),
  });
}

function callbackRequest(query = `?state=fixture-state&code=${code}`, options = {}) {
  return request(`${prefix}/callback${query}`, { method: "GET", headerOrigin: null, ...options });
}

function assertPrivateResponse(response, secrets = [cnpToken, code]) {
  const headers = JSON.stringify([...response.headers]);
  for (const secret of secrets) assert.equal(headers.includes(secret), false);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
}

test("client start reaches the renamed protected route and binds its browser cookie", async () => {
  let payload;
  const route = handler("link", { fetch: async (url, options) => {
    assert.equal(url, "https://backend.invalid/api/account/github/oauth/start");
    assert.equal(options.headers.Authorization, `Bearer ${cnpToken}`);
    assert.equal(options.redirect, "error");
    assert.equal(options.cache, "no-store");
    assert.equal(options.signal instanceof AbortSignal, true);
    payload = JSON.parse(options.body);
    return json({ authorization_url: authorizeURL, access_token: cnpToken });
  } });
  let response;
  const api = load("src/lib/api.ts", { fetch: async (url, options) => {
    assert.equal(url, `${prefix}/link`);
    response = await route.POST(request(url, { input: JSON.parse(options.body) }));
    return response;
  } });
  const result = await api.startGitHubLink();
  assert.equal(result.authorization_url, authorizeURL);
  assert.equal(JSON.stringify(result).includes(cnpToken), false);
  const cookie = response.cookies.get(bindingName);
  assert.equal(payload.mode, "authorize");
  assert.equal(payload.binding, cookie.value);
  assert.match(cookie.value, /^[A-Za-z0-9_-]{64}$/);
  assert.equal(cookie.httpOnly, true);
  assert.equal(cookie.secure, true);
  assert.equal(cookie.sameSite, "lax");
  assert.equal(cookie.path, prefix);
  assert.equal(cookie.maxAge, 600);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(route.logs, []);
});

test("missing, null and foreign Origins cannot start OAuth even with a CNP session", async () => {
  for (const headerOrigin of [null, "null", "https://attacker.test", "https://other.cmp.test"]) {
    const route = handler("link");
    const result = await route.POST(request(undefined, { headerOrigin }));
    assert.equal(route.requests.length, 0);
    assert.equal(result.status, 403);
    assert.equal(result.cookies.get(bindingName), undefined);
  }
});

test("missing or expired CNP credentials cannot start or finish OAuth", async () => {
  for (const session of [null, {}, { accessToken: "" }, { accessToken: cnpToken, error: "RefreshAccessTokenError" }]) {
    const start = handler("link", { session });
    assert.equal((await start.POST(request())).status, 401);
    assert.equal(start.requests.length, 0);
    const finish = handler("callback", { session });
    const result = await finish.GET(callbackRequest());
    assert.equal(finish.requests.length, 0);
    assert.equal(result.headers.get("location"), `${origin}/account?github=failed`);
    assert.equal(result.cookies.get(bindingName).maxAge, 0);
    assertPrivateResponse(result);
  }
});

test("NEXTAUTH_URL keeps the public origin behind the existing HTTP proxy", async () => {
  const publicOrigin = "https://cmp.public.test";
  const env = { AUTH_URL: undefined, NEXTAUTH_URL: publicOrigin };
  const start = await handler("link", { env, fetch: async () => json({ authorization_url: authorizeURL }) }).POST(
    request(undefined, { requestOrigin: "http://127.0.0.1:3000", headerOrigin: publicOrigin }));
  assert.equal(start.status, 200);
  assert.equal(start.cookies.get(bindingName).secure, true);
  const result = await handler("callback", { env, fetch: async () => json({ identity_linked: true }) }).GET(
    callbackRequest(undefined, { requestOrigin: "http://127.0.0.1:3000" }));
  assert.equal(result.headers.get("location"), `${publicOrigin}/account?github=linked`);
  assert.equal(result.cookies.get(bindingName).secure, true);
  assertPrivateResponse(result);
});

test("installation and null request bodies are handled without trusting an installation hint", async () => {
  for (const input of [{ mode: "install", installation_id: "attacker-choice" }, null]) {
    const expectedMode = input ? "install" : "authorize";
    let payload;
    const result = await handler("link", { fetch: async (_url, options) => {
      payload = JSON.parse(options.body);
      return json({ authorization_url: input ? installationURL : authorizeURL });
    } }).POST(request(undefined, { input }));
    assert.equal(result.status, 200);
    assert.deepEqual(Object.keys(payload).sort(), ["binding", "mode"]);
    assert.equal(payload.mode, expectedMode);
  }
  assert.equal((await handler("link").POST(request(undefined, { input: { mode: "delete" } }))).status, 422);
});

const unsafeURLs = ["https://attacker.test/login/oauth/authorize", "http://github.com/login/oauth/authorize",
  "https://github.com/login/oauth/authorize#private", "https://user:password@github.com/login/oauth/authorize",
  "https://github.com/somewhere-else", "javascript:alert(1)"];
for (const [index, authorization_url] of unsafeURLs.entries()) {
  test(`unsafe OAuth destination ${index + 1} is refused at start and callback`, async () => {
    const options = { fetch: async () => json({ authorization_url }) };
    const start = await handler("link", options).POST(request());
    assert.equal(start.status, 503);
    assert.equal(start.cookies.get(bindingName), undefined);
    const result = await handler("callback", options).GET(callbackRequest());
    assert.equal(result.headers.get("location"), `${origin}/account?github=failed`);
    assert.equal(result.cookies.get(bindingName).maxAge, 0);
    assertPrivateResponse(result, [cnpToken, code, "password", "#private"]);
  });
}

test("callback forwards only code/state/binding to its authenticated backend", async () => {
  let payload;
  const route = handler("callback", { fetch: async (url, options) => {
    assert.equal(url, "https://backend.invalid/api/account/github/oauth/callback");
    assert.equal(options.method, "POST");
    assert.equal(options.headers.Authorization, `Bearer ${cnpToken}`);
    assert.equal(options.redirect, "error");
    payload = JSON.parse(options.body);
    return json({ identity_linked: true, access_token: "private-upstream-token" });
  } });
  const result = await route.GET(callbackRequest(`?state=fixture-state&code=${code}&installation_id=999&setup_action=install`));
  assert.deepEqual(payload, { code, state: "fixture-state", binding });
  assert.equal(result.status, 303);
  assert.equal(result.headers.get("location"), `${origin}/account?github=linked`);
  assert.equal(result.cookies.get(bindingName).maxAge, 0);
  assertPrivateResponse(result, [cnpToken, code, "private-upstream-token", "installation_id"]);
  assert.deepEqual(route.logs, []);
});

test("expired browser binding, missing state or OAuth denial never reaches the backend", async () => {
  for (const incoming of [callbackRequest(undefined, { cookie: null }),
    callbackRequest(`?code=${code}`), callbackRequest(`?state=fixture-state&code=${code}&error=access_denied`)]) {
    const route = handler("callback");
    const result = await route.GET(incoming);
    assert.equal(route.requests.length, 0);
    assert.equal(result.headers.get("location"), `${origin}/account?github=failed`);
    assert.equal(result.cookies.get(bindingName).maxAge, 0);
    assertPrivateResponse(result);
  }
});

test("installation return continues into fresh PKCE and retains only the browser binding", async () => {
  let payload;
  const result = await handler("callback", { fetch: async (_url, options) => {
    payload = JSON.parse(options.body);
    return json({ authorization_url: authorizeURL });
  } }).GET(callbackRequest("?state=installation-state&installation_id=999"));
  assert.deepEqual(payload, { code: null, state: "installation-state", binding });
  assert.equal(result.headers.get("location"), authorizeURL);
  assert.equal(result.cookies.get(bindingName).value, binding);
  assert.equal(result.cookies.get(bindingName).maxAge, 600);
  assert.equal(result.cookies.get(bindingName).path, prefix);
  assertPrivateResponse(result);
});

for (const status of [302, 307, 401, 403, 503]) {
  test(`backend refusal ${status} cannot turn into success or expose credentials`, async () => {
    const fetch = async (_url, options) => {
      assert.equal(options.redirect, "error");
      return json({ access_token: "private-upstream-body", code }, status);
    };
    const start = handler("link", { fetch });
    const resultStart = await start.POST(request());
    assert.equal(resultStart.status, 503);
    assert.equal((await resultStart.text()).includes("private-upstream-body"), false);
    const finish = handler("callback", { fetch });
    const result = await finish.GET(callbackRequest());
    assert.equal(result.headers.get("location"), `${origin}/account?github=failed`);
    assert.equal(result.cookies.get(bindingName).maxAge, 0);
    assertPrivateResponse(result, [cnpToken, code, "private-upstream-body"]);
    assert.deepEqual([...start.logs, ...finish.logs], []);
  });
}

test("transport rejection remains generic and unknown success flags never claim a link", async () => {
  const route = handler("callback", { fetch: async () => { throw new Error("private-upstream-error"); } });
  const result = await route.GET(callbackRequest());
  assert.equal(result.headers.get("location"), `${origin}/account?github=failed`);
  assertPrivateResponse(result, [cnpToken, code, "private-upstream-error"]);
  assert.deepEqual(route.logs, []);
  const unknown = await handler("callback", { fetch: async () => json({ identity_linked: "true" }) }).GET(callbackRequest());
  assert.equal(unknown.headers.get("location"), `${origin}/account?github=failed`);
});


test("CNP session service failure stays generic and callback still clears its binding", async () => {
  const start = handler("link", { authFailure: true });
  const resultStart = await start.POST(request());
  assert.ok([401, 503].includes(resultStart.status));
  assert.equal((await resultStart.text()).includes("private-session-failure"), false);
  assert.equal(start.requests.length, 0);
  const finish = handler("callback", { authFailure: true });
  const result = await finish.GET(callbackRequest());
  assert.equal(result.headers.get("location"), `${origin}/account?github=failed`);
  assert.equal(result.cookies.get(bindingName).maxAge, 0);
  assert.equal(finish.requests.length, 0);
  assertPrivateResponse(result, [cnpToken, code, "private-session-failure"]);
  assert.deepEqual([...start.logs, ...finish.logs], []);
});
