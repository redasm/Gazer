import React from 'react';

/**
 * TableBlock — flat tabular display.
 *
 * Accepts either `{ columns, rows }` (explicit schema) or `{ rows }`
 * where columns are inferred from the first row's keys. `rows` may
 * alternatively be passed as the top-level payload for convenience.
 */
const TableBlock = ({ data = {} }) => {
    const rows = Array.isArray(data.rows)
        ? data.rows
        : Array.isArray(data)
            ? data
            : [];

    if (!rows.length) {
        return <div style={{ color: '#667', fontSize: 12 }}>Empty table</div>;
    }

    const columns = Array.isArray(data.columns) && data.columns.length
        ? data.columns
        : Object.keys(rows[0] || {});

    return (
        <div
            style={{
                overflowX: 'auto',
                borderRadius: 8,
                border: '1px solid rgba(255,255,255,0.08)',
            }}
        >
            <table
                style={{
                    width: '100%',
                    borderCollapse: 'collapse',
                    fontSize: 12,
                }}
            >
                <thead>
                    <tr>
                        {columns.map((col) => (
                            <th
                                key={col}
                                style={{
                                    padding: '6px 10px',
                                    borderBottom: '1px solid rgba(255,255,255,0.1)',
                                    color: '#aab',
                                    textAlign: 'left',
                                    fontWeight: 600,
                                }}
                            >
                                {col}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, i) => (
                        <tr key={i}>
                            {columns.map((col) => (
                                <td
                                    key={col}
                                    style={{
                                        padding: '4px 10px',
                                        borderBottom: '1px solid rgba(255,255,255,0.05)',
                                        color: '#ddd',
                                    }}
                                >
                                    {String(row?.[col] ?? '')}
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};

export default TableBlock;
