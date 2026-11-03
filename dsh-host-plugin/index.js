/**
 * refine-tests — DSH (Cordis) Host bundle.
 *
 * Registers the `refine_tests` tool, which shells out to the plugin's CLI
 * (`<pluginRoot>/refine-tests`, §9 modes). The Host half owns no logic: every
 * decision (gating, quarantine, proposals) lives in the Python tool so the
 * command line and the tool can never drift apart (ASSUMPTIONS A1).
 *
 * Export form: `apply(ctx, config)` + `inject = ['tools']`, per the Host
 * plugin contract. Registration is scoped to this bundle's activation; the
 * returned disposer unregisters the tool.
 */

import { spawn } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
// The Python plugin tree (CLI launcher + src/) is the parent directory.
const pluginRoot = resolve(here, '..');

const MODES = ['audit', 'plan', 'apply', 'approve', 'restore'];

const parameters = {
  type: 'object',
  additionalProperties: false,
  properties: {
    mode: {
      type: 'string',
      enum: MODES,
      description:
        'audit = read-only report (default, exit 0). plan = --cleanup ' +
        '--dry-run preview (no writes). apply = --cleanup --yes quarantine ' +
        '(writes quarantine/ + proposed/ only; needs yes). approve = sign ' +
        'off one quarantined removal. restore = reverse one quarantine.'
    },
    target: {
      type: 'string',
      description:
        'Path to the project whose test suite is audited. Defaults to the ' +
        'current working directory.'
    },
    quarantine_id: {
      type: 'string',
      description: 'qt-… id from the report / quarantine manifest ' +
        '(required for approve and restore).'
    },
    scope_path: {
      type: 'string',
      description: 'Glob restricting the audit, e.g. "src/billing/**". ' +
        'Defaults to smoke + core tiers.'
    },
    diff: {
      type: 'string',
      description: 'Git revision range (e.g. origin/main..HEAD): audit only ' +
        'what the range touches.'
    },
    offline: {
      type: 'boolean',
      description: 'Zero network calls; local .ci-history/ becomes the CI ' +
        'provider.',
      default: true
    },
    all_tiers: {
      type: 'boolean',
      description: 'Include the extended tier (default: smoke + core).',
      default: false
    },
    yes: {
      type: 'boolean',
      description: 'Explicit consent, required for mode=apply.',
      default: false
    },
    extra_args: {
      type: 'array',
      items: { type: 'string' },
      description: 'Raw passthrough flags for the CLI (e.g. ' +
        '"--allow-replace-proposals", "--mutation"). Never inserted by ' +
        'the tool itself.'
    }
  },
  required: ['mode']
};

const outputSchema = {
  type: 'object',
  additionalProperties: false,
  properties: {
    target: { type: 'string' },
    argv: { type: 'array', items: { type: 'string' } },
    exitCode: { type: 'number' },
    durationMs: { type: 'number' },
    stdout: { type: 'string' },
    stderr: { type: 'string' },
    reportMd: { type: 'string' },
    note: { type: 'string' }
  },
  required: ['target', 'argv', 'exitCode', 'stdout', 'stderr']
};

/**
 * Map a tool call onto the CLI argv (§9 / §17).
 * Returns { argv, error } — a defined `error` means the CLI is never run.
 */
function buildArgv(args) {
  const {
    mode, target, quarantine_id, scope_path, diff,
    offline, all_tiers, yes, extra_args
  } = args;

  if (mode === 'approve' || mode === 'restore') {
    if (!quarantine_id || !/^qt-[0-9a-f]+$/.test(quarantine_id)) {
      return { error: 'approve/restore need quarantine_id matching /^qt-[0-9a-f]+$/ (copy it from the report or quarantine/manifest.json).' };
    }
    return { argv: [mode, quarantine_id, ...(target ? [target] : [])] };
  }

  if (mode === 'apply' && yes !== true) {
    return { error: 'mode=apply writes to the target (quarantine stubs + proposals): set yes=true for explicit consent. mode=plan previews the identical plan with zero writes.' };
  }

  const argv = [];
  if (mode === 'plan') argv.push('--cleanup', '--dry-run');
  if (mode === 'apply') argv.push('--cleanup', '--yes');
  if (offline !== false) argv.push('--offline');
  if (all_tiers === true) argv.push('--all-tiers');
  if (scope_path) argv.push('--path', scope_path);
  if (diff) argv.push('--diff', diff);
  if (Array.isArray(extra_args)) argv.push(...extra_args);
  argv.push(target || '.');
  return { argv };
}

function runCli(argv, cwd, signal) {
  return new Promise((resolvePromise) => {
    const child = spawn('./refine-tests', argv, {
      cwd: pluginRoot,
      stdio: ['ignore', 'pipe', 'pipe'],
      signal
    });
    let stdout = '';
    let stderr = '';
    let truncated = false;
    const MAX = 200_000; // keep the model-facing output bounded
    child.stdout.on('data', (chunk) => {
      if (stdout.length < MAX) stdout += chunk.toString('utf8');
      else truncated = true;
    });
    child.stderr.on('data', (chunk) => {
      if (stderr.length < MAX) stderr += chunk.toString('utf8');
      else truncated = true;
    });
    const settle = (exitCode, note) => resolvePromise({
      exitCode: exitCode ?? -1,
      stdout, stderr,
      note: [note, truncated ? 'output truncated at 200 KiB per stream' : '']
        .filter(Boolean).join(' — ')
    });
    child.on('error', (err) => settle(null, `spawn failed: ${err.message}`));
    child.on('close', (code) => settle(code));
  });
}

export function apply(ctx) {
  const tools = ctx.tools || ctx.get('tools');
  if (!tools || typeof tools.register !== 'function') {
    throw new Error('refine-tests host plugin: ctx.tools registry unavailable');
  }

  const dispose = tools.register({
    name: 'refine_tests',
    description:
      'Audit and curate a project\'s test suite (Python/pytest today; ' +
      'JS/TS structural). Read-only audit by default: 11 detectors ' +
      '(never-failing, flaky, redundant, zombie, low-value, tautological, ' +
      'mock-heavy, typecheck-only, existence-only, brittle, ' +
      'implementation-detail), per-test coverage + CI history forensics, ' +
      'gated actions, quarantine (never deletes; reversible) and proposals. ' +
      'Always read refine-tests-report.md after running.',
    parameters,
    output: {
      schema: outputSchema,
      render: (_args, value) => {
        const lines = [
          `$ refine-tests ${value.argv.join(' ')}`,
          `exit ${value.exitCode} in ${Math.round(value.durationMs)} ms`
        ];
        if (value.note) lines.push(value.note);
        if (value.stdout) lines.push('', value.stdout.trimEnd());
        if (value.stderr) lines.push('', '[stderr]', value.stderr.trimEnd());
        return [{ type: 'text', text: lines.join('\n') }];
      }
    },

    async execute(args, exec) {
      const built = buildArgv(args || {});
      const target = resolve(args?.target || process.cwd());
      if (built.error) {
        return {
          target,
          argv: [],
          exitCode: 2,
          durationMs: 0,
          stdout: '',
          stderr: built.error,
          note: 'refused before any process was spawned'
        };
      }

      const started = Date.now();
      const signal = exec?.signal;
      const { exitCode, stdout, stderr, note } =
        await runCli(built.argv, target, signal);
      const value = {
        target,
        argv: built.argv,
        exitCode,
        durationMs: Date.now() - started,
        stdout,
        stderr,
        note
      };
      // Point the agent at the deliverable: the report path is printed by
      // the CLI on stdout; surface it as a first-class field when present.
      const match = /report \(md\)\s*:\s*(\S+)/.exec(stdout);
      if (match) value.reportMd = match[1];
      return value;
    },

    // The audit spawns pytest + coverage subprocesses and can take minutes.
    // Forward exec.signal so cancellation reaches the whole tree.
    timeoutMs: 30 * 60 * 1000,

    isConcurrencySafe: () => false
  });

  return dispose;
}

export const inject = ['tools'];
