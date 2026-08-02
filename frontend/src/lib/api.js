import axios from "axios";

const api = axios.create({ baseURL: "/api/v1" });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (res) => res,
  async (err) => {
    const original = err.config;

    if (err.response?.status === 401 && !original._retry) {
      original._retry = true;
      const refresh = localStorage.getItem("refresh_token");

      if (refresh) {
        try {
          // Must use the versioned path — not the axios instance (to avoid
          // infinite retry loops if the refresh endpoint itself returns 401)
          const { data } = await axios.post("/api/v1/auth/refresh", {
            refresh_token: refresh,
          });

          localStorage.setItem("access_token", data.access_token);
          localStorage.setItem("refresh_token", data.refresh_token);
          original.headers.Authorization = `Bearer ${data.access_token}`;

          return api(original);
        } catch {
          localStorage.clear();
          window.location.href = "/login";
        }
      } else {
        localStorage.clear();
        window.location.href = "/login";
      }
    }

    return Promise.reject(err);
  }
);

export default api;


/**
 * Extract a human-readable error message from an Axios error response.
 * Handles FastAPI validation errors (array of {loc, msg}), plain strings,
 * and object detail shapes.
 *
 * @param {unknown} err   - The error caught in a try/catch block.
 * @param {string}  fallback - Returned when no useful detail is found.
 */
export function errMsg(err, fallback = "Something went wrong") {
  const detail = err?.response?.data?.detail;
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => d.msg || JSON.stringify(d)).join(", ");
  }
  if (typeof detail === "object" && detail.message) return detail.message;
  if (typeof detail === "object" && detail.code) return detail.code;
  return fallback;
}