/**
 * Facebook Embed Card - Web Component (Custom Element: <facebook-embed-card>)
 * Sử dụng độc lập cho bất kỳ framework nào (Vue, Svelte, Angular, Vanilla JS, HTML tĩnh).
 */

class FacebookEmbedCardElement extends HTMLElement {
  static get observedAttributes() {
    return [
      "page-name",
      "avatar-url",
      "timestamp",
      "caption",
      "media-type",
      "media-urls",
      "video-stream-url",
      "duration",
      "like-count",
      "comment-count",
      "share-count",
      "post-url",
      "is-verified",
      "theme",
      "proxy-endpoint"
    ];
  }

  constructor() {
    super();
    this.isExpanded = false;
    this.retryState = {};
    this.imageErrors = {};
  }

  connectedCallback() {
    this.render();
  }

  attributeChangedCallback(name, oldValue, newValue) {
    if (oldValue !== newValue) {
      this.render();
    }
  }

  getProps() {
    let mediaUrls = [];
    try {
      const attr = this.getAttribute("media-urls");
      if (attr) mediaUrls = JSON.parse(attr);
    } catch (e) {
      mediaUrls = (this.getAttribute("media-urls") || "").split(",").map(s => s.trim()).filter(Boolean);
    }

    return {
      pageName: this.getAttribute("page-name") || "Facebook",
      avatarUrl: this.getAttribute("avatar-url") || "https://ui-avatars.com/api/?name=FB&background=1877f2&color=fff",
      timestamp: this.getAttribute("timestamp") || "Vừa xong",
      caption: this.getAttribute("caption") || "",
      mediaType: this.getAttribute("media-type") || "none",
      mediaUrls,
      videoStreamUrl: this.getAttribute("video-stream-url") || "",
      duration: this.getAttribute("duration") || "",
      likeCount: this.getAttribute("like-count") || "0",
      commentCount: this.getAttribute("comment-count") || null,
      shareCount: this.getAttribute("share-count") || null,
      postUrl: this.getAttribute("post-url") || "https://facebook.com",
      isVerified: this.hasAttribute("is-verified") && this.getAttribute("is-verified") !== "false",
      theme: this.getAttribute("theme") || "dark",
      proxyEndpoint: this.getAttribute("proxy-endpoint") || "/api/media-proxy?url=",
    };
  }

  resolveUrl(url, index = 0) {
    if (!url) return "";
    if (url.startsWith("data:") || url.startsWith("blob:")) return url;
    const { proxyEndpoint } = this.getProps();
    const retries = this.retryState[index] || 0;
    const cacheBust = retries > 0 ? `&_retry=${retries}` : "";
    return `${proxyEndpoint}${encodeURIComponent(url)}${cacheBust}`;
  }

  render() {
    const props = this.getProps();
    const isDark = props.theme === "dark";

    const lines = props.caption.split("\n");
    const isLongCaption = props.caption.length > 200 || lines.length > 4;

    let mediaHtml = "";

    // 1. Single Image
    if (props.mediaType === "image" && props.mediaUrls.length > 0) {
      if (this.imageErrors[0]) {
        mediaHtml = this.renderFallbackHtml(0);
      } else {
        mediaHtml = `
          <div class="fb-media-container" style="margin-top:10px; border-radius:8px; overflow:hidden; background:#000;">
            <img 
              src="${this.resolveUrl(props.mediaUrls[0], 0)}" 
              alt="Preview" 
              style="width:100%; max-height:500px; object-fit:cover; display:block;"
              onerror="window.__fbCardHandleImgError(this, ${0})"
            />
          </div>
        `;
      }
    } 
    // 2. Multi-image Grid
    else if (props.mediaType === "carousel" && props.mediaUrls.length > 1) {
      const visible = props.mediaUrls.slice(0, 4);
      const remaining = props.mediaUrls.length - 4;
      mediaHtml = `
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:3px; margin-top:10px; border-radius:8px; overflow:hidden;">
          ${visible.map((url, idx) => {
            if (this.imageErrors[idx]) return this.renderFallbackHtml(idx);
            const isLast = idx === 3 && remaining > 0;
            return `
              <div style="position:relative; aspect-ratio:1/1; overflow:hidden;">
                <img 
                  src="${this.resolveUrl(url, idx)}" 
                  alt="Photo" 
                  style="width:100%; height:100%; object-fit:cover; display:block;"
                  onerror="window.__fbCardHandleImgError(this, ${idx})"
                />
                ${isLast ? `<div style="position:absolute; inset:0; background:rgba(0,0,0,0.6); display:flex; align-items:center; justify-content:center; color:#fff; font-size:24px; font-weight:bold;">+${remaining + 1}</div>` : ''}
              </div>
            `;
          }).join("")}
        </div>
      `;
    }
    // 3. Video
    else if (props.mediaType === "video" && props.mediaUrls.length > 0) {
      if (this.imageErrors[0]) {
        mediaHtml = this.renderFallbackHtml(0);
      } else {
        mediaHtml = `
          <div style="position:relative; margin-top:10px; border-radius:8px; overflow:hidden; aspect-ratio:16/9; background:#000;">
            <img 
              src="${this.resolveUrl(props.mediaUrls[0], 0)}" 
              alt="Thumbnail" 
              style="width:100%; height:100%; object-fit:cover; display:block; opacity:0.9;"
              onerror="window.__fbCardHandleImgError(this, ${0})"
            />
            <div style="position:absolute; inset:0; display:flex; align-items:center; justify-content:center;">
              <div style="width:56px; height:56px; border-radius:50%; background:rgba(0,0,0,0.45); border:1.5px solid rgba(255,255,255,0.7); display:flex; align-items:center; justify-content:center;">
                <svg style="width:24px; height:24px; fill:#fff; margin-left:3px;" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
              </div>
            </div>
            ${props.duration ? `<div style="position:absolute; bottom:8px; right:8px; padding:2px 6px; border-radius:4px; font-size:11px; font-weight:bold; background:rgba(0,0,0,0.75); color:#fff;">${props.duration}</div>` : ''}
          </div>
        `;
      }
    }

    const bgCard = isDark ? "#242526" : "#ffffff";
    const borderCard = isDark ? "rgba(255,255,255,0.08)" : "#e4e6eb";
    const textPrimary = isDark ? "#e4e6eb" : "#050505";
    const textSecondary = isDark ? "#b0b3b8" : "#65676b";
    const divider = isDark ? "rgba(255,255,255,0.08)" : "#e4e6eb";

    this.innerHTML = `
      <div class="fb-embed-card" style="
        width: 100%;
        max-width: 520px;
        background: ${bgCard};
        color: ${textPrimary};
        border: 1px solid ${borderCard};
        border-radius: 12px;
        padding: 14px 16px;
        box-shadow: 0 4px 16px rgba(0,0,0,0.2);
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        box-sizing: border-box;
      ">
        <!-- Header -->
        <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:10px;">
          <div style="display:flex; align-items:center; gap:10px;">
            <img src="${props.avatarUrl}" style="width:40px; height:40px; border-radius:50%; object-fit:cover;" onerror="this.src='https://ui-avatars.com/api/?name=FB'" />
            <div>
              <div style="display:flex; align-items:center; gap:4px;">
                <span style="font-weight:bold; font-size:14.5px; color:${textPrimary};">${this.escape(props.pageName)}</span>
                ${props.isVerified ? `<svg style="width:14px; height:14px; fill:#1877f2;" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>` : ''}
              </div>
              <div style="font-size:12px; color:${textSecondary}; display:flex; align-items:center; gap:4px;">
                <span>${props.timestamp}</span> • <span>🌐</span>
              </div>
            </div>
          </div>
          <span style="color:${textSecondary}; font-size:18px; cursor:pointer;">•••</span>
        </div>

        <!-- Caption -->
        <div style="font-size:14px; line-height:1.45; white-space:pre-wrap; margin-bottom:8px; color:${textPrimary};">
          <span class="fb-caption-text">${this.escape(props.caption)}</span>
        </div>

        <!-- Media -->
        ${mediaHtml}

        <!-- Stats Bar -->
        <div style="display:flex; justify-content:space-between; font-size:12px; color:${textSecondary}; margin-top:12px; padding-top:8px; border-top:1px solid ${divider};">
          <div style="display:flex; align-items:center; gap:4px;">
            <span style="display:inline-flex; width:16px; height:16px; border-radius:50%; background:#1877f2; align-items:center; justify-content:center; color:#fff; font-size:10px;">👍</span>
            <span style="display:inline-flex; width:16px; height:16px; border-radius:50%; background:#fa383e; align-items:center; justify-content:center; color:#fff; font-size:10px;">❤️</span>
            <span style="font-weight:600; margin-left:3px;">${props.likeCount}</span>
          </div>
          <div style="display:flex; gap:12px;">
            ${props.commentCount ? `<span>💬 ${props.commentCount} bình luận</span>` : ''}
            ${props.shareCount ? `<span>↪️ ${props.shareCount} chia sẻ</span>` : ''}
          </div>
        </div>

        <!-- Source Link -->
        <div style="display:flex; justify-content:space-between; font-size:12px; color:${textSecondary}; margin-top:8px; padding-top:8px; border-top:1px solid ${divider};">
          <span>Facebook • ${props.timestamp}</span>
          <a href="${props.postUrl}" target="_blank" rel="noopener" style="color:#2d88ff; text-decoration:none; font-weight:500;">Xem bài gốc</a>
        </div>

        <!-- CTA Button -->
        <div style="margin-top:12px;">
          <a href="${props.postUrl}" target="_blank" rel="noopener" style="
            display:flex; align-items:center; justify-content:center; gap:6px;
            width:100%; padding:8px 0; border-radius:8px;
            background:${isDark ? "rgba(255,255,255,0.06)" : "#f0f2f5"};
            color:${textPrimary};
            font-size:13px; font-weight:600; text-decoration:none;
            border:1px solid ${isDark ? "rgba(255,255,255,0.1)" : "#d8dadf"};
            box-sizing:border-box;
          ">
            Xem trên Facebook ↗
          </a>
        </div>
      </div>
    `;
  }

  renderFallbackHtml(index) {
    return `
      <div style="
        width: 100%;
        min-height: 200px;
        border-radius: 8px;
        background: linear-gradient(135deg, #2a2b2e, #1a1b1e);
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 20px;
        text-align: center;
        box-sizing: border-box;
        margin-top: 10px;
      ">
        <div style="width:40px; height:40px; border-radius:50%; background:rgba(255,255,255,0.08); display:flex; align-items:center; justify-content:center; margin-bottom:8px; color:#b0b3b8;">
          🖼️
        </div>
        <div style="font-size:13px; font-weight:600; color:#e4e6eb;">Không thể tải xem trước</div>
        <div style="font-size:11px; color:#b0b3b8; margin-top:4px;">Ảnh đã hết hạn chữ ký hoặc bị bảo mật Facebook chặn</div>
      </div>
    `;
  }

  escape(str) {
    return (str || "").replace(/[&<>"']/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[m]));
  }
}

// Global handler cho lỗi ảnh của Web Component
window.__fbCardHandleImgError = function(imgEl, index) {
  const card = imgEl.closest("facebook-embed-card");
  if (!card) return;
  const currentRetries = card.retryState[index] || 0;
  if (currentRetries === 0) {
    card.retryState[index] = 1;
    setTimeout(() => {
      imgEl.src = card.resolveUrl(imgEl.src, 1);
    }, 400);
  } else {
    card.imageErrors[index] = true;
    card.render();
  }
};

if (typeof customElements !== "undefined" && !customElements.get("facebook-embed-card")) {
  customElements.define("facebook-embed-card", FacebookEmbedCardElement);
}

export default FacebookEmbedCardElement;
