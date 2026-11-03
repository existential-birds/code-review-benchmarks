import test from 'node:test';
import assert from 'node:assert/strict';
import { completeJudge, safeFailure } from '../benchmarks/pi/pi_judge.mjs';
import { refreshLogin } from '../benchmarks/pi/pi_refresh.mjs';

const request = {
  messages: [{ role: 'user', content: 'Original AACR prompt' }],
  temperature: 0, max_tokens: 3000, top_p: 0.95,
  extra_body: { chat_template_kwargs: { enable_thinking: false } },
};
const response = {
  provider: 'openai-codex', model: 'gpt-6.1-sol',
  content: [{ type: 'thinking', thinking: 'private reasoning' }, { type: 'text', text: 'Rationale\n[1]' }],
  stopReason: 'stop', usage: { input: 1, output: 2 },
};
function runtime(result = response) {
  return {
    isUsingSubscription: () => true,
    checkAuth: async () => ({ type: 'oauth' }),
    getModel: (provider, model) => ({ provider, id: model }),
    complete: async (...args) => { runtime.lastCall = args; return result; },
  };
}

test('preserves AACR prompt while omitting subscription-rejected sampling options', async () => {
  const result = await completeJudge(runtime(), request);
  assert.equal(result.text, 'Rationale\n[1]');
  assert.deepEqual(runtime.lastCall[0], { provider: 'openai-codex', id: 'gpt-6.1-sol' });
  assert.equal(runtime.lastCall[1].messages[0].content, request.messages[0].content);
  const { onResponse, ...options } = runtime.lastCall[2];
  assert.equal(typeof onResponse, 'function');
  assert.deepEqual(options, { transport: 'sse', maxRetries: 0 });
  assert.equal(result.metadata.subscription, true);
  assert.equal(result.metadata.effective_options.temperature, null);
  assert.equal(result.metadata.effective_options.server_output_limit, null);
  assert.deepEqual(result.metadata.effective_options.unsupported_upstream_options,
    ['temperature', 'top_p', 'chat_template_kwargs', 'max_tokens']);
});

test('rejects absent OAuth without attempting completion', async () => {
  const r = runtime();
  r.isUsingSubscription = () => false;
  r.complete = () => assert.fail('must not use fallback');
  await assert.rejects(completeJudge(r, request), { category: 'authentication' });
});

test('rejects SDK errors, aborted results, absent and nontext output', async () => {
  for (const value of [
    { ...response, stopReason: 'error', errorMessage: 'Bearer secret' },
    { ...response, stopReason: 'aborted' },
    { ...response, content: [] },
    { ...response, content: [{ type: 'text', text: '' }] },
    { ...response, content: [{ type: 'toolCall', id: 't' }] },
    undefined,
  ]) {
    const r = runtime();
    r.complete = async () => value;
    await assert.rejects(completeJudge(r, request));
  }
});

test('safely classifies SDK auth failures without dumping credentials', () => {
  const failure = safeFailure(Object.assign(new Error('Bearer secret'), { code: 'oauth' }));
  assert.equal(failure.category, 'authentication');
  assert.equal(JSON.stringify(failure).includes('secret'), false);
});

test('retains HTTP status and distinguishes authentication rejection', async () => {
  const r = runtime();
  r.complete = async (_model, _context, options) => {
    await options.onResponse({ status: 401, headers: { Authorization: 'Bearer private' } });
    return { ...response, stopReason: 'error', errorMessage: 'private token' };
  };
  try {
    await completeJudge(r, request);
    assert.fail('must fail');
  } catch (error) {
    const saved = safeFailure(error);
    assert.equal(saved.category, 'authentication');
    assert.equal(saved.http_status, 401);
    assert.equal(saved.stop_reason, 'error');
    assert.equal(JSON.stringify(saved).includes('private'), false);
  }
});

test('rejects completion identity different from selected judge', async () => {
  const r = runtime({ ...response, provider: 'openai' });
  await assert.rejects(completeJudge(r, request), { category: 'runtime' });
});

test('host refresh asks Pi for the pinned judge model with the requested validity', async () => {
  const calls = [];
  const r = runtime();
  r.getAuth = async (...args) => { calls.push(args); return { auth: { apiKey: 'token' }, source: 'OAuth' }; };
  await refreshLogin(r, 1_800_000);
  assert.deepEqual(calls, [[{ provider: 'openai-codex', id: 'gpt-6.1-sol' }, { minOAuthValidityMs: 1_800_000 }]]);
});

test('host refresh fails without a subscription login or when Pi cannot refresh', async () => {
  const absent = runtime();
  absent.isUsingSubscription = () => false;
  absent.getAuth = () => assert.fail('must not refresh another credential');
  await assert.rejects(refreshLogin(absent, 1_800_000), { category: 'authentication' });
  for (const getAuth of [async () => { throw Object.assign(new Error('Bearer secret'), { code: 'oauth' }); },
                         async () => undefined, async () => ({ auth: { apiKey: 'k' }, source: 'env' })]) {
    const r = runtime();
    r.getAuth = getAuth;
    const error = await refreshLogin(r, 1_800_000).catch((e) => e);
    assert.equal(safeFailure(error).category, 'authentication');
  }
});
