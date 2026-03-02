import React, { Suspense, useState, useEffect, useCallback } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import axios from 'axios';
import { translations } from './i18n';
import Layout from './components/Layout';
import API_BASE from './config';

axios.defaults.withCredentials = true;

// Route-level code splitting via React.lazy
const Dashboard = React.lazy(() => import('./pages/Dashboard'));
const Settings = React.lazy(() => import('./pages/Settings'));
const Evolution = React.lazy(() => import('./pages/Evolution'));
const Chat = React.lazy(() => import('./pages/Chat'));
const Skills = React.lazy(() => import('./pages/Skills'));
const MemoryGalaxy = React.lazy(() => import('./pages/MemoryGalaxy'));
const Logs = React.lazy(() => import('./pages/Logs'));
const Debug = React.lazy(() => import('./pages/Debug'));
const Security = React.lazy(() => import('./pages/Security'));
const Cron = React.lazy(() => import('./pages/Cron'));
const Canvas = React.lazy(() => import('./pages/Canvas'));
const ToolPolicy = React.lazy(() => import('./pages/ToolPolicy'));
const AgentPolicy = React.lazy(() => import('./pages/AgentPolicy'));
const LlmRouter = React.lazy(() => import('./pages/LlmRouter'));
const ModelProviders = React.lazy(() => import('./pages/ModelProviders'));
const PolicyAudit = React.lazy(() => import('./pages/PolicyAudit'));
const ReleaseGate = React.lazy(() => import('./pages/ReleaseGate'));
const OptimizationTasks = React.lazy(() => import('./pages/OptimizationTasks'));
const TrainerJobs = React.lazy(() => import('./pages/TrainerJobs'));
const Observability = React.lazy(() => import('./pages/Observability'));
const PersonaEval = React.lazy(() => import('./pages/PersonaEval'));
const WorkflowStudio = React.lazy(() => import('./pages/WorkflowStudio'));

const PageFallback = () => (
  <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '50vh', color: '#888' }}>
    Loading…
  </div>
);

/* ------------------------------------------------------------------ */
/*  Toast                                                              */
/* ------------------------------------------------------------------ */
const Toast = ({ message, type, onClose }) => {
  useEffect(() => {
    const timer = setTimeout(onClose, 3000);
    return () => clearTimeout(timer);
  }, [onClose]);

  const bg = type === 'error'
    ? 'rgba(239,68,68,0.85)'
    : 'rgba(34,197,94,0.85)';

  return (
    <div style={{
      position: 'fixed', bottom: 32, right: 32, zIndex: 9999,
      padding: '14px 28px', borderRadius: 12,
      background: bg, backdropFilter: 'blur(12px)',
      color: '#fff', fontWeight: 600, fontSize: 14,
      boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
      animation: 'fadeIn 0.3s ease-out',
      pointerEvents: 'auto',
    }}>
      {message}
    </div>
  );
};

/* ------------------------------------------------------------------ */
/*  App -- deployer = owner, no login required                         */
/* ------------------------------------------------------------------ */
function App() {
  const [config, setConfig] = useState(null);
  const [status, setStatus] = useState('Disconnected');
  const [modelProviders, setModelProviders] = useState({});
  const [lang, setLangRaw] = useState(() => localStorage.getItem('gazer_lang') || 'en');
  const setLang = (v) => { localStorage.setItem('gazer_lang', v); setLangRaw(v); };
  const [toast, setToast] = useState(null);
  const t = translations[lang];

  const showToast = useCallback((message, type = 'success') => {
    setToast({ message, type });
  }, []);

  const bootstrapLegacyToken = useCallback(async () => {
    // Legacy migration: old UI versions stored admin_token in localStorage.
    delete axios.defaults.headers.common.Authorization;
    const legacyToken = (localStorage.getItem('admin_token') || '').trim();
    if (!legacyToken) return;
    try {
      await axios.post(`${API_BASE}/auth/session`, { token: legacyToken });
    } catch (err) {
      console.warn('Failed to migrate legacy admin token to cookie session.', err);
    } finally {
      localStorage.removeItem('admin_token');
    }
  }, []);

  const requestAdminToken = useCallback(async () => {
    const input = window.prompt('Enter admin token (from config/owner.json):');
    const token = (input || '').trim();
    if (!token) return false;
    try {
      await axios.post(`${API_BASE}/auth/session`, { token });
      return true;
    } catch {
      return false;
    }
  }, []);

  // --- Config management ---
  const fetchConfig = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/config`);
      const next = res.data || {};
      if (next.models && typeof next.models === 'object' && 'providers' in next.models) {
        delete next.models.providers;
      }
      setConfig(next);
      try {
        const providersRes = await axios.get(`${API_BASE}/model-providers`);
        setModelProviders(providersRes.data?.providers || {});
      } catch {
        setModelProviders({});
      }
      setStatus('Connected');
    } catch (err) {
      if (err?.response?.status === 401 && await requestAdminToken()) {
        try {
          const retry = await axios.get(`${API_BASE}/config`);
          const next = retry.data || {};
          if (next.models && typeof next.models === 'object' && 'providers' in next.models) {
            delete next.models.providers;
          }
          setConfig(next);
          try {
            const providersRes = await axios.get(`${API_BASE}/model-providers`);
            setModelProviders(providersRes.data?.providers || {});
          } catch {
            setModelProviders({});
          }
          setStatus('Connected');
          return;
        } catch {
          // fallthrough to disconnected state
        }
      }
      console.error("Failed to load config", err);
      setStatus('Disconnected');
    }
  }, [requestAdminToken]);

  const fetchModelProviders = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/model-providers`);
      setModelProviders(res.data?.providers || {});
    } catch (err) {
      console.error('Failed to load model providers', err);
      setModelProviders({});
    }
  }, []);

  const saveConfig = async (nextConfig = config) => {
    try {
      const payload = JSON.parse(JSON.stringify(nextConfig || {}));
      if (payload.models && typeof payload.models === 'object' && 'providers' in payload.models) {
        delete payload.models.providers;
      }
      await axios.post(`${API_BASE}/config`, payload);
      showToast(t.saved, 'success');
      return true;
    } catch (err) {
      const detail = err?.response?.data?.detail;
      showToast(detail ? `Failed to save config: ${detail}` : "Failed to save config", 'error');
      return false;
    }
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
        await bootstrapLegacyToken();
        if (!cancelled) {
        await fetchConfig();
        }
    })();

    const interval = setInterval(async () => {
      try {
        await axios.get(`${API_BASE}/health`);
        setStatus('Connected');
      } catch {
        setStatus('Disconnected');
      }
    }, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [fetchConfig, bootstrapLegacyToken]);

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout lang={lang} setLang={setLang} status={status} />}>
          <Route index element={<Suspense fallback={<PageFallback />}><Dashboard status={status} t={t} /></Suspense>} />

          <Route path="settings" element={
            <Suspense fallback={<PageFallback />}>
              <Settings
                config={config}
                setConfig={setConfig}
                saveConfig={saveConfig}
                fetchConfig={fetchConfig}
                modelProviders={modelProviders}
                t={t}
              />
            </Suspense>
          } />
          <Route path="model-providers" element={
            <Suspense fallback={<PageFallback />}>
              <ModelProviders
                modelProviders={modelProviders}
                setModelProviders={setModelProviders}
                fetchModelProviders={fetchModelProviders}
                t={t}
              />
            </Suspense>
          } />
          <Route path="evolution" element={<Suspense fallback={<PageFallback />}><Evolution t={t} /></Suspense>} />
          <Route path="chat" element={<Suspense fallback={<PageFallback />}><Chat t={t} /></Suspense>} />
          <Route path="skills" element={<Suspense fallback={<PageFallback />}><Skills t={t} /></Suspense>} />
          <Route path="memory" element={<Suspense fallback={<PageFallback />}><MemoryGalaxy t={t} /></Suspense>} />
          <Route path="logs" element={<Suspense fallback={<PageFallback />}><Logs t={t} /></Suspense>} />
          <Route path="debug" element={<Suspense fallback={<PageFallback />}><Debug t={t} /></Suspense>} />
          <Route path="security" element={
            <Suspense fallback={<PageFallback />}>
              <Security
                t={t}
                config={config}
                setConfig={setConfig}
                saveConfig={saveConfig}
                fetchConfig={fetchConfig}
              />
            </Suspense>
          } />
          <Route path="cron" element={<Suspense fallback={<PageFallback />}><Cron t={t} /></Suspense>} />
          <Route path="canvas" element={<Suspense fallback={<PageFallback />}><Canvas t={t} /></Suspense>} />
          <Route path="policy/tools" element={
            <Suspense fallback={<PageFallback />}>
              <ToolPolicy
                config={config}
                setConfig={setConfig}
                saveConfig={saveConfig}
                t={t}
              />
            </Suspense>
          } />
          <Route path="policy/agents" element={
            <Suspense fallback={<PageFallback />}>
              <AgentPolicy
                config={config}
                setConfig={setConfig}
                saveConfig={saveConfig}
                t={t}
              />
            </Suspense>
          } />
          <Route path="policy/llm-router" element={<Suspense fallback={<PageFallback />}><LlmRouter t={t} /></Suspense>} />
          <Route path="policy/release-gate" element={<Suspense fallback={<PageFallback />}><ReleaseGate t={t} /></Suspense>} />
          <Route path="policy/optimization-tasks" element={<Suspense fallback={<PageFallback />}><OptimizationTasks t={t} /></Suspense>} />
          <Route path="policy/trainer-jobs" element={<Suspense fallback={<PageFallback />}><TrainerJobs t={t} /></Suspense>} />
          <Route path="policy/observability" element={<Suspense fallback={<PageFallback />}><Observability t={t} /></Suspense>} />
          <Route path="policy/persona-eval" element={<Suspense fallback={<PageFallback />}><PersonaEval t={t} /></Suspense>} />
          <Route path="workflow" element={<Suspense fallback={<PageFallback />}><WorkflowStudio t={t} showNotice={showToast} /></Suspense>} />
          <Route path="policy/audit" element={<Suspense fallback={<PageFallback />}><PolicyAudit t={t} /></Suspense>} />

          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>

      {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
    </BrowserRouter>
  );
}

export default App;
