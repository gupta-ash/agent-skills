import { createHash } from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import { pathToFileURL } from "node:url";
import { createInterface } from "node:readline";

// The Python controller owns contracts, effects, counters and acceptance. This
// finite host owns only a native session and observations of its bounded work.
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type Obj = Record<string, any>;
export type Request = { request_id: string; run_id: string; attempt_id: string; owner_epoch: number; operation: string; payload: Obj };
export type Reply = Omit<Request, "operation"> & { status: string; error: Obj | null };
export type Binding = { backend: "omp"; session_id: string; session_file: string; tool_call_id: string; child_id: string | null; job_id: string | null; owner_id: string };
export type SourceRef = { path: string; sha256: string; media_type: string };
export type ExecutionContext = {
  schema_version: 1; declaration_path: string; install_manifest_path: string;
  cwd: string; session_dir: string; evidence_dir: string;
  source_refs: SourceRef[];
  model?: string; agent_dir?: string; config_files?: string[];
  review_extension?: {path: string; sha256: string; media_type: "text/typescript"};
};
export interface NativeJob {
  id: string; agentId?: string; ownerId?: string; type: string; status: string;
  endTime?: number; promise: Promise<void>; structured?: Obj; resultText?: string;
  errorText?: string; latestDetails?: Obj;
}
export interface NativeManager {
  getAllJobs(filter?: { ownerId: string }): NativeJob[];
  getJob(id: string): NativeJob | undefined;
  watchJobs(ids: string[]): number; unwatchJobs(ids: string[]): number;
  consumeJobResults(ids: string[]): number;
  cancel(id: string, filter?: { ownerId: string }): boolean;
}
export interface NativeTool {
  name: string; execute(id: string, args: Obj, signal: AbortSignal, onUpdate: (result: Obj) => void): Promise<Obj>;
  parameters?: unknown;
}
export interface NativeSession {
  asyncJobManager: NativeManager; extensionRunner: any; model?: Obj;
  effectiveExtensionRoots: Obj; sessionManager: any;
  getAgentId(): string; getSessionAgents(): Obj[];
  getToolForEvalBridge(name: string): NativeTool | undefined;
  dispose(): Promise<void>;
}
export interface NativeRuntime {
  // Checked physical SDK loader base only, never receipt or admission identity.
  packageRoot: string;
  fingerprint: Obj;
  createSession(observer: (event: Obj) => Promise<Obj>, onFault: (error: Obj) => void, options?: {review: boolean}): Promise<{
    session: NativeSession; startup: Obj; reviewSchema?: Obj; events?: { on(channel: string, listener: (data: Obj) => void): () => void };
  }>;
  assertGeneric(session: NativeSession): Promise<Obj>;
  validate(tool: NativeTool, id: string, args: Obj): Obj;
  dispose(): Promise<void>;
}

export class Refusal extends Error {
  constructor(readonly code: string, message = code, readonly details: Obj = {}) { super(message); }
}
function need(value: unknown, code: string, details: Obj = {}): asserts value {
  if (!value) throw new Refusal(code, code, details);
}
function object(value: any): value is Obj { return value !== null && typeof value === "object" && !Array.isArray(value); }
function keys(value: any, names: string[], code: string): void {
  need(object(value) && Object.keys(value).sort().join("\0") === names.slice().sort().join("\0"), code);
}
export function canonical(value: any): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (object(value)) return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}
export function sha256(value: string | Buffer): string { return createHash("sha256").update(value).digest("hex"); }
function errorInfo(error: unknown): Obj {
  return error instanceof Refusal ? { code: error.code, message: error.message, details: error.details } :
    { code: "NATIVE_ADAPTER_ERROR", message: error instanceof Error ? error.message : String(error), details: {} };
}
function readJson(file: string): Obj { return JSON.parse(fs.readFileSync(file, "utf8")); }
function absolute(value: unknown): asserts value is string { need(typeof value === "string" && path.isAbsolute(value), "ABSOLUTE_PATH_REQUIRED"); }
function within(root: string, relative: string): string {
  need(typeof relative === "string" && relative.length > 0 && !path.isAbsolute(relative), "INVALID_MANIFEST_PATH");
  const target = path.resolve(root, relative), rel = path.relative(root, target);
  need(rel !== ".." && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel), "MANIFEST_PATH_ESCAPE");
  return target;
}
function nativeSourceWithin(root: string, file: string): void {
  const relative = path.relative(fs.realpathSync(root), fs.realpathSync(file));
  need(relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative), "NATIVE_SOURCE_ESCAPE");
}
export function durableJson(file: string, value: Obj): void {
  const fd = fs.openSync(file, "wx", 0o600);
  try { fs.writeFileSync(fd, `${JSON.stringify(value)}\n`, "utf8"); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
}
export function validateContext(raw: Obj): ExecutionContext {
  const names = ["schema_version", "declaration_path", "install_manifest_path", "cwd", "session_dir", "evidence_dir", "source_refs"];
  for (const key of ["model", "agent_dir", "config_files", "review_extension"]) if (object(raw) && Object.hasOwn(raw, key)) names.push(key);
  keys(raw, names, "INVALID_EXECUTION_CONTEXT");
  need(raw.schema_version === 1, "INVALID_EXECUTION_CONTEXT");
  if (Object.hasOwn(raw, "model")) need(typeof raw.model === "string" && raw.model.trim() === raw.model && /^[A-Za-z0-9][A-Za-z0-9._-]*\/[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(raw.model), "INVALID_EXECUTION_CONTEXT");
  for (const key of ["declaration_path", "install_manifest_path", "cwd", "session_dir", "evidence_dir"]) absolute(raw[key]);
  validateSourceRefs(raw.source_refs);
  if (Object.hasOwn(raw, "agent_dir")) { absolute(raw.agent_dir); need(fs.statSync(raw.agent_dir).isDirectory(), "INVALID_EXECUTION_CONTEXT"); }
  if (Object.hasOwn(raw, "config_files")) {
    need(Array.isArray(raw.config_files) && raw.config_files.length > 0, "INVALID_EXECUTION_CONTEXT");
    const seen = new Set<string>();
    for (const file of raw.config_files) {
      absolute(file);
      const resolved = sourcePath(file);
      need(fs.statSync(file).isFile() && !seen.has(resolved), "INVALID_EXECUTION_CONTEXT");
      seen.add(resolved);
    }
  }
  if (Object.hasOwn(raw, "review_extension")) reviewExtensionSources(raw.review_extension);
  need(fs.statSync(raw.cwd).isDirectory(), "INVALID_WORKING_DIRECTORY");
  return raw as ExecutionContext;
}

function pathKey(file: string): string {
  const normalized = path.normalize(file);
  return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}
function sourcePath(file: string): string { return pathKey(fs.realpathSync(file)); }
function sourceMedia(file: string): string {
  return ({".json": "application/json", ".py": "text/x-python", ".ts": "text/typescript", ".md": "text/markdown"} as Record<string, string>)[path.extname(file)] ?? "application/octet-stream";
}
export function validateSourceRefs(raw: unknown, required: string[] = []): SourceRef[] {
  need(Array.isArray(raw) && raw.length > 0, "INVALID_SOURCE_REFS");
  const seen = new Set<string>();
  for (const reference of raw) {
    keys(reference, ["path", "sha256", "media_type"], "INVALID_SOURCE_REFS");
    absolute(reference.path);
    need(fs.lstatSync(reference.path).isFile(), "SOURCE_REF_NOT_REGULAR");
    const identity = sourcePath(reference.path);
    need(!seen.has(identity), "DUPLICATE_SOURCE_REF");
    need(typeof reference.sha256 === "string" && /^[0-9a-f]{64}$/.test(reference.sha256) &&
      reference.media_type === sourceMedia(reference.path), "INVALID_SOURCE_REFS");
    seen.add(identity);
    need(sha256(fs.readFileSync(reference.path)) === reference.sha256, "SOURCE_REF_CHANGED", {path: reference.path});
  }
  for (const file of required) need(seen.has(sourcePath(file)), "SOURCE_REF_REQUIRED", {path: file});
  return raw as SourceRef[];
}
export async function loadArgumentValidator(core: Obj, loadLegacy: () => Promise<Obj>): Promise<{
  validate(tool: NativeTool, id: string, args: Obj): Obj;
}> {
  if (core.validateAgentToolArguments !== undefined) {
    need(typeof core.validateAgentToolArguments === "function", "NATIVE_API_UNAVAILABLE");
    return { validate: (tool, id, args) => core.validateAgentToolArguments(tool,
      { type: "toolCall", id, name: tool.name, arguments: args }, "payload") };
  }
  const legacy = await loadLegacy();
  need(typeof legacy.validateToolArguments === "function", "NATIVE_API_UNAVAILABLE");
  return { validate: (tool, id, args) => legacy.validateToolArguments(tool,
    { type: "toolCall", id, name: tool.name, arguments: args }) };
}
// Fixed discriminator export projection; unrelated installed neighbors are not
// runtime inputs and are neither traversed nor hashed.
const discriminatorRuntimeFiles = [
  "SKILL.md", "agents/discriminator.toml", "assets/preamble_envelope.txt", "assets/preamble_finding.txt",
  "assets/rubric.lock.json", "assets/rubric.txt", "claude_reviewer.py", "contracts/code-fleet.md",
  "contracts/code-service.md", "contracts/configuration.md", "contracts/model-instruction.md",
  "contracts/pipeline-template.md", "contracts/plan-design.md", "contracts/project-state.md",
  "contracts/research-report.md", "contracts/vault-raw.md", "contracts/vault-wiki.md", "envelope.py",
  "envelope_schema.py", "family_attestation.py", "native_review.py", "packet.py", "request.py",
  "result.py", "supervisor.py", "telemetry.py", "transport.py",
];

export function reviewExtensionSources(reference: Obj): Array<{path: string; sha256: string}> {
  keys(reference, ["path", "sha256", "media_type"], "INVALID_REVIEW_EXTENSION");
  absolute(reference.path);
  need(reference.media_type === "text/typescript" && typeof reference.sha256 === "string" && /^[0-9a-f]{64}$/.test(reference.sha256), "INVALID_REVIEW_EXTENSION");
  const extension = path.normalize(reference.path), home = path.resolve(path.dirname(extension), "../../../..");
  need(!reference.path.split(/[\\/]/).includes("..") &&
    extension === path.join(home, ".omp/shared/extensions/discriminator/index.ts"), "REVIEW_EXTENSION_CHANGED_OR_FOREIGN");
  const root = path.join(home, ".agents/skills/discriminator");
  const files = discriminatorRuntimeFiles.map(relative => within(root, relative));
  const checkedDirectories = new Set<string>();
  for (const file of [extension, ...files]) {
    const parents: string[] = [];
    for (let directory = path.dirname(file); !checkedDirectories.has(directory); directory = path.dirname(directory)) {
      parents.push(directory);
      if (path.dirname(directory) === directory) break;
    }
    for (const directory of parents.reverse()) {
      const info = fs.lstatSync(directory);
      need(!info.isSymbolicLink() && info.isDirectory(), "REVIEW_EXTENSION_EXTERNAL_RESOURCE");
      checkedDirectories.add(directory);
    }
    const info = fs.lstatSync(file);
    need(!info.isSymbolicLink(), "REVIEW_EXTENSION_EXTERNAL_RESOURCE");
    need(info.isFile(), file === extension ? "REVIEW_EXTENSION_CHANGED_OR_FOREIGN" : "REVIEW_EXTENSION_NONREGULAR_RESOURCE");
  }
  need(sha256(fs.readFileSync(extension)) === reference.sha256, "REVIEW_EXTENSION_CHANGED_OR_FOREIGN");
  return [extension, ...files.sort()].map(file => ({path: file, sha256: sha256(fs.readFileSync(file))}));
}

export async function loadNative(context: ExecutionContext): Promise<NativeRuntime> {
  validateContext(context);
  const runtimeRoot = path.resolve(import.meta.dir, "../.."), base = [import.meta.path, context.declaration_path, context.install_manifest_path,
    ...["controller.py", "workflow.py", "contracts.py", "store.py", "publication.py"].map(name => path.join(runtimeRoot, name)),
    ...(context.config_files ?? [])];
  validateSourceRefs(context.source_refs, base);
  const declaration = readJson(context.declaration_path), manifest = readJson(context.install_manifest_path);
  const home = declaration.tokens?.__USERPROFILE__;
  absolute(home);
  const configurationRoot = path.join(home, ".agents/agent-skills");
  need(sourcePath(context.declaration_path) === sourcePath(path.join(configurationRoot, "runtime.json")) &&
    sourcePath(context.install_manifest_path) === sourcePath(path.join(configurationRoot, "omp-install.json")), "INVALID_OMP_DECLARATION");
  need(object(declaration.workflow_native ?? {}) && Object.keys(declaration.workflow_native ?? {}).every(name => ["omp", "claude", "codex"].includes(name)), "INVALID_OMP_DECLARATION");
  const declared = declaration.workflow_native?.omp ?? {};
  need(object(declared) && Object.keys(declared).every(name => ["model", "agent_dir", "config_files", "review_extension"].includes(name)), "INVALID_OMP_DECLARATION");
  need(Object.hasOwn(context, "model") === Object.hasOwn(declared, "model") && context.model === declared.model, "NATIVE_MODEL_ROUTE_MISMATCH");
  if (context.agent_dir !== undefined) {
    absolute(declared.agent_dir);
    need(sourcePath(context.agent_dir) === sourcePath(declared.agent_dir), "NATIVE_SETTINGS_ROUTE_MISMATCH");
  } else need(!Object.hasOwn(declared, "agent_dir"), "NATIVE_SETTINGS_ROUTE_MISMATCH");
  if (context.config_files !== undefined) {
    need(Array.isArray(declared.config_files) && context.config_files.length === declared.config_files.length, "NATIVE_SETTINGS_ROUTE_MISMATCH");
    for (let index = 0; index < context.config_files.length; index++) {
      absolute(declared.config_files[index]);
      need(sourcePath(context.config_files[index]) === sourcePath(declared.config_files[index]), "NATIVE_SETTINGS_ROUTE_MISMATCH");
    }
  } else need(!Object.hasOwn(declared, "config_files"), "NATIVE_SETTINGS_ROUTE_MISMATCH");
  if (Object.hasOwn(declared, "review_extension")) {
    need(Object.hasOwn(context, "review_extension") &&
      canonical(context.review_extension) === canonical(declared.review_extension), "NATIVE_REVIEW_EXTENSION_MISMATCH");
  } else if (context.review_extension !== undefined) {
    need(context.review_extension.path === path.join(home, ".omp/shared/extensions/discriminator/index.ts"),
      "NATIVE_REVIEW_EXTENSION_MISMATCH");
  }
  const reviewSources = context.review_extension ? reviewExtensionSources(context.review_extension) : [];
  const reviewPaths = reviewSources.map(reference => reference.path);
  if (context.review_extension) {
    absolute(declaration.tokens?.__OMP_PYTHON__);
    reviewPaths.push(declaration.tokens.__OMP_PYTHON__);
  }
  validateSourceRefs(context.source_refs, reviewPaths);
  const root = declaration.omp_managed?.install_root;
  absolute(root);
  need(declaration.omp_managed.enabled === true && manifest.schema === 1 && manifest.package === "@oh-my-pi/pi-coding-agent", "INVALID_OMP_DECLARATION");
  const tree = within(root, manifest.package_tree_relative), pkgPath = path.join(tree, "package.json");
  const executable = within(root, manifest.executable_relative), bun = within(root, manifest.installer?.runtime_relative);
  const payload = within(root, manifest.payload_relative), runtimeFiles = [bun, executable, payload, pkgPath];
  nativeSourceWithin(root, tree);
  nativeSourceWithin(tree, pkgPath);
  const checkedDirectories = new Set<string>();
  for (const file of runtimeFiles) {
    nativeSourceWithin(root, file);
    need(fs.lstatSync(file).isFile(), "NATIVE_EXECUTABLE_UNAVAILABLE");
    for (let directory = path.dirname(file); !checkedDirectories.has(directory); directory = path.dirname(directory)) {
      const info = fs.lstatSync(directory);
      need(!info.isSymbolicLink() && info.isDirectory(), "NATIVE_SOURCE_ESCAPE");
      checkedDirectories.add(directory);
      if (path.dirname(directory) === directory) break;
    }
  }
  // These are current admission observations, not an approved SDK/version inventory.
  // Keep the supplied hashes for every later boundary; never refresh retained refs.
  validateSourceRefs(context.source_refs, runtimeFiles);
  const pkg = readJson(pkgPath);
  need(pkg.name === manifest.package, "INVALID_OMP_DECLARATION");
  need(sourcePath(process.execPath) === sourcePath(bun), "NATIVE_RUNTIME_MISMATCH");
  // Select only declared public exports from this installation. Public subpaths
  // may move internally without changing the source contract.
  const publicEntry = (specifier: string, from = tree, sourceRoot = root): string => {
    let entry: string;
    try { entry = Bun.resolveSync(specifier, from); }
    catch { throw new Refusal("NATIVE_API_UNAVAILABLE", "NATIVE_API_UNAVAILABLE", { api: specifier }); }
    nativeSourceWithin(sourceRoot, entry);
    return entry;
  };
  const loadPublic = (specifier: string, from = tree, sourceRoot = root): Promise<Obj> =>
    import(pathToFileURL(publicEntry(specifier, from, sourceRoot)).href);
  const sdk = await loadPublic(manifest.package, tree, tree);
  const init = sdk.initializeExtensions === undefined ? await loadPublic(`${manifest.package}/modes/runtime-init`, tree, tree) : sdk;
  const coreEntry = publicEntry("@oh-my-pi/pi-agent-core");
  const core = await import(pathToFileURL(coreEntry).href);
  const discovery = sdk.discoverAgents === undefined ? await loadPublic(`${manifest.package}/task/discovery`, tree, tree) : sdk;
  const agents = sdk.getBundledAgent === undefined ? await loadPublic(`${manifest.package}/task/agents`, tree, tree) : sdk;
  const pythonBridge = sdk.disposePyToolBridge === undefined ? await loadPublic(`${manifest.package}/eval/py/tool-bridge`, tree, tree) : sdk;
  const registry = sdk.lookup === undefined ? await loadPublic(`${manifest.package}/config/registry`, tree, tree) : sdk;
  need(typeof registry.lookup === "function", "NATIVE_API_UNAVAILABLE");
  need(registry.lookup("task.batch")?.definition?.type === "boolean" && registry.lookup("async.enabled")?.definition?.type === "boolean", "NATIVE_API_UNAVAILABLE");
  const structured = registry.lookup("task.structured");
  need(structured === undefined || structured?.definition?.type === "boolean", "NATIVE_API_UNAVAILABLE");
  const sessionOverrides: Obj = { "task.batch": true, "async.enabled": true };
  if (structured !== undefined) sessionOverrides["task.structured"] = true;
  need([sdk.createAgentSession, sdk.SessionManager?.create, sdk.Settings?.loadReadOnly, init.initializeExtensions, discovery.discoverAgents, agents.getBundledAgent, pythonBridge.disposePyToolBridge].every(fn => typeof fn === "function"), "NATIVE_API_UNAVAILABLE");
  const argumentValidator = await loadArgumentValidator(core, () => loadPublic("@oh-my-pi/pi-ai", path.dirname(coreEntry)));
  const reviewSchemaApi = context.review_extension ? (await loadPublic("@oh-my-pi/pi-ai")).toolWireSchema : undefined;
  need(context.review_extension === undefined || typeof reviewSchemaApi === "function", "NATIVE_REVIEW_SCHEMA_API_UNAVAILABLE");
  const fingerprint: Obj = {
    model_hint: context.model ?? null,
    agent_dir: context.agent_dir === undefined ? null : path.normalize(context.agent_dir),
    config_files: context.config_files?.map(file => ({ path: path.normalize(file), sha256: sha256(fs.readFileSync(file)) })) ?? [],
    review_extension: context.review_extension ?? null, review_extension_sources: reviewSources,
    adapter_sha256: sha256(fs.readFileSync(import.meta.path)),
    declaration_sha256: sha256(fs.readFileSync(context.declaration_path)),
    manifest_sha256: sha256(fs.readFileSync(context.install_manifest_path)),
  };
  return {
    packageRoot: fs.realpathSync(tree), fingerprint,
    async createSession(observer, onFault, purpose = {review: false}) {
      validateSourceRefs(context.source_refs);
      need(!purpose.review || context.review_extension !== undefined, "SOURCE_REVIEW_EXTENSION_REQUIRED");
      if (purpose.review) need(canonical(reviewExtensionSources(context.review_extension!)) === canonical(reviewSources), "REVIEW_EXTENSION_CHANGED");
      const settingsOptions: Obj = { cwd: context.cwd };
      if (context.agent_dir !== undefined) settingsOptions.agentDir = context.agent_dir;
      if (context.config_files !== undefined) settingsOptions.configFiles = context.config_files;
      const settings = (await sdk.Settings.loadReadOnly(settingsOptions)).overlay(sessionOverrides);
      const manager = sdk.SessionManager.create(context.cwd, context.session_dir);
      await manager.ensureOnDisk();
      const options: Obj = { cwd: context.cwd, sessionManager: manager, settings,
        extensions: [(pi: any) => { pi.on("before_subagent_spawn", observer); }] };
      if (context.model !== undefined) options.modelPattern = context.model;
      if (context.agent_dir !== undefined) options.agentDir = context.agent_dir;
      if (purpose.review) {
        options.additionalExtensionPaths = [context.review_extension!.path];
        options.disableExtensionDiscovery = true;
      }
      const result = await sdk.createAgentSession(options);
      const reportFault = (error: Obj) => {
        try { onFault(error); }
        finally {
          const owner = result.session.getAgentId(), jobs = result.session.asyncJobManager;
          for (const job of jobs?.getAllJobs({ ownerId: owner }) ?? []) jobs.cancel(job.id, { ownerId: owner });
        }
      };
      try {
        await init.initializeExtensions(result.session, {
          reportRuntimeError: reportFault,
          reportSendError: (_action: string, error: Error) => reportFault({ event: "extension_send", error: error.message }),
        });
        if (context.model !== undefined) {
          const selected = result.session.model;
          const observed = selected ? `${selected.provider}/${selected.id}` : null;
          need(observed === context.model, "NATIVE_MODEL_UNAVAILABLE", { requested_model: context.model, observed_model: observed,
            model_fallback_message: result.modelFallbackMessage ?? null });
        }
        let reviewRegistration: Obj | null = null;
        let reviewSchema: Obj | undefined;
        if (purpose.review) {
          const reference = context.review_extension!, roots = result.session.effectiveExtensionRoots;
          const tool = result.session.getToolForEvalBridge("adversarial_review"), runner = result.session.extensionRunner;
          need(object(roots) && roots.mode === "explicit-only" && canonical(roots.explicit) === canonical([reference.path]) &&
            tool?.name === "adversarial_review" && typeof tool.execute === "function" &&
            typeof runner?.getAllRegisteredTools === "function" && typeof runner.getLoadedExtensions === "function" &&
            typeof runner.isExtensionActive === "function", "SOURCE_REVIEW_REGISTRATION_UNOBSERVED");
          const registrations = runner.getAllRegisteredTools().filter((registered: Obj) => registered.definition?.name === "adversarial_review");
          const owners = runner.getLoadedExtensions().filter((extension: Obj) => extension.path === reference.path &&
            extension.resolvedPath === reference.path && runner.isExtensionActive(extension.path));
          need(registrations.length === 1 && owners.length === 1 && registrations[0].extensionPath === reference.path &&
            owners[0].tools?.get("adversarial_review") === registrations[0] &&
            typeof registrations[0].definition.execute === "function" &&
            tool.parameters === registrations[0].definition.parameters, "SOURCE_REVIEW_REGISTRATION_UNOBSERVED");
          // Serialize the actual enabled tool with the native public API. A foreign
          // same-named tool or an images-named scalar is not this registration.
          reviewSchema = reviewSchemaApi(tool);
          const images = reviewSchema?.properties?.images, item = images?.items;
          need(object(reviewSchema) && reviewSchema.type === "object" && object(images) && images.type === "array" &&
            object(item) && item.type === "object" && item.additionalProperties === false &&
            Array.isArray(item.required) && canonical(item.required.slice().sort()) === canonical(["media_type", "path", "sha256"]) &&
            item.properties?.path?.type === "string" && item.properties?.sha256?.type === "string" &&
            object(item.properties?.media_type) &&
            canonical(reviewExtensionSources(reference)) === canonical(reviewSources), "SOURCE_REVIEW_REGISTRATION_UNOBSERVED");
          reviewRegistration = {extension: context.review_extension, sources: reviewSources,
            effective_roots: roots, schema_sha256: sha256(canonical(reviewSchema))};
        }
        await manager.ensureOnDisk();
        await manager.flush();
        return { session: result.session, reviewSchema, events: result.subagentEventBus, startup: {
          model: result.session.model ? { provider: result.session.model.provider, id: result.session.model.id } : null,
          model_hint: context.model ?? null,
          agent_dir: fingerprint.agent_dir, config_files: fingerprint.config_files,
          review_registration: reviewRegistration,
          model_fallback_message: result.modelFallbackMessage ?? null } };
      } catch (error) { await result.session.dispose(); throw error; }
    },
    async assertGeneric(session) {
      const result = await discovery.discoverAgents(context.cwd, undefined, session.effectiveExtensionRoots);
      const matches = [...result.agents, ...session.getSessionAgents()].filter((agent: Obj) => agent.name === "task");
      const bundled = agents.getBundledAgent("task");
      need(matches.length === 1 && matches[0].source === "bundled" && canonical(matches[0]) === canonical(bundled) && matches[0].blocking !== true, "GENERIC_TASK_SHADOWED");
      return { name: "task", source: matches[0].source, file_path: matches[0].filePath ?? null,
        definition_sha256: sha256(canonical(matches[0])) };
    },
    validate: argumentValidator.validate,
    // Python eval's loopback bridge is process-owned, not session-owned. Close
    // it only after every owned session has disposed; never hard-exit around it.
    dispose: () => pythonBridge.disposePyToolBridge(),
  };
}

export function requireEnabledTool(session: Pick<NativeSession, "getToolForEvalBridge">, review: boolean): void {
  const tool = session.getToolForEvalBridge(review ? "adversarial_review" : "task");
  need(tool && typeof tool.execute === "function", review ? "NO_REVIEW" : "ENABLED_WRAPPED_TASK_UNAVAILABLE");
}

export class AckBroker {
  private pending = new Map<string, { request: Request; binding: Binding; accept: () => void; reject: (error: Error) => void }>();
  private closed = false;
  constructor(private emit: (reply: Reply) => void) {}
  wait(request: Request, binding: Binding, signal: AbortSignal, timeoutMs = 5_000): Promise<void> {
    need(!this.closed, "BINDING_CHANNEL_CLOSED");
    need(!this.pending.has(request.request_id), "DUPLICATE_BINDING_WAIT");
    return new Promise((resolve, reject) => {
      const finish = (error?: Error) => {
        clearTimeout(timer); signal.removeEventListener("abort", abort); this.pending.delete(request.request_id);
        error ? reject(error) : resolve();
      };
      const abort = () => finish(new Refusal("BINDING_CANCELLED"));
      const timer = setTimeout(() => finish(new Refusal("BINDING_ACK_TIMEOUT")), timeoutMs);
      this.pending.set(request.request_id, { request, binding, accept: () => finish(), reject: finish });
      signal.addEventListener("abort", abort, { once: true });
      if (signal.aborted) abort();
      else {
        try { this.emit(reply(request, "binding_required", { binding })); }
        catch (error) { finish(error instanceof Error ? error : new Error(String(error))); }
      }
    });
  }
  acknowledge(request: Request): void {
    const entry = this.pending.get(request.request_id);
    need(entry, "UNEXPECTED_BINDING_ACK");
    try {
      need(sameIdentity(entry.request, request), "BINDING_ACK_IDENTITY_MISMATCH");
      keys(request.payload, ["committed", "binding"], "INVALID_BINDING_ACK");
      need(request.payload.committed === true && canonical(request.payload.binding) === canonical(entry.binding), "BINDING_ACK_NOT_COMMITTED");
      entry.accept();
    } catch (error) { entry.reject(error instanceof Error ? error : new Error(String(error))); throw error; }
  }
  close(): void {
    this.closed = true;
    for (const entry of [...this.pending.values()]) entry.reject(new Refusal("BINDING_CHANNEL_CLOSED"));
  }
}

function sameIdentity(a: Request, b: Request): boolean {
  return a.request_id === b.request_id && a.run_id === b.run_id && a.attempt_id === b.attempt_id && a.owner_epoch === b.owner_epoch;
}
function reply(request: Request, status: string, payload: Obj = {}, error: Obj | null = null): Reply {
  return { request_id: request.request_id, run_id: request.run_id, attempt_id: request.attempt_id, owner_epoch: request.owner_epoch, status, payload, error };
}
export function validateRequest(raw: any): Request {
  keys(raw, ["request_id", "run_id", "attempt_id", "owner_epoch", "operation", "payload"], "INVALID_REQUEST");
  for (const key of ["request_id", "run_id", "attempt_id", "operation"]) need(typeof raw[key] === "string" && raw[key].length > 0, "INVALID_REQUEST_IDENTITY");
  need(Number.isSafeInteger(raw.owner_epoch) && raw.owner_epoch > 0 && object(raw.payload), "INVALID_REQUEST_IDENTITY");
  need(["prepare", "launch_with_binding_barrier", "observe", "cancel", "reconcile", "binding_ack"].includes(raw.operation), "UNKNOWN_OPERATION");
  return raw;
}
const taskFields = ["schema_version", "run_id", "step_id", "item_id", "attempt_id", "duty", "instructions", "inputs", "resources", "candidate_dir", "outputs", "allowed_actions", "read_roots", "write_roots", "result_schema", "artifact_id", "review_contract", "artifact_tier", "semantic_sha256"];
function validateTask(task: Obj, request: Request): void {
  keys(task, taskFields, "INVALID_TASK_SPEC");
  const { semantic_sha256, ...semantic } = task;
  need(task.schema_version === 1 && task.run_id === request.run_id && task.attempt_id === request.attempt_id &&
    /^[a-f0-9]{64}$/.test(semantic_sha256) && sha256(canonical(semantic)) === semantic_sha256, "TASK_IDENTITY_OR_HASH_MISMATCH");
  need(typeof task.instructions === "string" && task.instructions.length > 0 && object(task.result_schema) &&
    task.result_schema.additionalProperties === false, "STRICT_RESULT_SCHEMA_REQUIRED");
  absolute(task.candidate_dir);
}
export function taskArguments(task: Obj, attemptId: string): Obj {
  return {
    context: `Execute only this immutable explicit task. No implicit role duties, external research, undocumented workers or canonical writes. Respect cancellation; inaccessible input is blocked. Return the strict TaskResult through the native structured result mechanism.\nTaskSpec:\n${JSON.stringify(task)}`,
    tasks: [{ name: `Wf${sha256(attemptId).slice(0, 24)}`, agent: "task", task: task.instructions,
      solutionSpace: "One bounded explicit duty; input evidence, output ownership and return schema are fully specified in TaskSpec.",
      outputSchema: task.result_schema, schemaMode: "strict" }],
  };
}
export function strictCarrier(job: NativeJob, task: Obj): Obj {
  const carrier = job.structured;
  need(job.status === "completed" && job.endTime !== undefined, "NATIVE_JOB_NOT_SUCCESSFULLY_SETTLED");
  need(carrier?.source === "caller" && carrier.mode === "strict" && carrier.status === "valid" && object(carrier.data), "STRICT_NATIVE_RESULT_MISSING");
  for (const key of ["run_id", "step_id", "item_id", "attempt_id"]) need(carrier.data[key] === task[key], "NATIVE_RESULT_IDENTITY_MISMATCH");
  need(carrier.data.task_sha256 === task.semantic_sha256 && carrier.data.schema_version === 1, "NATIVE_RESULT_TASK_MISMATCH");
  return carrier;
}

// This gate is also exercised by source-only real-SDK probes. Production always
// supplies the actual enabled wrapped task and actual root job manager.
export class ReservationBarrier {
  binding?: Binding;
  committed = false;
  fault?: Obj;
  constructor(readonly signal: AbortController, private manager: NativeManager, private owner: string,
    private base: Omit<Binding, "child_id" | "job_id">,
    private acknowledge: (binding: Binding, signal: AbortSignal) => Promise<void>,
    private assertGeneric: () => Promise<unknown>, private retain: (kind: string, data: Obj) => void) {}
  runtimeFault(error: Obj): void {
    // Synchronous: runner emits this before its fail-open continuation resumes.
    this.fault ??= error;
    this.signal.abort(new Refusal("RESERVATION_RUNTIME_FAULT", String(error.error ?? error.event ?? "runtime fault")));
    for (const job of this.manager.getAllJobs({ ownerId: this.owner })) this.manager.cancel(job.id, { ownerId: this.owner });
  }
  async reserve(event: Obj): Promise<Obj> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const work = async () => {
        need(!this.signal.signal.aborted && !this.binding && event.agent === "task" && event.invocationKind === "task" && typeof event.spawnKey === "string", "UNEXPECTED_NATIVE_SPAWN");
        await this.assertGeneric();
        need(!this.signal.signal.aborted, "RESERVATION_CANCELLED");
        // register() starts its body before publishing the row; preflight yields
        // before this hook, but tolerate that single microtask boundary explicitly.
        await Promise.resolve();
        const matches = this.manager.getAllJobs({ ownerId: this.owner }).filter(job => job.agentId === event.spawnKey && job.type === "task");
        need(matches.length === 1, "ACTUAL_NATIVE_JOB_UNAVAILABLE");
        const job = matches[0];
        this.manager.watchJobs([job.id]);
        this.binding = { ...this.base, child_id: event.spawnKey, job_id: job.id };
        this.retain("binding_reserved", { binding: this.binding, event });
        await this.acknowledge(this.binding, this.signal.signal);
        need(!this.signal.signal.aborted, "RESERVATION_CANCELLED");
        this.retain("binding_committed", { binding: this.binding });
        this.committed = true;
      };
      await Promise.race([work(), new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Refusal("RESERVATION_INTERNAL_TIMEOUT")), 5_000);
      })]);
      return {};
    } catch (error) {
      const failure = errorInfo(error);
      this.runtimeFault({ event: "before_subagent_spawn", error: failure.message, details: failure });
      try { this.retain("reservation_blocked", failure); } catch { /* Abort/cancel has already latched. */ }
      return { block: true, reason: failure.code };
    } finally { clearTimeout(timer); }
  }
}

type Attempt = {
  request: Request; task?: Obj; review?: Obj; dir: string; sequence: number;
  phase: string; abort: AbortController; session?: NativeSession; startup?: Obj; generic?: Obj;
  barrier?: ReservationBarrier; binding?: Binding; submitted: boolean; cancelRequested: boolean;
  dispatch?: Promise<void>; terminal?: Obj; failure?: Obj; toolResult?: Obj; resultPath?: string;
};
export class OmpAdapter {
  private attempts = new Map<string, Attempt>();
  private epochs = new Map<string, number>();
  private closePromise?: Promise<void>;
  constructor(readonly context: ExecutionContext, readonly runtime: NativeRuntime, readonly acknowledgements: AckBroker) {}
  private directory(attemptId: string): string { return path.join(this.context.evidence_dir, sha256(attemptId)); }
  private retain(attempt: Attempt, kind: string, data: Obj): string {
    const file = path.join(attempt.dir, `${String(attempt.sequence++).padStart(6, "0")}-${kind}.json`);
    durableJson(file, { schema_version: 1, kind, run_id: attempt.request.run_id, attempt_id: attempt.request.attempt_id,
      owner_epoch: attempt.request.owner_epoch, observed_at: Date.now(), ...data });
    return file;
  }
  async handle(request: Request): Promise<Reply> {
    const epoch = this.epochs.get(request.run_id);
    need(epoch === undefined || epoch === request.owner_epoch, "OWNER_EPOCH_MISMATCH");
    this.epochs.set(request.run_id, request.owner_epoch);
    if (request.operation === "reconcile" && !this.attempts.has(request.attempt_id)) return this.recover(request);
    if (request.operation === "prepare") return this.prepare(request);
    const attempt = this.attempts.get(request.attempt_id);
    need(attempt && attempt.request.run_id === request.run_id && attempt.request.owner_epoch === request.owner_epoch, "UNKNOWN_OR_STALE_ATTEMPT");
    if (request.operation === "launch_with_binding_barrier") {
      keys(request.payload, [], "INVALID_LAUNCH_PAYLOAD");
      need(attempt.phase === "prepared" && !attempt.cancelRequested, "ATTEMPT_NOT_LAUNCHABLE");
      return this.launch(attempt, request);
    }
    if (request.operation === "cancel") {
      keys(request.payload, [], "INVALID_CANCEL_PAYLOAD");
      attempt.cancelRequested = true;
      this.retain(attempt, "cancel_requested", { binding: attempt.binding ?? null });
      attempt.abort.abort(new Refusal("CANCELLATION_REQUESTED"));
      const manager = attempt.session?.asyncJobManager, owner = attempt.session?.getAgentId();
      let cancelled = false;
      if (manager && owner) {
        const filter = { ownerId: owner };
        for (const job of manager.getAllJobs(filter)) cancelled = manager.cancel(job.id, filter) || cancelled;
      }
      if (attempt.phase === "prepared") {
        attempt.phase = "cancelled";
        attempt.terminal = { status: "cancelled", cancellation_requested: true, locally_settled: true, submitted: false,
          binding: null, native_result: null, retry_authorized: false, evidence_dir: attempt.dir };
        this.retain(attempt, "terminal", attempt.terminal);
      }
      return reply(request, "running", { cancellation_requested: true, native_cancel_requested: cancelled,
        locally_settled: attempt.terminal !== undefined, binding: attempt.binding ?? null });
    }
    need(request.operation === "observe" || request.operation === "reconcile", "INVALID_OPERATION_STATE");
    keys(request.payload, [], "INVALID_OBSERVATION_PAYLOAD");
    if (attempt.terminal) return reply(request, attempt.terminal.status, attempt.terminal, attempt.failure ?? null);
    return reply(request, "running", { phase: attempt.phase, submitted: attempt.submitted, binding: attempt.binding ?? null,
      cancellation_requested: attempt.cancelRequested, locally_settled: false, evidence_dir: attempt.dir });
  }
  private async prepare(request: Request): Promise<Reply> {
    need(!this.attempts.has(request.attempt_id), "ATTEMPT_ALREADY_PREPARED");
    validateSourceRefs(this.context.source_refs);
    const isReview = Object.hasOwn(request.payload, "review");
    keys(request.payload, [isReview ? "review" : "task"], "INVALID_PREPARE_PAYLOAD");
    if (isReview) {
      need(object(request.payload.review), "INVALID_REVIEW_REQUEST");
      keys(request.payload.review, ["caller_family", "contract", "source", "focus", ...(Object.hasOwn(request.payload.review, "images") ? ["images"] : [])], "INVALID_REVIEW_REQUEST");
      const review = request.payload.review;
      need(["gpt", "claude"].includes(review.caller_family) && ["contract", "source", "focus"].every(key => typeof review[key] === "string"), "INVALID_REVIEW_REQUEST");
      absolute(review.source);
      if (Object.hasOwn(review, "images")) {
        need(Array.isArray(review.images) && review.images.length > 0, "INVALID_REVIEW_IMAGES");
        const seen = new Set<string>();
        for (const image of review.images) {
          keys(image, ["path", "sha256", "media_type"], "INVALID_REVIEW_IMAGES");
          absolute(image.path);
          need(!seen.has(image.path) && /^[0-9a-f]{64}$/.test(image.sha256) &&
            ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(image.media_type), "INVALID_REVIEW_IMAGES");
          seen.add(image.path);
          need(fs.lstatSync(image.path).isFile() && sha256(fs.readFileSync(image.path)) === image.sha256, "REVIEW_IMAGE_CHANGED");
        }
      }
    } else validateTask(request.payload.task, request);
    fs.mkdirSync(this.context.evidence_dir, { recursive: true });
    const dir = this.directory(request.attempt_id);
    need(!fs.existsSync(dir), "ATTEMPT_EVIDENCE_EXISTS_RECONCILE_REQUIRED");
    fs.mkdirSync(dir);
    const attempt: Attempt = { request, ...(isReview ? { review: request.payload.review } : { task: request.payload.task }),
      dir, sequence: 0, phase: "preparing", abort: new AbortController(), submitted: false, cancelRequested: false };
    this.attempts.set(request.attempt_id, attempt);
    this.retain(attempt, "prepared_input", { payload: request.payload, fingerprint: this.runtime.fingerprint });
    try {
      const result = await this.runtime.createSession(
        event => attempt.barrier ? attempt.barrier.reserve(event) : Promise.resolve({ block: true, reason: "NO_ACTIVE_RESERVATION" }),
        error => {
          attempt.abort.abort(new Refusal("NATIVE_RUNTIME_FAULT"));
          attempt.barrier?.runtimeFault(error);
          attempt.failure ??= { code: "NATIVE_RUNTIME_FAULT", message: String(error.error ?? error.event), details: error };
        }, {review: isReview});
      attempt.session = result.session; attempt.startup = result.startup;
      need(!attempt.abort.signal.aborted, "NATIVE_INITIALIZATION_FAULT");
      const session = result.session, owner = session.getAgentId();
      need(typeof owner === "string" && session.sessionManager.getSessionId() && session.sessionManager.getSessionFile(), "DURABLE_NATIVE_SESSION_REQUIRED");
      if (attempt.task) {
        need(session.asyncJobManager, "NATIVE_ASYNC_MANAGER_UNAVAILABLE");
        attempt.generic = await this.runtime.assertGeneric(session);
      }
      requireEnabledTool(session, !!attempt.review);
      attempt.phase = "prepared";
      this.retain(attempt, "native_prepared", { startup: attempt.startup, generic: attempt.generic ?? null,
        session_id: session.sessionManager.getSessionId(), session_file: session.sessionManager.getSessionFile() });
      return reply(request, "prepared", { evidence_dir: dir, startup: attempt.startup, generic: attempt.generic ?? null });
    } catch (error) {
      attempt.failure = errorInfo(error); attempt.phase = attempt.cancelRequested ? "cancelled" : "blocked";
      attempt.terminal = { status: attempt.phase, locally_settled: true, submitted: false, native_result: null,
        error: attempt.failure, retry_authorized: false, evidence_dir: dir };
      this.retain(attempt, "terminal", attempt.terminal);
      if (attempt.session) await attempt.session.dispose();
      return reply(request, attempt.phase, attempt.terminal, attempt.failure);
    }
  }
  private async launch(attempt: Attempt, request: Request): Promise<Reply> {
    validateSourceRefs(this.context.source_refs);
    const session = attempt.session!;
    const tool = session.getToolForEvalBridge(attempt.review ? "adversarial_review" : "task");
    need(tool && typeof tool.execute === "function", attempt.review ? "NO_REVIEW" : "ENABLED_WRAPPED_TASK_UNAVAILABLE");
    const id = `wf-${sha256(attempt.request.attempt_id).slice(0, 32)}`;
    const base: Omit<Binding, "child_id" | "job_id"> = { backend: "omp", session_id: session.sessionManager.getSessionId(),
      session_file: session.sessionManager.getSessionFile(), tool_call_id: id, owner_id: session.getAgentId() };
    const args = this.runtime.validate(tool, id, attempt.review ?? taskArguments(attempt.task!, request.attempt_id));
    this.retain(attempt, "launch_intent", { args, tool: tool.name });
    attempt.phase = "launching";
    const bind = async (binding: Binding, signal: AbortSignal) => {
      attempt.binding = binding;
      await this.acknowledgements.wait(request, binding, signal);
      validateSourceRefs(this.context.source_refs);
    };
    if (attempt.task) {
      // Recheck the closed sources at the hook as well, after native rediscovery.
      attempt.barrier = new ReservationBarrier(attempt.abort, session.asyncJobManager, base.owner_id, base, bind,
        async () => { validateSourceRefs(this.context.source_refs); return this.runtime.assertGeneric(session); },
        (kind, data) => { this.retain(attempt, kind, data); });
    }
    attempt.dispatch = (async () => {
      try {
        if (attempt.review) {
          attempt.binding = { ...base, child_id: null, job_id: null };
          this.retain(attempt, "binding_reserved", { binding: attempt.binding });
          await bind(attempt.binding, attempt.abort.signal);
          this.retain(attempt, "binding_committed", { binding: attempt.binding });
        }
        need(!attempt.abort.signal.aborted, "CANCELLED_BEFORE_SUBMISSION");
        validateSourceRefs(this.context.source_refs);
        attempt.submitted = true;
        attempt.toolResult = await tool.execute(id, args, attempt.abort.signal, update => {
          if (update.details) this.retain(attempt, "native_update", { details: update.details });
        });
        this.retain(attempt, "native_tool_result", { result: attempt.toolResult });
        if (attempt.review) {
          need(object(attempt.toolResult.details), "NO_REVIEW");
          // Never grade or rewrite compact native 0/1/2/4 and evidence details.
          attempt.terminal = { status: attempt.cancelRequested || attempt.abort.signal.aborted ? "cancelled" : "completed", native_result: attempt.toolResult.details,
            binding: attempt.binding, startup: attempt.startup, locally_settled: true, submitted: true, retry_authorized: false, evidence_dir: attempt.dir };
        } else {
          // Wrapped async dispatch returns its pending job before the reservation hook runs.
          const jobs = session.asyncJobManager.getAllJobs({ ownerId: base.owner_id });
          need(jobs.length === 1 && jobs[0].type === "task", "NATIVE_JOB_SET_MISMATCH");
          const job = jobs[0];
          await job.promise; // Includes reservation/ACK and terminal work; cancelled status alone is not settlement.
          need(attempt.barrier?.binding, "NATIVE_SUBMISSION_WITHOUT_BINDING");
          need(job.id === attempt.barrier.binding.job_id && job.agentId === attempt.barrier.binding.child_id && job.ownerId === base.owner_id, "NATIVE_BINDING_MISMATCH");
          const progress = job.latestDetails?.progress?.find((value: Obj) => value.id === job.agentId);
          const workerIdentity = { model: progress?.resolvedModel ?? null, model_identity: progress?.resolvedModelIdentity ?? null,
            fallback: progress?.resolvedModelIsFallback ?? null, model_role: progress?.modelRole ?? null };
          const snapshot = { id: job.id, agent_id: job.agentId, owner_id: job.ownerId, status: job.status, end_time: job.endTime ?? null,
            structured: job.structured ?? null, result_text: job.resultText ?? null, error_text: job.errorText ?? null,
            details: job.latestDetails ?? null, worker_identity: workerIdentity };
          this.retain(attempt, "native_job_settled", snapshot);
          let carrier: Obj | null = null;
          if (!attempt.cancelRequested && !attempt.abort.signal.aborted) {
            need(attempt.barrier.committed, "BINDING_NOT_COMMITTED"); carrier = strictCarrier(job, attempt.task!);
          }
          attempt.terminal = { status: attempt.cancelRequested || attempt.abort.signal.aborted ? "cancelled" : "completed", native_result: carrier,
            native_settlement: snapshot, binding: attempt.binding, startup: attempt.startup, locally_settled: true, submitted: true, retry_authorized: false, evidence_dir: attempt.dir };
        }
      } catch (error) {
        attempt.failure ??= errorInfo(error);
        attempt.abort.abort(error);
        // Settle every job owned by this bounded session, including a submission
        // that failed before reporting its binding; never infer no effects.
        const jobs = session.asyncJobManager?.getAllJobs({ ownerId: base.owner_id }) ?? [];
        for (const job of jobs) { session.asyncJobManager.watchJobs([job.id]); session.asyncJobManager.cancel(job.id, { ownerId: base.owner_id }); }
        await Promise.all(jobs.map(job => job.promise));
        const unboundSubmission = attempt.submitted && !attempt.binding;
        attempt.terminal = { status: attempt.cancelRequested ? "cancelled" : unboundSubmission ? "unknown" : "blocked",
          native_result: null, binding: attempt.binding ?? null, error: attempt.failure, locally_settled: true, submitted: attempt.submitted,
          native_settlement: jobs.map(job => ({ job_id: job.id, status: job.status, end_time: job.endTime ?? null, structured: job.structured ?? null })),
          evidence_dir: attempt.dir, retry_authorized: false };
      } finally {
        if (attempt.terminal) {
          // Durable copy BEFORE consuming/unwatching native retained results.
          attempt.resultPath = this.retain(attempt, "terminal", attempt.terminal);
          attempt.phase = attempt.terminal.status;
          const jobs = session.asyncJobManager?.getAllJobs({ ownerId: base.owner_id }) ?? [];
          session.asyncJobManager?.consumeJobResults(jobs.map(job => job.id));
          session.asyncJobManager?.unwatchJobs(jobs.map(job => job.id));
          await session.sessionManager.flush();
        }
      }
    })();
    // JSONL continues to service binding_ack / observe / cancel concurrently.
    attempt.dispatch.catch(error => {
      attempt.failure = errorInfo(error); attempt.phase = "unknown";
      attempt.terminal = { status: "unknown", native_result: null, error: attempt.failure, binding: attempt.binding ?? null,
        locally_settled: false, evidence_dir: attempt.dir, retry_authorized: false };
    });
    return reply(request, "running", { phase: attempt.phase, submitted: attempt.submitted, evidence_dir: attempt.dir });
  }
  private recover(request: Request): Reply {
    keys(request.payload, [], "INVALID_RECONCILE_PAYLOAD");
    const dir = this.directory(request.attempt_id);
    if (!fs.existsSync(dir)) return reply(request, "unknown", { retry_authorized: false, reason: "NO_RETAINED_NATIVE_OBSERVATION" });
    const names = fs.readdirSync(dir).filter(name => /^\d{6}-.*\.json$/.test(name)).sort();
    const records = names.map(name => readJson(path.join(dir, name)));
    need(records.length > 0 && records.every(record => record.run_id === request.run_id && record.attempt_id === request.attempt_id && record.owner_epoch <= request.owner_epoch), "RECONCILE_IDENTITY_MISMATCH");
    const terminal = records.findLast(record => record.kind === "terminal");
    if (terminal) return reply(request, terminal.status, { ...terminal, retained_observation: true, replayed: false }, terminal.error ?? null);
    return reply(request, "unknown", { reason: "NATIVE_SUBMISSION_REQUIRES_RECONCILIATION", evidence_dir: dir,
      binding: records.findLast(record => record.binding)?.binding ?? null, locally_settled: false, retry_authorized: false, replayed: false });
  }
  close(): Promise<void> {
    return this.closePromise ??= this.dispose();
  }
  private async dispose(): Promise<void> {
    this.acknowledgements.close();
    for (const attempt of this.attempts.values()) {
      attempt.abort.abort(new Refusal("CONTROLLER_CHANNEL_CLOSED"));
      const manager = attempt.session?.asyncJobManager, owner = attempt.session?.getAgentId();
      for (const job of manager?.getAllJobs({ ownerId: owner! }) ?? []) manager!.cancel(job.id, { ownerId: owner! });
    }
    const dispatches = await Promise.allSettled([...this.attempts.values()].flatMap(attempt => attempt.dispatch ? [attempt.dispatch] : []));
    const sessions = await Promise.allSettled([...this.attempts.values()].flatMap(attempt => attempt.session ? [attempt.session.dispose()] : []));
    const failures = [...dispatches, ...sessions].flatMap(result => result.status === "rejected" ? [result.reason] : []);
    if (failures.length) throw new AggregateError(failures, "Native attempt/session teardown failed");
    await this.runtime.dispose();
  }
}

export async function main(argv = process.argv.slice(2)): Promise<void> {
  need(argv.length === 2 && argv[0] === "--context", "USAGE", { usage: "adapter.ts --context <absolute-execution-context.json>" });
  absolute(argv[1]);
  // Native extension diagnostics must not corrupt the JSON-lines wire.
  console.log = console.info = console.debug = (...args: any[]) => console.error(...args);
  const context = validateContext(readJson(argv[1])), runtime = await loadNative(context);
  let stdoutTail = Promise.resolve();
  const emit = (value: Reply) => {
    const line = `${JSON.stringify(value)}\n`;
    stdoutTail = stdoutTail.then(() => new Promise<void>((resolve, reject) => {
      process.stdout.write(line, error => error ? reject(error) : resolve());
    }));
    // Keep IPC failures observed while native dispatch/settlement continues.
    // The original rejected tail still propagates from the final drain below.
    stdoutTail.catch(() => {});
  };
  const broker = new AckBroker(emit), adapter = new OmpAdapter(context, runtime, broker);
  const pending = new Set<Promise<void>>();
  const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
  try {
    for await (const line of lines) {
      let request: Request;
      try { request = validateRequest(JSON.parse(line)); }
      catch (error) { throw new Refusal("INVALID_IPC_REQUEST", "Invalid JSON-lines request", errorInfo(error)); }
      if (request.operation === "binding_ack") {
        try { broker.acknowledge(request); }
        catch (error) { emit(reply(request, "blocked", {}, errorInfo(error))); }
        continue;
      }
      const work = adapter.handle(request).then(emit, error => emit(reply(request, "blocked", {}, errorInfo(error))));
      pending.add(work); work.finally(() => pending.delete(work));
    }
  } finally {
    // EOF is not retry authority; cancel local work and retain settlement.
    lines.close(); broker.close();
    try { await Promise.all(pending); }
    finally {
      try { await adapter.close(); }
      finally { await stdoutTail; }
    }
  }
}
if (import.meta.main) main().catch(error => { process.stderr.write(`${JSON.stringify(errorInfo(error))}\n`); process.exitCode = 1; });
