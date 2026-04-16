import React from 'react';
import { Outlet, NavLink } from 'react-router-dom';
import {
    LayoutDashboard,
    MessageSquare,
    Cpu,
    Brain,
    Settings,
    Activity,
    Shield,
    BookOpen,
    FileText,
    Bug,
    Globe,
    Clock,
    Layers,
    SlidersHorizontal,
} from 'lucide-react';
import { translations } from '../i18n';

const Layout = ({ lang, setLang, status }) => {
    const t = translations[lang];

    const navGroups = [
        {
            label: t.navGroupSystemConfig || 'System Config',
            items: [
                { path: '/settings', icon: <Settings size={16} />, label: t.tabConfig },
                { path: '/model-providers', icon: <Globe size={16} />, label: t.modelProviders || 'Model Providers' },
            ],
        },
        {
            label: t.navGroupChat || 'Chat',
            items: [
                { path: '/chat', icon: <MessageSquare size={16} />, label: t.chat },
                { path: '/multi-agent', icon: <Activity size={16} />, label: t.agentKanbanTitle || 'Multi-Agent' },
            ],
        },
        {
            label: t.navGroupControl || 'Control',
            items: [
                { path: '/', icon: <LayoutDashboard size={16} />, label: t.dashboard },
                { path: '/skills', icon: <Cpu size={16} />, label: t.skills },
                { path: '/memory', icon: <BookOpen size={16} />, label: t.memory },
                { path: '/cron', icon: <Clock size={16} />, label: t.cronJobs || 'Cron' },
                { path: '/canvas', icon: <Layers size={16} />, label: t.canvas || 'Canvas' },
            ],
        },
        {
            label: t.navGroupPolicy || 'Policy',
            items: [
                { path: '/security', icon: <Shield size={16} />, label: t.security || 'Security' },
                { path: '/policy/tools', icon: <SlidersHorizontal size={16} />, label: t.toolPolicy || 'Tool Policy' },
                { path: '/policy/llm-router', icon: <Globe size={16} />, label: t.llmRouter || 'LLM Router' },
                { path: '/policy/release-gate', icon: <Shield size={16} />, label: t.releaseGate || 'Release Gate' },
                { path: '/policy/optimization-tasks', icon: <Activity size={16} />, label: t.optimizationTasks || 'Optimization' },
                { path: '/policy/trainer-jobs', icon: <Cpu size={16} />, label: t.trainerJobs || 'Trainer Jobs' },
                { path: '/policy/observability', icon: <Activity size={16} />, label: t.observability || 'Observability' },
                { path: '/policy/audit', icon: <Activity size={16} />, label: t.policyAudit || 'Policy Audit' },
            ],
        },
        {
            label: t.navGroupSoul || 'Soul',
            items: [
                { path: '/personality', icon: <Brain size={16} />, label: t.personalityPage || 'Personality' },
            ],
        },
        {
            label: t.navGroupSystemTools || t.navGroupSettings || 'System',
            items: [
                { path: '/debug', icon: <Bug size={16} />, label: t.debug },
                { path: '/logs', icon: <FileText size={16} />, label: t.logs },
            ],
        },
    ];

    const isOnline = status === 'Connected';

    return (
        <div className="page-root">
            {/* Background layers */}
            <div className="app-bg" />
            <div className="app-orb" />

            {/* Sidebar */}
            <aside className="sidebar">
                {/* Logo */}
                <div className="sidebar-logo">
                    <div className="logo-diamond">
                        <div className="logo-diamond-inner" />
                    </div>
                    <div className="logo-text-wrap">
                        <span className="logo-name">GA<span>Z</span>ER</span>
                        <span className="logo-sub">Admin Console</span>
                    </div>
                </div>

                {/* Nav */}
                <nav style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', marginRight: '-4px', paddingRight: '4px' }}>
                    {navGroups.map((group) => (
                        <div key={group.label} style={{ marginBottom: '16px' }}>
                            <div className="nav-group-label">{group.label}</div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '1px' }}>
                                {group.items.map((item) => (
                                    <NavLink
                                        key={item.path}
                                        to={item.path}
                                        end={item.path === '/'}
                                        className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
                                    >
                                        {({ isActive }) => (
                                            <>
                                                <span className="nav-icon"
                                                    style={{ color: isActive ? 'var(--accent-red)' : undefined }}>
                                                    {item.icon}
                                                </span>
                                                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                                    {item.label}
                                                </span>
                                            </>
                                        )}
                                    </NavLink>
                                ))}
                            </div>
                        </div>
                    ))}
                </nav>

                {/* Language switcher */}
                <div className="lang-switcher" style={{ marginBottom: '8px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px', fontSize: '10px', color: 'var(--text-muted)' }}>
                        <Globe size={11} />
                        <span style={{ textTransform: 'uppercase', letterSpacing: '0.1em' }}>{t.language}</span>
                    </div>
                    <select
                        value={lang}
                        onChange={(e) => setLang(e.target.value)}
                        style={{ cursor: 'pointer' }}
                    >
                        <option value="en">English (US)</option>
                        <option value="zh">中文 (简体)</option>
                    </select>
                </div>

                {/* Status footer */}
                <div className="sidebar-footer">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '11px', color: 'var(--text-muted)' }}>
                        <span
                            className={`status-dot ${isOnline ? 'online' : 'offline'}`}
                        />
                        <span style={{ textTransform: 'uppercase', letterSpacing: '0.1em', fontSize: '10px' }}>
                            {t.systemStatus || 'System'}
                        </span>
                        <span style={{
                            marginLeft: 'auto',
                            color: isOnline ? 'var(--color-ok)' : 'var(--color-error)',
                            fontWeight: 600,
                            fontSize: '10px',
                            letterSpacing: '0.05em',
                        }}>
                            {isOnline ? (t.online || 'ONLINE') : (t.offline || 'OFFLINE')}
                        </span>
                    </div>
                </div>
            </aside>

            {/* Main content */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, position: 'relative', zIndex: 1 }}>
                {/* Top header */}
                <header className="top-header">
                    <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        fontSize: '11px',
                        fontWeight: 500,
                        color: isOnline ? 'var(--color-ok)' : 'var(--color-error)',
                    }}>
                        <span className={`status-dot ${isOnline ? 'online' : 'offline'}`} />
                        {isOnline ? (t.healthOk || 'Health OK') : (t.healthFail || 'Unhealthy')}
                    </div>
                </header>

                {/* Page content */}
                <main style={{
                    flex: 1,
                    padding: '20px 24px',
                    overflowY: 'auto',
                    minWidth: 0,
                    display: 'flex',
                    flexDirection: 'column',
                }}>
                    <Outlet />
                </main>
            </div>
        </div>
    );
};

export default Layout;
