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
export type RunBody = NonNullable<operations['startBookAnalysisPipelineRun']['requestBody']>['content']['application/json'];
export const run: RunBody = { steps: ['census'], expected_fingerprint: 'plan' };

// Tagged unions narrow on their tag, and a member's own fields exist only on that member.
export const clipSeconds = (audio: Schemas['ListeningAudio']): number =>
  audio.kind === 'clip' ? audio.clip_end - audio.clip_start : audio.duration;
export const jobDetail = (job: Schemas['Job']): string | null => {
  switch (job.kind) {
    case 'listen': return job.passage_id;
    case 'listen_chapter': return job.chapter_id;
    case 'pipeline': return job.run_id;
    case 'series': return job.series_id;
    case 'performance': return job.performance_id;
    case 'voice_preview': return job.preview_id;
    default: return null;
  }
};
// @ts-expect-error session_id belongs to the narration kinds that declare it, not to every Job
export const sessionOfAnyJob = (job: Schemas['Job']) => job.session_id;
export const volumeTitle = (volume: Schemas['SeriesVolume']): string =>
  volume.kind === 'supplied' ? volume.author : volume.title;
export const rowStep = (row: Schemas['PipelineVersionRow']): string => (row.step === 'quotes' ? row.kind : row.step);

// What Gemini blocked is reported by the chapter job that met it, and only there; a fallback narrator is a narration provider.
export const blockedPassages = (job: Schemas['Job']): string[] =>
  job.kind === 'listen_chapter' && job.content_blocked ? job.content_blocked.blocked_passage_ids : [];
export const fallbackNarrator = (job: Schemas['ListenChapterJob']): Schemas['NarrationProvider'] | null => job.fallback?.provider ?? null;
// @ts-expect-error content_blocked belongs to listen_chapter jobs, not to every Job
export const blockedOfAnyJob = (job: Schemas['Job']) => job.content_blocked;
export const failedForContent = (job: Schemas['Job']): boolean => job.error_code === 'content_blocked';
// A performance always says how much of it a fallback narrator read and which chapters were added later.
export const performanceNotes = (record: Schemas['Performance']): number =>
  record.progress.passages_fallback + record.progress.passages_blocked + record.chapters_added.length;

// The wire says passage, once. The retired name is not a field.
export const listen: Schemas['ListenRequest'] = { passage_id: 'p' };
// @ts-expect-error segment_id was renamed passage_id
export const oldListen: Schemas['ListenRequest'] = { passage_id: 'p', segment_id: 'p' };
export const passageCount = (book: Schemas['Book']): number => book.passages.length;

// Providers are named enumerations, and a value outside them does not compile.
export const narrator: Schemas['NarrationProvider'] = 'breeze';
// @ts-expect-error 'openai' is not a narration provider
export const badNarrator: Schemas['NarrationProvider'] = 'openai';

// Transport-level statuses are part of the contract.
export type WriteGuard = operations['createDemoBook']['responses'][403];
export type PartialAudio = operations['getListeningAudio']['responses'][206];
export type RangeRefused = operations['getListeningAudio']['responses'][416]['content']['application/json'];
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
