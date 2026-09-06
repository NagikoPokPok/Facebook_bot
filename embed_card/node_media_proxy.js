/**
 * Server-Side Media Proxy cho Node.js / Express
 * Endpoint: GET /api/media-proxy?url=<ENCODED_URL>
 */

const express = require("express");
const crypto = require("crypto");
const https = require("https");
const http = require("http");

const router = express.Router();

// Bộ nhớ cache tạm thời (In-memory cache) kèm TTL 24 giờ
const cache = new Map();
const CACHE_TTL_MS = 24 * 60 * 60 * 1000;

function getCacheKey(url) {
  return crypto.createHash("sha256").update(url).digest("hex");
}

router.get("/api/media-proxy", async (req, res) => {
  const targetUrl = req.query.url;

  if (!targetUrl) {
    return res.status(400).json({ error: "Thiếu tham số url" });
  }

  // Bảo vệ SSRF: Chỉ cho phép HTTP/HTTPS
  try {
    const parsed = new URL(targetUrl);
    if (!["http:", "https:"].includes(parsed.protocol)) {
      return res.status(400).json({ error: "Giao thức không hợp lệ" });
    }
  } catch (err) {
    return res.status(400).json({ error: "URL không đúng định dạng" });
  }

  const key = getCacheKey(targetUrl);

  // 1. Kiểm tra Cache
  const cached = cache.get(key);
  if (cached && Date.now() - cached.timestamp < CACHE_TTL_MS) {
    res.setHeader("Content-Type", cached.contentType);
    res.setHeader("Cache-Control", "public, max-age=86400, immutable");
    res.setHeader("X-Proxy-Cache", "HIT");
    return res.send(cached.buffer);
  }

  // 2. Fetch ảnh với Header chống hotlink của Facebook
  const fetchOptions = {
    headers: {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
      "Referer": "https://www.facebook.com/",
      "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
      "Sec-Fetch-Dest": "image",
      "Sec-Fetch-Mode": "no-cors",
      "Sec-Fetch-Site": "cross-site",
    },
    timeout: 6000,
  };

  try {
    const protocol = targetUrl.startsWith("https") ? https : http;
    const request = protocol.get(targetUrl, fetchOptions, (response) => {
      if (response.statusCode !== 200) {
        return res.status(502).json({ error: `Nguồn ngoài trả về HTTP ${response.statusCode}` });
      }

      const contentType = response.headers["content-type"] || "image/jpeg";
      const chunks = [];

      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => {
        const buffer = Buffer.concat(chunks);
        
        // Lưu vào cache
        cache.set(key, { buffer, contentType, timestamp: Date.now() });

        res.setHeader("Content-Type", contentType);
        res.setHeader("Cache-Control", "public, max-age=86400, immutable");
        res.setHeader("X-Proxy-Cache", "MISS");
        res.send(buffer);
      });
    });

    request.on("error", (err) => {
      console.error("[MediaProxy] Lỗi tải ảnh:", err.message);
      res.status(502).json({ error: "Lỗi kết nối khi tải ảnh", details: err.message });
    });
  } catch (error) {
    res.status(500).json({ error: "Lỗi server nội bộ", details: error.message });
  }
});

module.exports = router;
