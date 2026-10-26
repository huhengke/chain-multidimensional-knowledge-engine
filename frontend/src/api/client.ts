import axios from "axios";

// 统一 axios 实例：开发环境经 Vite 代理同源访问
export const http = axios.create({
  baseURL: "/",
  timeout: 120000,
  withCredentials: true,
  headers: { "X-Platform-Request": "1" },
});

http.interceptors.response.use(
  (res) => res,
  (error) => {
    if (error.response?.status === 401) {
      if (!location.pathname.startsWith("/login")) {
        location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

// 提取后端错误信息
export function errorMessage(e: unknown, fallback = "请求失败"): string {
  if (axios.isAxiosError(e)) {
    const data = e.response?.data;
    return data?.message || (typeof data?.detail === "string" ? data.detail : undefined) || data?.error?.message || e.message || fallback;
  }
  return fallback;
}

