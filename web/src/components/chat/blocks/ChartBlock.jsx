import React from 'react';

/**
 * ChartBlock — minimal inline SVG line/bar chart.
 *
 * Deliberately avoids pulling a charting library into the bundle; the
 * payload is a small series that can be sketched with raw SVG. Upgrade
 * path is to replace this with a real library (ECharts/Recharts) once
 * richer visuals are required.
 *
 * Accepted `data` shape:
 *   { type?: 'line'|'bar', title?, series: [{name, values:[n...]}],
 *     labels?: [string...] }
 */
const ChartBlock = ({ data = {} }) => {
    const type = data.type === 'bar' ? 'bar' : 'line';
    const series = Array.isArray(data.series) ? data.series : [];
    const labels = Array.isArray(data.labels) ? data.labels : [];

    const width = 360;
    const height = 160;
    const padding = 24;
    const innerW = width - padding * 2;
    const innerH = height - padding * 2;

    const flat = series.flatMap((s) => (Array.isArray(s.values) ? s.values : []));
    const max = flat.length ? Math.max(...flat) : 1;
    const min = flat.length ? Math.min(...flat, 0) : 0;
    const range = Math.max(max - min, 1);

    const palette = ['#ef233c', '#f59e0b', '#60a5fa', '#4ade80', '#c084fc'];

    const x = (i, total) => padding + (total <= 1 ? 0 : (innerW * i) / (total - 1));
    const y = (v) => padding + innerH - ((v - min) / range) * innerH;

    return (
        <div
            style={{
                padding: 12,
                borderRadius: 10,
                background: 'rgba(255,255,255,0.04)',
                border: '1px solid rgba(255,255,255,0.08)',
            }}
        >
            {data.title && (
                <div
                    style={{
                        color: '#e0e8f0',
                        fontSize: 13,
                        fontWeight: 600,
                        marginBottom: 8,
                    }}
                >
                    {data.title}
                </div>
            )}
            <svg width="100%" viewBox={`0 0 ${width} ${height}`} role="img">
                <rect
                    x={padding}
                    y={padding}
                    width={innerW}
                    height={innerH}
                    fill="none"
                    stroke="rgba(255,255,255,0.08)"
                />
                {series.map((s, si) => {
                    const values = Array.isArray(s.values) ? s.values : [];
                    const color = palette[si % palette.length];
                    if (type === 'bar') {
                        const barW = values.length ? innerW / values.length - 4 : 0;
                        return values.map((v, i) => (
                            <rect
                                key={`${si}-${i}`}
                                x={x(i, values.length) - barW / 2}
                                y={y(v)}
                                width={Math.max(barW, 2)}
                                height={Math.max(padding + innerH - y(v), 1)}
                                fill={color}
                                opacity={0.85}
                            />
                        ));
                    }
                    const d = values
                        .map((v, i) => `${i === 0 ? 'M' : 'L'} ${x(i, values.length)} ${y(v)}`)
                        .join(' ');
                    return (
                        <path
                            key={si}
                            d={d}
                            fill="none"
                            stroke={color}
                            strokeWidth={1.6}
                        />
                    );
                })}
            </svg>
            {labels.length > 0 && (
                <div
                    style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        fontSize: 10,
                        color: '#778',
                        marginTop: 4,
                        padding: `0 ${padding}px`,
                    }}
                >
                    {labels.map((l, i) => (
                        <span key={i}>{l}</span>
                    ))}
                </div>
            )}
            {series.length > 0 && (
                <div
                    style={{
                        display: 'flex',
                        flexWrap: 'wrap',
                        gap: 8,
                        marginTop: 6,
                        fontSize: 11,
                        color: '#aab',
                    }}
                >
                    {series.map((s, si) => (
                        <span
                            key={si}
                            style={{
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: 4,
                            }}
                        >
                            <span
                                style={{
                                    display: 'inline-block',
                                    width: 10,
                                    height: 10,
                                    borderRadius: 2,
                                    background: palette[si % palette.length],
                                }}
                            />
                            {s.name || `series${si + 1}`}
                        </span>
                    ))}
                </div>
            )}
        </div>
    );
};

export default ChartBlock;
