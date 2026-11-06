import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { pythonForRepo } from '../src/main/engine.mjs'

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repo = resolve(desktop, '..')
const manifest = JSON.parse(readFileSync(join(desktop, 'package.json'), 'utf8'))
let missing = false
const report = (ok, text) => {
  console.log(`${ok ? 'OK     ' : 'MISSING'} ${text}`)
  if (!ok) missing = true
}
const [major, minor] = process.versions.node.split('.').map(Number)
report(major > 22 || (major === 22 && minor >= 12), `Node ${process.versions.node} (requires 22.12+)`)
for (const [name, version] of Object.entries({ ...manifest.dependencies, ...manifest.devDependencies })) {
  const packagePath = join(desktop, 'node_modules', name, 'package.json')
  let installed
  try { installed = JSON.parse(readFileSync(packagePath, 'utf8')).version }
  catch { installed = null }
  report(installed === version, installed ? `${name} ${installed} (expected ${version})` : `${name} ${version}`)
}
const electronFolder = join(desktop, 'node_modules', 'electron')
const pathFile = join(electronFolder, 'path.txt')
const electronReady = existsSync(pathFile) && existsSync(join(electronFolder, 'dist', readFileSync(pathFile, 'utf8').trim()))
report(electronReady, 'Electron desktop runtime')
const python = pythonForRepo(repo)
report(existsSync(python), `Project Python: ${python}`)
if (existsSync(python)) {
  const probe = spawnSync(python, ['-c', 'import pyarrow, valcoach; print("PyArrow " + pyarrow.__version__)'], {
    cwd: repo, encoding: 'utf8', windowsHide: true, shell: false, timeout: 15_000
  })
  report(probe.status === 0, probe.status === 0 ? probe.stdout.trim() : 'Python imports failed; the existing environment needs attention.')
}
console.log('\nThis check never installs, downloads, launches Electron or connects to a model.')
if (missing) {
  console.log('For missing JavaScript dependencies/runtime, run npm install yourself in desktop/.')
  console.log('That command installs project dependencies and downloads Electron; no global installation is needed.')
}
process.exitCode = missing ? 1 : 0
