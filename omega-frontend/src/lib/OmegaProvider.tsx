"use client";

import { ReactNode, useEffect, useState } from "react";
import { useSSE, useInitialFetch } from "@/lib/sse";
import { useOmegaStore } from "@/store/omega";

interface OmegaProviderProps {
    children: ReactNode;
}

/**
 * OmegaProvider - Wrap your app with this to enable real-time SSE updates.
 * 
 * This component:
 * 1. Connects to the SSE endpoint
 * 2. Syncs data to Zustand store
 * 3. Shows connection status
 * 
 * Usage in layout.tsx:
 * ```tsx
 * export default function RootLayout({ children }) {
 *   return (
 *     <html>
 *       <body>
 *         <OmegaProvider>{children}</OmegaProvider>
 *       </body>
 *     </html>
 *   );
 * }
 * ```
 */
export function OmegaProvider({ children }: OmegaProviderProps) {
    // Connect to SSE
    useSSE();
    useInitialFetch();

    return <>{children}</>;
}

/**
 * Connection indicator component - shows SSE connection status.
 * Can be placed anywhere in the app.
 */
export function ConnectionIndicator() {
    const isConnected = useOmegaStore((s) => s.isConnected);
    const [showBanner, setShowBanner] = useState(false);

    useEffect(() => {
        // Show banner after 3 seconds if not connected
        const timeout = setTimeout(() => {
            if (!isConnected) {
                setShowBanner(true);
            }
        }, 3000);

        // Hide banner when connected
        if (isConnected) {
            setShowBanner(false);
        }

        return () => clearTimeout(timeout);
    }, [isConnected]);

    if (!showBanner) return null;

    return (
        <div
            style={{
                position: "fixed",
                bottom: 80,
                left: "50%",
                transform: "translateX(-50%)",
                backgroundColor: "rgba(239, 68, 68, 0.9)",
                color: "white",
                padding: "8px 16px",
                borderRadius: "8px",
                fontSize: "13px",
                fontWeight: 500,
                zIndex: 1000,
                display: "flex",
                alignItems: "center",
                gap: "8px",
                boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
            }}
        >
            <span style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                backgroundColor: "#fbbf24",
                animation: "pulse 1.5s infinite"
            }} />
            Connecting to server...
        </div>
    );
}
