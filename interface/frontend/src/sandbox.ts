/**
 * sandbox.ts — the code dock's two engines (study S4).
 *
 * Pyodide (Python) and sql.js (SQL), both vendored via npm and lazy-loaded
 * the first time they are actually asked to run something (§11): the
 * dashboard's initial load must not pay ~14 MB for a slot most sessions never
 * open, and a CDN load is banned outright — the frontend made zero external
 * requests before this and keeps making zero. The Python runtime's assets are
 * copied into the build by vite.config.ts; sql.js's wasm arrives as a `?url`
 * asset import.
 *
 * Both engines run on the main thread — a known, accepted limit: an infinite
 * Python loop freezes this tab until Chrome kills it. A worker bridge would
 * buy robustness at the price of a message protocol nobody needs yet.
 *
 * Failure is reported as failure (the phase's done-when): a Python traceback
 * or a SQL error comes back verbatim in `error`, never swallowed and never
 * summarised into "something went wrong".
 */
import type { Database, SqlJsStatic } from "sql.js";
// The BROWSER build's wasm, matching the glue `import("sql.js")` resolves to
// through the package's browser condition (the emitted chunk is
// sql-wasm-browser-*.js). Emscripten glue and wasm are compiled together; the
// default build's sql-wasm.wasm is byte-identical today, which is exactly the
// kind of coincidence a version bump breaks at runtime.
import sqlWasmUrl from "sql.js/dist/sql-wasm-browser.wasm?url";

export type PyRun = {
  ok: boolean;
  stdout: string;          // everything printed, stderr interleaved
  result: string | null;   // repr of the last expression, if any
  error: string | null;    // the full traceback, verbatim
  ms: number;
};
export type SqlRun = {
  ok: boolean;
  tables: { columns: string[]; values: string[][] }[];
  error: string | null;
  ms: number;
};

type Py = Awaited<ReturnType<typeof import("pyodide").loadPyodide>>;

// One promise per engine, kept across runs. Held as the *promise* rather than
// the instance so concurrent first-runs share one load; cleared on failure so
// a transient fetch error is retryable instead of permanent.
let pyLoad: Promise<Py> | null = null;
let pyReady = false;

/** True once Python has finished loading — the dock uses this to tell
 *  "running…" apart from "downloading the runtime, first open only". */
export function pythonReady(): boolean {
  return pyReady;
}

/** Kick the Python load without running anything, so opening the code slot
 *  overlaps the download with typing. Errors are deferred to the first run. */
export function warmPython(): void {
  void loadPy().catch(() => {});
}

async function loadPy(): Promise<Py> {
  if (!pyLoad) {
    pyLoad = (async () => {
      const { loadPyodide } = await import("pyodide");
      const py = await loadPyodide({
        indexURL: `${import.meta.env.BASE_URL}pyodide/`,
      });
      pyReady = true;
      return py;
    })();
    pyLoad.catch(() => { pyLoad = null; });   // retryable, not poisoned
  }
  return pyLoad;
}

// Runs are serialised on the one shared interpreter: setStdout/setStderr are
// installed per run, so two overlapping runs would capture each other's
// output. The chain itself never rejects — every job resolves to a PyRun.
let pyQueue: Promise<unknown> = Promise.resolve();

export function runPython(code: string): Promise<PyRun> {
  const job = pyQueue.then(() => runPythonNow(code));
  pyQueue = job.catch(() => {});
  return job;
}

async function runPythonNow(code: string): Promise<PyRun> {
  const t0 = performance.now();
  let py: Py;
  try {
    py = await loadPy();
  } catch (e) {
    return { ok: false, stdout: "", result: null, ms: Math.round(performance.now() - t0),
             error: `the Python runtime failed to load: ${e instanceof Error ? e.message : String(e)}` };
  }
  const lines: string[] = [];
  py.setStdout({ batched: s => lines.push(s) });
  py.setStderr({ batched: s => lines.push(s) });
  try {
    const r = await py.runPythonAsync(code);
    let result: string | null = null;
    if (r !== undefined && r !== null) {
      // A PyProxy leaks unless destroyed, and its str() can itself raise —
      // destroy in finally so the error path never leaks the proxy while the
      // exception still surfaces verbatim below.
      const proxy = r as { destroy?: () => void };
      try {
        result = String(r);
      } finally {
        if (typeof proxy.destroy === "function") proxy.destroy();
      }
    }
    return { ok: true, stdout: lines.join("\n"), result, error: null,
             ms: Math.round(performance.now() - t0) };
  } catch (e) {
    // PythonError.message is the real traceback — hand it over whole.
    return { ok: false, stdout: lines.join("\n"), result: null,
             error: e instanceof Error ? e.message : String(e),
             ms: Math.round(performance.now() - t0) };
  }
}

let sqlLoad: Promise<SqlJsStatic> | null = null;
let db: Database | null = null;

async function loadSql(): Promise<SqlJsStatic> {
  if (!sqlLoad) {
    const mod = import("sql.js");
    sqlLoad = mod.then(m => m.default({ locateFile: () => sqlWasmUrl }));
    sqlLoad.catch(() => { sqlLoad = null; });
  }
  return sqlLoad;
}

/** One in-memory database per session, so a CREATE TABLE survives into the
 *  next run — that is what makes multi-statement practice possible. */
export async function runSql(sql: string): Promise<SqlRun> {
  const t0 = performance.now();
  let SQL: SqlJsStatic;
  try {
    SQL = await loadSql();
  } catch (e) {
    return { ok: false, tables: [], ms: Math.round(performance.now() - t0),
             error: `the SQL engine failed to load: ${e instanceof Error ? e.message : String(e)}` };
  }
  db ??= new SQL.Database();
  try {
    const res = db.exec(sql);
    return {
      ok: true,
      tables: res.map(r => ({
        columns: r.columns,
        values: r.values.map(row =>
          row.map(v => (v === null ? "NULL" : String(v)))),
      })),
      error: null,
      ms: Math.round(performance.now() - t0),
    };
  } catch (e) {
    return { ok: false, tables: [], error: e instanceof Error ? e.message : String(e),
             ms: Math.round(performance.now() - t0) };
  }
}

/** Drop the session database. Explicit, user-triggered — never automatic. */
export function resetSql(): void {
  try { db?.close(); } catch { /* already closed */ }
  db = null;
}
