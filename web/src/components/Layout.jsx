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
    Workflow,
} from 'lucide-react';
import { translations } from '../i18n';

const Layout = ({ lang, setLang, status }) => {
    const t = translations[lang];

    const navGroups = [
        {
            label: t.navGroupSystemConfig || 'System Config',
            items: [
                { path: '/settings', icon: <Settings size={18} />, label: t.tabConfig },
                { path: '/model-providers', icon: <Globe size={18} />, label: t.modelProviders || 'Model Providers' },
            ],
        },
        {
            label: t.navGroupChat || 'Chat',
            items: [
                { path: '/chat', icon: <MessageSquare size={18} />, label: t.chat },
            ],
        },
        {
            label: t.navGroupControl || 'Control',
            items: [
                { path: '/', icon: <LayoutDashboard size={18} />, label: t.dashboard },
                { path: '/skills', icon: <Cpu size={18} />, label: t.skills },
                { path: '/memory', icon: <BookOpen size={18} />, label: t.memory },
                { path: '/cron', icon: <Clock size={18} />, label: t.cronJobs || 'Cron' },
                { path: '/canvas', icon: <Layers size={18} />, label: t.canvas || 'Canvas' },
                { path: '/workflow', icon: <Workflow size={18} />, label: t.workflowBuilder || 'Workflow' },
            ],
        },
        {
            label: t.navGroupPolicy || 'Policy',
            items: [
                { path: '/security', icon: <Shield size={18} />, label: t.security || 'Security' },
                { path: '/policy/tools', icon: <SlidersHorizontal size={18} />, label: t.toolPolicy || 'Tool Policy' },
                { path: '/policy/llm-router', icon: <Globe size={18} />, label: t.llmRouter || 'LLM Router' },
                { path: '/policy/release-gate', icon: <Shield size={18} />, label: t.releaseGate || 'Release Gate' },
                { path: '/policy/optimization-tasks', icon: <Activity size={18} />, label: t.optimizationTasks || 'Optimization Tasks' },
                { path: '/policy/trainer-jobs', icon: <Cpu size={18} />, label: t.trainerJobs || 'Trainer Jobs' },
                { path: '/policy/observability', icon: <Activity size={18} />, label: t.observability || 'Observability' },
                { path: '/policy/persona-eval', icon: <Brain size={18} />, label: t.personaEval || 'Persona Eval' },
                { path: '/policy/audit', icon: <Activity size={18} />, label: t.policyAudit || 'Policy Audit' },
            ],
        },
        {
            label: t.navGroupSoul || 'Soul',
            items: [
                { path: '/evolution', icon: <Brain size={18} />, label: t.tabEvolution },
            ],
        },
        {
            label: t.navGroupSystemTools || t.navGroupSettings || 'Settings',
            items: [
                { path: '/debug', icon: <Bug size={18} />, label: t.debug },
                { path: '/logs', icon: <FileText size={18} />, label: t.logs },
            ],
        },
    ];

    return (
        <div style={{ display: 'flex', minHeight: '100vh', width: '100%', background: 'radial-gradient(circle at top center, #1a2a4a 0%, #050a14 100%)' }}>
            {/* Sidebar */}
            <aside className="sidebar">
                {/* Brand */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '32px', paddingLeft: '12px' }}>
                    <img
                        src="/favicon.ico"
                        alt="Gazer Logo"
                        style={{
                            width: '28px',
                            height: '28px',
                            objectFit: 'contain',
                            borderRadius: '6px',
                        }}
                    />
                    <div>
                        <h1 style={{ fontSize: '17px', margin: 0, fontWeight: 'bold', background: 'linear-gradient(90deg, #fff, #aaa)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>GAZER</h1>
                        <span style={{ fontSize: '10px', color: '#555', letterSpacing: '1px' }}>ADMIN CONSOLE</span>
                    </div>
                </div>

                {/* Grouped Navigation */}
                <nav style={{ flex: 1, overflowY: 'auto' }}>
                    {navGroups.map((group) => (
                        <div key={group.label} style={{ marginBottom: '20px' }}>
                            <div style={{
                                fontSize: '10px',
                                fontWeight: 600,
                                color: '#556',
                                textTransform: 'uppercase',
                                letterSpacing: '1.5px',
                                padding: '0 16px',
                                marginBottom: '6px',
                            }}>
                                {group.label}
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                                {group.items.map((item) => (
                                    <NavLink
                                        key={item.path}
                                        to={item.path}
                                        end={item.path === '/'}
                                        className={({ isActive }) =>
                                            `nav-item ${isActive ? 'active' : ''}`
                                        }
                                        style={{
                                            display: 'flex',
                                            alignItems: 'center',
                                            gap: '10px',
                                            padding: '9px 16px',
                                            borderRadius: '8px',
                                            color: '#8899ac',
                                            textDecoration: 'none',
                                            transition: 'all 0.2s ease',
                                            fontSize: '13px',
                                            position: 'relative',
                                        }}
                                    >
                                        {({ isActive }) => (
                                            <>
                                                {isActive && (
                                                    <span style={{
                                                        position: 'absolute',
                                                        left: 0,
                                                        top: '50%',
                                                        transform: 'translateY(-50%)',
                                                        width: '3px',
                                                        height: '60%',
                                                        borderRadius: '0 3px 3px 0',
                                                        background: '#00ffff',
                                                    }} />
                                                )}
                                                <span style={{ color: isActive ? '#00ffff' : 'inherit' }}>{item.icon}</span>
                                                <span style={{ color: isActive ? '#fff' : 'inherit', fontWeight: isActive ? 500 : 400 }}>{item.label}</span>
                                            </>
                                        )}
                                    </NavLink>
                                ))}
                            </div>
                        </div>
                    ))}
                </nav>

                {/* Language Switcher */}
                <div style={{
                    padding: '12px',
                    background: 'rgba(0,0,0,0.2)',
                    borderRadius: '10px',
                    border: '1px solid rgba(255,255,255,0.05)',
                    marginBottom: '8px'
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px', fontSize: '11px', color: '#666' }}>
                        <Globe size={12} />
                        <span>{t.language}</span>
                    </div>
                    <select
                        value={lang}
                        onChange={(e) => setLang(e.target.value)}
                        style={{
                            width: '100%',
                            background: '#0a1020',
                            border: '1px solid #333',
                            color: '#fff',
                            padding: '6px 8px',
                            borderRadius: '6px',
                            outline: 'none',
                            cursor: 'pointer',
                            fontSize: '12px',
                        }}
                    >
                        <option value="en">English (US)</option>
                        <option value="zh">中文 (简体)</option>
                    </select>
                </div>

                {/* Status Footer */}
                <div style={{
                    padding: '12px',
                    background: 'rgba(0,0,0,0.2)',
                    borderRadius: '10px',
                    border: '1px solid rgba(255,255,255,0.05)',
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '11px', color: '#666' }}>
                        <Activity size={12} color={status === "Connected" ? "#4ade80" : "#ef4444"} />
                        <span>{t.systemStatus || 'SYSTEM STATUS'}</span>
                        <span style={{
                            marginLeft: 'auto',
                            color: status === "Connected" ? "#4ade80" : "#ef4444",
                            fontWeight: 600,
                            fontSize: '11px',
                        }}>
                            {status === "Connected" ? (t.online || 'ONLINE') : (t.offline || 'OFFLINE')}
                        </span>
                    </div>
                </div>
            </aside>

            {/* Main Content */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                {/* Top Header Bar */}
                <header style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'flex-end',
                    padding: '12px 24px',
                    borderBottom: '1px solid rgba(255,255,255,0.05)',
                    background: 'rgba(15, 25, 50, 0.4)',
                    backdropFilter: 'blur(10px)',
                    gap: '12px',
                    flexShrink: 0,
                }}>
                    <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        fontSize: '12px',
                        color: status === "Connected" ? '#4ade80' : '#ef4444',
                    }}>
                        <span style={{
                            width: '8px',
                            height: '8px',
                            borderRadius: '50%',
                            background: status === "Connected" ? '#4ade80' : '#ef4444',
                            boxShadow: status === "Connected" ? '0 0 8px rgba(74,222,128,0.5)' : 'none',
                        }} />
                        <span style={{ fontWeight: 500 }}>
                            {status === "Connected" ? (t.healthOk || 'Health OK') : (t.healthFail || 'Unhealthy')}
                        </span>
                    </div>
                </header>

                <main style={{ flex: 1, padding: '20px', overflowY: 'auto', minWidth: 0, display: 'flex', flexDirection: 'column', alignItems: 'stretch' }}>
                    <Outlet />
                </main>
            </div>
        </div>
    );
};

export default Layout;
