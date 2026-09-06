import React, { useState, useRef } from "react";

/**
 * FacebookEmbedCard Component (Chuẩn giao diện Facebook 2026)
 *
 * @param {Object} props
 * @param {string} props.pageName - Tên trang / Tác giả
 * @param {string} [props.avatarUrl] - Link ảnh đại diện
 * @param {string} props.timestamp - Thời gian đăng (vd: "2 giờ trước", "Hôm qua lúc 14:20")
 * @param {string} [props.caption] - Nội dung văn bản / caption
 * @param {"none"|"image"|"video"|"carousel"} [props.mediaType="none"] - Loại media đính kèm
 * @param {string[]} [props.mediaUrls=[]] - Danh sách URL ảnh / thumbnail video
 * @param {string} [props.videoStreamUrl] - URL stream video trực tiếp (.mp4) nếu có
 * @param {string} [props.duration] - Thời lượng video (vd: "03:45")
 * @param {number|string} [props.likeCount] - Số lượng likes / reactions
 * @param {number|string} [props.commentCount] - Số lượng bình luận
 * @param {number|string} [props.shareCount] - Số lượt chia sẻ
 * @param {string} props.postUrl - Đường dẫn bài viết gốc trên Facebook
 * @param {boolean} [props.isVerified=false] - Hiển thị tích xanh trang chính thức
 * @param {"dark"|"light"|"auto"} [props.theme="dark"] - Chế độ giao diện (mặc định Dark Mode)
 * @param {string} [props.proxyEndpoint="/api/media-proxy?url="] - Server-side proxy URL
 */
export default function FacebookEmbedCard({
  pageName,
  avatarUrl,
  timestamp,
  caption = "",
  mediaType = "none",
  mediaUrls = [],
  videoStreamUrl,
  duration,
  likeCount = 0,
  commentCount,
  shareCount,
  postUrl,
  isVerified = false,
  theme = "dark",
  proxyEndpoint = "/api/media-proxy?url=",
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [playingVideo, setPlayingVideo] = useState(false);
  const [imageErrors, setImageErrors] = useState({}); // { [index]: true }
  const [retryCounts, setRetryCounts] = useState({}); // { [index]: number }
  const [retryingIndex, setRetryingIndex] = useState(null);

  // Helper bọc URL qua Server-side Image Proxy để chống CORS & link CDN hết hạn
  const getProxiedUrl = (url, retryNum = 0) => {
    if (!url) return "";
    // Nếu là URL nội bộ hoặc blob data thì không bọc proxy
    if (url.startsWith("data:") || url.startsWith("blob:")) return url;
    
    // Nếu proxyEndpoint rỗng hoặc URL đã trỏ tới proxy
    if (!proxyEndpoint || url.includes("/api/media-proxy")) {
      return retryNum > 0 ? `${url}${url.includes("?") ? "&" : "?"}_retry=${retryNum}` : url;
    }

    const separator = proxyEndpoint.includes("?") ? "&" : "?";
    const cacheBuster = retryNum > 0 ? `${separator}_retry=${retryNum}` : "";
    return `${proxyEndpoint}${encodeURIComponent(url)}${cacheBuster}`;
  };

  // Xử lý khi ảnh bị lỗi (onerror) - Tự động retry 1 lần trước khi fallback
  const handleImageError = (index, originalUrl) => {
    const currentRetries = retryCounts[index] || 0;

    if (currentRetries === 0) {
      // Retry lần 1 sau 400ms
      setRetryCounts((prev) => ({ ...prev, [index]: 1 }));
      setRetryingIndex(index);
      setTimeout(() => {
        setRetryingIndex(null);
      }, 500);
    } else {
      // Đã thử lại 1 lần vẫn lỗi -> Đánh dấu hiển thị Fallback UI
      setImageErrors((prev) => ({ ...prev, [index]: true }));
    }
  };

  // Thử lại thủ công khi người dùng bấm nút "Thử lại" trên Fallback card
  const handleManualRetry = (e, index) => {
    e.stopPropagation();
    setImageErrors((prev) => ({ ...prev, [index]: false }));
    setRetryCounts((prev) => ({ ...prev, [index]: 0 }));
    setRetryingIndex(index);
    setTimeout(() => {
      setRetryingIndex(null);
    }, 600);
  };

  // Điều hướng khi click vào card (ngoại trừ các nút bấm tương tác bên trong)
  const handleCardClick = (e) => {
    if (e.target.closest("button") || e.target.closest("a") || e.target.closest("video")) {
      return;
    }
    if (postUrl) {
      window.open(postUrl, "_blank", "noopener,noreferrer");
    }
  };

  // Render Khối Fallback tinh tế khi ảnh không tải được
  const renderFallback = (index) => (
    <div className="w-full aspect-[16/9] min-h-[220px] rounded-lg overflow-hidden flex flex-col items-center justify-center relative p-6 text-center select-none bg-gradient-to-br from-[#2a2b2e] via-[#202124] to-[#1a1b1e] dark:from-[#2a2b2e] dark:to-[#1a1b1e] light:from-[#f0f2f5] light:to-[#e4e6eb] border border-white/5 dark:border-white/5 light:border-black/10">
      {/* Watermark Facebook mờ sang trọng */}
      <div className="absolute inset-0 flex items-center justify-center opacity-[0.04] pointer-events-none">
        <svg className="w-48 h-48 fill-current text-white dark:text-white light:text-black" viewBox="0 0 24 24">
          <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/>
        </svg>
      </div>

      <div className="relative z-10 flex flex-col items-center max-w-xs">
        <div className="w-12 h-12 rounded-full bg-white/5 dark:bg-white/5 light:bg-black/5 flex items-center justify-center text-white/50 dark:text-white/50 light:text-black/50 mb-2.5">
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
          </svg>
        </div>
        <p className="text-sm font-medium text-[#e4e6eb] dark:text-[#e4e6eb] light:text-[#050505]">Không thể tải xem trước</p>
        <p className="text-[11.5px] text-[#b0b3b8] dark:text-[#b0b3b8] light:text-[#65676b] mt-1 leading-snug">
          Ảnh có thể đã hết hạn chữ ký hoặc bị hạn chế bởi Facebook
        </p>

        <button
          onClick={(e) => handleManualRetry(e, index)}
          className="mt-3 px-3 py-1 text-xs font-medium rounded-md bg-white/10 dark:bg-white/10 light:bg-black/10 hover:bg-white/20 text-[#e4e6eb] dark:text-[#e4e6eb] light:text-[#050505] transition flex items-center gap-1.5 border border-white/10"
        >
          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
          </svg>
          <span>Thử lại</span>
        </button>
      </div>
    </div>
  );

  // Render Loading Skeleton
  const renderSkeleton = () => (
    <div className="w-full aspect-[16/9] rounded-lg animate-pulse bg-white/5 flex items-center justify-center">
      <span className="text-xs text-[#b0b3b8]">Đang tải media...</span>
    </div>
  );

  const isDark = theme === "dark" || (theme === "auto" && typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches);

  return (
    <article
      onClick={handleCardClick}
      className={`w-full max-w-[540px] rounded-xl p-3.5 sm:p-4 transition-all duration-200 cursor-pointer select-none border font-sans ${
        isDark
          ? "bg-[#242526] text-[#e4e6eb] border-white/10 shadow-[0_4px_20px_rgba(0,0,0,0.35)] hover:border-white/20"
          : "bg-white text-[#050505] border-[#e4e6eb] shadow-sm hover:border-[#ccd0d5]"
      }`}
    >
      {/* 1. HEADER: Avatar tròn (40x40) + Tên trang/người đăng + Tích xanh + Timestamp */}
      <header className="flex items-center justify-between gap-3 mb-2.5">
        <div className="flex items-center gap-2.5 min-w-0">
          <img
            src={avatarUrl || "https://ui-avatars.com/api/?name=FB&background=1877f2&color=fff"}
            alt={pageName}
            className="w-10 h-10 rounded-full object-cover border border-white/10 flex-shrink-0"
            onError={(e) => {
              e.currentTarget.src = "https://ui-avatars.com/api/?name=FB&background=1877f2&color=fff";
            }}
          />
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className={`font-bold text-[14.5px] leading-snug truncate hover:underline ${isDark ? "text-[#e4e6eb]" : "text-[#050505]"}`}>
                {pageName}
              </span>
              {isVerified && (
                <svg className="w-3.5 h-3.5 text-[#1877f2] flex-shrink-0" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
                </svg>
              )}
            </div>
            <div className={`flex items-center gap-1 text-xs ${isDark ? "text-[#b0b3b8]" : "text-[#65676b]"}`}>
              <span>{timestamp}</span>
              <span>•</span>
              {/* Globe Icon */}
              <svg className="w-3 h-3 opacity-80" fill="currentColor" viewBox="0 0 16 16">
                <path d="M8 0a8 8 0 1 0 0 16A8 8 0 0 0 8 0zm6.93 7h-2.97a13.3 13.3 0 0 0-.91-4.04A6.97 6.97 0 0 1 14.93 7zM8 1.07c.88 1.4 1.55 3.5 1.77 5.93H6.23C6.45 4.57 7.12 2.47 8 1.07zM1.07 9h2.97c.12 1.45.45 2.83.91 4.04A6.97 6.97 0 0 1 1.07 9zm2.97-2H1.07A6.97 6.97 0 0 1 4.95 2.96 13.3 13.3 0 0 0 4.04 7zm4.99 7.93c-.88-1.4-1.55-3.5-1.77-5.93h3.54c-.22 2.43-.89 4.53-1.77 5.93zm2.09-1.89c.46-1.21.79-2.59.91-4.04h2.97a6.97 6.97 0 0 1-3.88 4.04z"/>
              </svg>
            </div>
          </div>
        </div>

        {/* 3 Dots Menu Button */}
        <button
          type="button"
          aria-label="Tùy chọn khác"
          className="p-1.5 rounded-full hover:bg-white/10 text-[#b0b3b8] transition"
        >
          <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
            <path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zM12 10a2 2 0 11-4 0 2 2 0 014 0zM16 12a2 2 0 100-4 2 2 0 000 4z"/>
          </svg>
        </button>
      </header>

      {/* 2. NỘI DUNG TEXT (CAPTION): Giữ nguyên xuống dòng gốc, cắt bớt sau 4 dòng kèm "Xem thêm" */}
      {caption && (
        <div className="mb-2.5 text-[14px] leading-relaxed">
          <p className={`whitespace-pre-wrap ${!isExpanded ? "line-clamp-4" : ""} ${isDark ? "text-[#e4e6eb]" : "text-[#050505]"}`}>
            {caption}
          </p>
          {(caption.length > 200 || caption.split("\n").length > 4) && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setIsExpanded(!isExpanded);
              }}
              className="text-[13.5px] font-semibold text-[#1877f2] dark:text-[#4599ff] hover:underline mt-1 focus:outline-none"
            >
              {isExpanded ? "Thu gọn" : "Xem thêm"}
            </button>
          )}
        </div>
      )}

      {/* 3. MEDIA BLOCK */}
      {/* 3.1: Video Player / Thumbnail with Centered Play Button */}
      {mediaType === "video" && mediaUrls.length > 0 && (
        <div className="mt-2.5 rounded-lg overflow-hidden relative group bg-black">
          {playingVideo && videoStreamUrl ? (
            <video
              controls
              autoPlay
              className="w-full aspect-video object-contain"
              onClick={(e) => e.stopPropagation()}
            >
              <source src={videoStreamUrl} type="video/mp4" />
              Trình duyệt không hỗ trợ phát video.
            </video>
          ) : imageErrors[0] ? (
            renderFallback(0)
          ) : (
            <div
              className="relative cursor-pointer aspect-video"
              onClick={(e) => {
                e.stopPropagation();
                if (videoStreamUrl) setPlayingVideo(true);
                else if (postUrl) window.open(postUrl, "_blank");
              }}
            >
              <img
                src={getProxiedUrl(mediaUrls[0], retryCounts[0] || 0)}
                alt="Video thumbnail"
                loading="lazy"
                className="w-full h-full object-cover transition-all duration-300 opacity-90 group-hover:opacity-100"
                onError={() => handleImageError(0, mediaUrls[0])}
              />
              {/* Centered Circular Play Button */}
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="w-14 h-14 rounded-full bg-black/45 backdrop-blur-md border border-white/70 flex items-center justify-center shadow-lg group-hover:scale-110 group-hover:bg-black/60 transition-all duration-200">
                  <svg className="w-6 h-6 text-white ml-0.5 fill-current" viewBox="0 0 24 24">
                    <path d="M8 5v14l11-7z"/>
                  </svg>
                </div>
              </div>
              {/* Duration Badge */}
              {duration && (
                <div className="absolute bottom-2 right-2 px-2 py-0.5 rounded text-[11px] font-semibold bg-black/75 text-white backdrop-blur-sm">
                  {duration}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* 3.2: 1 Ảnh (Single Image Full-Width) */}
      {mediaType === "image" && mediaUrls.length > 0 && (
        <div className="mt-2.5 rounded-lg overflow-hidden relative group bg-black/10">
          {imageErrors[0] ? (
            renderFallback(0)
          ) : (
            <img
              src={getProxiedUrl(mediaUrls[0], retryCounts[0] || 0)}
              alt="Facebook preview"
              loading="lazy"
              className="w-full max-h-[520px] object-cover transition-transform duration-300 group-hover:scale-[1.01]"
              onError={() => handleImageError(0, mediaUrls[0])}
            />
          )}
        </div>
      )}

      {/* 3.3: Nhiều ảnh (Multi-image Facebook Grid với +N overlay) */}
      {mediaType === "carousel" && mediaUrls.length > 1 && (
        <div className="mt-2.5 rounded-lg overflow-hidden grid grid-cols-2 gap-1 bg-black/20">
          {mediaUrls.slice(0, 4).map((imgUrl, idx) => {
            const isLast = idx === 3 && mediaUrls.length > 4;
            const remainingCount = mediaUrls.length - 4;
            return (
              <div key={idx} className="relative aspect-square overflow-hidden group">
                {imageErrors[idx] ? (
                  renderFallback(idx)
                ) : (
                  <>
                    <img
                      src={getProxiedUrl(imgUrl, retryCounts[idx] || 0)}
                      alt={`Photo ${idx + 1}`}
                      loading="lazy"
                      className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
                      onError={() => handleImageError(idx, imgUrl)}
                    />
                    {isLast && (
                      <div className="absolute inset-0 bg-black/60 backdrop-blur-[2px] flex items-center justify-center text-white font-bold text-2xl tracking-wide group-hover:bg-black/50 transition">
                        +{remainingCount + 1}
                      </div>
                    )}
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* 4. HÀNG SỐ LIỆU TƯƠNG TÁC (REACTIONS, COMMENTS, SHARES) */}
      <div className={`mt-3 pt-2 flex items-center justify-between text-xs border-t ${isDark ? "border-white/10 text-[#b0b3b8]" : "border-black/10 text-[#65676b]"}`}>
        {/* Left: Reaction Icons Stacked */}
        <div className="flex items-center gap-1.5 cursor-pointer">
          <div className="flex items-center -space-x-1">
            <span className={`inline-flex items-center justify-center w-4 h-4 rounded-full bg-[#1877f2] text-white ring-1 ${isDark ? "ring-[#242526]" : "ring-white"}`}>
              <svg className="w-2.5 h-2.5 fill-current" viewBox="0 0 24 24"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>
            </span>
            <span className={`inline-flex items-center justify-center w-4 h-4 rounded-full bg-[#fa383e] text-white ring-1 ${isDark ? "ring-[#242526]" : "ring-white"}`}>
              <svg className="w-2.5 h-2.5 fill-current" viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>
            </span>
          </div>
          <span className="font-medium text-[12.5px] hover:underline">{likeCount}</span>
        </div>

        {/* Right: Comments & Shares */}
        <div className="flex items-center gap-3">
          {commentCount !== undefined && (
            <div className="flex items-center gap-1 hover:underline cursor-pointer">
              <svg className="w-3.5 h-3.5 opacity-80" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
              </svg>
              <span>{commentCount} bình luận</span>
            </div>
          )}
          {shareCount !== undefined && (
            <div className="flex items-center gap-1 hover:underline cursor-pointer">
              <svg className="w-3.5 h-3.5 opacity-80" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z"/>
              </svg>
              <span>{shareCount} chia sẻ</span>
            </div>
          )}
        </div>
      </div>

      {/* 5. HÀNG NGUỒN (SOURCE ROW): Icon FB + "Facebook • [time]" + "Xem bài gốc" */}
      <div className={`mt-2.5 pt-2 flex items-center justify-between text-xs border-t ${isDark ? "border-white/10 text-[#b0b3b8]" : "border-black/10 text-[#65676b]"}`}>
        <div className="flex items-center gap-1.5">
          <svg className="w-3.5 h-3.5 text-[#1877f2]" viewBox="0 0 24 24" fill="currentColor">
            <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/>
          </svg>
          <span>Facebook • {timestamp}</span>
        </div>
        <a
          href={postUrl}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          className="text-[#1877f2] dark:text-[#4599ff] hover:underline font-medium"
        >
          Xem bài gốc
        </a>
      </div>

      {/* 6. NÚT CTA: "Xem trên Facebook ↗" */}
      <div className="mt-3">
        <a
          href={postUrl}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          className={`w-full py-2 px-4 rounded-lg font-semibold text-xs flex items-center justify-center gap-1.5 transition-colors duration-150 border ${
            isDark
              ? "bg-white/[0.06] hover:bg-white/[0.12] text-[#e4e6eb] border-white/10"
              : "bg-[#f0f2f5] hover:bg-[#e4e6eb] text-[#050505] border-[#d8dadf]"
          }`}
        >
          <span>Xem trên Facebook</span>
          <svg className="w-3.5 h-3.5 opacity-80" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
          </svg>
        </a>
      </div>
    </article>
  );
}
