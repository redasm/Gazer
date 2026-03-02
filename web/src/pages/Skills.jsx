import React, { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Plus, Save, FileText, Cpu, Package, Eye, Pencil, ChevronRight, FolderOpen, File } from 'lucide-react';
import API_BASE from '../config';

const mdComponents = {
    h1: ({ children }) => <h1 style={{ fontSize: 18, fontWeight: 700, color: '#e0f0ff', margin: '16px 0 8px', borderBottom: '1px solid rgba(255,255,255,0.08)', paddingBottom: 8 }}>{children}</h1>,
    h2: ({ children }) => <h2 style={{ fontSize: 15, fontWeight: 600, color: '#c0d8f0', margin: '14px 0 6px' }}>{children}</h2>,
    h3: ({ children }) => <h3 style={{ fontSize: 14, fontWeight: 600, color: '#a0b8d0', margin: '10px 0 4px' }}>{children}</h3>,
    p: ({ children }) => <p style={{ margin: '6px 0', lineHeight: 1.7, color: '#b0c0d8' }}>{children}</p>,
    ul: ({ children }) => <ul style={{ margin: '6px 0', paddingLeft: 20, color: '#b0c0d8' }}>{children}</ul>,
    ol: ({ children }) => <ol style={{ margin: '6px 0', paddingLeft: 20, color: '#b0c0d8' }}>{children}</ol>,
    li: ({ children }) => <li style={{ marginBottom: 3, lineHeight: 1.6 }}>{children}</li>,
    code({ className, children }) {
        const isBlock = className || String(children).includes('\n');
        if (isBlock) {
            return (
                <pre style={{ background: '#0d1117', borderRadius: 8, padding: 12, margin: '8px 0', overflow: 'auto', border: '1px solid rgba(255,255,255,0.06)' }}>
                    <code style={{ fontSize: 13, lineHeight: 1.5, color: '#c9d1d9' }}>{children}</code>
                </pre>
            );
        }
        return <code style={{ background: 'rgba(255,255,255,0.08)', padding: '1px 5px', borderRadius: 4, fontSize: '0.9em', color: '#c9d1d9' }}>{children}</code>;
    },
    a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: '#58a6ff' }}>{children}</a>,
    blockquote: ({ children }) => <blockquote style={{ borderLeft: '3px solid rgba(96,165,250,0.4)', margin: '8px 0', paddingLeft: 14, color: '#8899ac' }}>{children}</blockquote>,
    hr: () => <hr style={{ border: 'none', borderTop: '1px solid rgba(255,255,255,0.06)', margin: '12px 0' }} />,
    table: ({ children }) => <table style={{ width: '100%', borderCollapse: 'collapse', margin: '8px 0', fontSize: 13 }}>{children}</table>,
    th: ({ children }) => <th style={{ padding: '6px 10px', borderBottom: '1px solid rgba(255,255,255,0.1)', color: '#9ca3af', textAlign: 'left', fontWeight: 600 }}>{children}</th>,
    td: ({ children }) => <td style={{ padding: '6px 10px', borderBottom: '1px solid rgba(255,255,255,0.05)', color: '#b0c0d8' }}>{children}</td>,
};

const SkillCard = ({ skill, selected, onClick }) => (
    <div
        onClick={onClick}
        style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '12px 14px',
            borderRadius: 10,
            cursor: 'pointer',
            background: selected ? 'rgba(139,92,246,0.12)' : 'rgba(255,255,255,0.02)',
            border: selected ? '1px solid rgba(139,92,246,0.3)' : '1px solid rgba(255,255,255,0.05)',
            transition: 'all 0.15s',
        }}
        onMouseEnter={(e) => { if (!selected) e.currentTarget.style.background = 'rgba(255,255,255,0.04)'; }}
        onMouseLeave={(e) => { if (!selected) e.currentTarget.style.background = 'rgba(255,255,255,0.02)'; }}
    >
        <div style={{
            width: 36, height: 36, borderRadius: 8,
            background: skill.builtin ? 'rgba(96,165,250,0.12)' : 'rgba(74,222,128,0.12)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
        }}>
            {skill.builtin
                ? <Package size={16} color="#60a5fa" />
                : <Cpu size={16} color="#4ade80" />
            }
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 500, color: selected ? '#fff' : '#c0d0e0' }}>{skill.name}</div>
            <div style={{ fontSize: 11, color: '#667', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{skill.description}</div>
        </div>
        <ChevronRight size={14} color={selected ? '#a78bfa' : '#445'} />
    </div>
);

const Skills = ({ t }) => {
    const [skills, setSkills] = useState([]);
    const [selectedSkill, setSelectedSkill] = useState(null);
    const [skillFiles, setSkillFiles] = useState([]);
    const [selectedFile, setSelectedFile] = useState("SKILL.md");
    const [content, setContent] = useState("");
    const [newSkillName, setNewSkillName] = useState("");
    const [isCreating, setIsCreating] = useState(false);
    const [editMode, setEditMode] = useState(false);
    const [toast, setToast] = useState(null);

    const showToast = (text, type = 'success') => {
        setToast({ text, type });
        setTimeout(() => setToast(null), 4000);
    };

    useEffect(() => { fetchSkills(); }, []);

    const fetchSkills = async () => {
        try {
            const res = await axios.get(`${API_BASE}/skills`);
            setSkills(res.data);
        } catch (err) {
            console.error("Failed to fetch skills", err);
        }
    };

    const builtinSkills = useMemo(() => skills.filter(s => s.builtin), [skills]);
    const extensionSkills = useMemo(() => skills.filter(s => !s.builtin), [skills]);

    const handleSelect = async (skill) => {
        try {
            const [contentRes, filesRes] = await Promise.all([
                axios.get(`${API_BASE}/skills/${skill.name}`),
                axios.get(`${API_BASE}/skills/${skill.name}/files`),
            ]);
            setSelectedSkill(skill);
            setSkillFiles(filesRes.data.files || []);
            setSelectedFile("SKILL.md");
            setContent(contentRes.data.content);
            setEditMode(false);
        } catch {
            showToast(t.loadFailed, 'error');
        }
    };

    const handleFileSelect = async (file) => {
        if (!selectedSkill || file === selectedFile) return;
        try {
            const res = await axios.get(`${API_BASE}/skills/${selectedSkill.name}`, { params: { file } });
            setSelectedFile(file);
            setContent(res.data.content);
            setEditMode(false);
        } catch {
            showToast(t.loadFailed, 'error');
        }
    };

    const handleSave = async () => {
        if (!selectedSkill) return;
        try {
            await axios.put(`${API_BASE}/skills/${selectedSkill.name}`, { content, file: selectedFile });
            showToast(t.skillSaved, 'success');
        } catch {
            showToast(t.skillSaveFailed, 'error');
        }
    };

    const handleCreate = async () => {
        const rawName = newSkillName.trim();
        if (!rawName) return;
        const sanitizedName = rawName.replace(/[^a-zA-Z0-9_-]/g, "");
        if (!sanitizedName) {
            showToast(t.skillCreateFailed, 'error');
            return;
        }
        try {
            await axios.post(`${API_BASE}/skills`, { name: sanitizedName });
            setIsCreating(false);
            setNewSkillName("");
            fetchSkills();
        } catch (err) {
            const detail = err?.response?.data?.detail;
            showToast(detail ? `${t.skillCreateFailed}: ${detail}` : t.skillCreateFailed, 'error');
        }
    };

    const renderSkillGroup = (label, icon, groupSkills) => {
        if (groupSkills.length === 0) return null;
        return (
            <div style={{ marginBottom: 20 }}>
                <div style={{
                    fontSize: 10, fontWeight: 600, color: '#556',
                    textTransform: 'uppercase', letterSpacing: 1.5,
                    padding: '0 4px', marginBottom: 8,
                    display: 'flex', alignItems: 'center', gap: 6,
                }}>
                    {icon} {label}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {groupSkills.map(skill => (
                        <SkillCard
                            key={skill.name}
                            skill={skill}
                            selected={selectedSkill?.name === skill.name}
                            onClick={() => handleSelect(skill)}
                        />
                    ))}
                </div>
            </div>
        );
    };

    return (
        <div style={{ display: 'flex', gap: 16, flex: 1, minHeight: 0, height: 'calc(100vh - 100px)' }}>
            {/* Left: Skill List */}
            <div style={{
                width: 280, flexShrink: 0,
                background: 'rgba(15, 25, 50, 0.65)',
                borderRadius: 14, border: '1px solid rgba(255,255,255,0.06)',
                display: 'flex', flexDirection: 'column',
                overflow: 'hidden',
            }}>
                <div style={{
                    padding: '14px 16px',
                    borderBottom: '1px solid rgba(255,255,255,0.06)',
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                }}>
                    <h2 style={{
                        fontSize: 15, fontWeight: 600, color: '#fff', margin: 0,
                        display: 'flex', alignItems: 'center', gap: 8,
                    }}>
                        <Cpu size={18} color="#a78bfa" />
                        {t.skills}
                    </h2>
                    <button onClick={() => setIsCreating(true)} className="btn-icon" title={t.create}>
                        <Plus size={16} />
                    </button>
                </div>

                {isCreating && (
                    <div style={{
                        padding: '12px 14px',
                        borderBottom: '1px solid rgba(255,255,255,0.06)',
                        background: 'rgba(0,0,0,0.15)',
                    }}>
                        <input
                            placeholder={t.skillNamePlaceholder}
                            value={newSkillName}
                            onChange={(e) => setNewSkillName(e.target.value)}
                            onKeyDown={(e) => { if (e.key === 'Enter') handleCreate(); }}
                            style={{
                                width: '100%', background: 'rgba(0,0,0,0.3)',
                                border: '1px solid rgba(255,255,255,0.1)', borderRadius: 6,
                                padding: '7px 10px', color: '#fff', fontSize: 13, outline: 'none',
                                marginBottom: 8,
                            }}
                        />
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 6 }}>
                            <button onClick={() => { setIsCreating(false); setNewSkillName(""); }} className="btn-ghost">{t.cancel}</button>
                            <button onClick={handleCreate} className="btn-primary">{t.create}</button>
                        </div>
                    </div>
                )}

                <div style={{ flex: 1, overflowY: 'auto', padding: '12px 10px' }}>
                    {renderSkillGroup(
                        t.skillsBuiltin || 'Built-in',
                        <Package size={10} color="#60a5fa" />,
                        builtinSkills
                    )}
                    {renderSkillGroup(
                        t.skillsExtension || 'Extension',
                        <Cpu size={10} color="#4ade80" />,
                        extensionSkills
                    )}
                    {skills.length === 0 && (
                        <div style={{ color: '#556', fontSize: 13, textAlign: 'center', padding: 20 }}>
                            {t.noSkills || 'No skills found.'}
                        </div>
                    )}
                </div>
            </div>

            {/* Right: Content Area */}
            <div style={{
                flex: 1,
                background: 'rgba(15, 25, 50, 0.65)',
                borderRadius: 14, border: '1px solid rgba(255,255,255,0.06)',
                display: 'flex', flexDirection: 'column',
                overflow: 'hidden', minWidth: 0,
            }}>
                {selectedSkill ? (
                    <>
                        {/* Header */}
                        <div style={{
                            padding: '10px 16px',
                            borderBottom: '1px solid rgba(255,255,255,0.06)',
                            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                            background: 'rgba(0,0,0,0.15)',
                            flexShrink: 0,
                        }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#c0d0e0', fontSize: 13 }}>
                                <FileText size={15} color="#667" />
                                <span style={{ fontFamily: 'monospace' }}>{selectedSkill.name}/{selectedFile}</span>
                            </div>
                            <div style={{ display: 'flex', gap: 6 }}>
                                {selectedFile.endsWith('.md') && (
                                    <button
                                        onClick={() => setEditMode(!editMode)}
                                        className="btn-ghost"
                                        style={{ gap: 5 }}
                                    >
                                        {editMode
                                            ? <><Eye size={14} /> {t.skillPreview || 'Preview'}</>
                                            : <><Pencil size={14} /> {t.skillEdit || 'Edit'}</>
                                        }
                                    </button>
                                )}
                                {(editMode || !selectedFile.endsWith('.md')) && (
                                    <button onClick={handleSave} className="btn-primary">
                                        <Save size={14} /> {t.saveChanges}
                                    </button>
                                )}
                            </div>
                        </div>

                        {/* File tabs (only shown when skill has multiple files) */}
                        {skillFiles.length > 1 && (
                            <div style={{
                                display: 'flex', flexWrap: 'wrap', gap: 2,
                                padding: '6px 12px',
                                borderBottom: '1px solid rgba(255,255,255,0.06)',
                                background: 'rgba(0,0,0,0.1)',
                                flexShrink: 0,
                            }}>
                                {skillFiles.map(f => (
                                    <button
                                        key={f}
                                        onClick={() => handleFileSelect(f)}
                                        style={{
                                            display: 'flex', alignItems: 'center', gap: 5,
                                            padding: '4px 10px', borderRadius: 6,
                                            fontSize: 12, cursor: 'pointer',
                                            border: f === selectedFile ? '1px solid rgba(139,92,246,0.3)' : '1px solid transparent',
                                            background: f === selectedFile ? 'rgba(139,92,246,0.12)' : 'transparent',
                                            color: f === selectedFile ? '#c4b5fd' : '#778',
                                            transition: 'all 0.15s',
                                        }}
                                    >
                                        {f.includes('/') ? <FolderOpen size={11} /> : <File size={11} />}
                                        {f}
                                    </button>
                                ))}
                            </div>
                        )}

                        {/* Content */}
                        {selectedFile.endsWith('.md') && !editMode ? (
                            <div style={{
                                flex: 1, overflowY: 'auto', padding: '16px 24px',
                            }}>
                                <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                                    {content}
                                </ReactMarkdown>
                            </div>
                        ) : (
                            <textarea
                                value={content}
                                onChange={(e) => setContent(e.target.value)}
                                spellCheck={false}
                                style={{
                                    flex: 1, background: '#0d1117', color: '#c9d1d9',
                                    padding: 20, fontFamily: "'Cascadia Code', 'Fira Code', monospace",
                                    fontSize: 13, lineHeight: 1.6, resize: 'none',
                                    border: 'none', outline: 'none',
                                }}
                            />
                        )}
                    </>
                ) : (
                    <div style={{
                        flex: 1, display: 'flex', flexDirection: 'column',
                        alignItems: 'center', justifyContent: 'center', color: '#445',
                        gap: 12,
                    }}>
                        <Cpu size={48} style={{ strokeWidth: 1.2 }} />
                        <p style={{ fontSize: 13, color: '#556' }}>{t.selectSkillPrompt}</p>
                    </div>
                )}
            </div>

            {/* Toast */}
            {toast && (
                <div style={{
                    position: 'fixed', bottom: 32, right: 32, zIndex: 9999,
                    padding: '12px 24px', borderRadius: 10,
                    background: toast.type === 'error' ? 'rgba(239,68,68,0.85)' : 'rgba(34,197,94,0.85)',
                    backdropFilter: 'blur(12px)', color: '#fff', fontWeight: 600, fontSize: 13,
                    boxShadow: '0 8px 32px rgba(0,0,0,0.4)', animation: 'fadeIn 0.3s ease-out',
                }}>
                    {toast.text}
                </div>
            )}
        </div>
    );
};

export default Skills;
