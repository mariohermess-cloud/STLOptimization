import { useEffect } from 'react'
import { AnalysisColumn } from './components/AnalysisColumn'
import { InputColumn } from './components/InputColumn'
import { useStore } from './state/store'
import { Viewer } from './viewer/Viewer'

export default function App() {
  const loadCatalogue = useStore((state) => state.loadCatalogue)
  const catalogueLoaded = useStore((state) => state.catalogueLoaded)
  const model = useStore((state) => state.model)
  const result = useStore((state) => state.result)
  const mobileTab = useStore((state) => state.mobileTab)
  const setMobileTab = useStore((state) => state.setMobileTab)

  useEffect(() => {
    void loadCatalogue()
  }, [loadCatalogue])

  return (
    <div className="app">
      <header className="header">
        <h1>Print Engineering Optimizer</h1>
        <span className="meta">
          {model
            ? `${model.filename} · ${model.triangle_count.toLocaleString()} triangles`
            : catalogueLoaded
              ? 'ready'
              : 'connecting…'}
        </span>
        <span className="spacer" />
        <nav className="mobile-tabs">
          {(['input', 'model', 'analysis'] as const).map((tab) => (
            <button
              key={tab}
              className={`btn small ${mobileTab === tab ? 'active' : ''}`}
              onClick={() => setMobileTab(tab)}
              disabled={tab === 'analysis' && !result}
            >
              {tab === 'input' ? 'Input' : tab === 'model' ? 'Model' : 'Analysis'}
            </button>
          ))}
        </nav>
      </header>

      <div
        className={`workspace ${mobileTab === 'analysis' ? 'show-analysis' : ''}`}
        data-tab={mobileTab}
      >
        <InputColumn />
        <div className="viewer-column">
          <Viewer />
        </div>
        <AnalysisColumn />
      </div>

      <Toasts />
    </div>
  )
}

function Toasts() {
  const toasts = useStore((state) => state.toasts)
  const dismiss = useStore((state) => state.dismissToast)
  if (toasts.length === 0) return null
  return (
    <div className="toasts">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast ${toast.kind}`}>
          <span>{toast.message}</span>
          <button onClick={() => dismiss(toast.id)} aria-label="Dismiss">
            ×
          </button>
        </div>
      ))}
    </div>
  )
}
