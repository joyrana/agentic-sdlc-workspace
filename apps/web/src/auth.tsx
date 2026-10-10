import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, tokenStore, unwrap, type Schemas } from "./api/client";

type AuthState = {
  token: string | null;
  me: Schemas["MeOut"] | undefined;
  loading: boolean;
  signIn: (token: string) => void;
  signOut: () => void;
  rolesIn: (projectId: string) => string[];
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => tokenStore.get());
  const qc = useQueryClient();
  const me = useQuery({
    queryKey: ["me", token],
    queryFn: () => unwrap(api.GET("/api/v1/me")),
    enabled: !!token,
    retry: false,
  });

  const signOut = useCallback(() => {
    tokenStore.set(null);
    setToken(null);
    qc.clear();
  }, [qc]);

  useEffect(() => {
    const onUnauthorized = () => signOut();
    window.addEventListener("asw:unauthorized", onUnauthorized);
    return () => window.removeEventListener("asw:unauthorized", onUnauthorized);
  }, [signOut]);

  const value = useMemo<AuthState>(
    () => ({
      token,
      me: me.data,
      loading: !!token && me.isLoading,
      signIn: (t: string) => {
        tokenStore.set(t);
        setToken(t);
      },
      signOut,
      rolesIn: (projectId: string) => me.data?.project_roles[projectId] ?? [],
    }),
    [token, me.data, me.isLoading, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
