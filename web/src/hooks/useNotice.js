import { useCallback, useState } from 'react';

const DEFAULT_DURATION_MS = 3000;

const useNotice = (durationMs = DEFAULT_DURATION_MS) => {
    const [notice, setNotice] = useState(null);

    const showNotice = useCallback((message, type = 'success') => {
        setNotice({ message, type });
        window.setTimeout(() => setNotice(null), durationMs);
    }, [durationMs]);

    return { notice, showNotice, setNotice };
};

export default useNotice;

