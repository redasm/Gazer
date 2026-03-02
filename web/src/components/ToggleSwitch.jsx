import React from 'react';

const ToggleSwitch = ({ checked, onChange, disabled = false }) => (
    <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-disabled={disabled}
        onClick={() => {
            if (!disabled) {
                onChange(!checked);
            }
        }}
        style={{
            position: 'relative',
            width: 44,
            height: 24,
            borderRadius: 12,
            background: checked ? '#3b82f6' : 'rgba(255,255,255,0.1)',
            border: '1px solid rgba(255,255,255,0.15)',
            cursor: disabled ? 'not-allowed' : 'pointer',
            flexShrink: 0,
            opacity: disabled ? 0.45 : 1,
        }}
    >
        <span
            style={{
                position: 'absolute',
                top: 2,
                left: checked ? 22 : 2,
                width: 18,
                height: 18,
                borderRadius: '50%',
                background: '#fff',
                transition: 'left 0.2s',
                boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
            }}
        />
    </button>
);

export default ToggleSwitch;
