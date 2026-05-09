/**
 * WebSocket hook — connects to API Gateway WebSocket for real-time progress.
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import { config } from './config';
import { getToken } from './auth';

export interface ProgressMessage {
  type: 'progress';
  slug: string;
  message: string;
  step: string;
  progressPct: number;
}

export function useWebSocket(slug: string | null) {
  const wsRef = useRef<WebSocket | null>(null);
  const [messages, setMessages] = useState<ProgressMessage[]>([]);
  const [connected, setConnected] = useState(false);

  const connect = useCallback(async () => {
    if (!slug || !config.wsUrl) return;

    const token = await getToken();
    if (!token) return;

    const ws = new WebSocket(`${config.wsUrl}?token=${token}`);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);

    ws.onmessage = (event) => {
      try {
        const msg: ProgressMessage = JSON.parse(event.data);
        if (msg.slug === slug) {
          setMessages((prev) => [...prev, msg]);
        }
      } catch {
        // ignore non-JSON messages
      }
    };
  }, [slug]);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  return { messages, connected };
}
