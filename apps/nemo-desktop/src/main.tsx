import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { useBackend, detectSetup, type AppSettings } from './services/backend'

/**
 * App component with backend lifecycle management
 */
export function App() {
  const [backendStatus, setBackendStatus] = useState<'connecting' | 'connected' | 'error'>('connecting')
  const [errorMsg, setErrorMsg] = useState<string>('')
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const backend = useBackend()

  useEffect(() => {
    // Initialize backend on app load
    initializeBackend()

    // Periodic health check
    const interval = setInterval(checkBackendHealth, 30000)
    return () => clearInterval(interval)
  }, [])

  async function initializeBackend() {
    try {
      // Load settings
      const loadedSettings = await backend.loadSettings()
      setSettings(loadedSettings)

      // Start backend if configured to auto-start
      if (loadedSettings.auto_start_backend) {
        console.log('Auto-starting backend...')
        const info = await backend.startBackend()
        console.log(`Backend started: PID=${info.pid}, status=${info.status}`)
      }

      // Wait a moment for backend to be ready
      await new Promise(r => setTimeout(r, 2000))

      // Check health
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

      if (isHealthy) {
        setBackendStatus('connected')
        setErrorMsg('')
      } else {
        setBackendStatus('error')
        setErrorMsg('Backend is not responding')
      }
    } catch (err) {
      setBackendStatus('error')
      setErrorMsg(
        err instanceof Error ? err.message : 'Health check failed'
      )
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

  return (
    <div className="app-container">
      <header className="app-header">
        <h1>Nemocode Desktop</h1>
        <div className={`status-indicator ${backendStatus}`}>
          {backendStatus === 'connecting' && '⏳ Connecting...'}
          {backendStatus === 'connected' && '✓ Connected'}
          {backendStatus === 'error' && `✗ Error: ${errorMsg}`}
        </div>
      </header>

      <main className="app-main">
        {backendStatus === 'connected' ? (
          <iframe
            src="http://localhost:8787"
            title="Mission Control UI"
            className="mission-control-frame"
          />
        ) : (
          <div className="connection-error">
            <h2>Unable to Connect to Backend</h2>
            {errorMsg && (
              <p className="error-details">
                {errorMsg}
              </p>
            )}
            <p>
              Make sure the Mission Control backend is configured correctly.
            </p>
            {settings && (
              <div className="settings-info">
                <p>
                  <strong>Backend Port:</strong> {settings.backend_port}
                </p>
                <p>
                  <strong>LM Studio:</strong> {settings.lm_studio_url}
                </p>
              </div>
            )}
            <div className="button-group">
              <button onClick={handleRetrySetup} className="primary">
                Retry Connection
              </button>
            </div>
          </div>
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
