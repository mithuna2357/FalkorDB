import type { NextConfig } from "next"

// BACKEND_URL = base URL of the FastAPI backend (e.g. http://localhost:8000).
// When set, /api/* is proxied there; when unset, the UI runs on fixtures/*.json.
const backend = process.env.BACKEND_URL?.replace(/\/+$/, "")
const useFixtures = process.env.USE_FIXTURES === "true" || !backend

const nextConfig: NextConfig = {
  env: {
    HOR_DATA_MODE: useFixtures ? "fixtures" : "live",
  },
  async rewrites() {
    return useFixtures ? [] : [{ source: "/api/:path*", destination: `${backend}/api/:path*` }]
  },
}

export default nextConfig
