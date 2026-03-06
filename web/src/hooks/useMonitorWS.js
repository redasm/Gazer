import { useEffect, useRef, useState } from 'react';

import API_BASE from '../config';

function buildMonitorWsUrl() {
  const base = new URL(API_BASE, window.location.origin);
  const wsBase = new URL('/ws/monitor', base);
  wsBase.protocol = wsBase.protocol === 'https:' ? 'wss:' : 'ws:';
  return wsBase.toString();
}

export function useMonitorWS({ onEvent }) {
  const [status, setStatus] = useState('connecting');
  const handlerRef = useRef(onEvent);

  useEffect(() => {
    handlerRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    let socket = null;
    let reconnectTimer = null;
    let disposed = false;

    const connect = () => {
      if (disposed) {
        return;
      }

      setStatus((prev) => (prev === 'live' ? 'reconnecting' : 'connecting'));

      try {
        socket = new WebSocket(buildMonitorWsUrl());
      } catch (error) {
        console.error('Failed to open monitor websocket', error);
        setStatus('error');
        reconnectTimer = window.setTimeout(connect, 2000);
        return;
      }

      socket.onopen = () => {
        setStatus('live');
      };

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          handlerRef.current?.(payload);
        } catch (error) {
          console.error('Failed to parse monitor websocket payload', error);
        }
      };

      socket.onerror = () => {
        setStatus('error');
      };

      socket.onclose = () => {
        if (disposed) {
          setStatus('closed');
          return;
        }
        setStatus('reconnecting');
        reconnectTimer = window.setTimeout(connect, 2000);
      };
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer);
      }
      if (socket) {
        socket.close();
      }
    };
  }, []);

  return status;
}
