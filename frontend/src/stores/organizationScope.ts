type ResetHandler = () => void;

const resetHandlers = new Map<string, ResetHandler>();

export function registerOrganizationReset(name: string, handler: ResetHandler): () => void {
  resetHandlers.set(name, handler);
  return () => resetHandlers.delete(name);
}

export function resetOrganizationScope(): void {
  for (const handler of resetHandlers.values()) handler();
}

export function getRegisteredOrganizationScopes(): string[] {
  return [...resetHandlers.keys()].sort();
}
