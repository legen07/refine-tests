/**
 * Standalone validation for the refine-tests Host bundle.
 *
 * The Host contract this exercises (no DSH installation needed):
 *   - `index.js` parses as ESM and exports apply + inject
 *   - apply(ctx) registers exactly one tool on a mocked ctx.tools
 *   - the tool definition satisfies the registry invariants we know:
 *     name (not reserved), parameters/output JSON schemas, output.render,
 *     execute(args, exec)
 *   - end-to-end: the tool drives the real CLI on the fixture suite
 *     (audit mode is read-only, so this is safe to run)
 *
 * Run: node dsh-host-plugin/smoke.mjs
 */

import { mkdtemp, rm, cp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const mod = await import(pathToFileURL(join(process.cwd(), 'dsh-host-plugin/index.js')).href);

let failures = 0;
const check = (label, cond, extra = '') => {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}${extra ? ` — ${extra}` : ''}`);
  if (!cond) failures++;
};

check('exports apply (function)', typeof mod.apply === 'function');
check('exports inject === ["tools"]', JSON.stringify(mod.inject) === '["tools"]');

let registered = null;
const ctx = {
  tools: {
    register: (definition) => {
      if (registered) throw new Error('register() called twice');
      registered = definition;
      return () => { registered = null; };
    }
  }
};
const dispose = mod.apply(ctx);
check('apply registers exactly one tool', !!registered, registered?.name ?? '');

const tool = registered;
check('tool name is "refine_tests"', tool.name === 'refine_tests');
check('tool name is not the reserved run_code', tool.name !== 'run_code');
check('description is agent-facing prose',
  typeof tool.description === 'string' && tool.description.length > 200);
check('parameters is a JSON schema object',
  typeof tool.parameters === 'object' && tool.parameters.type === 'object');
check('parameters declares mode enum',
  JSON.stringify(tool.parameters.properties?.mode?.enum) === JSON.stringify(
    ['audit', 'plan', 'apply', 'approve', 'restore', 'bootstrap']));
// argv mapping is the contract; actually provisioning needs the network,
// so a fresh checkout without .venv may legitimately fail here (offline).
const boot = await tool.execute({ mode: 'bootstrap' }, {});
check('bootstrap maps to the CLI subcommand',
  JSON.stringify(boot.argv) === '["bootstrap"]',
  boot.stdout || boot.stderr)
check('output.schema is an object schema',
  tool.output?.schema?.type === 'object');
check('output.render is a function', typeof tool.output?.render === 'function');
check('render returns text blocks', (() => {
  const blocks = tool.output.render({ mode: 'audit' }, {
    target: '/tmp/x', argv: ['--offline', '/tmp/x'], exitCode: 0,
    durationMs: 5, stdout: 'ok', stderr: '', note: ''
  });
  return Array.isArray(blocks) && blocks.length === 1 &&
    blocks[0].type === 'text' && typeof blocks[0].text === 'string';
})());
check('execute is an async function',
  typeof tool.execute === 'function' && tool.execute.constructor.name === 'AsyncFunction');
check('timeoutMs is a positive number',
  Number.isFinite(tool.timeoutMs) && tool.timeoutMs > 0);

// buildArgv guards — exercise through execute (never spawns a process).
const refused = await tool.execute({ mode: 'apply' }, {});
check('apply without yes is refused',
  refused.stderr.includes('set yes=true') && refused.exitCode === 2,
  'no process spawned');
const badId = await tool.execute({ mode: 'restore', quarantine_id: '../../etc' }, {});
check('restore with a bad quarantine_id is refused',
  badId.exitCode === 2 && /quarantine_id/.test(badId.stderr));
const approveMissing = await tool.execute({ mode: 'approve' }, {});
check('approve without quarantine_id is refused', approveMissing.exitCode === 2);

// End-to-end on a throwaway copy of the fixture suite (read-only audit).
const fixtureRoot = join(process.cwd(), 'src/tests/fixtures');
const sandbox = await mkdtemp(join(tmpdir(), 'refine-tests-smoke-'));
const target = join(sandbox, 'fixtures');
await cp(fixtureRoot, target, { recursive: true });
try {
  const value = await tool.execute({ mode: 'audit', target }, {});
  check('audit runs the real CLI end-to-end',
    value.exitCode === 0, `exit=${value.exitCode} in ${value.durationMs} ms`);
  check('CLI stdout reaches the tool result', /detectors?|tests/i.test(value.stdout));
  check('report path is surfaced', !!value.reportMd && value.reportMd.endsWith('.md'));
} finally {
  await rm(sandbox, { recursive: true, force: true });
}

// Disposal path.
dispose();
check('dispose unregisters the tool', registered === null);

console.log(failures === 0 ? '\nsmoke: all checks passed' : `\nsmoke: ${failures} check(s) failed`);
process.exit(failures === 0 ? 0 : 1);
