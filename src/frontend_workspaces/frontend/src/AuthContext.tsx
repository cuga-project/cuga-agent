import React, { createContext, useContext, useState, useEffect, ReactNode } from "react";
import * as api from "./api";

interface UserInfo {
  name?: string;
  email?: string;
  sub?: string;
  roles?: string[];
}

interface AuthContextType {
  user: UserInfo | null;
  isLoading: boolean;
  authorizationEnabled: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserInfo | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [authorizationEnabled, setAuthorizationEnabled] = useState(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const config = await api.getAuthConfig();
        if (!cancelled) {
          setAuthorizationEnabled(config.authorization_enabled || false);
        }
        
        if (!config.enabled) {
          setUser(null);
          setIsLoading(false);
          return;
        }

        const response = await api.apiFetch("/auth/userinfo");
        if (response.ok) {
          const data = await response.json();
          if (!cancelled) {
            setUser({
              name: data.name,
              email: data.email,
              sub: data.sub,
              roles: data.roles || [],
            });
          }
        }
      } catch (error) {
        console.error("Failed to fetch user info:", error);
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <AuthContext.Provider value={{ user, isLoading, authorizationEnabled }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}

// Made with Bob
