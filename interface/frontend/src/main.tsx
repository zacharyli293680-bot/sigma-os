import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { bootTheme } from './theme.ts'

// Before the first render, not inside it: the saved palette has to be on :root
// when the shell paints, or every reload opens on VOID and swaps a frame later.
bootTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
