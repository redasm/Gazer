import React from 'react';

/**
 * WeatherCard — Gazer-specific sample card.
 *
 * Accepted `data` shape:
 *   { city, temp, condition, humidity?, forecast_7d?: [{date, temp_high, temp_low}] }
 */
const WeatherCard = ({ data = {} }) => {
    const forecast = Array.isArray(data.forecast_7d) ? data.forecast_7d.slice(0, 7) : [];
    return (
        <div
            style={{
                padding: '12px 14px',
                borderRadius: 12,
                background: 'linear-gradient(135deg, rgba(239,35,60,0.12), rgba(96,165,250,0.08))',
                border: '1px solid rgba(239,35,60,0.22)',
            }}
        >
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
                <span style={{ fontSize: 16, fontWeight: 700, color: '#fff' }}>
                    {data.city || '—'}
                </span>
                <span style={{ fontSize: 22, color: '#ffd0d6', fontWeight: 600 }}>
                    {data.temp ?? '—'}°
                </span>
                <span style={{ fontSize: 13, color: '#cbd' }}>{data.condition || ''}</span>
            </div>
            {data.humidity != null && (
                <div style={{ fontSize: 12, color: '#aab', marginTop: 2 }}>
                    湿度 {data.humidity}%
                </div>
            )}
            {forecast.length > 0 && (
                <div
                    style={{
                        display: 'grid',
                        gridTemplateColumns: `repeat(${forecast.length}, 1fr)`,
                        gap: 6,
                        marginTop: 10,
                    }}
                >
                    {forecast.map((d, i) => (
                        <div
                            key={i}
                            style={{
                                textAlign: 'center',
                                padding: '6px 4px',
                                borderRadius: 6,
                                background: 'rgba(255,255,255,0.05)',
                            }}
                        >
                            <div style={{ fontSize: 10, color: '#778' }}>{d.date}</div>
                            <div style={{ fontSize: 11, color: '#ffd0d6' }}>
                                {d.temp_low}° / {d.temp_high}°
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

export default WeatherCard;
