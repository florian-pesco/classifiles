import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/inter'
import '@fontsource-variable/plus-jakarta-sans'
import './styles/tokens.css'
import './styles/base.css'
import './styles/layout.css'
import './styles/review.css'
import './styles/dialogs.css'
import './styles/pages.css'
import './styles/responsive.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
