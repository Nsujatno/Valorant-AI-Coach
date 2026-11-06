import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ command }) => ({
  main: {},
  preload: {},
  renderer: {
    plugins: [react(), {
      name: 'valcoach-content-policy',
      // React Refresh injects an inline preamble during development only.
      transformIndexHtml: (html: string) => html.replace('__SCRIPT_POLICY__', command === 'serve'
        ? "'self' 'unsafe-inline'" : "'self'")
    }],
    server: { host: '127.0.0.1' }
  }
}))
