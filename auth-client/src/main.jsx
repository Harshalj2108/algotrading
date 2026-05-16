import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

const originalFetch = window.fetch;
window.fetch = async function () {
  let [resource, config] = arguments;
  if (typeof resource === 'string' && resource.includes('/api/')) {
    const token = localStorage.getItem("synthcrypto_token");
    if (token) {
      config = config || {};
      config.headers = {
        ...config.headers,
        "Authorization": `Bearer ${token}`
      };
    }
  }
  return originalFetch(resource, config);
};

const originalWebSocket = window.WebSocket;
window.WebSocket = function(url, protocols) {
  const token = localStorage.getItem("synthcrypto_token");
  if (token && typeof url === 'string' && url.includes('/api/')) {
    try {
      const wsUrl = new URL(url);
      wsUrl.searchParams.set("token", token);
      return new originalWebSocket(wsUrl.toString(), protocols);
    } catch {
      // Ignore if URL parsing fails
    }
  }
  return new originalWebSocket(url, protocols);
};

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
