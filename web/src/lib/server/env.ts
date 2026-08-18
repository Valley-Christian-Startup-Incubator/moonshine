export const DISTILL_HOME = process.env.DISTILL_HOME ?? `${process.env.HOME}/.distill`;
export const JOBS_DIR = `${DISTILL_HOME}/jobs`;
export const RESULTS_DIR = `${DISTILL_HOME}/results`;

export const DAGU_BASE_URL = process.env.DAGU_BASE_URL ?? 'http://127.0.0.1:8081';
export const DAGU_USER = process.env.DAGU_USER ?? 'admin';
export const DAGU_PASSWORD = process.env.DAGU_PASSWORD ?? 'admin';

export const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD ?? 'change-me';

export const ADMIN_SESSION_COOKIE = 'distill_admin';
