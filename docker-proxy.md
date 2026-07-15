Bước 1: Chuẩn bị môi trường
Bạn cần đảm bảo máy chủ (VPS) hoặc máy tính của bạn đã được cài đặt Docker và Docker Compose.

```
sudo apt-get install docker-compose-plugin -y
```

Nếu bạn dùng Linux (Ubuntu/CentOS), hãy chắc chắn đã chạy lệnh cài đặt Docker trước.

Bước 2: Tạo không gian làm việc
Mở terminal (hoặc SSH vào VPS của bạn) và tạo một thư mục riêng để chứa toàn bộ cấu hình Proxy, sau đó di chuyển vào thư mục đó:

```
mkdir my-proxy-server
cd my-proxy-server
```

Bước 3: Tạo tài khoản và mật khẩu
Chúng ta sẽ tạo một file chứa thông tin đăng nhập. Chạy lệnh dưới đây trong terminal.
Thay myuser bằng Tên đăng nhập và mypassword bằng Mật khẩu bạn muốn đặt:

```
docker run --rm httpd:alpine htpasswd -nb myuser mypassword > squid_passwd
```

Lệnh này sẽ tự động tạo ra một file tên là squid_passwd trong thư mục hiện tại, chứa mật khẩu đã được mã hóa.

Bước 4: Tạo file cấu hình Proxy (squid.conf)
Dùng trình soạn thảo văn bản (như nano hoặc vim) để tạo file squid.conf:

```
nano squid.conf
```

Sau đó, copy và dán toàn bộ đoạn cấu hình chuẩn dưới đây vào file:

```
# Lắng nghe ở cổng 3128 (bên trong container)
http_port 3128

# Cấu hình đọc file mật khẩu để xác thực
auth_param basic program /usr/lib/squid/basic_ncsa_auth /etc/squid/squid_passwd
auth_param basic children 5
auth_param basic realm Proxy Security
auth_param basic credentialsttl 2 hours

# Bắt buộc người dùng phải có tài khoản
acl authenticated proxy_auth REQUIRED

# Quy tắc: Cho phép người có tài khoản, chặn tất cả những ai không có
http_access allow authenticated
http_access deny all
```
(Lưu file lại: Nếu dùng nano, bấm Ctrl+O -> Enter để lưu, sau đó Ctrl+X để thoát).

Bước 5: Tạo file docker-compose.yml
Tạo file để quản lý container Docker:
```
nano docker-compose.yml
```
Dán nội dung sau vào file. Ở đây, mình sẽ cấu hình cổng ra bên ngoài là 8080 để giống hệt với bức ảnh bạn đã chụp:
```
version: '3.8'

services:
  proxy:
    image: ubuntu/squid:latest
    container_name: proxy-server
    restart: always
    ports:
      # Ánh xạ cổng 8080 của máy chủ vào cổng 3128 của Proxy
      - "8080:3128" 
    volumes:
      - ./squid.conf:/etc/squid/squid.conf:ro
      - ./squid_passwd:/etc/squid/squid_passwd:ro
```
(Lưu và thoát tương tự Bước 4).

Bước 6: Khởi chạy hệ thống Proxy
Đến bước này, trong thư mục my-proxy-server của bạn đã có đủ 3 file: squid_passwd, squid.conf và docker-compose.yml.

Hãy gõ lệnh sau để khởi chạy proxy ngầm:
```
docker-compose up -d
```
Hệ thống sẽ tải image về (nếu chưa có) và bật Proxy lên.

Bước 7: Nhập thông tin vào phần mềm theo ảnh của bạn
Bây giờ proxy đã chạy thành công, hãy mở phần mềm cấu hình trên máy tính/điện thoại của bạn và điền vào form "Add Proxy" như sau:

Name: My Personal Proxy (Hoặc bất cứ tên nào bạn thích).

Type: Chọn HTTP.

Host: Điền địa chỉ IP của cái máy chủ/VPS nơi bạn vừa gõ các lệnh cài đặt Docker ở trên (Ví dụ: 14.22.33.44). Nếu bạn cài trực tiếp trên máy tính đang dùng, hãy điền 127.0.0.1.

Port: Điền 8080 (Vì ở Bước 5, chúng ta đã mở port 8080 ra ngoài).

Username: Điền myuser (Đã cấu hình ở Bước 3).

Password: Điền mypassword (Đã cấu hình ở Bước 3).
