import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { ApiError } from "./api/client";

/** Global refresh signal: bumping `version` makes every mounted useApi() reload. */
export const RefreshContext = createContext<{ version: number; refresh: () => void }>({ version: 0, refresh: () => {} });
export const useRefresh = () => useContext(RefreshContext);

export interface ApiState<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  reload: () => void;
}

/** Load data from the backend with loading / error state. Pass `null` as fn to skip loading. */
export function useApi<T>(fn: (() => Promise<T>) | null, deps: unknown[]): ApiState<T> {
  const { version } = useRefresh();
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError>();
  const [loading, setLoading] = useState<boolean>(fn !== null);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    const current = fnRef.current;
    if (!current) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(undefined);
    current()
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, version, tick, fn === null]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}
