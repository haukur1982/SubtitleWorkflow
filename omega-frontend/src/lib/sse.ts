"use client";

import { useEffect, useRef, useCallback } from "react";
import { useOmegaStore, Job, HealthData } from "@/store/omega";

interface SSEEvent {
    type: "init" | "job.created" | "job.updated" | "job.deleted" | "health.updated";
    data: unknown;
    timestamp: string;
}

interface UseSSEOptions {
    reconnectDelay?: number;
    maxReconnectAttempts?: number;
}

/**
 * Hook to connect to the SSE endpoint and sync with Zustand store.
 * 
 * Usage:
 * ```tsx
 * function App() {
 *   useSSE(); // Connect once at root level
 *   return <YourApp />;
 * }
 * ```
 */
export function useSSE(options: UseSSEOptions = {}) {
    const { reconnectDelay = 3000, maxReconnectAttempts = 10 } = options;

    const eventSourceRef = useRef<EventSource | null>(null);
    const reconnectAttemptsRef = useRef(0);
    const reconnectTimeoutRef = useRef<NodeJS.Timeout | undefined>(undefined);

    const setJobs = useOmegaStore((s) => s.setJobs);
    const updateJob = useOmegaStore((s) => s.updateJob);
    const removeJob = useOmegaStore((s) => s.removeJob);
    const setHealth = useOmegaStore((s) => s.setHealth);
    const setConnected = useOmegaStore((s) => s.setConnected);

    const handleMessage = useCallback((event: MessageEvent) => {
        try {
            const parsed: SSEEvent = JSON.parse(event.data);

            switch (parsed.type) {
                case "init": {
                    const data = parsed.data as { jobs: Job[]; health: HealthData };
                    setJobs(data.jobs);
                    setHealth(data.health);
                    setConnected(true);
                    reconnectAttemptsRef.current = 0;
                    break;
                }

                case "job.created":
                case "job.updated": {
                    updateJob(parsed.data as Job);
                    break;
                }

                case "job.deleted": {
                    const data = parsed.data as { file_stem: string };
                    removeJob(data.file_stem);
                    break;
                }

                case "health.updated": {
                    setHealth(parsed.data as HealthData);
                    break;
                }
            }
        } catch (e) {
            console.error("SSE parse error:", e);
        }
    }, [setJobs, updateJob, removeJob, setHealth, setConnected]);

    const connect = useCallback(() => {
        // Close existing connection
        if (eventSourceRef.current) {
            eventSourceRef.current.close();
        }

        const es = new EventSource("/api/events");
        eventSourceRef.current = es;

        es.onopen = () => {
            console.log("SSE connected");
            setConnected(true);
            reconnectAttemptsRef.current = 0;
        };

        es.onmessage = handleMessage;

        es.onerror = () => {
            console.warn("SSE connection error");
            setConnected(false);
            es.close();

            // Attempt reconnect if not at max attempts
            if (reconnectAttemptsRef.current < maxReconnectAttempts) {
                reconnectAttemptsRef.current++;
                const delay = reconnectDelay * Math.min(reconnectAttemptsRef.current, 5);
                console.log(`SSE reconnecting in ${delay}ms (attempt ${reconnectAttemptsRef.current})`);

                reconnectTimeoutRef.current = setTimeout(() => {
                    connect();
                }, delay);
            }
        };
    }, [handleMessage, reconnectDelay, maxReconnectAttempts, setConnected]);

    useEffect(() => {
        connect();

        return () => {
            if (reconnectTimeoutRef.current) {
                clearTimeout(reconnectTimeoutRef.current);
            }
            if (eventSourceRef.current) {
                eventSourceRef.current.close();
            }
            setConnected(false);
        };
    }, [connect, setConnected]);
}

/**
 * Fallback hook for initial data load (before SSE connects).
 * Also serves as a backup if SSE is unavailable.
 */
export function useInitialFetch() {
    const refreshJobs = useOmegaStore((s) => s.refreshJobs);
    const refreshHealth = useOmegaStore((s) => s.refreshHealth);
    const isConnected = useOmegaStore((s) => s.isConnected);

    useEffect(() => {
        // If not connected via SSE after 5 seconds, fetch manually
        const timeout = setTimeout(() => {
            if (!isConnected) {
                refreshJobs();
                refreshHealth();
            }
        }, 5000);

        return () => clearTimeout(timeout);
    }, [isConnected, refreshJobs, refreshHealth]);
}
