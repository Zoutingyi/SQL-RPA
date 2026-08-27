import { create } from "zustand";
import { listTables, getTableSchema, getOperationLogs } from "../api/database";
import { isOrganizationRequestCancelled } from "../api/client";
import type { DbTable, DbColumn, DbOperation } from "../types";
import { registerOrganizationReset } from "./organizationScope";

interface DatabaseStore {
  tables: DbTable[];
  selectedTable: string | null;
  columns: DbColumn[];
  operations: DbOperation[];
  loading: boolean;
  error: string | null;

  loadTables: () => Promise<void>;
  selectTable: (name: string) => Promise<void>;
  loadOperations: () => Promise<void>;
  clearError: () => void;
}

export const useDatabaseStore = create<DatabaseStore>((set) => ({
  tables: [],
  selectedTable: null,
  columns: [],
  operations: [],
  loading: false,
  error: null,

  loadTables: async () => {
    set({ loading: true, error: null });
    try {
      const tables = await listTables();
      set({ tables, loading: false });
    } catch (error) {
      if (isOrganizationRequestCancelled(error)) return;
      set({ error: "加载表列表失败", loading: false });
    }
  },

  selectTable: async (name: string) => {
    set({ selectedTable: name, loading: true });
    try {
      const schema = await getTableSchema(name);
      set({ columns: schema.columns || [], loading: false });
    } catch (error) {
      if (isOrganizationRequestCancelled(error)) return;
      set({ error: "加载表结构失败", loading: false });
    }
  },

  loadOperations: async () => {
    set({ loading: true, error: null });
    try {
      const data = await getOperationLogs();
      set({ operations: data.items || data || [], loading: false });
    } catch (error) {
      if (isOrganizationRequestCancelled(error)) return;
      set({ error: "加载操作日志失败", loading: false });
    }
  },

  clearError: () => set({ error: null }),
}));

registerOrganizationReset("database", () => {
  useDatabaseStore.setState({
    tables: [], selectedTable: null, columns: [], operations: [],
    loading: false, error: null,
  });
});
