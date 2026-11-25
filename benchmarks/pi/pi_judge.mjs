// Pi SDK bridge for a judge that sends one user message, such as AACR's scorer.
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const PROVIDER = 'openai';
export const MODEL = 'gpt-6.1-sol';
// The judge slot (#81) a task asks for; without one, the judge every catalog used before slots.
export const DEFAULT_JUDGE = { harness: 'pi', provider: PROVIDER, model: MODEL, effort: 'default' };
// What the openai-responses API takes as reasoningEffort; it has no 'off'.
const EFFORTS = ['minimal', 'low', 'medium', 'high', 'xhigh', 'max'];
const SDK_VERSION = '1.6.0';

export function judgeSlot(value = DEFAULT_JUDGE) {
  const keys = ['harness', 'provider', 'model', 'effort'];
  if (!value || typeof value !== 'object' || Object.keys(value).sort().join() !== [...keys].sort().join()
      || keys.some((key) => typeof value[key] !== 'string' || !value[key].trim())
      || value.harness !== 'pi' || value.provider !== PROVIDER
      || (value.effort !== 'default' && !EFFORTS.includes(value.effort))) {
    throw failure('request', 'Invalid judge slot');
  }
  return value;
}

export function failure(category, message, details = {}) {
  return Object.assign(new Error(message), { category, ...details });
}

// Never serialize arbitrary SDK errors: causes can contain HTTP/auth details.
export function safeFailure(error) {
  const categories = new Set(['authentication', 'transport', 'output', 'runtime', 'request']);
  let category = categories.has(error?.category) ? error.category : 'transport';
  if (['auth', 'oauth'].includes(error?.code)) category = 'authentication';
  const result = { category, message: `Pi subscription judge ${category} failure` };
  if (Number.isInteger(error?.http_status) && error.http_status >= 100 && error.http_status <= 599) {
    result.http_status = error.http_status;
  }
  if (['error', 'aborted'].includes(error?.stop_reason)) result.stop_reason = error.stop_reason;
  return result;
}

export async function completeJudge(runtime, request, judge = DEFAULT_JUDGE) {
  const { provider, model: modelId, effort } = judgeSlot(judge);
  if (!Array.isArray(request?.messages) || request.messages.length !== 1
      || request.messages[0]?.role !== 'user'
      || typeof request.messages[0].content !== 'string') {
    throw failure('request', 'Expected a single user prompt');
  }
  if (!runtime.isUsingSubscription(provider)
      || (await runtime.checkAuth(provider))?.type !== 'oauth') {
    throw failure('authentication', 'Pi subscription login required');
  }
  const model = runtime.getModel(provider, modelId);
  if (!model) throw failure('runtime', 'Pinned judge model unavailable');
  let httpStatus = null;
  const options = {
    // The subscription endpoint rejects temperature with HTTP 400, so AACR's
    // requested value is kept in the evidence but not sent.
    transport: 'sse', maxRetries: 0,
    onResponse: ({ status }) => { httpStatus = status; },
    ...(effort === 'default' ? {} : { reasoningEffort: effort }),
  };
  let result;
  try {
    result = await runtime.complete(model, {
      messages: [{ role: 'user', content: request.messages[0].content, timestamp: Date.now() }],
    }, options);
  } catch (error) {
    if ([401, 403].includes(httpStatus)) {
      throw failure('authentication', 'Subscription request rejected', { http_status: httpStatus });
    }
    throw error;
  }
  if (!result || ['error', 'aborted'].includes(result.stopReason)) {
    throw failure([401, 403].includes(httpStatus) ? 'authentication' : 'transport',
      'Pi did not complete the judge request', { http_status: httpStatus, stop_reason: result?.stopReason });
  }
  if (result.provider !== provider || result.model !== modelId) {
    throw failure('runtime', 'Pi response has unexpected judge identity');
  }
  if (!['stop', 'length'].includes(result.stopReason) || !Array.isArray(result.content)) {
    throw failure('output', 'Pi did not return completed text');
  }
  const blocks = result.content.filter((block) => block.type === 'text');
  if (blocks.some((block) => typeof block.text !== 'string')) {
    throw failure('output', 'Pi returned invalid text');
  }
  const text = blocks.map((block) => block.text).join('\n');
  if (!text.trim()) throw failure('output', 'Pi returned no text');
  return {
    ok: true,
    text,
    metadata: {
      provider, model: modelId, subscription: true,
      sdk_version: SDK_VERSION, pi_ai_version: SDK_VERSION,
      node_version: process.versions.node,
      auth_type: 'oauth', stop_reason: result.stopReason, http_status: httpStatus,
      response_model: result.responseModel ?? null,
      provider_thinking_level: result.providerThinkingLevel ?? null,
      usage: result.usage ?? null,
      effective_options: {
        temperature: null,
        transport: 'sse', max_retries: 0,
        reasoning: effort === 'default' ? 'provider default (no reasoningEffort override)' : effort,
        models_path: null, allow_model_network: false,
        server_output_limit: null,
        unsupported_upstream_options: ['temperature', 'top_p', 'chat_template_kwargs', 'max_tokens'],
      },
    },
  };
}

async function packageVersion(name) {
  let directory = dirname(fileURLToPath(import.meta.resolve(name)));
  while (true) {
    try {
      const data = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'));
      if (data.name === name) return data.version;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    const parent = dirname(directory);
    if (parent === directory) throw failure('runtime', 'Cannot verify Pi version');
    directory = parent;
  }
}

export async function requirePinnedRuntime() {
  const [major, minor] = process.versions.node.split('.').map(Number);
  if (major < 22 || (major === 22 && minor < 19)) {
    throw failure('runtime', 'Pi requires Node >=22.19');
  }
  for (const name of ['@earendil-works/pi-coding-agent', '@earendil-works/pi-ai']) {
    if (await packageVersion(name) !== SDK_VERSION) throw failure('runtime', 'Wrong Pi SDK version');
  }
}

async function main() {
  try {
    await requirePinnedRuntime();
    let input = '';
    for await (const chunk of process.stdin) input += chunk;
    const request = JSON.parse(input);
    const judge = process.argv[2] ? JSON.parse(process.argv[2]) : DEFAULT_JUDGE;
    const { ModelRuntime } = await import('@earendil-works/pi-coding-agent');
    const runtime = await ModelRuntime.create({
      authPath: '/run/judge-pi/auth.json', modelsPath: null, allowModelNetwork: false,
    });
    process.stdout.write(JSON.stringify(await completeJudge(runtime, request, judge)) + '\n');
  } catch (error) {
    process.stdout.write(JSON.stringify({ ok: false, error: safeFailure(error) }) + '\n');
    process.exitCode = 1;
  }
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  await main();
}
