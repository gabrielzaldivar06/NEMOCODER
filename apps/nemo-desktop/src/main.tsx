import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { useBackend, detectSetup, type AppSettings } from './services/backend'
import { invoke } from '@tauri-apps/api/tauri'

/**
 * App component with backend lifecycle management
 */
export function App() {
  const missionControlUrl = 'http://127.0.0.1:5173'
  const [backendStatus, setBackendStatus] = useState<'connecting' | 'connected' | 'error'>('connecting')
  const [errorMsg, setErrorMsg] = useState<string>('')
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [statePreview, setStatePreview] = useState<string>('')
  const [missionControlAvailable, setMissionControlAvailable] = useState<boolean>(true)
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const backend = useBackend()

  // postMessage bridge: pick-folder requests from MC iframe → native Tauri dialog
  useEffect(() => {
    async function handleMessage(event: MessageEvent) {
      if (event.data?.type !== 'pick-folder') return
      try {
        const path = await invoke<string | null>('pick_folder')
        iframeRef.current?.contentWindow?.postMessage(
          { type: 'pick-folder-result', path: path ?? null },
          '*'
        )
      } catch {
        iframeRef.current?.contentWindow?.postMessage(
          { type: 'pick-folder-result', path: null },
          '*'
        )
      }
    }
    window.addEventListener('message', handleMessage)
    return () => window.removeEventListener('message', handleMessage)
  }, [])

  useEffect(() => {
    initializeBackend()
    const interval = setInterval(checkBackendHealth, 30000)
    return () => clearInterval(interval)
  }, [])

  async function initializeBackend() {
    try {
      const loadedSettings = await backend.loadSettings()
      setSettings(loadedSettings)

      if (loadedSettings.auto_start_backend) {
        console.log('Auto-starting backend...')
        try {
          const info = await backend.startBackend()
          console.log(`Backend started: PID=${info.pid}, status=${info.status}`)
        } catch (startErr) {
          const externalHealthy = await backend.isHealthy(loadedSettings.backend_port)
          if (!externalHealthy) {
            throw startErr
          }
          console.warn('Backend start command failed, but backend is already reachable; continuing.')
        }
      }

      await new Promise((resolve) => setTimeout(resolve, 1500))
      await checkBackendHealth()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to initialize backend'
      console.error('Initialization error:', message)
      setBackendStatus('error')
      setErrorMsg(message)
    }
  }

  async function checkBackendHealth() {
    try {
      const isHealthy = await backend.isHealthy()

      if (!isHealthy) {
        setBackendStatus('error')
        setErrorMsg('Backend is not responding')
        return
      }

      setBackendStatus('connected')
      setErrorMsg('')
      try {
        const payload = await backend.proxyRequest('/api/state', 'GET')
        setStatePreview(payload)
      } catch {
        setStatePreview('')
      }

      try {
        const response = await fetch(missionControlUrl, { method: 'GET' })
        setMissionControlAvailable(response.ok)
      } catch {
        setMissionControlAvailable(false)
      }
    } catch (err) {
      setBackendStatus('error')
      setErrorMsg(err instanceof Error ? err.message : 'Health check failed')
    }
  }

  async function handleRetrySetup() {
    setBackendStatus('connecting')
    setErrorMsg('')

    try {
      const setup = await detectSetup()

      if (setup.ready) {
        await initializeBackend()
      } else {
        const issues = []
        if (!setup.lmStudioFound) issues.push('LM Studio not reachable')
        if (!setup.nemoDbFound) issues.push('NEMO database missing')
        if (!setup.diskSpaceOk) issues.push('Insufficient disk space')
        if (!setup.permissionsOk) issues.push('Permission denied')

        setErrorMsg(`Setup issues: ${issues.join(', ')}`)
        setBackendStatus('error')
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Setup detection failed')
      setBackendStatus('error')
    }
  }

  function statusLabel() {
    if (backendStatus === 'connecting') return 'Connecting'
    if (backendStatus === 'connected') return 'Connected'
    return 'Error'
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <h1>Nemocode Desktop</h1>
          <p>Mission Control Workspace</p>
        </div>
        <div className={`status-pill ${backendStatus}`}>{statusLabel()}</div>
      </header>

      <main className="workspace-canvas">
        {backendStatus === 'connected' ? (
          <section className="connected-pane">
            <div className="connected-meta">
              <span className="meta-chip">
                <strong>Backend</strong>
                <em>http://localhost:8787</em>
              </span>
              <span className="meta-chip">
                <strong>Mission Control</strong>
                <em>{missionControlUrl}</em>
              </span>
            </div>

            {missionControlAvailable ? (
              <div className="mission-control-wrap">
                <iframe
                  ref={iframeRef}
                  src={missionControlUrl}
                  title="Mission Control UI"
                  className="mission-control-frame"
                />
              </div>
            ) : (
              <div className="error-pane compact">
                <h2>Mission Control UI is not running</h2>
                <p>Start the real frontend with:</p>
                <pre className="diagnostic-box">cd apps/mission-control && npm run dev</pre>
                {statePreview && <pre className="diagnostic-box scrollable">{statePreview}</pre>}
              </div>
            )}
          </section>
        ) : (
          <section className="error-pane">
            <h2>Unable to Connect to Backend</h2>
            {errorMsg && <p className="error-text">{errorMsg}</p>}
            <p className="support-text">Make sure Mission Control backend is configured and running.</p>
            {settings && (
              <div className="settings-panel">
                <p>
                  <strong>Backend Port:</strong> {settings.backend_port}
                </p>
                <p>
                  <strong>LM Studio:</strong> {settings.lm_studio_url}
                </p>
              </div>
            )}
            <div className="button-row">
              <button onClick={handleRetrySetup} className="primary-btn">
                Retry Connection
              </button>
            </div>
          </section>
        )}
      </main>
    </div>
  )
}

export default App

const rootElement = document.getElementById('root')
if (!rootElement) {
  throw new Error('Root element #root not found')
}

createRoot(rootElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
