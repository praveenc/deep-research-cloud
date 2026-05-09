import { useState, useEffect } from 'react';
import { login, logout, getToken, getCurrentEmail } from './auth';
import { submitResearch, getResearchStatus, ResearchResponse, ResearchStatus } from './api';
import { useWebSocket, ProgressMessage } from './useWebSocket';
import ReactMarkdown from 'react-markdown';

// ─── Login Form ──────────────────────────────────────────────────────

function LoginForm({ onLogin }: { onLogin: () => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login(email, password);
      onLogin();
    } catch (err: any) {
      setError(err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-card">
      <h2>Sign In</h2>
      <form onSubmit={handleSubmit}>
        <input type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <input type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={loading}>{loading ? 'Signing in...' : 'Sign In'}</button>
      </form>
    </div>
  );
}

// ─── Research Form ───────────────────────────────────────────────────

function ResearchForm({ onSubmit }: { onSubmit: (res: ResearchResponse) => void }) {
  const [query, setQuery] = useState('');
  const [depth, setDepth] = useState<'quick' | 'standard' | 'comprehensive'>('standard');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (query.trim().length < 10) {
      setError('Query must be at least 10 characters');
      return;
    }
    setError('');
    setLoading(true);
    try {
      const res = await submitResearch({ query, options: { depth } });
      onSubmit(res);
      setQuery('');
    } catch (err: any) {
      setError(err.message || 'Submission failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="research-form">
      <form onSubmit={handleSubmit}>
        <textarea
          placeholder="What would you like to research? (e.g., Compare Amazon Bedrock vs SageMaker for RAG workloads)"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          rows={3}
        />
        <div className="form-row">
          <select value={depth} onChange={(e) => setDepth(e.target.value as any)}>
            <option value="quick">Quick (~2 min)</option>
            <option value="standard">Standard (~5 min)</option>
            <option value="comprehensive">Comprehensive (~10 min)</option>
          </select>
          <button type="submit" disabled={loading}>
            {loading ? 'Submitting...' : '🔬 Research'}
          </button>
        </div>
        {error && <p className="error">{error}</p>}
      </form>
    </div>
  );
}

// ─── Progress View ───────────────────────────────────────────────────

function ProgressView({ slug }: { slug: string }) {
  const { messages, connected } = useWebSocket(slug);
  const [status, setStatus] = useState<ResearchStatus | null>(null);
  const [report, setReport] = useState<string | null>(null);

  // Poll status every 10s
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const s = await getResearchStatus(slug);
        setStatus(s);
        if (s.status === 'COMPLETE' || s.status === 'FAILED') {
          clearInterval(interval);
        }
      } catch { /* ignore */ }
    }, 10000);

    // Initial fetch
    getResearchStatus(slug).then(setStatus).catch(() => {});

    return () => clearInterval(interval);
  }, [slug]);

  // Fetch report when complete
  useEffect(() => {
    if (status?.status === 'COMPLETE' && status.reportUrl) {
      fetch(status.reportUrl)
        .then((r) => r.text())
        .then(setReport)
        .catch(() => {});
    }
  }, [status]);

  const latestProgress = messages[messages.length - 1];
  const pct = latestProgress?.progressPct || 0;

  return (
    <div className="progress-view">
      <div className="progress-header">
        <h3>Research: {slug}</h3>
        <span className={`status-badge ${status?.status?.toLowerCase() || 'pending'}`}>
          {status?.status || 'PENDING'}
        </span>
        {connected && <span className="ws-indicator">● Live</span>}
      </div>

      {/* Progress bar */}
      <div className="progress-bar-container">
        <div className="progress-bar" style={{ width: `${pct}%` }} />
      </div>

      {/* Progress messages */}
      <div className="progress-messages">
        {messages.map((msg, i) => (
          <div key={i} className="progress-msg">
            <span className="step">[{msg.step}]</span> {msg.message}
          </div>
        ))}
      </div>

      {/* Cost summary */}
      {status?.cost && (
        <div className="cost-summary">
          <span>Tokens: {status.cost.totalTokens.toLocaleString()}</span>
          <span>Cost: ${status.cost.estimatedCostUsd.toFixed(4)}</span>
        </div>
      )}

      {/* Report viewer */}
      {report && (
        <div className="report-viewer">
          <ReactMarkdown>{report}</ReactMarkdown>
        </div>
      )}
    </div>
  );
}

// ─── Main App ────────────────────────────────────────────────────────

export function App() {
  const [authenticated, setAuthenticated] = useState(false);
  const [activeSlug, setActiveSlug] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Check existing session on mount
  useEffect(() => {
    getToken().then((token) => {
      setAuthenticated(!!token);
      setLoading(false);
    });
  }, []);

  if (loading) {
    return <div className="app"><div className="loading">Loading...</div></div>;
  }

  if (!authenticated) {
    return (
      <div className="app">
        <header><h1>Deep Research Cloud</h1><p>AI-powered research reports on AWS & cloud technology</p></header>
        <LoginForm onLogin={() => setAuthenticated(true)} />
      </div>
    );
  }

  return (
    <div className="app">
      <header>
        <h1>Deep Research Cloud</h1>
        <div className="header-actions">
          <span>{getCurrentEmail()}</span>
          <button className="btn-secondary" onClick={() => { logout(); setAuthenticated(false); }}>
            Sign Out
          </button>
        </div>
      </header>

      <main>
        <ResearchForm onSubmit={(res) => setActiveSlug(res.slug)} />

        {activeSlug && <ProgressView slug={activeSlug} />}
      </main>
    </div>
  );
}
