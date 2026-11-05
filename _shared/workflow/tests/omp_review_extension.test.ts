// Source-only deterministic contract doubles. No native SDK, session, model or approval is exercised.
import { afterEach, expect, test } from "bun:test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { pathToFileURL } from "node:url";
import { AckBroker, OmpAdapter, reviewExtensionSources, sha256, validateContext, type ExecutionContext, type Reply, type Request } from "../harness/omp/adapter";

const repository = path.resolve(import.meta.dir, "../../..");
const temporary: string[] = [], registries: string[] = [];
afterEach(() => {
  for (const dir of temporary.splice(0)) fs.rmSync(dir, {recursive: true, force: true});
  for (const key of registries.splice(0)) delete (globalThis as any)[key];
});
function write(file: string, text: string) { fs.mkdirSync(path.dirname(file), {recursive: true}); fs.writeFileSync(file, text); }
function json(file: string, value: unknown) { write(file, JSON.stringify(value)); }
function bindSources(context: ExecutionContext) {
  const adapter = context.source_refs.find(reference => reference.path.endsWith(`${path.sep}adapter.ts`))!.path;
  const runtime = path.resolve(path.dirname(adapter), "../..");
  const declaration = JSON.parse(fs.readFileSync(context.declaration_path, "utf8"));
  const manifest = JSON.parse(fs.readFileSync(context.install_manifest_path, "utf8")), root = declaration.omp_managed.install_root;
  const files = [adapter, ...["controller.py", "workflow.py", "contracts.py", "store.py", "publication.py"].map(name => path.join(runtime, name)),
    context.declaration_path, context.install_manifest_path, ...(context.config_files ?? []),
    ...[manifest.installer.runtime_relative, manifest.executable_relative, manifest.payload_relative,
      `${manifest.package_tree_relative}/package.json`].map(relative => path.join(root, relative)),
    ...(context.review_extension ? [...reviewExtensionSources(context.review_extension).map(reference => reference.path),
      declaration.tokens.__OMP_PYTHON__] : [])];
  const media: Record<string, string> = {".json": "application/json", ".py": "text/x-python", ".ts": "text/typescript", ".md": "text/markdown"};
  context.source_refs = files.map(file => ({path: file, sha256: sha256(fs.readFileSync(file)), media_type: media[path.extname(file)] ?? "application/octet-stream"}));
}
async function loadNative(context: ExecutionContext, executingPath?: string) {
  const adapter = path.join(context.cwd, ".agents/skills/_workflow/harness/omp/adapter.ts");
  const implementation = await import(pathToFileURL(adapter).href);
  // Source-only process-path observation double; no executable is launched.
  const previous = process.execPath;
  process.execPath = executingPath ?? path.join(path.dirname(path.dirname(path.dirname(context.declaration_path))), "inert-install/bun.exe");
  try { return await implementation.loadNative(context); }
  finally { process.execPath = previous; }
}
function fixture() {
  const home = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "review-export-source-only-")));
  temporary.push(home);
  const entries = JSON.parse(fs.readFileSync(path.join(repository, "deploy/exports.json"), "utf8")).entries
    .filter((entry: any) => entry.source.startsWith("discriminator/"));
  expect(entries).toHaveLength(29);
  for (const entry of entries) {
    const destination = path.join(home, path.relative("harness", entry.destination));
    fs.mkdirSync(path.dirname(destination), {recursive: true});
    fs.copyFileSync(path.join(repository, entry.source), destination);
  }
  const extension = path.join(home, ".omp/shared/extensions/discriminator/index.ts");
  const reference = {path: extension, sha256: sha256(fs.readFileSync(extension)), media_type: "text/typescript" as const};
  const runtime = path.join(home, ".agents/skills/_workflow"), adapter = path.join(runtime, "harness/omp/adapter.ts");
  write(adapter, fs.readFileSync(path.join(import.meta.dir, "../harness/omp/adapter.ts"), "utf8"));
  for (const name of ["controller.py", "workflow.py", "contracts.py", "store.py", "publication.py"]) write(path.join(runtime, name), "# inert source-only controller closure\n");
  const context: ExecutionContext = {schema_version: 1, declaration_path: path.join(home, ".agents/agent-skills/runtime.json"),
    install_manifest_path: path.join(home, ".agents/agent-skills/omp-install.json"), cwd: home, session_dir: path.join(home, "sessions"),
    evidence_dir: path.join(home, "evidence"), source_refs: [{path: adapter, sha256: sha256(fs.readFileSync(adapter)), media_type: "text/typescript"}]};
  const owned = entries.filter((entry: any) => entry.destination.startsWith("harness/.agents/skills/discriminator/"))
    .map((entry: any) => path.join(home, path.relative("harness", entry.destination)));
  return {home, extension, reference, context, adapter, runtime, owned, skill: path.join(home, ".agents/skills/discriminator")};
}

test("actual parser binds generated suffix, closed InputRef and complete source population", () => {
  const f = fixture();
  expect(validateContext(f.context).review_extension).toBeUndefined();
  expect(validateContext({...f.context, review_extension: f.reference}).review_extension).toEqual(f.reference);
  const sources = reviewExtensionSources(f.reference);
  expect(sources).toHaveLength(28);
  expect(new Set(sources.map(source => source.path))).toEqual(new Set([f.extension, ...f.owned]));
  expect(sources[0]).toEqual({path: f.extension, sha256: f.reference.sha256});
  expect(sources.slice(1).map(item => item.path)).toEqual(sources.slice(1).map(item => item.path).sort());
  const foreign = path.join(f.home, "foreign/index.ts");
  write(foreign, fs.readFileSync(f.extension, "utf8"));
  for (const reference of [{...f.reference, extra: true}, {...f.reference, path: "relative.ts"},
    {...f.reference, sha256: "0".repeat(64)}, {...f.reference, sha256: f.reference.sha256.toUpperCase()},
    {...f.reference, media_type: "application/javascript"}, {...f.reference, path: foreign}]) {
    expect(() => validateContext({...f.context, review_extension: reference})).toThrow();
  }
});

test("missing owned helper, rubric, ordinary resource and directory extension refuse", () => {
  const f = fixture();
  write(path.join(f.skill, "retained-user-notes.txt"), "not an owned resource substitute");
  for (const name of ["native_review.py", "assets/rubric.lock.json", "contracts/code-service.md"]) {
    const file = path.join(f.skill, name), original = fs.readFileSync(file);
    fs.unlinkSync(file);
    expect(() => reviewExtensionSources(f.reference)).toThrow();
    fs.writeFileSync(file, original);
  }
  fs.unlinkSync(f.extension); fs.mkdirSync(f.extension);
  expect(() => reviewExtensionSources(f.reference)).toThrow("REVIEW_EXTENSION_CHANGED_OR_FOREIGN");
});

test("symlink at an owned resource is rejected when the platform permits creating one", () => {
  const f = fixture(), link = path.join(f.skill, "native_review.py");
  fs.unlinkSync(link);
  try { fs.symlinkSync(f.extension, link, "file"); }
  catch (error: any) { if (["EPERM", "EACCES", "ENOSYS", "ENOTSUP"].includes(error.code)) return; throw error; }
  expect(() => reviewExtensionSources(f.reference)).toThrow("REVIEW_EXTENSION_EXTERNAL_RESOURCE");
});

test("a linked owned resource directory is rejected without following its children", () => {
  const f = fixture(), assets = path.join(f.skill, "assets"), retained = path.join(f.home, "retained-assets");
  fs.renameSync(assets, retained);
  try { fs.symlinkSync(retained, assets, process.platform === "win32" ? "junction" : "dir"); }
  catch (error: any) { if (["EPERM", "EACCES", "ENOSYS", "ENOTSUP"].includes(error.code)) return; throw error; }
  expect(() => reviewExtensionSources(f.reference)).toThrow("REVIEW_EXTENSION_EXTERNAL_RESOURCE");
});

test("unmanaged notes, tests and caches stay outside review and native source closures", async () => {
  const f = await syntheticSdk("explicit"), baseline = reviewExtensionSources(f.reference);
  const bound = structuredClone(f.context.source_refs);
  const neighbors = ["retained-user-notes.txt", "__pycache__/user-cache.pyc", "tests/user-fixture.py"]
    .map(name => ({file: path.join(f.skill, name), content: `retained unmanaged bytes: ${name}`}));
  for (const neighbor of neighbors) write(neighbor.file, neighbor.content);
  expect(reviewExtensionSources(f.reference)).toEqual(baseline);
  const runtime = await loadNative(f.context);
  expect(runtime.fingerprint.review_extension_sources).toEqual(baseline);
  expect(f.context.source_refs).toEqual(bound);
  for (const neighbor of neighbors) {
    expect(bound.some(reference => reference.path === neighbor.file)).toBe(false);
    expect(fs.readFileSync(neighbor.file, "utf8")).toBe(neighbor.content);
  }
  await runtime.dispose();
});

type ReviewSelection = "explicit" | "default" | "task";
async function syntheticSdk(selection: ReviewSelection, fault?: {missingExport?: string; malformedApi?: string; missingSetting?: string;
  rootHelpers?: boolean; structured?: "absent" | "boolean" | "malformed"; selectedModel?: string | null;
  reviewTool?: "missing" | "noncallable" | "foreign_schema";
  reviewOwner?: "foreign" | "resolved_foreign" | "inactive" | "duplicate" | "missing";
  reviewRoots?: "foreign" | "discovery" | "extra"; reviewSchema?: "images" | "absent" | "scalar"}) {
  const f = fixture(), root = path.join(f.home, "inert-install"), tree = path.join(root, "package");
  // Only public export names are contractual; no internal source tree exists.
  // Task sessions expose an ambient double, which must never satisfy review admission.
  const sdkEntry = path.join(tree, "public/session.ts"), key = `omp-source-only:${f.home}`;
  const sdk = {calls: [] as Record<string, unknown>[], schemaCalls: [] as Record<string, unknown>[], loaded: false,
    settingsCalls: 0, managerCalls: 0, disposals: 0, executions: 0};
  (globalThis as Record<string, unknown>)[key] = sdk; registries.push(key);
  const selectedModel = fault?.selectedModel === null ? null : fault?.selectedModel === undefined ? undefined :
    {provider: fault.selectedModel.slice(0, fault.selectedModel.indexOf("/")), id: fault.selectedModel.slice(fault.selectedModel.indexOf("/") + 1)};
  const foreign = path.join(f.home, "ambient/index.ts");
  const roots = {mode: fault?.reviewRoots === "discovery" ? "merge" : "explicit-only",
    explicit: fault?.reviewRoots === "extra" ? [f.extension, foreign] : [fault?.reviewRoots === "foreign" ? foreign : f.extension]};
  const sessionBody = `async (options: Record<string, unknown>) => {
  calls.push(options);
  const parameters = () => {}, definition = {name: 'adversarial_review', parameters,
    execute: async () => {observation.executions++; throw new Error('must not dispatch');}};
  const registered = {definition, extensionPath: ${JSON.stringify(fault?.reviewOwner === "foreign" ? foreign : f.extension)}};
  const owner = {path: ${JSON.stringify(f.extension)}, resolvedPath: ${JSON.stringify(fault?.reviewOwner === "resolved_foreign" ? foreign : f.extension)},
    tools: new Map([['adversarial_review', registered]])};
  const tool = ${fault?.reviewTool === "missing" ? "undefined" : fault?.reviewTool === "noncallable" ? "{...definition, execute: null}" : fault?.reviewTool === "foreign_schema" ? "{...definition, parameters: () => {}}" : "definition"};
  return {session: {model: ${JSON.stringify(selectedModel ?? null)}, getAgentId: () => 'Main', asyncJobManager: {getAllJobs: () => [], consumeJobResults: () => 0, unwatchJobs: () => 0},
    sessionManager: options.sessionManager, getSessionAgents: () => [],
    effectiveExtensionRoots: options.disableExtensionDiscovery ? ${JSON.stringify(roots)} : {mode: 'merge', explicit: [${JSON.stringify(foreign)}]},
    extensionRunner: {getAllRegisteredTools: () => ${fault?.reviewOwner === "duplicate" ? "[registered, {...registered, extensionPath: " + JSON.stringify(foreign) + "}]" : "[registered]"},
      getLoadedExtensions: () => ${fault?.reviewOwner === "missing" ? "[]" : "[owner]"}, isExtensionActive: () => ${fault?.reviewOwner !== "inactive"}},
    getToolForEvalBridge: () => tool, dispose: async () => {observation.disposals++;}}};
}`;
  write(sdkEntry, `const observation = (globalThis as Record<string, unknown>)[${JSON.stringify(key)}] as {loaded: boolean; calls: Record<string, unknown>[]; settingsCalls: number; managerCalls: number; disposals: number; executions: number}; observation.loaded = true;
export const calls = observation.calls;
export const Settings = {loadReadOnly: async () => {observation.settingsCalls++; return {overlay: (value: unknown) => value};}};
export const SessionManager = {create: () => {observation.managerCalls++; return {ensureOnDisk: async () => {}, flush: async () => {},
  getSessionId: () => 'source-only-session', getSessionFile: () => ${JSON.stringify(path.join(f.home, "source-only-session.jsonl"))}};}};
export const createAgentSession = ${fault?.malformedApi === "createAgentSession" ? "null" : selectedModel === undefined ? "async (options: Record<string, unknown>) => {calls.push(options); throw new Error('SOURCE_ONLY_SESSION_BOUNDARY');}" : sessionBody};
`);
  const exports: Record<string, string> = {".": "./public/session.ts"};
  for (const [subpath, api, implementation] of [
    ["modes/runtime-init", "initializeExtensions", "async () => {}"],
    ["task/discovery", "discoverAgents", "() => {throw new Error('unreachable');}"],
    ["task/agents", "getBundledAgent", "() => {throw new Error('unreachable');}"],
    ["eval/py/tool-bridge", "disposePyToolBridge", "async () => {}"],
    ["config/registry", "lookup", `(id: string) => id === ${JSON.stringify(fault?.missingSetting ?? "")} ? undefined : id === 'task.structured' ? ${fault?.structured === "absent" ? "undefined" : fault?.structured === "malformed" ? "{definition: {type: 'string'}}" : "{definition: {type: 'boolean'}}"} : {definition: {type: 'boolean'}}`],
  ]) {
    const entry = `./public/${api}.ts`;
    write(path.join(tree, entry), `export const ${api} = ${fault?.malformedApi === api ? "null" : implementation};\n`);
    if (fault?.rootHelpers) fs.appendFileSync(sdkEntry, `export {${api}} from './${api}.ts';\n`);
    else if (fault?.missingExport !== api) exports[`./${subpath}`] = entry;
  }
  const core = path.join(root, "node_modules/@oh-my-pi/pi-agent-core");
  json(path.join(core, "package.json"), {name: "@oh-my-pi/pi-agent-core", type: "module", exports: {".": "./index.ts"}});
  write(path.join(core, "index.ts"), "export function validateAgentToolArguments(_tool: unknown, call: {arguments: unknown}) { return call.arguments; }");
  const ai = path.join(root, "node_modules/@oh-my-pi/pi-ai");
  json(path.join(ai, "package.json"), {name: "@oh-my-pi/pi-ai", type: "module", exports: {".": "./index.ts"}});
  const imageSchema = {type: "array", minItems: 1, items: {type: "object", additionalProperties: false,
    required: ["path", "sha256", "media_type"], properties: {path: {type: "string"}, sha256: {type: "string", pattern: "^[0-9a-f]{64}$"},
      media_type: {enum: ["image/png", "image/jpeg", "image/gif", "image/webp"]}}}};
  const schema = {type: "object", properties: fault?.reviewSchema === "images" ? {images: imageSchema} : fault?.reviewSchema === "scalar" ? {images: {type: "string"}} : {}};
  write(path.join(ai, "index.ts"), `export const toolWireSchema = ${fault?.malformedApi === "toolWireSchema" ? "null" : fault?.reviewSchema === undefined ? "() => {throw new Error('unreachable');}" : `(tool: {name: string; parameters: unknown}) => {const observation = (globalThis as Record<string, unknown>)[${JSON.stringify(key)}] as {schemaCalls: Record<string, unknown>[]}; observation.schemaCalls.push({name: tool.name, parameters_type: typeof tool.parameters}); return ${JSON.stringify(schema)};}`};`);
  json(path.join(tree, "package.json"), {name: "@oh-my-pi/pi-coding-agent", type: "module", exports});
  for (const name of ["bun.exe", "omp.exe", "payload.js"]) write(path.join(root, name), "never executed");
  const declared = selection === "explicit" ? {review_extension: f.reference} : {};
  const declaration = {tokens: {__USERPROFILE__: f.home, __OMP_PYTHON__: path.join(f.home, "python.exe")},
    omp_managed: {enabled: true, install_root: root}, workflow_native: {omp: declared}};
  if (selection !== "task") write(declaration.tokens.__OMP_PYTHON__, "never executed helper interpreter");
  json(f.context.declaration_path, declaration);
  const manifest = {schema: 1, package: "@oh-my-pi/pi-coding-agent", package_tree_relative: "package",
    executable_relative: "omp.exe", payload_relative: "payload.js", installer: {runtime_relative: "bun.exe"}};
  json(f.context.install_manifest_path, manifest);
  if (selection !== "task") f.context.review_extension = f.reference;
  bindSources(f.context);
  return {...f, root, tree, sdkEntry, declaration, manifest, sdk, imageSchema};
}

test("explicit and derived-default reviews are exact-source-only while task-only routes refuse review before SDK preparation", async () => {
  for (const selection of ["explicit", "default", "task"] as const) {
    const f = await syntheticSdk(selection), retained = structuredClone(f.context), runtime = await loadNative(validateContext(f.context));
    const bound = selection !== "task";
    expect(runtime.fingerprint.review_extension).toEqual(bound ? f.reference : null);
    expect(runtime.fingerprint.review_extension_sources).toEqual(bound ? reviewExtensionSources(f.reference) : []);
    await expect(runtime.createSession(async () => ({}), () => {}, {review: false})).rejects.toThrow("SOURCE_ONLY_SESSION_BOUNDARY");
    expect(Object.hasOwn(f.sdk.calls[0], "additionalExtensionPaths")).toBe(false);
    expect(Object.hasOwn(f.sdk.calls[0], "disableExtensionDiscovery")).toBe(false);
    expect(Object.hasOwn(f.sdk.calls[0], "modelPattern")).toBe(false);
    const settings = f.sdk.settingsCalls, managers = f.sdk.managerCalls;
    await expect(runtime.createSession(async () => ({}), () => {}, {review: true})).rejects.toThrow(bound ? "SOURCE_ONLY_SESSION_BOUNDARY" : "SOURCE_REVIEW_EXTENSION_REQUIRED");
    if (bound) {
      expect(f.sdk.calls[1].additionalExtensionPaths).toEqual([f.extension]);
      expect(f.sdk.calls[1].disableExtensionDiscovery).toBe(true);
      expect(Object.hasOwn(f.sdk.calls[1], "modelPattern")).toBe(false);
    } else {
      expect(f.sdk.calls).toHaveLength(1);
      expect(f.sdk.settingsCalls).toBe(settings); expect(f.sdk.managerCalls).toBe(managers);
    }
    expect(f.context).toEqual(retained);
    expect(JSON.parse(fs.readFileSync(f.context.declaration_path, "utf8"))).toEqual(f.declaration);
    await runtime.dispose();
  }
});

test("declaration mismatch refuses before SDK selection; stale closure refuses before SDK session boundary", async () => {
  const f = await syntheticSdk("explicit"), {review_extension: _extension, ...withoutExtension} = f.context;
  await expect(loadNative(withoutExtension)).rejects.toThrow("NATIVE_REVIEW_EXTENSION_MISMATCH");
  expect(f.sdk.loaded).toBe(false);
  const runtime = await loadNative(f.context);
  const helper = path.join(f.skill, "native_review.py");
  fs.appendFileSync(helper, "\n# source-only changed closure\n");
  await expect(runtime.createSession(async () => ({}), () => {}, {review: true})).rejects.toThrow("SOURCE_REF_CHANGED");
  expect(f.sdk.calls).toHaveLength(0);
  await runtime.dispose();
});

test("derived defaults cannot select another home and explicit stale declarations never refresh", async () => {
  const derived = await syntheticSdk("default"), foreign = fixture();
  derived.context.review_extension = foreign.reference;
  bindSources(derived.context);
  await expect(loadNative(derived.context)).rejects.toThrow("NATIVE_REVIEW_EXTENSION_MISMATCH");
  expect(derived.sdk.loaded).toBe(false);
  const explicit = await syntheticSdk("explicit");
  fs.appendFileSync(explicit.extension, "\n// updated generated extension\n");
  explicit.context.review_extension = {...explicit.reference, sha256: sha256(fs.readFileSync(explicit.extension))};
  bindSources(explicit.context);
  const retained = structuredClone(explicit.context);
  await expect(loadNative(explicit.context)).rejects.toThrow("NATIVE_REVIEW_EXTENSION_MISMATCH");
  expect(explicit.sdk.loaded).toBe(false);
  expect(explicit.context).toEqual(retained);
  expect(JSON.parse(fs.readFileSync(explicit.context.declaration_path, "utf8"))).toEqual(explicit.declaration);
});

test("a task-only session's callable ambient review and image schema cannot authorize a review session", async () => {
  const f = await syntheticSdk("task", {selectedModel: "provider/model", reviewSchema: "images"}), runtime = await loadNative(f.context);
  const task = await runtime.createSession(async () => ({}), () => {}, {review: false});
  expect(typeof task.session.getToolForEvalBridge("adversarial_review")?.execute).toBe("function");
  expect(task.startup.review_registration).toBeNull();
  expect(task.session.effectiveExtensionRoots.mode).toBe("merge");
  const settings = f.sdk.settingsCalls, managers = f.sdk.managerCalls;
  await expect(runtime.createSession(async () => ({}), () => {}, {review: true})).rejects.toThrow("SOURCE_REVIEW_EXTENSION_REQUIRED");
  expect(f.sdk.settingsCalls).toBe(settings); expect(f.sdk.managerCalls).toBe(managers);
  expect(f.sdk.calls).toHaveLength(1); expect(f.sdk.schemaCalls).toEqual([]); expect(f.sdk.executions).toBe(0);
  await task.session.dispose(); await runtime.dispose();
});

for (const member of ["extension", "native_review.py", "python"]) {
  test(`frozen derived-default ${member} tamper refuses before SDK session preparation`, async () => {
    const f = await syntheticSdk("default"), runtime = await loadNative(f.context), retained = structuredClone(f.context);
    const file = member === "extension" ? f.extension : member === "python" ? f.declaration.tokens.__OMP_PYTHON__ : path.join(f.skill, member);
    fs.appendFileSync(file, "\n# source-only tamper\n");
    await expect(runtime.createSession(async () => ({}), () => {}, {review: true})).rejects.toThrow("SOURCE_REF_CHANGED");
    expect(f.sdk.settingsCalls).toBe(0); expect(f.sdk.managerCalls).toBe(0); expect(f.sdk.calls).toEqual([]);
    expect(f.context).toEqual(retained); await runtime.dispose();
  });
  for (const boundary of ["launch", "validation", "committed_ack"] as const) test(`derived-default ${member} tamper at ${boundary} never submits the bound tool`, async () => {
    const f = await syntheticSdk("default", {selectedModel: "provider/model", reviewSchema: "images"}), runtime = await loadNative(f.context);
    const retained = structuredClone(f.context), file = member === "extension" ? f.extension : member === "python" ? f.declaration.tokens.__OMP_PYTHON__ : path.join(f.skill, member);
    const tamper = () => fs.appendFileSync(file, "\n# changed frozen review input\n");
    if (boundary === "validation") {
      const validate = runtime.validate;
      runtime.validate = (...args) => {const result = validate(...args); tamper(); return result;};
    }
    let emitted: Reply | undefined;
    const broker = new AckBroker(value => {emitted = value;}), adapter = new OmpAdapter(f.context, runtime, broker);
    const request = (operation: string, payload: Record<string, unknown> = {}): Request => ({request_id: operation,
      run_id: "run", attempt_id: "attempt", owner_epoch: 1, operation, payload});
    const prepared = await adapter.handle(request("prepare", {review: {caller_family: "gpt", contract: "code-service", source: path.join(f.home, "artifact.txt"), focus: ""}}));
    expect(prepared.status).toBe("prepared");
    expect(f.sdk.calls[0].additionalExtensionPaths).toEqual([f.extension]); expect(f.sdk.calls[0].disableExtensionDiscovery).toBe(true);
    if (boundary === "launch") {
      tamper();
      await expect(adapter.handle(request("launch_with_binding_barrier"))).rejects.toThrow("SOURCE_REF_CHANGED");
    } else {
      await adapter.handle(request("launch_with_binding_barrier"));
      expect(emitted?.status).toBe("binding_required");
      if (boundary === "committed_ack") tamper();
      broker.acknowledge({...request("binding_ack", {committed: true, binding: emitted!.payload.binding}), request_id: "launch_with_binding_barrier"});
      await Bun.sleep(0);
      const observed = await adapter.handle(request("observe"));
      expect(observed.status).toBe("blocked"); expect(observed.error?.code).toBe("SOURCE_REF_CHANGED");
      expect(observed.payload.submitted).toBe(false);
    }
    expect(f.sdk.executions).toBe(0); expect(f.context).toEqual(retained);
    await adapter.close();
  });
}

test("runtime fingerprints describe observations and contain no SDK identity allowlist", async () => {
  const f = await syntheticSdk("task"), runtime = await loadNative(validateContext(f.context));
  expect(runtime.packageRoot).toBe(fs.realpathSync(f.tree));
  expect(Object.keys(runtime.fingerprint).sort()).toEqual(["model_hint", "agent_dir", "config_files", "review_extension",
    "review_extension_sources", "adapter_sha256", "declaration_sha256", "manifest_sha256"].sort());
  expect(runtime.fingerprint.model_hint).toBeNull();
  expect(runtime.fingerprint.agent_dir).toBeNull();
  expect(runtime.fingerprint.config_files).toEqual([]);
  expect(runtime.fingerprint.adapter_sha256).toBe(sha256(fs.readFileSync(path.join(import.meta.dir, "../harness/omp/adapter.ts"))));
  expect(runtime.fingerprint.declaration_sha256).toBe(sha256(fs.readFileSync(f.context.declaration_path)));
  expect(runtime.fingerprint.manifest_sha256).toBe(sha256(fs.readFileSync(f.context.install_manifest_path)));
  expect(fs.existsSync(path.join(f.tree, "src"))).toBe(false);
  await runtime.dispose();
});

test("same-named public root helpers need no extra subpath exports", async () => {
  const f = await syntheticSdk("task", {rootHelpers: true}), runtime = await loadNative(f.context);
  await expect(runtime.createSession(async () => ({}), () => {})).rejects.toThrow("SOURCE_ONLY_SESSION_BOUNDARY");
  expect(f.sdk.calls).toHaveLength(1);
  await runtime.dispose();
  const malformed = await syntheticSdk("task", {rootHelpers: true, malformedApi: "initializeExtensions"});
  await expect(loadNative(malformed.context)).rejects.toThrow("NATIVE_API_UNAVAILABLE");
  expect(malformed.sdk.calls).toHaveLength(0);
});

test("model and native settings selection remain source-policy bound and reach the SDK", async () => {
  const f = await syntheticSdk("task"), config = path.join(f.home, "managed.config.yml");
  write(config, "modelRoles: {}\n");
  f.context.model = "provider/model";
  f.context.agent_dir = f.home;
  f.context.config_files = [config];
  json(f.context.declaration_path, {...f.declaration, workflow_native: {omp: {model: f.context.model, agent_dir: f.home, config_files: [config]}}});
  bindSources(f.context);
  const runtime = await loadNative(validateContext(f.context));
  expect(runtime.fingerprint.model_hint).toBe(f.context.model);
  expect(runtime.fingerprint.agent_dir).toBe(path.normalize(f.home));
  expect(runtime.fingerprint.config_files).toEqual([{path: path.normalize(config), sha256: sha256(fs.readFileSync(config))}]);
  await expect(runtime.createSession(async () => ({}), () => {})).rejects.toThrow("SOURCE_ONLY_SESSION_BOUNDARY");
  expect(f.sdk.calls[0].agentDir).toBe(f.home);
  expect(f.sdk.calls[0].modelPattern).toBe(f.context.model);
  await runtime.dispose();
  write(config, "modelRoles: {reviewer: explicit}\n");
  await expect(loadNative(f.context)).rejects.toThrow("SOURCE_REF_CHANGED");
  bindSources(f.context);
  const changed = await loadNative(f.context);
  expect(changed.fingerprint.config_files).not.toEqual(runtime.fingerprint.config_files);
  await changed.dispose();
});

test("missing public exports and malformed required APIs refuse before a session", async () => {
  for (const api of ["initializeExtensions", "discoverAgents", "getBundledAgent", "disposePyToolBridge", "lookup"]) {
    const absent = await syntheticSdk("task", {missingExport: api});
    await expect(loadNative(absent.context)).rejects.toThrow("NATIVE_API_UNAVAILABLE");
    expect(absent.sdk.calls).toHaveLength(0);
  }
  for (const api of ["createAgentSession", "initializeExtensions", "discoverAgents", "getBundledAgent", "disposePyToolBridge", "lookup"]) {
    const malformed = await syntheticSdk("task", {malformedApi: api});
    await expect(loadNative(malformed.context)).rejects.toThrow("NATIVE_API_UNAVAILABLE");
    expect(malformed.sdk.calls).toHaveLength(0);
  }
  const review = await syntheticSdk("explicit", {malformedApi: "toolWireSchema"});
  await expect(loadNative(review.context)).rejects.toThrow("NATIVE_REVIEW_SCHEMA_API_UNAVAILABLE");
  expect(review.sdk.calls).toHaveLength(0);
});

test("required native boolean settings must actually be registered", async () => {
  for (const missingSetting of ["task.batch", "async.enabled"]) {
    const f = await syntheticSdk("task", {missingSetting});
    await expect(loadNative(f.context)).rejects.toThrow("NATIVE_API_UNAVAILABLE");
    expect(f.sdk.calls).toHaveLength(0);
  }
});

test("registered structured setting is enabled and absent setting is omitted", async () => {
  for (const structured of ["absent", "boolean", "malformed"] as const) {
    const f = await syntheticSdk("task", {structured});
    if (structured === "malformed") {
      await expect(loadNative(f.context)).rejects.toThrow("NATIVE_API_UNAVAILABLE");
      expect(f.sdk.calls).toHaveLength(0);
      continue;
    }
    const runtime = await loadNative(f.context);
    await expect(runtime.createSession(async () => ({}), () => {})).rejects.toThrow("SOURCE_ONLY_SESSION_BOUNDARY");
    expect(f.sdk.calls[0].settings).toEqual({"task.batch": true, "async.enabled": true, ...(structured === "boolean" ? {"task.structured": true} : {})});
    await runtime.dispose();
  }
});

test("manifest traversal and public export physical escape refuse", async () => {
  const f = await syntheticSdk("task");
  json(f.context.install_manifest_path, {...f.manifest, package_tree_relative: "../foreign"});
  f.context.source_refs.find(reference => reference.path === f.context.install_manifest_path)!.sha256 = sha256(fs.readFileSync(f.context.install_manifest_path));
  await expect(loadNative(f.context)).rejects.toThrow("MANIFEST_PATH_ESCAPE");
  expect(f.sdk.calls).toHaveLength(0);
  json(f.context.install_manifest_path, f.manifest);
  bindSources(f.context);
  const outside = path.join(f.home, "foreign-session.ts");
  write(outside, fs.readFileSync(f.sdkEntry, "utf8"));
  fs.unlinkSync(f.sdkEntry);
  try { fs.symlinkSync(outside, f.sdkEntry, "file"); }
  catch (error: any) { if (["EPERM", "EACCES", "ENOSYS", "ENOTSUP"].includes(error.code)) return; throw error; }
  await expect(loadNative(f.context)).rejects.toThrow("NATIVE_SOURCE_ESCAPE");
  expect(f.sdk.calls).toHaveLength(0);
});

for (const selection of ["explicit", "default"] as const) {
  // Each mode checks 42 real filesystem admissions; batch05's combined table took 9659ms.
  test(`${selection === "default" ? "derived-default" : selection} contexts require every controller, declaration, runtime, settings, discriminator and Python ref before SDK import`, async () => {
    const f = await syntheticSdk(selection), config = path.join(f.home, "managed.yml");
    write(config, "modelRoles: {}\n"); f.context.config_files = [config];
    json(f.context.declaration_path, {...f.declaration, workflow_native: {omp: {...f.declaration.workflow_native.omp, config_files: [config]}}});
    bindSources(f.context);
    const complete = f.context.source_refs;
    expect(complete).toHaveLength(42); // adapter + five controllers + two declarations + four runtime files + settings + extension + 27 resources + interpreter
    for (const reference of complete) {
      f.context.source_refs = complete.filter(item => item !== reference);
      await expect(loadNative(f.context)).rejects.toThrow("SOURCE_REF_REQUIRED");
      expect(f.sdk.loaded).toBe(false); expect(f.sdk.calls).toHaveLength(0);
    }
    f.context.source_refs = complete;
  }, 10_000);
}

test("every explicit and derived-default source is actual-byte checked before SDK loading", async () => {
  for (const selection of ["explicit", "default"] as const) {
    const f = await syntheticSdk(selection), retained = structuredClone(f.context);
    for (const reference of f.context.source_refs) {
      const original = fs.readFileSync(reference.path);
      fs.appendFileSync(reference.path, "\n// source-only tamper\n");
      await expect(loadNative(f.context)).rejects.toThrow("SOURCE_REF_CHANGED");
      expect(f.sdk.loaded).toBe(false); expect(f.sdk.settingsCalls).toBe(0); expect(f.sdk.managerCalls).toBe(0);
      expect(f.context).toEqual(retained);
      fs.writeFileSync(reference.path, original);
    }
  }
});

for (const member of ["bun.exe", "omp.exe", "payload.js", "package/package.json"]) {
  test(`same-path ${member} byte drift refuses before SDK import; only a fresh admission can bind an update`, async () => {
    const f = await syntheticSdk("task"), file = path.join(f.root, member);
    const retained = structuredClone(f.context.source_refs);
    expect(retained.some(reference => reference.path === file)).toBe(true);
    fs.appendFileSync(file, "\n ");
    await expect(loadNative(f.context)).rejects.toThrow("SOURCE_REF_CHANGED");
    expect(f.sdk.loaded).toBe(false); expect(f.sdk.calls).toHaveLength(0);
    expect(f.context.source_refs).toEqual(retained);
    const fresh = structuredClone(f.context);
    bindSources(fresh);
    const runtime = await loadNative(fresh);
    expect(f.sdk.loaded).toBe(true);
    expect(fresh.source_refs.find(reference => reference.path === file)!.sha256).not.toBe(retained.find(reference => reference.path === file)!.sha256);
    await expect(runtime.createSession(async () => ({}), () => {})).rejects.toThrow("SOURCE_ONLY_SESSION_BOUNDARY");
    await expect(loadNative(f.context)).rejects.toThrow("SOURCE_REF_CHANGED");
    expect(f.context.source_refs).toEqual(retained);
    await runtime.dispose();
  });
  test(`admitted ${member} byte drift refuses before the SDK session boundary`, async () => {
    const f = await syntheticSdk("task"), runtime = await loadNative(f.context);
    const retained = structuredClone(f.context.source_refs);
    fs.appendFileSync(path.join(f.root, member), "\n ");
    await expect(runtime.createSession(async () => ({}), () => {})).rejects.toThrow("SOURCE_REF_CHANGED");
    expect(f.sdk.calls).toHaveLength(0);
    expect(f.context.source_refs).toEqual(retained);
    await runtime.dispose();
  });
}

test("all selected runtime manifest paths are required and contained before SDK import", async () => {
  for (const field of ["runtime_relative", "executable_relative", "payload_relative", "package_tree_relative"]) {
    for (const value of [undefined, "../outside"]) {
      const f = await syntheticSdk("task"), manifest: Record<string, any> = structuredClone(f.manifest);
      if (field === "runtime_relative") manifest.installer.runtime_relative = value;
      else manifest[field] = value;
      json(f.context.install_manifest_path, manifest);
      f.context.source_refs.find(reference => reference.path === f.context.install_manifest_path)!.sha256 = sha256(fs.readFileSync(f.context.install_manifest_path));
      await expect(loadNative(f.context)).rejects.toThrow(value === undefined ? "INVALID_MANIFEST_PATH" : "MANIFEST_PATH_ESCAPE");
      expect(f.sdk.loaded).toBe(false); expect(f.sdk.calls).toHaveLength(0);
    }
  }
});

test("each selected runtime member must remain a regular unlinked file before SDK import", async () => {
  for (const member of ["bun.exe", "omp.exe", "payload.js", "package/package.json"]) {
    const f = await syntheticSdk("task"), file = path.join(f.root, member);
    fs.unlinkSync(file); fs.mkdirSync(file);
    await expect(loadNative(f.context)).rejects.toThrow("SOURCE_REF_NOT_REGULAR");
    expect(f.sdk.loaded).toBe(false);
  }
  const f = await syntheticSdk("task"), file = path.join(f.root, "payload.js"), target = path.join(f.root, "retained-payload.js");
  fs.renameSync(file, target);
  try { fs.symlinkSync(target, file, "file"); }
  catch (error: any) { if (["EPERM", "EACCES", "ENOSYS", "ENOTSUP"].includes(error.code)) return; throw error; }
  await expect(loadNative(f.context)).rejects.toThrow("SOURCE_REF_NOT_REGULAR");
  expect(f.sdk.loaded).toBe(false);
});

test("a linked selected runtime ancestor refuses even when its current bytes and containment match", async () => {
  const f = await syntheticSdk("task"), retained = path.join(f.home, "retained-install");
  fs.renameSync(f.root, retained);
  try { fs.symlinkSync(retained, f.root, process.platform === "win32" ? "junction" : "dir"); }
  catch (error: any) { if (["EPERM", "EACCES", "ENOSYS", "ENOTSUP"].includes(error.code)) return; throw error; }
  await expect(loadNative(f.context)).rejects.toThrow("NATIVE_SOURCE_ESCAPE");
  expect(f.sdk.loaded).toBe(false); expect(f.sdk.calls).toHaveLength(0);
});

test("certification fields are rejected, never treated as admission aliases", async () => {
  for (const field of ["capability_receipt_path", "qualification_receipt_path"]) {
    const f = await syntheticSdk("task");
    json(f.context.declaration_path, {...f.declaration, workflow_native: {omp: {[field]: path.join(f.home, "not-admission.json")}}});
    bindSources(f.context);
    await expect(loadNative(f.context)).rejects.toThrow("INVALID_OMP_DECLARATION");
    expect(f.sdk.loaded).toBe(false);
  }
  const f = await syntheticSdk("task");
  json(f.context.declaration_path, {...f.declaration, workflow_native: {omp: {}, claude: {qualification_receipt_path: "unavailable unrelated route"}}});
  bindSources(f.context);
  const runtime = await loadNative(f.context);
  await expect(runtime.createSession(async () => ({}), () => {})).rejects.toThrow("SOURCE_ONLY_SESSION_BOUNDARY");
  await runtime.dispose();
});

test("model selection including its absence exactly matches the declaration before SDK import", async () => {
  for (const selection of ["task", "explicit", "default"] as const)
    for (const [hint, declared] of [["provider/model", undefined], [undefined, "provider/model"], ["provider/model", "provider/other"]]) {
    const f = await syntheticSdk(selection);
    if (hint !== undefined) f.context.model = hint;
    json(f.context.declaration_path, {...f.declaration, workflow_native: {omp: {...f.declaration.workflow_native.omp,
      ...(declared === undefined ? {} : {model: declared})}}});
    bindSources(f.context);
    await expect(loadNative(f.context)).rejects.toThrow("NATIVE_MODEL_ROUTE_MISMATCH");
    expect(f.sdk.loaded).toBe(false);
  }
});

test("the actual selected SDK model must equal the exact hint and failed sessions are disposed", async () => {
  for (const selectedModel of ["provider/model", "provider/other", null]) {
    const f = await syntheticSdk("task", {selectedModel}); f.context.model = "provider/model";
    json(f.context.declaration_path, {...f.declaration, workflow_native: {omp: {model: f.context.model}}});
    bindSources(f.context);
    const runtime = await loadNative(f.context), prepared = runtime.createSession(async () => ({}), () => {});
    if (selectedModel === f.context.model) {
      const result = await prepared;
      expect(result.startup.model).toEqual({provider: "provider", id: "model"});
      await result.session.dispose();
    } else await expect(prepared).rejects.toThrow("NATIVE_MODEL_UNAVAILABLE");
    expect(f.sdk.disposals).toBe(1); await runtime.dispose();
  }
});

test("the executing Bun path is selected by the install manifest, never by a version or matching hash", async () => {
  const f = await syntheticSdk("task"), foreign = path.join(f.home, "foreign-bun.exe");
  fs.copyFileSync(path.join(f.root, "bun.exe"), foreign);
  await expect(loadNative(f.context, foreign)).rejects.toThrow("NATIVE_RUNTIME_MISMATCH");
  expect(f.sdk.loaded).toBe(false);
  const runtime = await loadNative(f.context); await runtime.dispose();
});

test("runtime and install declarations have only the fixed per-user paths, with no staging fallback", async () => {
  for (const field of ["declaration_path", "install_manifest_path"] as const) {
    const f = await syntheticSdk("task"), foreign = path.join(f.home, `old-${field}.json`);
    fs.copyFileSync(f.context[field], foreign); f.context[field] = foreign; bindSources(f.context);
    await expect(loadNative(f.context)).rejects.toThrow("INVALID_OMP_DECLARATION");
    expect(f.sdk.loaded).toBe(false);
  }
});

test("package identity is checked from the selected public package without an SDK byte allowlist", async () => {
  const f = await syntheticSdk("task");
  const file = path.join(f.tree, "package.json"), original = JSON.parse(fs.readFileSync(file, "utf8"));
  json(file, {...original, name: "foreign/package"});
  bindSources(f.context);
  await expect(loadNative(f.context)).rejects.toThrow("INVALID_OMP_DECLARATION");
  expect(f.sdk.loaded).toBe(false);
});

for (const selection of ["explicit", "default"] as const) for (const boundary of ["valid", "missing_tool", "noncallable", "foreign_schema",
  "foreign_owner", "foreign_resolved", "inactive", "duplicate", "missing_owner", "foreign_roots", "extra_roots", "discovery", "missing_images", "scalar_images"] as const) {
  test(`${selection === "default" ? "derived-default" : selection} review requires the exact active tool owner, effective roots and native image InputRef schema: ${boundary}`, async () => {
    const f = await syntheticSdk(selection, {selectedModel: "provider/model",
      reviewTool: boundary === "missing_tool" ? "missing" : boundary === "noncallable" ? "noncallable" : boundary === "foreign_schema" ? "foreign_schema" : undefined,
      reviewOwner: boundary === "foreign_owner" ? "foreign" : boundary === "foreign_resolved" ? "resolved_foreign" : boundary === "inactive" ? "inactive" : boundary === "duplicate" ? "duplicate" : boundary === "missing_owner" ? "missing" : undefined,
      reviewRoots: boundary === "foreign_roots" ? "foreign" : boundary === "extra_roots" ? "extra" : boundary === "discovery" ? "discovery" : undefined,
      reviewSchema: boundary === "missing_images" ? "absent" : boundary === "scalar_images" ? "scalar" : "images"});
    const runtime = await loadNative(f.context), prepared = runtime.createSession(async () => ({}), () => {}, {review: true});
    if (boundary === "valid") {
      const result = await prepared;
      expect(result.reviewSchema).toEqual({type: "object", properties: {images: f.imageSchema}});
      expect(result.startup.review_registration.extension).toEqual(f.reference);
      expect(result.startup.review_registration.sources).toEqual(reviewExtensionSources(f.reference));
      expect(result.startup.review_registration.effective_roots).toEqual({mode: "explicit-only", explicit: [f.extension]});
      await result.session.dispose();
    } else await expect(prepared).rejects.toThrow("SOURCE_REVIEW_REGISTRATION_UNOBSERVED");
    expect(f.sdk.calls[0].additionalExtensionPaths).toEqual([f.extension]); expect(f.sdk.calls[0].disableExtensionDiscovery).toBe(true);
    expect(f.sdk.schemaCalls).toEqual(["valid", "missing_images", "scalar_images"].includes(boundary) ? [{name: "adversarial_review", parameters_type: "function"}] : []);
    expect(f.sdk.executions).toBe(0); expect(f.sdk.disposals).toBe(1); await runtime.dispose();
  });
}
