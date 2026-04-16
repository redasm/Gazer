import React from 'react';
import { AlertTriangle } from 'lucide-react';

const ConfirmModal = ({ open, title, message, confirmText, cancelText, onConfirm, onCancel, danger = true }) => {
    if (!open) return null;

    return (
        <div
            onClick={onCancel}
            style={{
                position: 'fixed',
                inset: 0,
                zIndex: 10000,
                background: 'rgba(0,0,0,0.6)',
                backdropFilter: 'blur(4px)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
            }}
        >
            <div
                onClick={e => e.stopPropagation()}
                style={{
                    background: 'rgba(12,12,12,0.97)',
                    border: '1px solid rgba(255,255,255,0.1)',
                    borderRadius: '16px',
                    padding: '24px',
                    maxWidth: '400px',
                    width: '90%',
                    boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
                }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '12px' }}>
                    <AlertTriangle size={20} color={danger ? '#ef4444' : '#facc15'} />
                    <h3 style={{ fontSize: '16px', fontWeight: 600, color: '#fff', margin: 0 }}>
                        {title || 'Confirm'}
                    </h3>
                </div>
                <p style={{ fontSize: '13px', color: '#889', lineHeight: 1.6, margin: '0 0 20px' }}>
                    {message || 'Are you sure you want to proceed?'}
                </p>
                <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                    <button
                        onClick={onCancel}
                        style={{
                            background: 'rgba(255,255,255,0.06)',
                            border: '1px solid rgba(255,255,255,0.1)',
                            color: '#aab',
                            padding: '8px 16px',
                            borderRadius: '8px',
                            cursor: 'pointer',
                            fontSize: '13px',
                        }}
                    >
                        {cancelText || 'Cancel'}
                    </button>
                    <button
                        onClick={onConfirm}
                        style={{
                            background: danger ? '#ef233c' : 'rgba(255,255,255,0.12)',
                            border: 'none',
                            color: '#fff',
                            padding: '8px 16px',
                            borderRadius: '8px',
                            cursor: 'pointer',
                            fontSize: '13px',
                            fontWeight: 500,
                        }}
                    >
                        {confirmText || 'Confirm'}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default ConfirmModal;
