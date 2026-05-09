import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { useBackend, detectSetup, type AppSettings, type SetupDiagnostics } from './services/backend'
import { invoke } from '@tauri-apps/api/tauri'

/**
 * App component with backend lifecycle management
 */
export function App() {
  const missionControlUrl = 'http://127.0.0.1:5173'
  const [backendStatus, setBackendStatus] = useState<'connecting' | 'connected' | 'degraded' | 'setup_required' | 'fatal_error'>('connecting')
  const [errorMsg, setErrorMsg] = useState<string>('')
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [settingsDraft, setSettingsDraft] = useState<AppSettings | null>(null)
  const [statePreview, setStatePreview] = useState<string>('')
  const [missionControlAvailable, setMissionControlAvailable] = useState<boolean>(true)
  const [setupDiagnostics, setSetupDiagnostics] = useState<SetupDiagnostics | null>(null)
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
    void initializeBackend()
  }, [])

  useEffect(() => {
    const interval = setInterval(() => {
      void refreshSetupState(settings?.backend_port ?? 8787)
    }, 30000)
    return () => clearInterval(interval)
  }, [settings?.backend_port])

  function buildSetupIssueList(diagnostics: SetupDiagnostics | null): string[] {
    if (!diagnostics) return []
    return diagnostics.issues.length > 0
      ? diagnostics.issues
      : [
          'LM Studio reachable',
          'NEMO database present',
          'Disk space sufficient',
          'Permissions valid',
        ].filter((label, index) => {
          const flags = [diagnostics.lmStudioFound, diagnostics.nemoDbFound, diagnostics.diskSpaceOk, diagnostics.permissionsOk]
          return !flags[index]
        })
  }

  async function initializeBackend() {
    try {
      setBackendStatus('connecting')
      setErrorMsg('')
      const loadedSettings = await backend.loadSettings()
      setSettings(loadedSettings)
      setSettingsDraft(loadedSettings)

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
      const healthy = await probeBackendHealth(loadedSettings.backend_port)
      if (!healthy) {
        setSetupDiagnostics({
          lmStudioFound: false,
          nemoDbFound: false,
          diskSpaceOk: true,
          permissionsOk: true,
          ready: false,
          status: 'setup_required',
          issues: ['Backend is not responding yet', 'Use Start Backend or Retry Setup'],
        })
        setBackendStatus('setup_required')
        setErrorMsg('Mission Control backend is not reachable yet.')
        return
      }

      await refreshSetupState(loadedSettings.backend_port)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to initialize backend'
      console.error('Initialization error:', message)
      setBackendStatus('fatal_error')
      setErrorMsg(message)
    }
  }

  async function probeBackendHealth(port: number = settings?.backend_port ?? 8787) {
    try {
      const isHealthy = await backend.isHealthy(port)

      if (!isHealthy) {
        setStatePreview('')
        setMissionControlAvailable(false)
        return false
      }

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

      return true
    } catch (err) {
      setStatePreview('')
      setMissionControlAvailable(false)
      setErrorMsg(err instanceof Error ? err.message : 'Health check failed')
      return false
    }
  }

  async function refreshSetupState(port: number = settings?.backend_port ?? 8787) {
    const healthy = await probeBackendHealth(port)
    if (!healthy) {
      setBackendStatus('setup_required')
      return false
    }

    const setup = await detectSetup()
    setSetupDiagnostics(setup)
    setErrorMsg(setup.issues.join(' • '))
    if (setup.status === 'ready') {
      setBackendStatus('connected')
    } else if (setup.status === 'degraded') {
      setBackendStatus('degraded')
    } else if (setup.status === 'setup_required') {
      setBackendStatus('setup_required')
    } else {
      setBackendStatus('fatal_error')
    }
    return setup.status === 'ready'
  }

  async function handleRetrySetup() {
    setBackendStatus('connecting')
    setErrorMsg('')

    try {
      await initializeBackend()
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Setup detection failed')
      setBackendStatus('fatal_error')
    }
  }

  async function handleStartBackend() {
    setBackendStatus('connecting')
    setErrorMsg('')

    try {
      await backend.startBackend()
      await refreshSetupState(settings?.backend_port ?? 8787)
    } catch (err) {
      setBackendStatus('setup_required')
      setErrorMsg(err instanceof Error ? err.message : 'Backend start failed')
    }
  }

  function updateSettingsDraft(patch: Partial<AppSettings>) {
    setSettingsDraft((current) => (current ? { ...current, ...patch } : current))
  }

  async function handleSaveSettings() {
    if (!settingsDraft) {
      return
    }

    setBackendStatus('connecting')
    setErrorMsg('')

    try {
      await backend.saveSettings(settingsDraft)
      setSettings(settingsDraft)
      await initializeBackend()
    } catch (err) {
      setBackendStatus('setup_required')
      setErrorMsg(err instanceof Error ? err.message : 'Unable to save settings')
    }
  }

  async function handleResetSettings() {
    if (!settings) {
      return
    }

    setSettingsDraft(settings)
    setErrorMsg('Settings reset to saved values.')
  }

  function statusLabel() {
    if (backendStatus === 'connecting') return 'Connecting'
    if (backendStatus === 'connected') return 'Connected'
    if (backendStatus === 'degraded') return 'Degraded'
    if (backendStatus === 'setup_required') return 'Setup Required'
    return 'Fatal Error'
  }

  function isOnboardingState() {
    return backendStatus !== 'connected'
  }

  const issueList = buildSetupIssueList(setupDiagnostics)

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
                <div className="button-row split">
                  <button onClick={handleRetrySetup} className="secondary-btn">
                    Refresh
                  </button>
                </div>
              </div>
            )}
          </section>
        ) : isOnboardingState() ? (
          <section className="onboarding-pane">
            <div className="setup-hero">
              <span className={`status-pill ${backendStatus}`}>{statusLabel()}</span>
              <h2>Desktop is not ready yet</h2>
              <p>
                This release candidate now guides you through startup instead of leaving you at a blank error screen.
                Fix the blocker, start the backend, then the Mission Control shell will appear automatically.
              </p>
            </div>

            <div className="setup-grid">
              <section className="setup-card emphasis">
                <h3>What needs attention</h3>
                {errorMsg ? <p className="error-text">{errorMsg}</p> : <p className="support-text">Waiting for startup diagnostics.</p>}
                <ul className="checklist">
                  <li className={setupDiagnostics?.lmStudioFound ? 'ok' : 'bad'}>LM Studio reachable</li>
                  <li className={setupDiagnostics?.nemoDbFound ? 'ok' : 'bad'}>NEMO database available</li>
                  <li className={setupDiagnostics?.diskSpaceOk ? 'ok' : 'bad'}>Disk space sufficient</li>
                  <li className={setupDiagnostics?.permissionsOk ? 'ok' : 'bad'}>Permissions valid</li>
                </ul>
              </section>

              <section className="setup-card">
                <h3>Setup wizard</h3>
                {settingsDraft ? (
                  <form
                    className="settings-panel compact wizard-form"
                    onSubmit={(event) => {
                      event.preventDefault()
                      void handleSaveSettings()
                    }}
                  >
                    <label>
                      <span>Backend Port</span>
                      <input
                        type="number"
                        min={1}
                        value={settingsDraft.backend_port}
                        onChange={(event) => updateSettingsDraft({ backend_port: Number(event.target.value) })}
                      />
                    </label>
                    <label>
                      <span>LM Studio URL</span>
                      <input
                        type="text"
                        value={settingsDraft.lm_studio_url}
                        onChange={(event) => updateSettingsDraft({ lm_studio_url: event.target.value })}
                      />
                    </label>
                    <label>
                      <span>NEMO Database Path</span>
                      <input
                        type="text"
                        value={settingsDraft.nemo_database_path}
                        onChange={(event) => updateSettingsDraft({ nemo_database_path: event.target.value })}
                      />
                    </label>
                    <label>
                      <span>Theme</span>
                      <select
                        value={settingsDraft.theme}
                        onChange={(event) => updateSettingsDraft({ theme: event.target.value as AppSettings['theme'] })}
                      >
                        <option value="light">Light</option>
                        <option value="dark">Dark</option>
                      </select>
                    </label>
                    <label className="toggle-row">
                      <input
                        type="checkbox"
                        checked={settingsDraft.auto_start_backend}
                        onChange={(event) => updateSettingsDraft({ auto_start_backend: event.target.checked })}
                      />
                      <span>Auto start backend on launch</span>
                    </label>

                    <div className="button-row split wizard-actions">
                      <button type="submit" className="primary-btn">
                        Save Settings
                      </button>
                      <button type="button" onClick={() => void handleResetSettings()} className="secondary-btn">
                        Reset
                      </button>
                    </div>
                  </form>
                ) : (
                  <p className="support-text">Loading settings...</p>
                )}
              </section>
            </div>

            {issueList.length > 0 && (
              <div className="issue-row">
                {issueList.map((issue) => (
                  <span key={issue} className="issue-pill">
                    {issue}
                  </span>
                ))}
              </div>
            )}

            <div className="button-row split">
              <button onClick={handleStartBackend} className="primary-btn">
                Start Backend
              </button>
              <button onClick={handleRetrySetup} className="secondary-btn">
                Retry Setup
              </button>
            </div>

            <p className="support-text subtle">
              Once the backend responds, Mission Control will load automatically. If the frontend is missing, the app will show a direct recovery path.
            </p>
          </section>
        ) : null}
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
