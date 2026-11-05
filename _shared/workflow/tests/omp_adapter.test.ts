import { afterEach, describe, expect, test } from "bun:test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import {
  AckBroker, OmpAdapter, ReservationBarrier, Refusal, canonical,
  durableJson, loadArgumentValidator, requireEnabledTool, sha256, strictCarrier, validateContext, validateRequest, validateSourceRefs,
  type Binding, type ExecutionContext, type NativeJob, type NativeManager, type NativeRuntime, type NativeSession, type Request,
} from "../harness/omp/adapter";

const dirs: string[] = [];
afterEach(() => { for (const dir of dirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true }); });
const deferred = <T>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(yes => { resolve = yes; });
  return { promise, resolve };
};
function fixture() { const dir = fs.mkdtempSync(path.join(os.tmpdir(), "wiki-omp-unit-")); dirs.push(dir); return dir; }
function request(operation: string, payload: any = {}, extra: Partial<Request> = {}): Request {
  return { request_id: operation, run_id: "run", attempt_id: "attempt", owner_epoch: 1, operation, payload, ...extra };
}
const base: Omit<Binding, "child_id" | "job_id"> = { backend: "omp", session_id: "session", session_file: "session.jsonl", tool_call_id: "call", owner_id: "Main" };
function job(overrides: Partial<NativeJob> = {}): NativeJob {
  return { id: "Child_2", agentId: "Child", ownerId: "Main", type: "task", status: "running", promise: Promise.resolve(), ...overrides };
}
class Manager implements NativeManager {
  jobs: NativeJob[] = [job()]; calls: string[] = [];
  getAllJobs(filter?: { ownerId: string }) { return this.jobs.filter(j => !filter || j.ownerId === filter.ownerId); }
  getJob(id: string) { return this.jobs.find(j => j.id === id); }
  watchJobs(ids: string[]) { this.calls.push(`watch:${ids.join()}`); return ids.length; }
  unwatchJobs(ids: string[]) { this.calls.push(`unwatch:${ids.join()}`); return ids.length; }
  consumeJobResults(ids: string[]) { this.calls.push(`consume:${ids.join()}`); return ids.length; }
  cancel(id: string, filter?: { ownerId: string }) {
    const j = this.getJob(id); if (!j || (filter && filter.ownerId !== j.ownerId) || j.status !== "running") return false;
    this.calls.push(`cancel:${id}`); j.status = "cancelled"; return true;
  }
}
const spawn = { type: "before_subagent_spawn", agent: "task", invocationKind: "task", spawnKey: "Child", patterns: ["@task"] };
const task = (dir: string) => {
  const semantic = { schema_version: 1, run_id: "run", step_id: "step", item_id: "item", attempt_id: "attempt", duty: "verify",
    instructions: "Read only fixture evidence; report the bounded criteria.", inputs: [], resources: [], candidate_dir: dir,
    outputs: [], allowed_actions: ["read"], read_roots: [dir], write_roots: [],
    result_schema: { type: "object", additionalProperties: false }, artifact_id: "artifact", review_contract: null, artifact_tier: "B" };
  return { ...semantic, semantic_sha256: sha256(canonical(semantic)) };
};
const runtimeMembers = ["inert-install/bun.exe", "inert-install/omp.exe", "inert-install/payload.js", "inert-install/package/package.json"];
function context(dir: string): ExecutionContext {
  const files = ["source.json", ...runtimeMembers].map(member => path.join(dir, member));
  for (const file of files) {
    fs.mkdirSync(path.dirname(file), {recursive: true});
    fs.writeFileSync(file, file.endsWith("package.json") ? '{"name":"@oh-my-pi/pi-coding-agent"}\n' : file.endsWith(".json") ? '{"source_only_double":true}\n' : "never executed source-only runtime\n");
  }
  return { schema_version: 1, declaration_path: path.join(dir, "runtime.json"), install_manifest_path: path.join(dir, "omp-install.json"),
    cwd: dir, session_dir: path.join(dir, "sessions"), evidence_dir: path.join(dir, "evidence"),
    source_refs: files.map(file => ({path: file, sha256: sha256(fs.readFileSync(file)), media_type: file.endsWith(".json") ? "application/json" : "application/octet-stream"})) };
}

test("execution context is closed and requires the declared schema", () => {
  const ctx = context(fixture());
  expect(validateContext(ctx)).toEqual(ctx);
  for (const extra of [{ schema_version: 2 }, { semantic_override: true }, { tools: [] },
    {capability_receipt_path: path.join(ctx.cwd, "not-admission.json")}, {qualification_receipt_path: path.join(ctx.cwd, "not-admission.json")}]) {
    expect(() => validateContext({ ...ctx, ...extra })).toThrow("INVALID_EXECUTION_CONTEXT");
  }
});

test("optional model hints accept exact selectors and refuse aliases, patterns or extra context fields", () => {
  const ctx = context(fixture());
  expect(validateContext(ctx).model).toBeUndefined();
  for (const model of ["github-copilot/gpt-5.6-sol", "openrouter/vendor/model-1.0"]) {
    expect(validateContext({ ...ctx, model }).model).toBe(model);
  }
  for (const model of [undefined, null, 7, "", "gpt-5.6-sol", "github-*/gpt-5.6-sol", "github-copilot/gpt-5.6-sol:xhigh",
    " github-copilot/gpt-5.6-sol", "github-copilot/gpt-5.6-sol\n", "github-copilot/", "../model"]) {
    expect(() => validateContext({ ...ctx, model })).toThrow("INVALID_EXECUTION_CONTEXT");
  }
  expect(() => validateContext({ ...ctx, modelPattern: "github-copilot/gpt-5.6-sol" })).toThrow("INVALID_EXECUTION_CONTEXT");
});

test("native settings routes reject relative, missing, duplicate and non-file configuration", () => {
  const dir = fixture(), ctx = context(dir), config = path.join(dir, "managed.config.yml");
  fs.writeFileSync(config, "modelRoles: {}\n");
  expect(validateContext({ ...ctx, agent_dir: dir, config_files: [config] }).config_files).toEqual([config]);
  for (const route of [{ agent_dir: "relative" }, { agent_dir: config }, { config_files: [] },
    { config_files: config }, { config_files: ["relative.yml"] }, { config_files: [config, config] },
    { config_files: [dir] }, { config_files: [path.join(dir, "absent.yml")] }]) {
    expect(() => validateContext({ ...ctx, ...route })).toThrow();
  }
});

describe("native argument validator API selection", () => {
  const tool = {name: "task", execute: async () => ({})}, args = {tasks: [{agent: "task"}]};
  test("modern public API receives the unchanged tool call and payload source", async () => {
    const calls: unknown[][] = [];
    const nativeResult = {validated: true};
    const validator = await loadArgumentValidator({validateAgentToolArguments: (...values: unknown[]) => {calls.push(values); return nativeResult;}},
      async () => {throw new Error("must not load another API");});
    expect(validator.validate(tool, "call", args)).toBe(nativeResult);
    expect(calls).toEqual([[tool, {type: "toolCall", id: "call", name: "task", arguments: args}, "payload"]]);
  });
  test("absent modern API selects the real public two-argument validator", async () => {
    const calls: unknown[][] = [];
    const validator = await loadArgumentValidator({}, async () => ({validateToolArguments: (...values: unknown[]) => {calls.push(values); return args;}}));
    expect(validator.validate(tool, "call", args)).toBe(args);
    expect(calls).toEqual([[tool, {type: "toolCall", id: "call", name: "task", arguments: args}]]);
  });
  test("malformed or unavailable APIs refuse without a weaker branch", async () => {
    let otherLoads = 0;
    for (const invalid of [null, true, "function"]) {
      await expect(loadArgumentValidator({validateAgentToolArguments: invalid}, async () => {otherLoads++; return {};})).rejects.toThrow("NATIVE_API_UNAVAILABLE");
      await expect(loadArgumentValidator({}, async () => ({validateToolArguments: invalid}))).rejects.toThrow("NATIVE_API_UNAVAILABLE");
    }
    expect(otherLoads).toBe(0);
    await expect(loadArgumentValidator({}, async () => ({}))).rejects.toThrow("NATIVE_API_UNAVAILABLE");
  });
  test("native validation errors propagate without fallback or argument repair", async () => {
    for (const modern of [true, false]) {
      const failure = new Error("native arguments invalid");
      const reject = () => {throw failure;};
      const validator = await loadArgumentValidator(modern ? {validateAgentToolArguments: reject} : {}, async () => {
        if (modern) throw new Error("must not retry another API");
        return {validateToolArguments: reject};
      });
      expect(() => validator.validate(tool, "call", args)).toThrow(failure);
      expect(args).toEqual({tasks: [{agent: "task"}]});
    }
  });
});

describe("durable reservation, no inference or candidate write before ACK", () => {
  test("watch actual collision-suffixed job before emitting binding; wait for committed identical ACK", async () => {
    const manager = new Manager(), controller = new AbortController(), trace: string[] = [];
    let emitted: any;
    const broker = new AckBroker(value => { emitted = value; trace.push("emit"); });
    const barrier = new ReservationBarrier(controller, manager, "Main", base, (binding, signal) => broker.wait(request("launch_with_binding_barrier"), binding, signal), async () => {},
      kind => trace.push(kind));
    const pending = barrier.reserve(spawn);
    await Bun.sleep(0);
    expect(manager.calls).toEqual(["watch:Child_2"]);
    expect(barrier.committed).toBe(false);
    expect(emitted.payload.binding.child_id).toBe("Child");
    expect(emitted.payload.binding.job_id).toBe("Child_2");
    broker.acknowledge(request("binding_ack", { committed: true, binding: emitted.payload.binding }, { request_id: "launch_with_binding_barrier" }));
    expect(await pending).toEqual({}); expect(barrier.committed).toBe(true);
    expect(trace).toEqual(["binding_reserved", "emit", "binding_committed"]);
  });
  for (const failure of ["ack_failure", "hook_error", "closed_pipe"]) test(`${failure} blocks/cancels before inference/write`, async () => {
    const manager = new Manager(), controller = new AbortController(); let inference = 0, writes = 0;
    const barrier = new ReservationBarrier(controller, manager, "Main", base,
      async () => { throw new Error(failure); }, async () => { if (failure === "hook_error") throw new Error(failure); }, () => {});
    const result = await barrier.reserve(spawn);
    if (!result.block && !controller.signal.aborted) { inference++; writes++; }
    expect(result.block).toBe(true); expect(controller.signal.aborted).toBe(true);
    expect(inference).toBe(0); expect(writes).toBe(0); expect(manager.calls).toContain("cancel:Child_2");
  });
  test("internal five-second timeout aborts a stuck ACK and late ACK cannot release", async () => {
    const manager = new Manager(), controller = new AbortController(), ack = deferred<void>();
    const barrier = new ReservationBarrier(controller, manager, "Main", base, () => ack.promise, async () => {}, () => {});
    const result = await barrier.reserve(spawn);
    expect(result.block).toBe(true); expect(result.reason).toBe("RESERVATION_INTERNAL_TIMEOUT");
    ack.resolve(); await Bun.sleep(0);
    expect(barrier.committed).toBe(false); expect(controller.signal.aborted).toBe(true);
  }, 8_000);
  test("outer hook timeout callback synchronously cancels actual job despite SDK fail-open", async () => {
    const manager = new Manager(), controller = new AbortController();
    const barrier = new ReservationBarrier(controller, manager, "Main", base, async () => {}, async () => {}, () => {});
    barrier.runtimeFault({ event: "before_subagent_spawn", error: "handler timed out after 30000ms" });
    expect(controller.signal.aborted).toBe(true); expect(manager.calls).toEqual(["cancel:Child_2"]);
    expect((await barrier.reserve(spawn)).block).toBe(true);
  });
  test("EOF latches the binding channel closed before a late reservation can wait", () => {
    const broker = new AckBroker(() => { throw new Error("must not emit after EOF"); });
    broker.close();
    expect(() => broker.wait(request("launch_with_binding_barrier"), { ...base, child_id: "Child", job_id: "Child_2" }, new AbortController().signal)).toThrow("BINDING_CHANNEL_CLOSED");
  });
  test("wrong epoch/noncommitted/mismatched binding ACK refuses", async () => {
    for (const mode of ["epoch", "committed", "binding"]) {
      let emitted: any; const broker = new AckBroker(value => { emitted = value; }); const abort = new AbortController();
      const binding: Binding = { ...base, child_id: "Child", job_id: "Child_2" };
      const waiting = broker.wait(request("launch_with_binding_barrier"), binding, abort.signal);
      const rejected = waiting.then(() => null, error => error);
      expect(() => broker.acknowledge(request("binding_ack", { committed: mode !== "committed", binding: mode === "binding" ? { ...binding, job_id: "Other" } : binding },
        { request_id: emitted.request_id, owner_epoch: mode === "epoch" ? 2 : 1 }))).toThrow();
      expect(await rejected).toBeInstanceOf(Refusal);
    }
  });
  test("missing/ambiguous/foreign jobs and shadowed generic definition cannot release", async () => {
    for (const mode of ["missing", "duplicate", "foreign", "shadowed"]) {
      const manager = new Manager(), controller = new AbortController();
      if (mode === "missing") manager.jobs = [];
      if (mode === "duplicate") manager.jobs.push(job({ id: "Child_3" }));
      if (mode === "foreign") manager.jobs[0].ownerId = "Other";
      const barrier = new ReservationBarrier(controller, manager, "Main", base, async () => {}, async () => { if (mode === "shadowed") throw new Refusal("GENERIC_TASK_SHADOWED"); }, () => {});
      expect((await barrier.reserve(spawn)).block).toBe(true); expect(barrier.committed).toBe(false);
    }
  });
});

describe("strict carrier, bounded wire and retained native observations", () => {
  test("only caller/strict/valid/successfully-settled matching TaskResult carrier qualifies", () => {
    const spec = task(fixture());
    const good = job({ status: "completed", endTime: 1, structured: { source: "caller", mode: "strict", status: "valid", data: {
      schema_version: 1, run_id: "run", step_id: "step", item_id: "item", attempt_id: "attempt", task_sha256: spec.semantic_sha256,
    } } });
    expect(strictCarrier(good, spec)).toBe(good.structured);
    for (const patch of [{ structured: undefined }, { status: "failed" }, { endTime: undefined },
      { structured: { ...good.structured, source: "agent" } }, { structured: { ...good.structured, mode: "permissive" } },
      { structured: { ...good.structured, status: "invalid" } }, { structured: { ...good.structured, data: { ...good.structured!.data, attempt_id: "old" } } }]) {
      expect(() => strictCarrier({ ...good, ...patch }, spec)).toThrow();
    }
  });
  test("closed IPC rejects foreign fields, malformed epochs and unknown operations", () => {
    for (const raw of [{ ...request("prepare"), profile: "semantic override" }, request("unknown"), request("prepare", {}, { owner_epoch: 0 })]) expect(() => validateRequest(raw)).toThrow();
  });
  test("source authority is closed, nonempty, deduplicated and actual-byte checked", () => {
    const ctx = context(fixture()), reference = ctx.source_refs[0];
    expect(validateSourceRefs(ctx.source_refs, [reference.path])).toEqual(ctx.source_refs);
    for (const refs of [[], null, {}, [{...reference, sha256: "0".repeat(64)}], [{...reference, media_type: "text/plain"}],
      [{...reference, path: "relative.json"}], [{...reference, extra: true}], [{...reference, sha256: reference.sha256.toUpperCase()}]]) {
      expect(() => validateSourceRefs(refs)).toThrow();
    }
    expect(() => validateSourceRefs([reference, {...reference}])).toThrow("DUPLICATE_SOURCE_REF");
    expect(() => validateSourceRefs([reference, {...reference, path: path.join(ctx.cwd, "nested", "..", "source.json")}])).toThrow("DUPLICATE_SOURCE_REF");
    if (process.platform === "win32") expect(() => validateSourceRefs([reference, {...reference, path: reference.path.toUpperCase()}])).toThrow("DUPLICATE_SOURCE_REF");
    const absent = path.join(ctx.cwd, "required.json"); fs.writeFileSync(absent, "{}");
    expect(() => validateSourceRefs(ctx.source_refs, [absent])).toThrow("SOURCE_REF_REQUIRED");
    fs.appendFileSync(reference.path, "changed");
    expect(() => validateSourceRefs(ctx.source_refs)).toThrow("SOURCE_REF_CHANGED");
  });
  test("enabled task and review must be real callable tools", () => {
    for (const review of [false, true]) {
      const name = review ? "adversarial_review" : "task";
      expect(() => requireEnabledTool({getToolForEvalBridge: observed => observed === name ? {name, execute: async () => ({})} : undefined}, review)).not.toThrow();
      for (const tool of [undefined, {name}, {name, execute: true}]) {
        expect(() => requireEnabledTool({getToolForEvalBridge: () => tool as any}, review)).toThrow(review ? "NO_REVIEW" : "ENABLED_WRAPPED_TASK_UNAVAILABLE");
      }
    }
  });
  for (const member of ["source.json", ...runtimeMembers]) for (const boundary of ["prepare", "launch", "submission", "committed_ack"])
    test(`same-path ${member} tamper at ${boundary} never submits native work`, async () => {
    const ctx = context(fixture()), manager = new Manager(); manager.jobs = [];
    let sessions = 0, executions = 0, emitted: any;
    const retained = structuredClone(ctx.source_refs), tamper = () => fs.appendFileSync(path.join(ctx.cwd, member), "changed");
    const runtime: NativeRuntime = {packageRoot: ctx.cwd, fingerprint: {observation_only: true}, validate: (_t, _i, args) => {
      if (boundary === "submission") tamper();
      return args;
    },
      assertGeneric: async () => ({}), dispose: async () => {}, createSession: async () => {
        sessions++;
        return {startup: {}, session: {asyncJobManager: manager, extensionRunner: {}, effectiveExtensionRoots: {}, sessionManager: {
          getSessionId: () => "session", getSessionFile: () => path.join(ctx.cwd, "session.jsonl"), flush: async () => {},
        }, getAgentId: () => "Main", getSessionAgents: () => [], dispose: async () => {},
          getToolForEvalBridge: () => ({name: "adversarial_review", execute: async () => {executions++; return {details: {}};}})}};
      }};
    const broker = new AckBroker(value => {emitted = value;}), adapter = new OmpAdapter(ctx, runtime, broker);
    if (boundary === "prepare") tamper();
    const payload = boundary === "submission" ? {task: task(ctx.cwd)} :
      {review: {caller_family: "gpt", contract: "code-service", source: path.join(ctx.cwd, "artifact.txt"), focus: ""}};
    const preparation = adapter.handle(request("prepare", payload));
    if (boundary === "prepare") await expect(preparation).rejects.toThrow("SOURCE_REF_CHANGED");
    else {
      expect((await preparation).status).toBe("prepared");
      if (boundary === "launch") {
        tamper();
        await expect(adapter.handle(request("launch_with_binding_barrier"))).rejects.toThrow("SOURCE_REF_CHANGED");
      } else if (boundary === "submission") {
        await adapter.handle(request("launch_with_binding_barrier"));
        await Bun.sleep(0);
        const observed = await adapter.handle(request("observe"));
        expect(observed.status).toBe("blocked"); expect(observed.payload.submitted).toBe(false);
        expect(observed.error?.code).toBe("SOURCE_REF_CHANGED"); expect(emitted).toBeUndefined();
      } else {
        await adapter.handle(request("launch_with_binding_barrier"));
        tamper();
        broker.acknowledge(request("binding_ack", {committed: true, binding: emitted.payload.binding}, {request_id: "launch_with_binding_barrier"}));
        await Bun.sleep(0);
        const observed = await adapter.handle(request("observe"));
        expect(observed.status).toBe("blocked"); expect(observed.payload.submitted).toBe(false);
        expect(observed.error?.code).toBe("SOURCE_REF_CHANGED");
      }
    }
    expect(sessions).toBe(boundary === "prepare" ? 0 : 1); expect(executions).toBe(0);
    expect(ctx.source_refs).toEqual(retained);
    await adapter.close();
  });
  test("NO_REVIEW capability refuses without task/backend substitute", async () => {
    const ctx = context(fixture()), manager = new Manager(); let calls = 0;
    const runtime: NativeRuntime = { packageRoot: ctx.cwd, fingerprint: {}, assertGeneric: async () => ({}), validate: (_tool, _id, args) => args,
      dispose: async () => {},
      createSession: async () => ({ startup: {}, session: { asyncJobManager: manager, extensionRunner: {}, effectiveExtensionRoots: {}, sessionManager: {
        getSessionId: () => "native-session", getSessionFile: () => path.join(ctx.cwd, "native.jsonl"), flush: async () => {},
      }, getAgentId: () => "Main", getSessionAgents: () => [], getToolForEvalBridge: () => { calls++; return undefined; }, dispose: async () => {} } }) };
    const adapter = new OmpAdapter(ctx, runtime, new AckBroker(() => {}));
    const result = await adapter.handle(request("prepare", { review: { caller_family: "gpt", contract: "vault-wiki", source: path.join(ctx.cwd, "note.md"), focus: "bounded" } }));
    expect(result.status).toBe("blocked"); expect(result.error?.code).toBe("NO_REVIEW"); expect(calls).toBe(1);
    await adapter.close();
  });
  test("task-only context passes review purpose to native admission and retains its named pre-session refusal", async () => {
    for (const review of [false, true]) {
      const ctx = context(fixture()), manager = new Manager(); manager.jobs = [];
      let sessions = 0, lookups = 0;
      const purposes: boolean[] = [];
      // Transport-only double: the real loader's pre-SDK source guard is covered
      // with the complete default/explicit closures in omp_review_extension.test.ts.
      const runtime: NativeRuntime = {packageRoot: ctx.cwd, fingerprint: {}, assertGeneric: async () => ({}),
        validate: (_tool, _id, args) => args, dispose: async () => {}, createSession: async (_observer, _fault, purpose) => {
          purposes.push(purpose?.review ?? false);
          if (purpose?.review) throw new Refusal("SOURCE_REVIEW_EXTENSION_REQUIRED");
          sessions++;
          return {startup: {}, session: {asyncJobManager: manager, extensionRunner: {}, effectiveExtensionRoots: {}, sessionManager: {
            getSessionId: () => "task-session", getSessionFile: () => path.join(ctx.cwd, "task-session.jsonl"), flush: async () => {},
          }, getAgentId: () => "Main", getSessionAgents: () => [], dispose: async () => {}, getToolForEvalBridge: name => {
            lookups++; return {name, execute: async () => {throw new Error("must not submit");}};
          }}};
        }};
      const adapter = new OmpAdapter(ctx, runtime, new AckBroker(() => {}));
      const result = await adapter.handle(request("prepare", review ? {review: {caller_family: "gpt", contract: "code-service",
        source: path.join(ctx.cwd, "artifact.txt"), focus: ""}} : {task: task(ctx.cwd)}));
      expect(purposes).toEqual([review]); expect(sessions).toBe(review ? 0 : 1); expect(lookups).toBe(review ? 0 : 1);
      expect(result.status).toBe(review ? "blocked" : "prepared");
      if (review) {
        expect(result.error?.code).toBe("SOURCE_REVIEW_EXTENSION_REQUIRED");
        expect(result.payload.submitted).toBe(false); expect(result.payload.native_result).toBeNull();
      }
      expect(ctx.review_extension).toBeUndefined(); await adapter.close();
    }
  });
  test("cancel requests and native settlement are separate; durable copy precedes consume/unwatch", async () => {
    const ctx = context(fixture()), manager = new Manager(), settled = deferred<void>(); let observer!: (event: any) => Promise<any>; let emitted: any;
    const teardown: string[] = [];
    manager.jobs[0].promise = settled.promise;
    const spec = task(ctx.cwd), fingerprint = { kind: "double-only-observation" };
    const session: NativeSession = { asyncJobManager: manager, extensionRunner: {}, effectiveExtensionRoots: {}, sessionManager: {
      getSessionId: () => "session", getSessionFile: () => path.join(ctx.cwd, "session.jsonl"), flush: async () => {},
    }, getAgentId: () => "Main", getSessionAgents: () => [], getToolForEvalBridge: () => ({ name: "task", execute: async () => { await observer(spawn); return { details: {} }; } }), dispose: async () => { teardown.push("session"); } };
    const runtime: NativeRuntime = { packageRoot: ctx.cwd, fingerprint, validate: (_t, _i, args) => args, assertGeneric: async () => ({ source: "bundled" }),
      dispose: async () => { teardown.push("runtime"); },
      createSession: async fn => { observer = fn; return { session, startup: {} }; } };
    const broker = new AckBroker(value => { emitted = value; }), adapter = new OmpAdapter(ctx, runtime, broker);
    expect((await adapter.handle(request("prepare", { task: spec }))).status).toBe("prepared");
    await adapter.handle(request("launch_with_binding_barrier")); await Bun.sleep(0);
    broker.acknowledge(request("binding_ack", { committed: true, binding: emitted.payload.binding }, { request_id: "launch_with_binding_barrier" }));
    await Bun.sleep(0);
    const cancelled = await adapter.handle(request("cancel")); expect(cancelled.payload.locally_settled).toBe(false);
    expect((await adapter.handle(request("observe"))).status).toBe("running");
    const closing = adapter.close();
    expect(adapter.close()).toBe(closing); expect(teardown).toEqual([]);
    manager.jobs[0].endTime = Date.now(); settled.resolve(); await Bun.sleep(0);
    const observed = await adapter.handle(request("observe")); expect(observed.status).toBe("cancelled");
    const files = fs.readdirSync(observed.payload.evidence_dir); expect(files.some(file => file.endsWith("-terminal.json"))).toBe(true);
    expect(manager.calls.indexOf("consume:Child_2")).toBeGreaterThan(manager.calls.indexOf("watch:Child_2"));
    expect(manager.calls.indexOf("unwatch:Child_2")).toBeGreaterThan(manager.calls.indexOf("consume:Child_2"));
    await closing;
    expect(teardown).toEqual(["session", "runtime"]);
  });
  for (const outcome of ["completed", "cancelled", "bun_tamper_during_ack"]) test(`pending async dispatch ${outcome} retains the reservation barrier`, async () => {
    const cancelBeforeHook = outcome === "cancelled";
    const ctx = context(fixture()), manager = new Manager(), startHook = deferred<void>(), settled = deferred<void>();
    let observer!: (event: any) => Promise<any>, emitted: any, executions = 0;
    manager.jobs[0].promise = settled.promise;
    const spec = task(ctx.cwd), fingerprint = { kind: "async-dispatch-double-only-observation" };
    const session: NativeSession = { asyncJobManager: manager, extensionRunner: {}, effectiveExtensionRoots: {}, sessionManager: {
      getSessionId: () => "session", getSessionFile: () => path.join(ctx.cwd, "session.jsonl"), flush: async () => {},
    }, getAgentId: () => "Main", getSessionAgents: () => [], dispose: async () => {}, getToolForEvalBridge: () => ({ name: "task", execute: async () => {
      void (async () => {
        await startHook.promise;
        const permission = await observer(spawn), nativeJob = manager.jobs[0];
        if (!permission.block) {
          executions++;
          nativeJob.status = "completed";
          nativeJob.structured = { source: "caller", mode: "strict", status: "valid", data: {
            schema_version: 1, run_id: "run", step_id: "step", item_id: "item", attempt_id: "attempt", task_sha256: spec.semantic_sha256,
          } };
        } else nativeJob.status = "cancelled";
        nativeJob.endTime = Date.now(); settled.resolve();
      })();
      return { details: {} };
    } }) };
    const runtime: NativeRuntime = { packageRoot: ctx.cwd, fingerprint, validate: (_t, _i, args) => args, assertGeneric: async () => ({ source: "bundled" }),
      dispose: async () => {},
      createSession: async fn => { observer = fn; return { session, startup: {} }; } };
    const broker = new AckBroker(value => { emitted = value; }), adapter = new OmpAdapter(ctx, runtime, broker);
    await adapter.handle(request("prepare", { task: spec }));
    await adapter.handle(request("launch_with_binding_barrier")); await Bun.sleep(0);
    expect(executions).toBe(0); expect(emitted).toBeUndefined();
    if (cancelBeforeHook) {
      const cancellation = await adapter.handle(request("cancel"));
      expect(cancellation.payload.native_cancel_requested).toBe(true);
      expect(cancellation.payload.locally_settled).toBe(false);
    }
    startHook.resolve(); await Bun.sleep(0);
    if (!cancelBeforeHook) {
      expect(emitted.payload.binding.job_id).toBe("Child_2"); expect(executions).toBe(0);
      if (outcome === "bun_tamper_during_ack") fs.appendFileSync(path.join(ctx.cwd, runtimeMembers[0]), "changed while awaiting ACK");
      broker.acknowledge(request("binding_ack", { committed: true, binding: emitted.payload.binding }, { request_id: "launch_with_binding_barrier" }));
    }
    await settled.promise; await Bun.sleep(0);
    const observed = await adapter.handle(request("observe"));
    expect(observed.status).toBe(outcome === "completed" ? "completed" : "cancelled");
    expect(observed.payload.locally_settled).toBe(true); expect(executions).toBe(outcome === "completed" ? 1 : 0);
    if (outcome === "bun_tamper_during_ack") {
      const blocked = fs.readdirSync(observed.payload.evidence_dir).find(file => file.endsWith("-reservation_blocked.json"));
      expect(blocked).toBeDefined();
      expect(JSON.parse(fs.readFileSync(path.join(observed.payload.evidence_dir, blocked!), "utf8")).code).toBe("SOURCE_REF_CHANGED");
    }
    await adapter.close();
  });
  test("crashed unbound attempt reconciles unknown, never creates session or reruns", async () => {
    const ctx = context(fixture()), dir = path.join(ctx.evidence_dir, sha256("attempt")); fs.mkdirSync(dir, { recursive: true });
    durableJson(path.join(dir, "000000-launch_intent.json"), { run_id: "run", attempt_id: "attempt", owner_epoch: 1, kind: "launch_intent", submitted: true });
    let launches = 0;
    const runtime: NativeRuntime = { packageRoot: ctx.cwd, fingerprint: {}, validate: (_t, _i, args) => args, assertGeneric: async () => ({}),
      dispose: async () => {},
      createSession: async () => { launches++; throw new Error("must not run"); } };
    const adapter = new OmpAdapter(ctx, runtime, new AckBroker(() => {}));
    const result = await adapter.handle(request("reconcile", {}, { owner_epoch: 2 }));
    expect(result.status).toBe("unknown"); expect(result.payload.retry_authorized).toBe(false); expect(launches).toBe(0);
    await adapter.close();
  });
  test("session disposal failure propagates without disposing process resources", async () => {
    const ctx = context(fixture()), manager = new Manager(), disposal = deferred<void>(), calls: string[] = [];
    manager.jobs = [];
    const runtime: NativeRuntime = { packageRoot: ctx.cwd, fingerprint: {}, validate: (_t, _i, args) => args, assertGeneric: async () => ({}),
      dispose: async () => { calls.push("runtime"); },
      createSession: async () => ({ startup: {}, session: { asyncJobManager: manager, extensionRunner: {}, effectiveExtensionRoots: {}, sessionManager: {
        getSessionId: () => "session", getSessionFile: () => path.join(ctx.cwd, "session.jsonl"), flush: async () => {},
      }, getAgentId: () => "Main", getSessionAgents: () => [], getToolForEvalBridge: () => ({ name: "adversarial_review", execute: async () => { throw new Error("must not launch"); } }),
        dispose: async () => { calls.push("session"); await disposal.promise; throw new Error("store flush failed"); } } }) };
    const adapter = new OmpAdapter(ctx, runtime, new AckBroker(() => {}));
    await adapter.handle(request("prepare", { review: { caller_family: "gpt", contract: "vault-wiki", source: path.join(ctx.cwd, "note.md"), focus: "bounded" } }));
    const closing = adapter.close();
    const rejected = closing.then(() => null, error => error);
    await Bun.sleep(0); expect(calls).toEqual(["session"]);
    disposal.resolve(); expect(await rejected).toBeInstanceOf(AggregateError);
    expect(calls).toEqual(["session"]); expect(adapter.close()).toBe(closing);
  });
  test("runtime shutdown failures remain failures on repeated close", async () => {
    const ctx = context(fixture()); let disposed = 0;
    const runtime: NativeRuntime = { packageRoot: ctx.cwd, fingerprint: {}, validate: (_t, _i, args) => args, assertGeneric: async () => ({}),
      createSession: async () => { throw new Error("must not launch"); },
      dispose: async () => { disposed++; throw new Error("bridge stop failed"); } };
    const adapter = new OmpAdapter(ctx, runtime, new AckBroker(() => {})), closing = adapter.close();
    await expect(closing).rejects.toThrow("bridge stop failed");
    expect(adapter.close()).toBe(closing); expect(disposed).toBe(1);
  });
  test("a dispatch persistence failure cannot become a clean runtime close", async () => {
    const ctx = context(fixture()), manager = new Manager(), calls: string[] = []; let emitted: any;
    manager.jobs = [];
    const runtime: NativeRuntime = { packageRoot: ctx.cwd, fingerprint: {}, validate: (_t, _i, args) => args, assertGeneric: async () => ({}),
      dispose: async () => { calls.push("runtime"); },
      createSession: async () => ({ startup: {}, session: { asyncJobManager: manager, extensionRunner: {}, effectiveExtensionRoots: {}, sessionManager: {
        getSessionId: () => "session", getSessionFile: () => path.join(ctx.cwd, "session.jsonl"), flush: async () => { throw new Error("terminal flush failed"); },
      }, getAgentId: () => "Main", getSessionAgents: () => [], getToolForEvalBridge: () => ({ name: "adversarial_review", execute: async () => ({ details: {} }) }),
        dispose: async () => { calls.push("session"); } } }) };
    const broker = new AckBroker(value => { emitted = value; }), adapter = new OmpAdapter(ctx, runtime, broker);
    await adapter.handle(request("prepare", { review: { caller_family: "gpt", contract: "vault-wiki", source: path.join(ctx.cwd, "note.md"), focus: "bounded" } }));
    await adapter.handle(request("launch_with_binding_barrier"));
    broker.acknowledge(request("binding_ack", { committed: true, binding: emitted.payload.binding }, { request_id: "launch_with_binding_barrier" }));
    await Bun.sleep(0);
    await expect(adapter.close()).rejects.toBeInstanceOf(AggregateError);
    expect(calls).toEqual(["session"]);
  });
});

describe("explicit review image authority", () => {
  for (const fault of ["hash", "missing", "media", "directory", "duplicate", "empty", "relative", "extra"]) {
    test(`rejects ${fault} before creating a native session`, async () => {
      const ctx = context(fixture()), imagePath = path.join(ctx.cwd, "image.png");
      fs.writeFileSync(imagePath, Buffer.from([137, 80, 78, 71]));
      const image: any = {path: imagePath, sha256: sha256(fs.readFileSync(imagePath)), media_type: "image/png"};
      let images = [image];
      if (fault === "hash") image.sha256 = "0".repeat(64);
      if (fault === "missing") image.path = path.join(ctx.cwd, "absent.png");
      if (fault === "media") image.media_type = "image/svg+xml";
      if (fault === "directory") image.path = ctx.cwd;
      if (fault === "duplicate") images.push({...image});
      if (fault === "empty") images = [];
      if (fault === "relative") image.path = "image.png";
      if (fault === "extra") image.url = "https://example.invalid/not-read";
      let sessions = 0;
      const runtime: NativeRuntime = {packageRoot: ctx.cwd, fingerprint: {}, validate: () => {throw new Error("must not validate");},
        assertGeneric: async () => ({}), dispose: async () => {}, createSession: async () => {sessions++; throw new Error("must not create");}};
      const adapter = new OmpAdapter(ctx, runtime, new AckBroker(() => {}));
      await expect(adapter.handle(request("prepare", {review: {caller_family: "gpt", contract: "vault-raw", source: path.join(ctx.cwd, "source.txt"), focus: "focus.txt", images}}))).rejects.toThrow();
      expect(sessions).toBe(0);
      expect(fs.existsSync(ctx.evidence_dir)).toBe(false);
      await adapter.close();
    });
  }
});
