import { contextBridge, ipcRenderer } from 'electron'
import type { DesktopApi } from '../shared/contracts'

const api: DesktopApi = {
  environment: () => ipcRenderer.invoke('desktop:environment'),
  chooseExport: () => ipcRenderer.invoke('replay:choose'),
  cancelQuery: () => ipcRenderer.invoke('replay:cancel'),
  openDevTools: () => ipcRenderer.invoke('desktop:devtools')
}

contextBridge.exposeInMainWorld('valcoach', api)
