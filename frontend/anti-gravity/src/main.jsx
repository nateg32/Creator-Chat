import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './theme-cosy.css'
import App from './App.jsx'
import { FeedbackProvider } from './components/feedback/FeedbackProvider.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <FeedbackProvider>
      <App />
    </FeedbackProvider>
  </StrictMode>,
)
