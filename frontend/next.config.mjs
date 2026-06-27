/** @type {import('next').NextConfig} */

// When NEXT_OUTPUT_EXPORT=1 (used by the Docker build) we emit a fully static
// site to `out/`, which the FastAPI backend serves directly on a single origin.
// In that mode rewrites are a no-op (there is no Next server), and the browser
// talks to the same origin that serves the HTML, so /api and /ws just work.
// In dev (flag unset) we keep the proxy rewrites so `npm run dev` on :3000 can
// reach the backend on :8000 without CORS.
const isExport = process.env.NEXT_OUTPUT_EXPORT === "1";

const nextConfig = {
  reactStrictMode: true,
  ...(isExport
    ? { output: "export", images: { unoptimized: true }, trailingSlash: true }
    : {
        async rewrites() {
          const backend = process.env.BACKEND_ORIGIN || "http://localhost:8000";
          return [
            { source: "/api/:path*", destination: `${backend}/api/:path*` },
            { source: "/ws/:path*", destination: `${backend}/ws/:path*` },
          ];
        },
      }),
};

export default nextConfig;
