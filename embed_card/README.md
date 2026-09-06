# Hướng Dẫn Tích Hợp "Facebook Embed Card" v2.0

Bộ giải pháp hoàn chỉnh refactor component **Facebook Embed Card** chuẩn giao diện Facebook (Dark/Light mode), giải quyết dứt điểm lỗi tải ảnh/video thumbnail do CDN Facebook hết hạn token hoặc chặn hotlink/CORS.

---

## 📁 Cấu Trúc Thư Mục Bàn Giao

```
embed_card/
├── media_proxy.py           # Endpoint Python Flask (/api/media-proxy) với Cache & Auto-Retry
├── node_media_proxy.js      # Endpoint Node.js Express tương ứng nếu backend dùng Node
├── FacebookEmbedCard.jsx    # React / Next.js Component với Tailwind CSS & full animations
├── facebook-embed-card.js   # Web Component chuẩn (<facebook-embed-card>) cho Vue / Svelte / Vanilla JS
├── test_cases.json          # 5 bộ dữ liệu test mẫu (Text only, 1 ảnh, Grid +N, Video, Lỗi Fallback)
└── README.md                # Tài liệu hướng dẫn chi tiết
```

---

## 🛠️ Phần 1: Giải Pháp Server-Side Image Proxy

### 1. Tại sao ảnh Facebook thường bị lỗi?
- URL ảnh CDN của Facebook (`scontent.xx.fbcdn.net`) có kèm chữ ký và thời hạn hết hạn (`oe=...`, `_nc_sid=...`). Sau một khoảng thời gian, link sẽ trả về `403 Forbidden`.
- Khi nhúng thẻ `<img src="...">` trực tiếp trên website/chatbot khác, trình duyệt gửi header `Referer` của domain bạn, Facebook phát hiện và kích hoạt cơ chế chống hotlink (Hotlink Protection).

### 2. Nguyên lý hoạt động của Proxy (`media_proxy.py`)
1. **Header chuẩn:** Thiết lập `Referer: https://www.facebook.com/`, `User-Agent: Chrome/126...` và `Sec-Fetch-Dest: image` để vượt qua bộ lọc hotlink của Facebook CDN.
2. **Bộ đệm 2 lớp (In-Memory + Disk Cache):** Băm URL bằng SHA-256. Lưu trữ ảnh trong 24 giờ (`Cache-Control: public, max-age=86400, immutable`), tránh request trùng lặp nhiều lần đến Facebook.
3. **Bảo mật SSRF:** Chỉ cho phép giao thức `http/https` và chặn toàn bộ dải IP private/loopback nội bộ (`127.0.0.1`, `10.x`, `192.168.x`).
4. **Cơ chế Retry:** Nếu gặp lỗi kết nối hoặc timeout, backend tự động retry 1 lần trước khi trả lỗi 502 về client.

### 3. Tích hợp Backend:
- Đã tích hợp sẵn vào file `app.py` trong dự án:
```python
from embed_card.media_proxy import media_proxy_bp
app.register_blueprint(media_proxy_bp)
```
- Khi server Flask chạy, endpoint khả dụng tại: `http://localhost:5000/api/media-proxy?url=<IMAGE_URL>`

---

## 🎨 Phần 2: Giao Diện UI & Client Fallback

### 1. Client-Side Retry & Fallback Tinh Tế
- Khi ảnh tải lần đầu bị lỗi (`onerror`), client tự động thêm param `&_retry=1` để thử lại 1 lần sau 300ms.
- Nếu tiếp tục lỗi, component **KHÔNG** hiển thị icon vỡ mặc định của trình duyệt. Thay vào đó hiển thị một khối card gradient mềm mại, watermark logo Facebook mờ, icon hình ảnh nhẹ nhàng và thông báo: *"Không thể tải xem trước"*. Kèm theo nút bấm *"Thử lại"* cho người dùng.

### 2. Cấu trúc UI Chuẩn Facebook:
- **Header:** Avatar tròn 40x40px, tên trang in đậm, huy hiệu tích xanh chính chủ, thời gian tương đối ("2 giờ trước • 🌐"), nút tùy chọn 3 chấm.
- **Caption:** Giữ nguyên xuống dòng (`whitespace-pre-wrap`), tự động cắt gọn 4 dòng (`line-clamp-4`) với nút bấm "Xem thêm" / "Thu gọn".
- **Media Block:**
  - **Video:** Thumbnail full-width, nút tròn Play ở giữa phủ kính mờ 40% (`backdrop-blur`), nhãn thời lượng video góc dưới phải ("04:18").
  - **1 ảnh:** Tỷ lệ khung hình giữ nguyên, góc bo mềm `8px`, hiệu ứng hover zoom nhẹ.
  - **Nhiều ảnh:** Bố cục lưới 2 cột kiểu Facebook, ảnh thứ 4 có lớp phủ mờ đen `+N` thể hiện số ảnh còn lại.
- **Thanh số liệu tương tác:** Cụm reaction tròn xếp đè lên nhau (👍 Thích, ❤️ Yêu thích, 😆 Haha), số lượt like, số bình luận (icon tin nhắn), số chia sẻ.
- **Thanh nguồn & Nút CTA:**
  - Logo Facebook + "Facebook • [thời gian]" + link xanh "Xem bài gốc".
  - Nút CTA full-width "Xem trên Facebook ↗" với hiệu ứng hover đổi màu nền.
- **Tokens màu sắc:**
  - Dark mode: Nền `#242526`, viền `1px solid rgba(255,255,255,0.08)`, chữ `#e4e6eb`, chữ phụ `#b0b3b8`.
  - Light mode: Nền `#ffffff`, viền `1px solid #e4e6eb`, chữ `#050505`, chữ phụ `#65676b`.

---

## 💻 Cách Sử Dụng Component

### A. Trong dự án React / Next.js / Vite:
```jsx
import FacebookEmbedCard from "./embed_card/FacebookEmbedCard";

function App() {
  const postData = {
    pageName: "Trang Công Nghệ",
    avatarUrl: "https://example.com/avatar.jpg",
    isVerified: true,
    timestamp: "2 giờ trước",
    caption: "Nội dung bài viết Facebook...",
    mediaType: "image", // "none" | "image" | "video" | "carousel"
    mediaUrls: ["https://example.com/image.jpg"],
    likeCount: "14.2K",
    commentCount: "1.8K",
    shareCount: "942",
    postUrl: "https://facebook.com/post/123",
    theme: "dark" // "dark" | "light" | "auto"
  };

  return <FacebookEmbedCard {...postData} />;
}
```

### B. Trong Vue / Svelte / HTML tĩnh / PHP:
Nhúng file `facebook-embed-card.js`:
```html
<script type="module" src="./embed_card/facebook-embed-card.js"></script>

<facebook-embed-card
  page-name="Trang Công Nghệ"
  avatar-url="https://example.com/avatar.jpg"
  timestamp="2 giờ trước"
  caption="Nội dung bài viết..."
  media-type="image"
  media-urls='["https://example.com/image.jpg"]'
  like-count="14.2K"
  comment-count="1.8K"
  share-count="942"
  post-url="https://facebook.com"
  theme="dark"
  is-verified="true">
</facebook-embed-card>
```
