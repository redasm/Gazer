import { useEffect, useMemo, useState } from 'react';

const useUrlState = (key, defaultValue, { parse, serialize } = {}) => {
    const read = () => {
        try {
            const qs = new URLSearchParams(window.location.search || '');
            const raw = qs.get(key);
            if (raw === null) return defaultValue;
            return parse ? parse(raw) : raw;
        } catch {
            return defaultValue;
        }
    };

    const initial = useMemo(read, []);
    const [value, setValue] = useState(initial);

    useEffect(() => {
        try {
            const url = new URL(window.location.href);
            const encoded = serialize ? serialize(value) : String(value);
            url.searchParams.set(key, encoded);
            window.history.replaceState({}, '', url.toString());
        } catch {
            // no-op
        }
    }, [key, value, serialize]);

    return [value, setValue];
};

export default useUrlState;

