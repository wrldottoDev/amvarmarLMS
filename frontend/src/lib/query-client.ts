import { QueryClient } from "@tanstack/react-query";

export function crearQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: (intentos, error) => {
          if (error instanceof Error && "status" in error) {
            const status = Number(error.status);
            return status >= 500 && intentos < 2;
          }
          return intentos < 1;
        },
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
}
