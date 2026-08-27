/** Commercial builds enable V1 unless a non-production deployment explicitly disables it. */
export const USER_MANAGEMENT_V1_ENABLED = import.meta.env.VITE_USER_MANAGEMENT_V1 !== "false";
