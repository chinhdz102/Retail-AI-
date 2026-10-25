import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    // ONNX's dynamically imported .mjs may include Vite's '?import' query.
    // Serve only pinned local runtime files with a JS MIME type in dev mode.
    // Production assets live under /vad/onnx/ and are served as static files.
    {
      name: 'local-onnx-wasm-runtime',
      configureServer(server) {
        server.middlewares.use(async (request, response, next) => {
          const pathname = request.url?.split('?')[0] ?? ''
          const filename = pathname.startsWith('/vad/onnx/')
            ? pathname.slice('/vad/onnx/'.length) : ''
          if (!/^ort-[a-zA-Z0-9_.-]+\.(mjs|wasm)$/.test(filename)) {
            next()
            return
          }
          try {
            const data = await readFile(resolve(import.meta.dirname, 'public/vad/onnx', filename))
            response.setHeader('Content-Type',
              filename.endsWith('.mjs') ? 'text/javascript; charset=utf-8' : 'application/wasm')
            response.setHeader('Cache-Control', 'no-store')
            response.end(data)
          } catch {
            next()
          }
        })
      },
    },
    vue({
      template: {
        compilerOptions: {
          isCustomElement: (tag) => tag === 'model-viewer',
        },
      },
    }),
  ],
})
