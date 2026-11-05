import * as fs from "node:fs";
import * as path from "node:path";
import { randomUUID } from "node:crypto";
import { pathToFileURL } from "node:url";
import type { ExecutionContext, NativeRuntime, NativeSession, ReservationBarrier as ReservationBarrierType } from "../harness/omp/adapter";

const adapterArgs = process.argv.slice(2), adapterIndex = adapterArgs.indexOf("--adapter-path");
if (adapterIndex < 0 || adapterIndex !== adapterArgs.length - 2 || !path.isAbsolute(adapterArgs[adapterIndex + 1]))
  throw new Error("An explicit absolute --adapter-path is required");
const adapterPath = adapterArgs[adapterIndex + 1];
if (!fs.lstatSync(adapterPath).isFile() || fs.lstatSync(adapterPath).isSymbolicLink()) throw new Error("Regular adapter file required");
const { AckBroker, ReservationBarrier, Refusal, canonical, durableJson,
  loadNative, requireEnabledTool, sha256, strictCarrier, taskArguments, validateContext, validateSourceRefs } = await import(pathToFileURL(adapterPath).href);
const adapterSha256 = sha256(fs.readFileSync(adapterPath)), supportSha256 = sha256(fs.readFileSync(import.meta.path));
const observationCases = ["ack_failure", "internal_timeout", "outer_hook_timeout", "hook_error", "crash", "strict_carrier", "cancellation", "no_review"];

// SOURCE ONLY. These probes exercise the installed SDK's real wrapped dispatch
// and real extension timeout/error paths. They never replace a production tool,
// install an agent definition, or grant permissions. All tasks are intentionally
// stopped BEFORE child inference. These opt-in observations grant no startup,
// source approval, worker readiness or native-review certification authority.
// Reuse the supplied current admission refs, including Bun/launcher/payload/package.json.
// Never refresh their hashes or convert this independent output into an admission receipt.
function assert(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
function json(file: string) { return JSON.parse(fs.readFileSync(file, "utf8")); }
function spec(candidate: string, attempt: string) {
  const fields = { schema_version: 1, run_id: "omp-capability", step_id: "negative-native-probe", item_id: attempt, attempt_id: attempt,
    duty: "verify", instructions: `This is a synthetic reservation negative control. Do not research, call tools, or write outside ${candidate}. If launched unexpectedly, return a blocked TaskResult.`,
    inputs: [], resources: [], candidate_dir: candidate, outputs: [], allowed_actions: ["read"], read_roots: [candidate], write_roots: [],
    result_schema: { type: "object", additionalProperties: false, required: ["probe"], properties: { probe: { type: "string" } } },
    artifact_id: attempt, review_contract: null, artifact_tier: "B" };
  return { ...fields, semantic_sha256: sha256(canonical(fields)) };
}
function files(root: string): string[] {
  const result: string[] = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const target = path.join(root, entry.name);
    if (entry.isDirectory()) result.push(...files(target)); else result.push(target);
  }
  return result;
}
async function bounded<T>(promise: Promise<T>, ms: number, abort: () => void): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try { return await Promise.race([promise, new Promise<never>((_, reject) => {
    timer = setTimeout(() => { abort(); reject(new Error(`Probe deadline ${ms}ms exceeded; observation incomplete`)); }, ms);
  })]); } finally { clearTimeout(timer); }
}
const channels = ["task:subagent:event", "task:subagent:lifecycle", "task:subagent:progress"];
const unsafeFrame = (frame: any) => frame.channel === "task:subagent:lifecycle" ||
  ["agent_start", "turn_start", "message_start", "tool_execution_start"].includes(frame.data?.event?.type) ||
  Number(frame.data?.requests ?? frame.data?.progress?.requests ?? 0) > 0;

async function faultCase(name: string, context: ExecutionContext, root: string) {
  const candidate = path.join(root, "candidates"); fs.mkdirSync(candidate, { recursive: true });
  const abort = new AbortController(), frames: any[] = [], faults: any[] = [], trace: any[] = [];
  const broker = new AckBroker(() => {}), unsubscribe: Array<() => void> = [];
  let runtime: NativeRuntime | undefined, session: NativeSession | undefined, barrier: ReservationBarrierType | undefined, hookSeen = false;
  let phase = "load_native", progressOrdinal = 0, fingerprint: NativeRuntime["fingerprint"] | null = null;
  let nativeResult: unknown = null;
  const persist = (event: string, error?: unknown) => {
    const manager = session?.asyncJobManager, owner = session?.getAgentId();
    const jobs = manager && owner ? manager.getAllJobs({ ownerId: owner }) : [];
    durableJson(path.join(root, event === "failed" ? "failure.json" : `progress-${String(++progressOrdinal).padStart(6, "0")}.json`), {
      schema_version: 1, probe: name, phase, event, admission_authority: false, fingerprint,
      session_id: session?.sessionManager.getSessionId() ?? null, session_file: session?.sessionManager.getSessionFile() ?? null,
      binding: barrier?.binding ?? null, hook_seen: hookSeen, abort_requested: abort.signal.aborted,
      native_jobs: jobs.map(job => ({ id: job.id, child_id: job.agentId, owner_id: job.ownerId, status: job.status, end_time: job.endTime ?? null })),
      ...(event === "failed" || event === "tool_returned" ? { native_tool_result: nativeResult } : {}),
      ...(event === "failed" ? { trace, frames, faults } : { latest_trace: trace.at(-1) ?? null, latest_frame: frames.at(-1) ?? null, latest_fault: faults.at(-1) ?? null }),
      ...(error === undefined ? {} : { message: error instanceof Error ? error.message : String(error) }),
    });
  };
  let hookReady!: () => void;
  const ready = new Promise<void>(resolve => { hookReady = resolve; });
  try {
    persist("before_await");
    runtime = await loadNative({ ...context, session_dir: path.join(root, "sessions") });
    fingerprint = runtime.fingerprint;
    phase = "create_native_session"; persist("before_await");
    const result = await runtime.createSession(async event => {
      hookSeen = true; trace.push({ kind: "native_spawn_hook", event }); hookReady();
      persist("native_spawn_hook");
      if (name === "outer_hook_timeout") return new Promise(() => {}); // actual native 30s runner timeout
      if (name === "hook_error") throw new Error("source-only injected reservation hook exception");
      assert(barrier, "Reservation barrier unavailable before native dispatch");
      return barrier.reserve(event);
    }, error => {
      faults.push(error);
      // Exactly the production synchronous fail-open mitigation; never await.
      barrier?.runtimeFault(error);
      abort.abort();
      persist("native_runtime_fault");
    });
    session = result.session;
    assert(result.events, "Installed SDK lacks subagent observation bus");
    for (const channel of channels) unsubscribe.push(result.events.on(channel, data => { frames.push({ channel, data }); persist("native_frame"); }));
    phase = "validate_generic_agent"; persist("before_await");
    const generic = await runtime.assertGeneric(session);
    const manager = session.asyncJobManager, owner = session.getAgentId();
    assert(manager && owner, "Missing owned native async manager");
    const id = `wf-probe-${randomUUID()}`;
    const launch = { request_id: id, run_id: "omp-capability", attempt_id: name, owner_epoch: 1, operation: "launch_with_binding_barrier", payload: {} };
    const base = { backend: "omp" as const, session_id: session.sessionManager.getSessionId(), session_file: session.sessionManager.getSessionFile(), tool_call_id: id, owner_id: owner };
    const preparedSession = session, activeRuntime = runtime;
    barrier = new ReservationBarrier(abort, manager, owner, base, async (binding, signal) => {
      trace.push({ kind: "binding_offered_without_commit", binding });
      persist("binding_offered_without_commit");
      if (name === "ack_failure") throw new Error("source-only simulated rejected SQLite commit ACK");
      if (name === "internal_timeout") return new Promise(() => {});
      await broker.wait(launch, binding, signal);
      validateSourceRefs(context.source_refs);
    }, () => {
      validateSourceRefs(context.source_refs);
      return activeRuntime.assertGeneric(preparedSession);
    }, (kind, data) => { trace.push({ kind, data }); persist("reservation_trace"); });
    const tool = session.getToolForEvalBridge("task");
    assert(tool, "Real enabled wrapped task is unavailable; no substitution permitted");
    phase = "validate_task_arguments"; persist("before_validation");
    const args = runtime.validate(tool, id, taskArguments(spec(candidate, name), name));
    phase = "await_tool_execute"; persist("before_await");
    validateSourceRefs(context.source_refs);
    const reservation = barrier;
    nativeResult = await bounded(tool.execute(id, args, abort.signal, update => { trace.push({ kind: "tool_update", update }); persist("tool_update"); }), 45_000, () => reservation.runtimeFault({ event: "probe_deadline", error: "deadline" }));
    persist("tool_returned");
    phase = "await_spawn_hook"; persist("before_await");
    await bounded(ready, 45_000, () => reservation.runtimeFault({ event: "probe_deadline", error: "hook never reached" }));
    if (name === "cancellation") {
      // Job.status changes immediately; only its promise establishes settlement.
      abort.abort(new Refusal("SOURCE_PROBE_CANCEL_REQUEST"));
      for (const job of manager.getAllJobs({ ownerId: owner })) manager.cancel(job.id, { ownerId: owner });
      trace.push({ kind: "cancel_requested", locally_settled: false });
      persist("cancel_requested");
    }
    const jobs = manager.getAllJobs({ ownerId: owner });
    assert(jobs.length === 1, "Expected exactly one actual native job, not a prose success");
    phase = "await_job_settlement"; persist("before_await");
    await bounded(Promise.all(jobs.map(job => job.promise)), 45_000, () => reservation.runtimeFault({ event: "probe_deadline", error: "unsettled job" }));
    phase = "validate_fail_closed_observations"; persist("after_await");
    assert(hookSeen && abort.signal.aborted && !barrier.committed, "Reservation did not fail closed before commit");
    assert(frames.every(frame => !unsafeFrame(frame)), "Child inference/session/tool activity occurred without binding ACK");
    assert(files(candidate).length === 0, "Candidate write occurred without binding ACK");
    assert(jobs.every(job => job.endTime !== undefined && job.status !== "completed"), "Unbound job completed or remains unsettled");
    if (name === "outer_hook_timeout") assert(faults.some(error => error.event === "before_subagent_spawn" && /timed out/.test(error.error)), "Actual SDK outer timeout callback was not observed");
    if (name === "hook_error") assert(faults.some(error => error.event === "before_subagent_spawn" && /injected/.test(error.error)), "Actual SDK hook error callback was not observed");
    let strictRejected = false;
    try { strictCarrier(jobs[0], spec(candidate, name)); } catch { strictRejected = true; }
    assert(strictRejected, "Unbound/failed native carrier was accepted");
    const snapshot = { schema_version: 1, probe: name, passed: true, proof_kind: "installed-sdk-fault-injection", fingerprint: runtime.fingerprint,
      startup: result.startup, generic, trace, frames, faults, actual_native_result: nativeResult, binding: barrier.binding ?? null,
      native_jobs: jobs.map(job => ({ id: job.id, child_id: job.agentId, owner_id: job.ownerId, status: job.status,
        end_time: job.endTime, structured: job.structured ?? null, details: job.latestDetails ?? null })),
      child_inference_frames: 0, candidate_write_count: 0, locally_settled: true, strict_carrier_rejected: strictRejected };
    durableJson(path.join(root, "evidence.json"), snapshot);
    manager.consumeJobResults(jobs.map(job => job.id)); manager.unwatchJobs(jobs.map(job => job.id));
    return snapshot;
  } catch (error) {
    persist("failed", error);
    throw error;
  } finally {
    broker.close(); abort.abort();
    try {
      const manager = session?.asyncJobManager, owner = session?.getAgentId();
      if (manager && owner) for (const job of manager.getAllJobs({ ownerId: owner })) manager.cancel(job.id, { ownerId: owner });
      for (const off of unsubscribe) off();
      phase = "dispose_native_session"; persist("before_await");
    } finally {
      try { if (session) await session.dispose(); }
      finally { if (runtime) await runtime.dispose(); }
    }
    durableJson(path.join(root, "cleanup.json"), { schema_version: 1, probe: name, native_session_created: !!session,
      native_session_dispose_returned: !!session, admission_authority: false });
  }
}

async function missingReview(context: ExecutionContext, root: string) {
  const native = await loadNative({ ...context, session_dir: path.join(root, "sessions") });
  const abort = new AbortController();
  let session: NativeSession | undefined;
  try {
    if (context.review_extension === undefined) {
      let refusal: string | null = null;
      try {
        const result = await native.createSession(async () => ({block: true, reason: "NO_PROBE_RESERVATION"}),
          () => abort.abort(), {review: true});
        session = result.session;
      } catch (error) {
        if (!(error instanceof Refusal)) throw error;
        refusal = error.code;
      }
      assert(refusal === "SOURCE_REVIEW_EXTENSION_REQUIRED" && session === undefined,
        "Task-only context did not refuse unbound review before native preparation");
      durableJson(path.join(root, "evidence.json"), {schema_version: 1, probe: "no_review", passed: true,
        admission_authority: false, proof_kind: "installed-sdk-loaded-with-production-unbound-review-guard",
        review_extension: null, native_session_returned: false, actual_review_registered: null,
        native_review_invoked: false, task_or_fallback_invoked: false, native_job_count: null,
        result: {status: "blocked", error: {code: refusal}}});
      return;
    }
    const result = await native.createSession(async () => ({ block: true, reason: "NO_PROBE_RESERVATION" }),
      () => abort.abort(), { review: true });
    session = result.session;
    assert(!abort.signal.aborted, "Native initialization fault; missing-review observation incomplete");
    const actualRegistered = !!session.getToolForEvalBridge("adversarial_review");
    assert(actualRegistered && result.startup.review_registration && result.reviewSchema &&
      canonical(result.startup.review_registration.extension) === canonical(context.review_extension),
      "Bound source review registration was not observed");
    requireEnabledTool(session, true);
    const actualSession = session;
    const absentReview = { getToolForEvalBridge: (name: string) => name === "adversarial_review" ? undefined : actualSession.getToolForEvalBridge(name) };
    // Exercise the production lookup guard with registry absence injected over
    // a real SDK session. This records the negative boundary, never admission.
    let refusal: string | null = null;
    try {
      requireEnabledTool(absentReview, true);
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
      refusal = error.code;
    }
    assert(refusal === "NO_REVIEW", "Missing native review did not refuse before execution");
    const jobs = session.asyncJobManager.getAllJobs({ ownerId: session.getAgentId() });
    assert(jobs.length === 0, "Missing-review guard created native work");
    durableJson(path.join(root, "evidence.json"), { schema_version: 1, probe: "no_review", passed: true,
      admission_authority: false, proof_kind: "installed-sdk-with-bound-source-and-production-missing-registry-guard",
      review_extension: context.review_extension, review_registration: result.startup.review_registration,
      actual_review_registered: actualRegistered,
      native_review_invoked: false, task_or_fallback_invoked: false, native_job_count: jobs.length,
      result: { status: "blocked", error: { code: refusal } } });
  } finally {
    try { if (session) await session.dispose(); } finally { await native.dispose(); }
  }
}

async function crashChild(context: ExecutionContext, root: string) {
  const runtime = await loadNative({ ...context, session_dir: path.join(root, "sessions") });
  const candidate = path.join(root, "candidates"); fs.mkdirSync(candidate, { recursive: true });
  const abort = new AbortController();
  let session: NativeSession | undefined;
  const result = await runtime.createSession(async event => {
    if (!session) return { block: true, reason: "NO_PROBE_RESERVATION_DURING_INITIALIZATION" };
    validateSourceRefs(context.source_refs);
    const jobs = session.asyncJobManager.getAllJobs({ ownerId: session.getAgentId() });
    const matching = jobs.filter(job => job.agentId === event.spawnKey);
    assert(matching.length === 1, "Crash probe could not bind actual native job");
    result.session.asyncJobManager.watchJobs([matching[0].id]);
    durableJson(path.join(root, "crash-held.json"), { schema_version: 1, event, job_id: matching[0].id,
      child_id: event.spawnKey, binding_committed: false, inference_frames: 0, hook_released: false });
    process.stdout.write(`${JSON.stringify({ probe: "crash_ready", pid: process.pid })}\n`);
    return new Promise(() => {}); // test process is killed while this real hook is held
  }, error => {
    abort.abort();
    durableJson(path.join(root, `runtime-fault-${randomUUID()}.json`), { error, admission_authority: false });
  });
  session = result.session;
  assert(!abort.signal.aborted, "Native initialization fault; crash observation incomplete");
  assert(result.events, "No real native subagent observer");
  for (const channel of channels) result.events.on(channel, data => {
    const file = path.join(root, `unexpected-child-frame-${randomUUID()}.json`);
    durableJson(file, { channel, data });
  });
  await runtime.assertGeneric(result.session);
  const tool = result.session.getToolForEvalBridge("task"); assert(tool, "No enabled real wrapped task");
  const id = `crash-${randomUUID()}`, args = runtime.validate(tool, id, taskArguments(spec(candidate, id), id));
  validateSourceRefs(context.source_refs);
  await tool.execute(id, args, abort.signal, () => {});
  // Stay finite only under the owning probe process; no test daemon is created.
  await new Promise(() => {});
}
async function crashCase(contextPath: string, context: ExecutionContext, root: string) {
  const child = Bun.spawn([process.execPath, import.meta.path, "--crash-child", contextPath, root, "--adapter-path", adapterPath], { cwd: context.cwd, stdin: "ignore", stdout: "pipe", stderr: "pipe" });
  const stderr = new Response(child.stderr).text();
  let ready = false;
  const readReady = (async () => {
    const reader = child.stdout.getReader(), decoder = new TextDecoder(); let text = "";
    try {
      while (true) {
        const chunk = await reader.read(); if (chunk.done) break;
        text += decoder.decode(chunk.value, { stream: true });
        const lines = text.split("\n"); text = lines.pop()!;
        for (const line of lines) {
          let parsed: any; try { parsed = JSON.parse(line); } catch { continue; }
          if (parsed.probe === "crash_ready" && parsed.pid === child.pid) { ready = true; return; }
        }
      }
      throw new Error("Crash child exited before real native reservation");
    } finally { reader.releaseLock(); }
  })();
  try {
    await bounded(readReady, 60_000, () => child.kill());
    assert(ready, "Real native crash barrier not reached");
    child.kill(); const exit = await child.exited;
    assert(exit !== 0 && fs.existsSync(path.join(root, "crash-held.json")), "Owned crash child was not killed in held hook");
    assert(!files(root).some(file => path.basename(file).startsWith("unexpected-child-frame-")), "Unbound child activity before crash");
    assert(files(path.join(root, "candidates")).length === 0, "Candidate write before crash");
    durableJson(path.join(root, "evidence.json"), { schema_version: 1, probe: "crash", passed: true,
      proof_kind: "installed-sdk-process-kill-before-binding", child_pid: child.pid, observed_exit: exit,
      held: json(path.join(root, "crash-held.json")), child_stderr: await stderr, child_inference_frames: 0, candidate_write_count: 0,
      native_submission: "unknown-after-process-crash", retry_authorized: false });
  } finally { if (child.exitCode === null) { child.kill(); await child.exited; } }
}

async function main() {
  const args = adapterArgs.slice(0, adapterIndex);
  if (args[0] === "--crash-child") {
    assert(args.length === 3, "Invalid source-only crash child arguments");
    await crashChild(validateContext(json(args[1])), args[2]); return;
  }
  assert(args.length === 4 && args[0] === "--context" && args[2] === "--out", "Usage: omp_sdk_smoke.ts --context <absolute-execution-context.json> --out <absolute-observation.json> --adapter-path <absolute-adapter.ts>");
  const contextPath = args[1], out = args[3];
  assert(path.isAbsolute(contextPath) && path.isAbsolute(out), "Absolute context and independent observation output paths are required");
  const context = validateContext(json(contextPath));
  assert(!fs.existsSync(out), "Never overwrite observations; select a new owned output path");
  const root = `${out}.evidence`; fs.mkdirSync(root); // expected absence, never a live source/vault root
  const cases: Record<string, any> = {};
  let currentCase: string | null = null;
  let native: NativeRuntime | undefined;
  try {
    native = await loadNative(context);
    for (const name of ["ack_failure", "internal_timeout", "outer_hook_timeout", "hook_error", "cancellation"]) {
      currentCase = name;
      const dir = path.join(root, name); fs.mkdirSync(dir);
      const observed = await faultCase(name, context, dir);
      assert(canonical(observed.fingerprint) === canonical(native.fingerprint), "Source selection changed between observation cases");
      const evidence = path.join(dir, "evidence.json"); cases[name] = { passed: true, evidence_path: evidence, evidence_sha256: sha256(fs.readFileSync(evidence)) };
    }
    // Strict-carrier rejection is observed on the actual unbound failed native
    // job above. Valid carrier positive proof remains a real worker checkpoint.
    cases.strict_carrier = { ...cases.ack_failure };
    currentCase = "crash";
    const crash = path.join(root, "crash"); fs.mkdirSync(crash); await crashCase(contextPath, context, crash);
    currentCase = "no_review";
    const noReview = path.join(root, "no_review"); fs.mkdirSync(noReview); await missingReview(context, noReview);
    for (const name of ["crash", "no_review"]) {
      const evidence = path.join(root, name, "evidence.json"); cases[name] = { passed: true, evidence_path: evidence, evidence_sha256: sha256(fs.readFileSync(evidence)) };
    }
    currentCase = null;
    assert(observationCases.every(name => cases[name]?.passed === true), "Observation matrix incomplete");
    validateSourceRefs(context.source_refs);
    assert(native.fingerprint.adapter_sha256 === adapterSha256 && sha256(fs.readFileSync(adapterPath)) === adapterSha256 &&
      sha256(fs.readFileSync(import.meta.path)) === supportSha256, "Probe adapter/support changed during observation");
    const fingerprint = native.fingerprint, settledNative = native;
    native = undefined;
    await settledNative.dispose();
    durableJson(out, { schema_version: 1, kind: "omp-sdk-observation", status: "passed", admission_authority: false, fingerprint,
      adapter_ref: { path: adapterPath, sha256: sha256(fs.readFileSync(adapterPath)), media_type: "text/typescript" },
      support_ref: { path: import.meta.path, sha256: sha256(fs.readFileSync(import.meta.path)), media_type: "text/typescript" },
      cases, observed_at: Date.now(), positive_worker_or_native_review_proof: false });
    process.stdout.write(`${JSON.stringify({ status: "passed", observation_path: out, evidence_root: root, admission_authority: false })}\n`);
  } catch (error) {
    const caseRoot = currentCase === null ? null : path.join(root, currentCase);
    const caseFailure = caseRoot === null ? null : path.join(caseRoot, "failure.json");
    const reference = (file: string) => ({ path: file, sha256: sha256(fs.readFileSync(file)), media_type: "application/json" });
    durableJson(path.join(root, "failure.json"), { schema_version: 1, status: "failed", completed_cases: cases,
      message: error instanceof Error ? error.message : String(error), admission_authority: false, current_case: currentCase,
      case_failure_ref: caseFailure !== null && fs.existsSync(caseFailure) ? reference(caseFailure) : null,
      progress_refs: caseRoot !== null ? files(caseRoot).filter(file => /^progress-\d+\.json$/.test(path.basename(file))).sort().map(reference) : [] });
    throw error;
  } finally { if (native) await native.dispose(); }
}
if (import.meta.main) main().catch(error => { process.stderr.write(`${JSON.stringify({ status: "failed", message: error.message })}\n`); process.exitCode = 1; });
