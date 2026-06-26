# 🤖 Facebook → WordPress Bot (Telegram + Gemini AI)

Bot Telegram tự động scrape nội dung từ Facebook, dùng Gemini AI viết lại chuẩn SEO
rồi đăng lên WordPress của bạn.

---

## 📋 BƯỚC 1: Tạo Telegram Bot

1. Mở Telegram → tìm **@BotFather**
2. Gửi lệnh `/newbot`
3. Đặt tên bot (ví dụ: `FB WordPress Bot`)
4. Đặt username (ví dụ: `my_fb_wp_bot`)
5. **Copy Token** dạng: `7123456789:AAFxxx...`

---

## 📋 BƯỚC 2: Lấy Gemini API Key (Miễn phí)

1. Truy cập: https://aistudio.google.com/app/apikey
2. Đăng nhập bằng tài khoản Google
3. Nhấn **"Create API Key"**
4. **Copy API Key**

> ⚡ Giới hạn miễn phí: 15 request/phút, 1500 request/ngày — đủ dùng tốt!

---

## 📋 BƯỚC 3: Tạo WordPress Application Password

1. Đăng nhập **WordPress Admin**
2. Vào **Users → Profile** (hoặc Users → All Users → chọn user admin)
3. Kéo xuống phần **"Application Passwords"**
4. Nhập tên ứng dụng: `Telegram Bot`
5. Nhấn **"Add New Application Password"**
6. **Copy mật khẩu** dạng: `xxxx xxxx xxxx xxxx xxxx xxxx`

> ⚠️ WordPress phải bật REST API (mặc định đã bật với WordPress 5.0+)

---

## 📋 BƯỚC 4: Lấy Telegram User ID của bạn

1. Tìm **@userinfobot** trên Telegram
2. Gửi `/start`
3. Bot sẽ trả về User ID của bạn (ví dụ: `123456789`)

---

## 📋 BƯỚC 5: Deploy lên Railway (Miễn phí)

### 5.1 Upload code lên GitHub

1. Tạo tài khoản [GitHub](https://github.com) nếu chưa có
2. Tạo repository mới (private) tên `fb-wp-bot`
3. Upload toàn bộ file trong thư mục này lên repo đó
   - `bot.py`
   - `requirements.txt`
   - `Procfile`
   - `railway.toml`
   - `.env.example` (không upload `.env` thật!)

### 5.2 Deploy trên Railway

1. Truy cập: https://railway.app
2. Đăng nhập bằng GitHub
3. Nhấn **"New Project" → "Deploy from GitHub repo"**
4. Chọn repo `fb-wp-bot`
5. Railway sẽ tự detect Python và build

### 5.3 Thêm biến môi trường

Trong Railway dashboard → chọn project → tab **"Variables"** → thêm:

| Tên biến | Giá trị |
|----------|---------|
| `TELEGRAM_TOKEN` | Token từ BotFather |
| `GEMINI_API_KEY` | API Key từ Google AI Studio |
| `WP_URL` | `https://yourwebsite.com` |
| `WP_USERNAME` | `admin` (username WP của bạn) |
| `WP_APP_PASSWORD` | Application Password vừa tạo |
| `ALLOWED_USER_IDS` | User ID Telegram của bạn |

### 5.4 Deploy

- Railway tự động deploy sau khi thêm biến
- Kiểm tra **Logs** để đảm bảo bot chạy thành công
- Tìm dòng: `🤖 Bot đang chạy...`

---

## 🎯 CÁCH SỬ DỤNG

### Gửi link Facebook:
```
https://www.facebook.com/pagename/posts/1234567890
```

### Gửi text trực tiếp:
```
Dán nội dung bài viết Facebook vào đây.
Bot sẽ tự xử lý và viết lại chuẩn SEO.
```

### Các lệnh:
- `/start` — Khởi động
- `/help` — Hướng dẫn
- `/status` — Kiểm tra kết nối WordPress

### Luồng hoạt động:
1. Bạn gửi link/text vào Telegram
2. Bot scrape + Gemini viết lại (10-20 giây)
3. Bot hiển thị **preview** gồm: tiêu đề SEO, meta description, từ khóa, tags
4. Bạn chọn:
   - ✅ **Đăng lên WordPress** → Bài được publish ngay
   - 🔄 **Viết lại khác** → Gemini tạo phiên bản mới
   - ❌ **Huỷ** → Bỏ qua
5. Bot gửi link bài vừa đăng cho bạn

---

## ⚠️ LƯU Ý QUAN TRỌNG

### Facebook Scraping:
- Facebook **hạn chế scrape** mạnh từ 2023
- Link public page thường lấy được qua OG tags
- **Cách tốt nhất:** Copy text bài viết → paste vào Telegram
- Bot xử lý cả hai cách

### Bảo mật:
- Luôn điền `ALLOWED_USER_IDS` để chỉ mình bạn dùng được
- Không chia sẻ file `.env` chứa API keys
- Application Password WP chỉ dùng được qua HTTPS

---

## 🔧 NÂNG CẤP THÊM (tùy chọn)

- Thêm **WooCommerce product** thay vì blog post
- Hỗ trợ đặt **danh mục WordPress** tự động
- Hỗ trợ **lên lịch đăng** (schedule post)
- Thêm **Yoast SEO** meta fields qua custom plugin

---

## 📞 Hỗ trợ

Nếu gặp lỗi, kiểm tra:
1. **Railway Logs** → tìm error message
2. Chạy `/status` trên Telegram để test kết nối WP
3. Đảm bảo WordPress REST API bật (thử: `https://yoursite.com/wp-json/wp/v2/posts`)
