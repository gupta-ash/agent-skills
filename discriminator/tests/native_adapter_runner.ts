/** Source-only executeReview boundary runner: real adapter/helper, no native provider or auth. */
import {createHash, randomUUID} from 'node:crypto';
import {
  appendFileSync, copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync,
  realpathSync, rmSync, statSync, writeFileSync, mkdtempSync
} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const config = JSON.parse(await Bun.stdin.text());
const codeNames = ['native_review.py', 'request.py', 'packet.py', 'envelope.py', 'envelope_schema.py', 'result.py'];
const sha256 = (bytes: string | Uint8Array) => createHash('sha256').update(bytes).digest('hex');
const source = readFileSync(config.extension, 'utf8');
const imports = source.match(/^import[^\n]*$/gm) ?? [];
for (const declaration of imports) {
  if (!declaration.trimEnd().endsWith(';') || !(
    declaration.startsWith('import type ') || declaration.includes("from 'node:") ||
    declaration === "import {Tokenizer} from '@oh-my-pi/pi-agent-core';" ||
    declaration === "import {resolveApiKeyOnce, streamSimple} from '@oh-my-pi/pi-ai';"
  )) throw new Error('Unexpected adapter import: ' + declaration);
}

let providerCalls = 0;
let authCalls = 0;
let active: {
  controller: AbortController; checks: unknown[]; contexts: unknown[];
} | null = null;

// Deterministic synthetic accounting only; it is not SDK/provider token evidence.
class SyntheticTokenizer {
  constructor(_model: unknown) {}
  checkTokenBudget(input: string[], budget: number) {
    const tokens = config.synthetic_tokens ?? Math.ceil(input.join('\n').length / 4);
    const checked = config.budget_result ?? {tokens, fits: tokens <= budget, exact: false};
    active!.checks.push({input, budget, checked});
    return checked;
  }
}

// An explicit path lets an integrator reuse this runner for a separate real-tokenizer smoke.
// Permanent offline tests do not depend on a workstation SDK installation.
const Tokenizer = config.tokenizer_module ?
  (await import(pathToFileURL(config.tokenizer_module).href)).Tokenizer : SyntheticTokenizer;

function changeHelper() {
  appendFileSync(config.helper, '\n# Synthetic active-invocation byte change.\n');
}

async function resolveApiKeyOnce(resolver: () => unknown, _signal: AbortSignal) {
  authCalls++;
  const value = await resolver();
  if (config.scenario === 'change-before-inference') changeHelper();
  if (config.scenario === 'change-declaration-before-inference') appendFileSync(config.declaration, '\n ');
  return value;
}

function streamSimple(model: {provider: string; id: string; api: string}, context: unknown, _options: unknown) {
  providerCalls++;
  active!.contexts.push(context);
  if (config.scenario === 'change-during-stream') changeHelper();
  if (config.scenario === 'change-declaration-during-stream') appendFileSync(config.declaration, '\n ');
  const raw = JSON.stringify(config.review);
  const message = {
    role: 'assistant', provider: model.provider, model: model.id, api: model.api,
    stopReason: 'stop', content: [{type: 'text', text: raw}], timestamp: Date.now()
  };
  const stream = {
    done: false, resultSettled: false, hasPendingLocalWork: true,
    async *[Symbol.asyncIterator]() {
      yield {type: 'text_delta', contentIndex: 0, delta: raw};
      if (config.scenario === 'cancel-during-stream') active!.controller.abort();
      // Even a valid late approval and settled local stream must not defeat cancellation.
      yield {type: 'done', reason: 'stop', message};
      stream.done = true;
    },
    async result() {
      stream.resultSettled = true;
      stream.hasPendingLocalWork = false;
      return message;
    }
  };
  return stream;
}

// Transpile the complete literal adapter, not an extracted/reimplemented executeReview.
// Only module syntax/import locations change; Node primitives stay real, and the three
// native tokenizer/provider/auth imports above are the explicit offline boundaries.
const executable = source.replace(/^import[^\n]*\n/gm, '')
  .replace(/^export default (?=function )/m, '')
  .replaceAll('import.meta.url', JSON.stringify(pathToFileURL(config.extension).href));
const compiled = new Bun.Transpiler({loader: 'ts'}).transformSync(executable);
const bindings = {createHash, randomUUID, appendFileSync, copyFileSync, mkdirSync, readFileSync,
  realpathSync, rmSync, statSync, writeFileSync, mkdtempSync, os, path, fileURLToPath, pathToFileURL,
  Tokenizer, resolveApiKeyOnce, streamSimple};
const execute = new Function(...Object.keys(bindings), compiled + '\nreturn executeReview;')(...Object.values(bindings));
if (typeof execute !== 'function') throw new Error('Missing source executeReview');

function retained(filename: string | null) {
  return filename ? JSON.parse(readFileSync(filename, 'utf8')) : null;
}

const invocations = [];
for (let index = 0; index < (config.invocations ?? 1); index++) {
  if (index > 0 && config.between_invocations_append)
    appendFileSync(config.helper, config.between_invocations_append);
  const codeHashes = Object.fromEntries(codeNames.map(name =>
    [name, sha256(readFileSync(path.join(path.dirname(config.helper), name)))]));
  const controller = new AbortController();
  active = {controller, checks: [], contexts: []};
  if (config.scenario === 'cancel-before-call') controller.abort();
  const beforeProvider = providerCalls;
  const beforeAuth = authCalls;
  const ctx = {
    cwd: config.cwd,
    sessionManager: {getSessionId: () => 'synthetic-caller-session'},
    modelRegistry: {
      find(provider: string, id: string) {
        return {provider, id, api: id.startsWith('claude-') ? 'anthropic-messages' : 'openai-responses',
          input: ['text', 'image'], compat: {},
          contextWindow: config.context_window ?? 1_000_000, maxTokens: config.max_tokens ?? 8192};
      },
      resolver() { return async () => 'offline-test-auth-not-a-live-credential'; }
    }
  };
  const result = await execute('offline-adapter-' + index, {
    caller_family: config.caller_family ?? 'gpt', contract: 'code-fleet', source: config.source
  }, controller.signal, ctx);
  const compact = result.details;
  const receipt = retained(compact.receipt_path);
  const evidence = compact.receipt_path ? path.dirname(compact.receipt_path) : null;
  const optional = (name: string) => evidence && existsSync(path.join(evidence, name)) ?
    retained(path.join(evidence, name)) : null;
  invocations.push({
    result, receipt, delivery: retained(compact.result_path),
    prepared: optional('prepare-output.json'), handshake: optional('handshake-output.json'),
    evidence_files: evidence ? readdirSync(evidence) : [],
    provider_calls: providerCalls - beforeProvider, auth_calls: authCalls - beforeAuth,
    tokenizer_checks: active.checks, provider_contexts: active.contexts,
    code_hashes: codeHashes, helper_sha256_after: sha256(readFileSync(config.helper))
  });
}
process.stdout.write(JSON.stringify({
  source_only: true,
  tokenizer: config.tokenizer_module ? {kind: 'native-module', path: config.tokenizer_module} :
    {kind: 'deterministic-test-double'},
  extension_sha256: sha256(readFileSync(config.extension)), provider_calls: providerCalls, invocations
}));
