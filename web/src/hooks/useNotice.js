import { useCallback, useEffect, useRef, useState } from 'react';

const DEFAULT_DURATION_MS = 3000;

const useNotice = (durationMs = DEFAULT_DURATION_MS) => {
    const [notice, setNotice] = useState(null);
    const timerRef = useRef(null);

    // Clear timer on unmount
    useEffect(() => () => {
        if (timerRef.current) window.clearTimeout(timerRef.current);
    }, []);

    const showNotice = useCallback((message, type = 'success') => {
        if (timerRef.current) window.clearTimeout(timerRef.current);
        setNotice({ message, type });
        timerRef.current = window.setTimeout(() => {
            setNotice(null);
            timerRef.current = null;
        }, durationMs);
    }, [durationMs]);

    return { notice, showNotice, setNotice };
};

export default useNotice;

