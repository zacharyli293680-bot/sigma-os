import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { viteStaticCopy } from 'vite-plugin-static-copy'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    // The code dock's Python runtime (study S4). Vendored via npm, never a
    // CDN (§11): the bundler carries pyodide.mjs itself, but at run time it
    // fetches its siblings — the wasm, the stdlib zip, the lockfile —
    // relative to indexURL, so those are copied into the build verbatim.
    // sql.js needs no entry here: its one wasm file rides in as a `?url`
    // asset import.
    viteStaticCopy({
      targets: [
        {
          // stripBase flattens: the plugin otherwise preserves the whole
          // node_modules/pyodide/ prefix inside dest, burying the runtime
          // where indexURL cannot see it. package.json rides along
          // harmlessly rather than being worth a second target.
          src: 'node_modules/pyodide/*.{asm.mjs,asm.wasm,zip,json}',
          dest: 'pyodide',
          rename: { stripBase: true },
        },
      ],
    }),
  ],
  // Pre-bundling pyodide.mjs breaks its relative asset resolution; it is
  // already an ESM file and needs no optimisation pass.
  optimizeDeps: { exclude: ['pyodide'] },
})
