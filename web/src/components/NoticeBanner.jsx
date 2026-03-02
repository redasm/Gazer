import React from 'react';

const NoticeBanner = ({ notice }) => {
    if (!notice) return null;

    const isError = notice.type === 'error';
    return (
        <div
            className="card"
            style={{
                padding: 10,
                marginBottom: 12,
                color: isError ? '#fecaca' : '#bbf7d0',
                border: isError ? '1px solid rgba(239,68,68,0.35)' : '1px solid rgba(34,197,94,0.35)',
                background: isError ? 'rgba(127,29,29,0.25)' : 'rgba(20,83,45,0.25)',
            }}
        >
            {notice.message}
        </div>
    );
};

export default NoticeBanner;

