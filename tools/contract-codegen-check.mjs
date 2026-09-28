// Smoke test: can a client generate usable TypeScript types from contract/openapi.json?
//
//   npm ci                     # once, installs the pinned dev tools in package.json
//   npm run contract:codegen   # or: node tools/contract-codegen-check.mjs
//
// Generates types with openapi-typescript (failing on any generator warning), then
// compiles them in strict mode with a generated file that references every operation
// and asserts a few contract guarantees, including ones that must NOT compile.
// Nothing is written into the repository.
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const contract = join(root, 'contract', 'openapi.json');
const { default: openapiTS, astToString } = await import('openapi-typescript');

const warnings = [];
const { warn, error } = console;
console.warn = (...args) => warnings.push(args.join(' '));
console.error = (...args) => warnings.push(args.join(' '));
let types;
try {
  // Request fields with defaults are optional (see the contract's compatibility rules).
  types = astToString(await openapiTS(pathToFileURL(contract), { defaultNonNullable: false }));
} finally {
  Object.assign(console, { warn, error });
}
if (warnings.length) {
  console.error(`openapi-typescript reported ${warnings.length} problem(s):\n  ${warnings.join('\n  ')}`);
  process.exit(1);
}

const spec = JSON.parse(readFileSync(contract, 'utf8'));
const ids = Object.values(spec.paths).flatMap((methods) => Object.values(methods).map((op) => op.operationId));

const smoke = `import type { components, operations, paths } from './api';
type Schemas = components['schemas'];

// Every published operation has generated types.
export type AllOperations = [${ids.map((id) => `operations['${id}']`).join(', ')}];

// Enumerations are precise, and a value outside them does not compile.
export const status: Schemas['Job']['status'] = 'quota_limited';
// @ts-expect-error 'paused' is not a published job status
export const unknownStatus: Schemas['Job']['status'] = 'paused';

// Required response fields are required.
// @ts-expect-error a Job needs id, status, progress and the other always-sent fields
export const partialJob: Schemas['Job'] = { book_id: 'b', kind: 'render' };

// Responses, parameters and request bodies resolve to usable types.
export type Jobs = operations['listJobs']['responses'][200]['content']['application/json'];
export const firstId = (jobs: Jobs): string | undefined => jobs[0]?.id;
export const bookPath: paths['/api/books/{book_id}']['get']['parameters']['path'] = { book_id: 'b' };
export type AnalyzeBody = NonNullable<operations['startClassicAnalysis']['requestBody']>['content']['application/json'];
export const analyze: AnalyzeBody = { phase: 'scan' };

// Transport-level statuses are part of the contract.
export type WriteGuard = operations['createDemoBook']['responses'][403];
export type PartialAudio = operations['getListeningAudio']['responses'][206];
`;

const dir = mkdtempSync(join(tmpdir(), 'bardic-codegen-'));
try {
  writeFileSync(join(dir, 'api.d.ts'), types);
  writeFileSync(join(dir, 'smoke.ts'), smoke);
  const tsc = join(root, 'node_modules', 'typescript', 'bin', 'tsc');
  execFileSync(process.execPath, [tsc, '--noEmit', '--strict', '--target', 'es2022', '--module', 'esnext',
    '--moduleResolution', 'bundler', join(dir, 'smoke.ts')], { stdio: 'inherit' });
} catch {
  console.error('Generated types do not compile, or a guarantee in the smoke file failed (see tsc output above).');
  process.exitCode = 1;
} finally {
  rmSync(dir, { recursive: true, force: true });
}
if (!process.exitCode) {
  console.log(`contract codegen ok: ${ids.length} operations, ${Object.keys(spec.components.schemas).length} schemas, ` +
    `${types.split('\n').length} lines of types, strict tsc clean.`);
}
