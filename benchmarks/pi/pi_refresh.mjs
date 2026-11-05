// Host-side refresh of the judge's Pi login, run before scoring starts.
// The judge's container gets only a copy of the credential, so it must never need
// to refresh: a refresh there would rotate the token without saving it here.
import { pathToFileURL } from 'node:url';
import { MODEL, PROVIDER, failure, requirePinnedRuntime, safeFailure } from './pi_judge.mjs';

// getAuth refreshes under Pi's auth.json.lock only when less than
// minOAuthValidityMs remains, then rejects a token that is still too short.
export async function refreshLogin(runtime, minOAuthValidityMs) {
  if (!runtime.isUsingSubscription(PROVIDER)) {
    throw failure('authentication', 'Pi subscription login required');
  }
  const model = runtime.getModel(PROVIDER, MODEL);
  if (!model) throw failure('runtime', 'Pinned judge model unavailable');
  const auth = await runtime.getAuth(model, { minOAuthValidityMs });
  if (auth?.source !== 'OAuth') throw failure('authentication', 'Pi subscription login required');
}

async function main() {
  try {
    const [authPath, validity] = process.argv.slice(2);
    const minOAuthValidityMs = Number(validity);
    if (!authPath || !Number.isSafeInteger(minOAuthValidityMs) || minOAuthValidityMs <= 0) {
      throw failure('request', 'Expected an auth.json path and a validity in milliseconds');
    }
    await requirePinnedRuntime();
    const { ModelRuntime } = await import('@earendil-works/pi-coding-agent');
    const runtime = await ModelRuntime.create({ authPath, modelsPath: null, allowModelNetwork: false });
    await refreshLogin(runtime, minOAuthValidityMs);
    process.stdout.write(JSON.stringify({ ok: true }) + '\n');
  } catch (error) {
    process.stdout.write(JSON.stringify({ ok: false, error: safeFailure(error) }) + '\n');
    process.exitCode = 1;
  }
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  await main();
}
