import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { bootTheme } from './theme.ts'
import { bootRoom } from './room.ts'

// Before the first render, not inside it: the saved palette has to be on :root
// when the shell paints, or every reload opens on VOID and swaps a frame later.
// The room boots the same way, or a reload flashes the cockpit before the
// office mounts.
bootTheme()
bootRoom()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
