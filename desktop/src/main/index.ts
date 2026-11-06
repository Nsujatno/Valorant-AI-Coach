import { app, BrowserWindow, dialog, ipcMain, type IpcMainInvokeEvent } from 'electron'
import { existsSync } from 'node:fs'
import { basename, join, resolve } from 'node:path'
import { createEngineClient } from './engine.mjs'
import type { PlayerListing, ReplaySelection, Result } from '../shared/contracts'

const repoRoot = resolve(app.getAppPath(), '..')
const engine = createEngineClient({ repoRoot })
let mainWindow: BrowserWindow | null = null
let selected: ReplaySelection | null = null
let busy = false

function trusted(event: IpcMainInvokeEvent): void {
  if (!mainWindow || event.sender !== mainWindow.webContents
    || event.senderFrame !== mainWindow.webContents.mainFrame) {
    throw new Error('Only the main application window can use the replay bridge.')
  }
}

function register<T>(channel: string, action: (request: unknown) => Promise<T> | T): void {
  ipcMain.handle(channel, async (event, request): Promise<Result<T>> => {
    try {
      trusted(event)
      return { ok: true, value: await action(request) }
    } catch (error) {
      const detail = error as { code?: string; message?: string }
      return { ok: false, error: { code: detail.code || 'desktop_error', message: detail.message || 'The desktop operation failed.' } }
    }
  })
}

async function exclusive<T>(action: () => Promise<T>): Promise<T> {
  if (busy) throw new Error('Wait for the current query, or cancel it first.')
  busy = true
  try { return await action() }
  finally { busy = false }
}

register('desktop:environment', () => ({ ...engine.environment(), version: app.getVersion() }))
register('desktop:devtools', () => {
  mainWindow?.webContents.openDevTools({ mode: 'detach' })
  return null
})
register('replay:cancel', () => { engine.cancel(); return null })
register('replay:choose', () => exclusive(async () => {
  const picked = await dialog.showOpenDialog(mainWindow!, {
    title: 'Choose an existing vrfkit export folder containing manifest.json',
    defaultPath: selected?.exportPath || (existsSync(join(repoRoot, 'output')) ? join(repoRoot, 'output') : repoRoot),
    properties: ['openDirectory']
  })
  if (picked.canceled || picked.filePaths.length === 0) return null
  const exportPath = picked.filePaths[0]
  const listing = await engine.listPlayers(exportPath) as PlayerListing
  selected = { ...listing, exportPath, folderName: basename(exportPath) }
  return selected
}))

function createWindow(): void {
  const window = new BrowserWindow({
    title: 'ValCoach — local prototype',
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 640,
    show: false,
    backgroundColor: '#11161d',
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  })
  mainWindow = window
  window.once('ready-to-show', () => window.show())
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', (event) => event.preventDefault())
  window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
  window.webContents.on('before-input-event', (event, input) => {
    if (input.type === 'keyDown' && input.key === 'F12') {
      event.preventDefault()
      window.webContents.toggleDevTools()
    }
  })
  window.on('closed', () => {
    engine.cancel()
    selected = null
    mainWindow = null
  })
  if (!app.isPackaged && process.env.ELECTRON_RENDERER_URL) {
    void window.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else void window.loadFile(join(__dirname, '../renderer/index.html'))
}

void app.whenReady().then(() => {
  app.setAppUserModelId('dev.valcoach.desktop')
  createWindow()
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow() })
})
app.on('before-quit', () => engine.cancel())
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
