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
 * Download a job's result file with auth. `window.open`/plain <a href>
 * navigation never carries the Authorization header this API requires (it's
 * a Bearer token in localStorage, not a cookie), so every job-result
 * download button hit "Not authenticated" — verified live. Routing through
 * the authenticated `api` client for the initial request, then letting the
 * browser follow the 302 to the presigned MinIO URL (which needs no auth of
 * its own), fixes it.
 *
 * Takes the job object, not a pre-built filename — computing the name here
 * once (extension from result_file_path, not original_filename) instead of
 * duplicating that logic at each call site is what a pdf_to_markdown/
 * pdf_to_word job actually needs: original_filename is still the *source*
 * PDF's name, so a caller-guessed "textlens_resume.pdf" downloaded genuinely
 * Markdown/docx bytes under the wrong extension — verified live. Mirrors the
 * same fix in jobs.py's download route (the canonical Content-Disposition
 * value), since a browser's `download` attribute always wins over that
 * header anyway — this just keeps both in agreement instead of only one.
 */
export async function downloadJobResult(job) {
  const res = await api.get(`/jobs/${job.id}/download`, { responseType: "blob" });
  const base = (job.original_filename || "download").replace(/\.[^./]+$/, "");
  const resultExt = job.result_file_path?.match(/\.[^./]+$/)?.[0];
  const sourceExt = job.original_filename?.match(/\.[^./]+$/)?.[0];
  const ext = resultExt || sourceExt || "";
  const url = URL.createObjectURL(res.data);
  const a = document.createElement("a");
  a.href = url;
  a.download = `textlens_${base}${ext}`;
  a.click();
  URL.revokeObjectURL(url);
}


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